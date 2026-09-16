from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


ROOT = Path(__file__).resolve().parent
DB_PATH = ROOT / "experiments.db"
JOBS: dict[str, dict[str, Any]] = {}
JOBS_LOCK = threading.Lock()
DB_LOCK = threading.Lock()

ROLES = [
    ("planner", "规划 Agent", "拆解问题、识别约束、定义可检验的决策标准"),
    ("researcher", "研究 Agent", "寻找事实缺口、比较方案并标注不确定性"),
    ("user", "用户代表", "从实际使用者、采用阻力和学习成本角度评估"),
    ("finance", "成本 Agent", "评估直接成本、迁移成本与机会成本"),
    ("risk", "风险 Agent", "寻找失败路径、反例、依赖和不可逆风险"),
    ("executor", "执行 Agent", "把建议转化为阶段、负责人和验收条件"),
    ("critic", "批判 Agent", "检查逻辑跳跃、证据不足与群体偏差"),
    ("tester", "验证 Agent", "按照约束逐项验证候选方案"),
    ("reviewer", "评审 Agent", "比较方案并提出可操作的修正意见"),
    ("judge", "裁判 Agent", "依据统一量表评分并作出最终裁决"),
]

PERTURBATIONS = {
    "none": "无额外扰动",
    "D1": "执行中途新增一个关键约束，要求方案能够重规划",
    "D2": "关键资料出现冲突或暂时不可用，要求显式处理不确定性",
    "D3": "一个 Agent 的阶段产出低质或缺失，要求识别并恢复",
    "D4": "一次外部工具调用失败，要求提供降级路径",
}

TASKS_FILE = ROOT / "tasks.json"


def load_task_bank() -> list[dict[str, Any]]:
    if not TASKS_FILE.exists():
        return []
    try:
        with TASKS_FILE.open(encoding="utf-8") as fh:
            data = json.load(fh)
        tasks = data.get("tasks", data if isinstance(data, list) else [])
        return [task for task in tasks if isinstance(task, dict)]
    except (json.JSONDecodeError, OSError):
        return []


TASK_BANK: list[dict[str, Any]] = load_task_bank()
TASK_BY_ID: dict[str, dict[str, Any]] = {task.get("id"): task for task in TASK_BANK if task.get("id")}


def task_summaries() -> list[dict[str, Any]]:
    return [
        {
            "id": task.get("id"),
            "type": task.get("type"),
            "title": task.get("title"),
            "difficulty": task.get("difficulty"),
            "coupling": task.get("coupling"),
            "perturbation": task.get("perturbation"),
            "success_criteria": task.get("success_criteria"),
            "problem": task.get("problem"),
            "role": task.get("role"),
        }
        for task in TASK_BANK
    ]


def apply_task_to_config(config: dict[str, Any], task_id: str) -> dict[str, Any]:
    task = TASK_BY_ID.get(task_id)
    if not task:
        raise ValueError(f"任务不存在: {task_id}")
    merged = dict(config)
    merged["task_id"] = task_id
    merged["problem"] = str(task.get("problem") or merged.get("problem") or "").strip()
    merged["task_type"] = task.get("type")
    merged["task_title"] = task.get("title")
    merged["coupling"] = task.get("coupling") or merged.get("coupling", "low")
    merged["perturbation"] = task.get("perturbation") or merged.get("perturbation", "none")
    if task.get("perturbation_message"):
        merged["perturbation_message"] = task.get("perturbation_message")
    if task.get("success_criteria"):
        merged["success_criteria"] = task.get("success_criteria")
    if task.get("test_code"):
        merged["test_code"] = task.get("test_code")
    if task.get("answer_spec"):
        merged["answer_spec"] = task.get("answer_spec")
    if task.get("generator"):
        merged["generator"] = task.get("generator")
    if task.get("reference_answer"):
        merged["reference_answer_text"] = str(task.get("reference_answer"))
    return merged

MODEL_PROFILES: dict[str, dict[str, int]] = {
    "deepseek-v4-flash": {"max_input_tokens": 128000, "default_output_tokens": 8192, "max_output_tokens": 32768},
}


def model_profile(model: str) -> dict[str, int]:
    return MODEL_PROFILES.get(model.strip().lower(), {"max_input_tokens": 128000, "default_output_tokens": 1600, "max_output_tokens": 8192})


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def init_db() -> None:
    with sqlite3.connect(DB_PATH) as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS runs (
                id TEXT PRIMARY KEY,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                status TEXT NOT NULL,
                mode TEXT NOT NULL,
                baseline TEXT NOT NULL,
                problem TEXT NOT NULL,
                config_json TEXT NOT NULL,
                result_json TEXT,
                prompt_tokens INTEGER NOT NULL DEFAULT 0,
                completion_tokens INTEGER NOT NULL DEFAULT 0,
                total_tokens INTEGER NOT NULL DEFAULT 0,
                usage_missing INTEGER NOT NULL DEFAULT 0,
                duration_seconds REAL,
                score REAL,
                error TEXT
            );
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id TEXT NOT NULL,
                created_at TEXT NOT NULL,
                kind TEXT NOT NULL,
                agent TEXT,
                message TEXT NOT NULL,
                tokens INTEGER NOT NULL DEFAULT 0,
                payload_json TEXT,
                FOREIGN KEY(run_id) REFERENCES runs(id)
            );
            CREATE INDEX IF NOT EXISTS idx_events_run_id ON events(run_id, id);
            """
        )


def db_execute(sql: str, params: tuple[Any, ...]) -> None:
    with DB_LOCK, sqlite3.connect(DB_PATH) as conn:
        conn.execute(sql, params)
        conn.commit()


def db_query(sql: str, params: tuple[Any, ...] = ()) -> list[sqlite3.Row]:
    with DB_LOCK, sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        return conn.execute(sql, params).fetchall()


def json_text(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def _text_from_value(value: Any) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, list):
        return "\n".join(part for item in value if (part := _text_from_value(item))).strip()
    if isinstance(value, dict):
        for key in ("text", "content", "value", "output_text"):
            if key in value and (text := _text_from_value(value[key])):
                return text
    return ""


def extract_response_content(data: dict[str, Any]) -> tuple[str, str]:
    """Extract text from common OpenAI-compatible response variants."""
    choices = data.get("choices")
    if isinstance(choices, list) and choices and isinstance(choices[0], dict):
        choice = choices[0]
        message = choice.get("message")
        if isinstance(message, dict):
            for field in ("content", "reasoning_content", "reasoning"):
                if text := _text_from_value(message.get(field)):
                    return text, f"choices[0].message.{field}"
        if text := _text_from_value(choice.get("text")):
            return text, "choices[0].text"
    if text := _text_from_value(data.get("output_text")):
        return text, "output_text"
    if text := _text_from_value(data.get("output")):
        return text, "output"
    return "", ""


def response_usage(data: dict[str, Any]) -> tuple[int, int, int, int]:
    usage = data.get("usage")
    if not isinstance(usage, dict) or not usage:
        return 0, 0, 0, 1

    def number(*keys: str) -> int:
        for key in keys:
            try:
                if usage.get(key) is not None:
                    return max(0, int(usage[key]))
            except (TypeError, ValueError):
                continue
        return 0

    prompt_tokens = number("prompt_tokens", "input_tokens")
    completion_tokens = number("completion_tokens", "output_tokens")
    total_tokens = number("total_tokens") or prompt_tokens + completion_tokens
    return prompt_tokens, completion_tokens, total_tokens, 0


def response_diagnostics(data: dict[str, Any], api_key: str = "") -> dict[str, Any]:
    choices = data.get("choices")
    choice = choices[0] if isinstance(choices, list) and choices and isinstance(choices[0], dict) else {}
    message = choice.get("message") if isinstance(choice.get("message"), dict) else {}
    preview = json_text(data)[:2400]
    if api_key:
        preview = preview.replace(api_key, "[REDACTED]")
    preview = re.sub(r"(?i)(bearer\s+)[^\s\"']+", r"\1[REDACTED]", preview)
    return {
        "top_level_fields": sorted(str(key) for key in data.keys()),
        "choice_fields": sorted(str(key) for key in choice.keys()),
        "message_fields": sorted(str(key) for key in message.keys()),
        "finish_reason": choice.get("finish_reason"),
        "response_preview": preview,
    }

def _is_loopback_url(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    return host in {"localhost", "::1"} or host.startswith("127.")


def _open_url(request: urllib.request.Request, timeout: int, retries: int = 4):
    """Open a request, bypassing HTTP(S) proxy for loopback addresses.

    Many local OpenAI-compatible servers (Ollama, vLLM, LM Studio) listen on
    127.0.0.1. Python's urllib would otherwise route localhost through the
    system proxy in some environments and return 502.

    Network-level failures (connection refused/reset/timeout) are retried with
    backoff so a transient outage does not fail an entire experiment run.
    HTTP errors (4xx/5xx) are raised immediately for the caller to handle.
    """
    if _is_loopback_url(request.full_url):
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        return opener.open(request, timeout=timeout)
    last_exc: Exception | None = None
    for attempt in range(retries):
        try:
            return urllib.request.urlopen(request, timeout=timeout)
        except urllib.error.HTTPError:
            raise
        except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
            last_exc = exc
            if attempt < retries - 1:
                time.sleep(10 * (attempt + 1))
    assert last_exc is not None
    raise last_exc



class RunContext:
    def __init__(self, run_id: str):
        self.run_id = run_id

    def event(self, kind: str, message: str, agent: str = "", tokens: int = 0, payload: Any = None) -> None:
        event = {
            "created_at": utc_now(),
            "kind": kind,
            "agent": agent,
            "message": message,
            "tokens": tokens,
            "payload": payload,
        }
        with JOBS_LOCK:
            if self.run_id in JOBS:
                JOBS[self.run_id]["events"].append(event)
                JOBS[self.run_id]["updated_at"] = event["created_at"]
        db_execute(
            "INSERT INTO events(run_id, created_at, kind, agent, message, tokens, payload_json) VALUES(?,?,?,?,?,?,?)",
            (self.run_id, event["created_at"], kind, agent, message, tokens, json_text(payload) if payload is not None else None),
        )


class ModelClient:
    def __init__(self, config: dict[str, Any], ctx: RunContext):
        self.base_url = str(config.get("base_url") or os.getenv("OPENAI_BASE_URL") or "https://api.openai.com/v1").rstrip("/")
        self.api_key = str(config.get("api_key") or os.getenv("OPENAI_API_KEY") or "")
        self.model = str(config.get("model") or os.getenv("OPENAI_MODEL") or "gpt-4.1-mini")
        profile = model_profile(self.model)
        self.timeout = int(config.get("timeout") or 120)
        self.enable_thinking = config.get("enable_thinking")
        self.max_input_tokens = max(1024, min(profile["max_input_tokens"], int(config.get("max_input_tokens") or profile["max_input_tokens"])))
        self.max_output_tokens = max(128, min(profile["max_output_tokens"], int(config.get("max_output_tokens") or profile["default_output_tokens"])))
        self.ctx = ctx
        self.lock = threading.Lock()
        self.prompt_tokens = 0
        self.completion_tokens = 0
        self.total_tokens = 0
        self.usage_missing = 0
        self.call_records: list[dict[str, Any]] = []

    def record_call(
        self,
        *,
        agent: str,
        system: str,
        prompt: str,
        output: str,
        data: dict[str, Any],
        elapsed: float,
        endpoint: str,
        status: str,
        content_source: str = "",
        error: str = "",
        attempt: int = 1,
        requested_max_tokens: int = 0,
    ) -> dict[str, Any]:
        prompt_tokens, completion_tokens, total_tokens, missing = response_usage(data)
        diagnostics = response_diagnostics(data, self.api_key)
        with self.lock:
            self.prompt_tokens += prompt_tokens
            self.completion_tokens += completion_tokens
            self.total_tokens += total_tokens
            self.usage_missing += missing
            record = {
                "sequence": len(self.call_records) + 1,
                "attempt": attempt,
                "requested_max_tokens": requested_max_tokens,
                "agent": agent,
                "system": system,
                "prompt": prompt,
                "max_input_tokens": self.max_input_tokens,
                "output": output,
                "status": status,
                "error": error,
                "content_source": content_source,
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "total_tokens": total_tokens,
                "usage_missing": bool(missing),
                "duration_seconds": elapsed,
                "endpoint": endpoint,
                **diagnostics,
            }
            self.call_records.append(record)
            with JOBS_LOCK:
                if self.ctx.run_id in JOBS:
                    JOBS[self.ctx.run_id]["calls"] = list(self.call_records)
        return record

    def chat_urls(self) -> list[str]:
        raw = self.base_url.rstrip("/")
        path = urlparse(raw).path.rstrip("/")
        if path.endswith("/chat/completions"):
            return [raw]
        if path.endswith("/v1"):
            return [f"{raw}/chat/completions"]
        if not path:
            return [f"{raw}/v1/chat/completions", f"{raw}/chat/completions"]
        return [f"{raw}/chat/completions", f"{raw}/v1/chat/completions"]

    def chat(self, agent: str, system: str, prompt: str, temperature: float = 0.2, json_mode: bool = False, max_tokens: int | None = None) -> str:
        self.ctx.event("agent_start", "开始模型调用", agent)
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": prompt}],
            "temperature": temperature,
            "max_tokens": max(64, min(model_profile(self.model)["max_output_tokens"], int(max_tokens or self.max_output_tokens))),
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
        if isinstance(self.enable_thinking, bool):
            payload["enable_thinking"] = self.enable_thinking
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        def make_request(url: str) -> urllib.request.Request:
            return urllib.request.Request(
                url,
                data=json.dumps(payload).encode("utf-8"),
                headers=headers,
                method="POST",
            )

        def request_once(urls: list[str]) -> tuple[dict[str, Any], str]:
            attempted: list[str] = []
            last_error = ""
            for url in urls:
                attempted.append(url)
                try:
                    with _open_url(make_request(url), self.timeout) as response:
                        response_data = json.loads(response.read().decode("utf-8"))
                    if not isinstance(response_data, dict):
                        raise RuntimeError(f"模型接口 {url} 返回的 JSON 不是对象")
                    if len(attempted) > 1:
                        self.ctx.event("compatibility", f"已自动切换接口路径：{url}", agent)
                    return response_data, url
                except urllib.error.HTTPError as exc:
                    detail = exc.read().decode("utf-8", errors="replace")[:1000] or "响应体为空"
                    last_error = f"HTTP {exc.code}: {detail}"
                    if exc.code == 404 and url != urls[-1]:
                        continue
                    if json_mode and exc.code in {400, 422} and "response_format" in payload:
                        payload.pop("response_format", None)
                        try:
                            with _open_url(make_request(url), self.timeout) as response:
                                response_data = json.loads(response.read().decode("utf-8"))
                            if not isinstance(response_data, dict):
                                raise RuntimeError("返回的 JSON 不是对象")
                            self.ctx.event("compatibility", "接口不支持 JSON mode，已自动降级为普通文本 JSON", agent)
                            return response_data, url
                        except Exception as retry_exc:
                            raise RuntimeError(f"模型接口 {url} 返回 {last_error}；JSON 降级重试失败: {retry_exc}") from retry_exc
                    raise RuntimeError(f"模型接口 {url} 返回 {last_error}") from exc
                except urllib.error.URLError as exc:
                    if isinstance(exc.reason, TimeoutError):
                        raise RuntimeError(f"{agent} 请求 {url} 超时（{self.timeout}s）") from exc
                    raise RuntimeError(f"无法连接模型接口 {url}: {exc.reason}") from exc
                except TimeoutError as exc:
                    raise RuntimeError(f"{agent} 请求 {url} 超时（{self.timeout}s）") from exc
            raise RuntimeError(f"模型接口路径均不可用：{', '.join(attempted)}；最后错误：{last_error}")

        retry_url: str | None = None
        active_prompt = prompt
        last_diagnostics: dict[str, Any] = {}
        for attempt in (1, 2):
            started = time.perf_counter()
            data, selected_url = request_once([retry_url] if retry_url else self.chat_urls())
            elapsed = time.perf_counter() - started
            content, content_source = extract_response_content(data)
            finish_reason = response_diagnostics(data, self.api_key).get("finish_reason")
            if finish_reason == "length" and content_source in {
                "choices[0].message.reasoning_content",
                "choices[0].message.reasoning",
            }:
                content = ""
                content_source = ""
            prompt_tokens, completion_tokens, total_tokens, missing = response_usage(data)
            if content:
                self.record_call(
                    agent=agent, system=system, prompt=active_prompt, output=content, data=data,
                    elapsed=elapsed, endpoint=selected_url, status="completed",
                    content_source=content_source, attempt=attempt,
                    requested_max_tokens=int(payload["max_tokens"]),
                )
                self.ctx.event(
                    "agent_complete",
                    f"完成模型调用 · {elapsed:.1f}s" + (f" · {total_tokens} Token" if not missing else " · usage 未提供"),
                    agent,
                    total_tokens,
                    {"prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens, "usage_missing": bool(missing), "endpoint": selected_url, "content_source": content_source, "attempt": attempt},
                )
                return content.strip()

            last_diagnostics = response_diagnostics(data, self.api_key)
            reason = "模型响应没有可提取的文本内容"
            self.record_call(
                agent=agent, system=system, prompt=active_prompt, output="", data=data,
                elapsed=elapsed, endpoint=selected_url, status="empty",
                error=reason, attempt=attempt,
                requested_max_tokens=int(payload["max_tokens"]),
            )
            self.ctx.event(
                "agent_empty",
                f"{reason} · {elapsed:.1f}s" + (f" · {total_tokens} Token" if not missing else " · usage 未提供"),
                agent,
                total_tokens,
                {"finish_reason": last_diagnostics.get("finish_reason"), "response_fields": last_diagnostics.get("top_level_fields"), "endpoint": selected_url, "attempt": attempt},
            )
            if attempt == 1:
                retry_url = selected_url
                if last_diagnostics.get("finish_reason") == "length":
                    previous_limit = int(payload["max_tokens"])
                    expanded_limit = min(model_profile(self.model)["max_output_tokens"], max(previous_limit * 2, previous_limit + 512))
                    payload["max_tokens"] = expanded_limit
                    active_prompt = (
                        prompt
                        + "\n\n上一次响应因输出达到上限而没有产生最终正文。请压缩分析过程，"
                        "不要输出思维链，直接给出简洁、完整、可执行的最终内容。"
                    )
                    payload["messages"][-1]["content"] = active_prompt
                    self.ctx.event(
                        "agent_retry",
                        f"输出达到 {previous_limit} Token 上限，自动提高到 {expanded_limit} Token 后重试一次",
                        agent,
                    )
                else:
                    self.ctx.event("agent_retry", "检测到空响应，自动重试一次", agent)

        fields = ", ".join(last_diagnostics.get("top_level_fields") or []) or "无"
        finish_reason = last_diagnostics.get("finish_reason") or "未提供"
        raise RuntimeError(f"模型连续两次未返回可提取文本；finish_reason={finish_reason}；响应字段={fields}。详情已记录在 Agent 调用明细中")


def safe_json(text: str) -> dict[str, Any]:
    try:
        value = json.loads(text)
        return value if isinstance(value, dict) else {}
    except json.JSONDecodeError:
        match = re.search(r"\{[\s\S]*\}", text)
        if not match:
            return {}
        try:
            value = json.loads(match.group(0))
            return value if isinstance(value, dict) else {}
        except json.JSONDecodeError:
            return {}


def compact_candidates(candidates: list[dict[str, Any]], limit: int = 2600) -> str:
    blocks = []
    for index, item in enumerate(candidates, 1):
        content = str(item.get("content") or "")[:limit]
        blocks.append(f"候选 {index}｜{item.get('agent', 'Agent')}\n{content}")
    return "\n\n".join(blocks)


def role_system(role_name: str, specialty: str) -> str:
    return (
        f"你是多 Agent 研究系统中的{role_name}。你的职责是：{specialty}。"
        "只输出可用于团队决策的结论、证据、风险和下一步，不披露隐藏推理过程。"
        "明确区分事实、假设和不确定性，避免空泛赞同。"
    )


def artifact_role_system(role_name: str, specialty: str, artifact_type: str) -> str:
    if artifact_type == "html":
        return (
            f"你是多 Agent 工件生成系统中的{role_name}。你的职责是：{specialty}。"
            "本任务的唯一交付物是一个完整、可独立运行的 HTML 文档，必须使用内联 SVG 或内联脚本实现动画。"
            "你只能输出 HTML 源码，不得输出分析、评审、风险列表、下一步或 Markdown 代码块。"
            "如果发现前序候选有问题，直接给出修正后的完整 HTML 源码。"
        )
    return role_system(role_name, specialty)


def is_html_task(config: dict[str, Any]) -> bool:
    return str(config.get("artifact_type") or "").lower() == "html"


def artifact_contract(config: dict[str, Any]) -> str:
    return (
        "\n\n【交付契约】\n"
        "1. 唯一交付物是一个完整、可独立运行的 HTML 文档；\n"
        "2. 必须使用内联 SVG 绘制图形，使用 animate/animateTransform 或内联 JavaScript 实现循环动画；\n"
        "3. 不得引用外部图片、字体、脚本或网络资源；\n"
        "4. 只输出 HTML 源码本身，不要 Markdown 代码块，不要解释、分析、权衡、执行步骤或退出条件；\n"
        "5. 所有 Agent 的中间产出也要遵守该格式，除非是在评价或修正其他候选。"
    )


def task_prompt(config: dict[str, Any], extra: str = "", reveal_perturbation: bool = False) -> str:
    if is_html_task(config):
        prompt = f"任务：\n{config['problem']}" + artifact_contract(config)
        if reveal_perturbation:
            perturbation = PERTURBATIONS.get(str(config.get("perturbation") or "none"), "无额外扰动")
            perturbation_message = str(config.get("perturbation_message") or "").strip()
            prompt += f"\n\n扰动条件：{perturbation}"
            if perturbation_message:
                prompt += f"\n\n【执行中途扰动】{perturbation_message}"
        if extra:
            prompt += f"\n\n补充上下文：\n{extra}"
        return prompt
    coupling = "高耦合、存在跨步骤依赖" if config.get("coupling") == "high" else "低耦合、子任务可并行"
    prompt = (
        f"研究问题：\n{config['problem']}\n\n"
        f"任务结构：{coupling}\n"
        "请给出具体、可验证的分析。最终建议必须包含选择、关键权衡、执行步骤和失败退出条件。"
    )
    # 扰动只在执行中途注入时向 Agent 揭示，初始提示不提前泄露（实验处理要求）
    if reveal_perturbation:
        perturbation = PERTURBATIONS.get(str(config.get("perturbation") or "none"), "无额外扰动")
        perturbation_message = str(config.get("perturbation_message") or "").strip()
        prompt += f"\n\n扰动条件：{perturbation}"
        if perturbation_message:
            prompt += f"\n\n【执行中途扰动】{perturbation_message}"
    # 注意：success_criteria 只提供给独立评估器，不出现在任务提示中，避免被测系统对评分细则应试
    if extra:
        prompt += f"\n\n补充上下文：\n{extra}"
    return prompt


def call_role(client: ModelClient, config: dict[str, Any], role: tuple[str, str, str], extra: str = "", reveal_perturbation: bool = False) -> dict[str, Any]:
    role_id, role_name, specialty = role
    artifact_type = str(config.get("artifact_type") or "").lower()
    system = artifact_role_system(role_name, specialty, artifact_type) if artifact_type else role_system(role_name, specialty)
    content = client.chat(role_name, system, task_prompt(config, extra, reveal_perturbation), temperature=0.35)
    return {"role": role_id, "agent": role_name, "specialty": specialty, "content": content}


def inject_perturbation(client: ModelClient, config: dict[str, Any], candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """执行中途真实注入扰动：各产出方在不知情产出后，基于扰动修订自己的产出。"""
    message = str(config.get("perturbation_message") or "").strip()
    if not message or not candidates:
        return candidates
    client.ctx.event("perturbation", f"执行中途注入扰动：{message[:100]}", "实验控制器", payload={"perturbation": message})

    def revise(item: dict[str, Any]) -> dict[str, Any]:
        extra = (
            f"你此前的产出：\n{str(item.get('content') or '')[:4000]}\n\n"
            "请结合上述中途扰动修订产出：显式说明哪些结论改变、哪些保留，并给出修订后的完整内容。"
        )
        role = (
            str(item.get("role") or "agent"),
            str(item.get("agent") or "Agent"),
            str(item.get("specialty") or "根据中途扰动修订自己的产出"),
        )
        return call_role(client, config, role, extra, reveal_perturbation=True)

    revised: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=min(5, len(candidates))) as pool:
        futures = {pool.submit(revise, item): item for item in candidates}
        for future in as_completed(futures):
            item = futures[future]
            try:
                revised.append(future.result())
            except Exception as exc:
                client.ctx.event("stage_skip", f"扰动修订失败，保留原产出：{exc}", str(item.get("agent") or "Agent"))
                revised.append(item)
    client.ctx.event("perturbation_done", f"完成 {len(revised)} 份扰动修订", "实验控制器")
    return revised


def parallel_roles(client: ModelClient, config: dict[str, Any], roles: list[tuple[str, str, str]], extra: str = "") -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    errors: list[str] = []
    with ThreadPoolExecutor(max_workers=min(5, len(roles))) as pool:
        futures = {pool.submit(call_role, client, config, role, extra): role for role in roles}
        for future in as_completed(futures):
            role = futures[future]
            try:
                results.append(future.result())
            except Exception as exc:  # preserve partial runs and expose exact failures
                errors.append(f"{role[1]}: {exc}")
                client.ctx.event("agent_error", str(exc), role[1])
    if not results:
        raise RuntimeError("所有 Agent 调用均失败：" + "；".join(errors))
    return results


def vote_candidates(client: ModelClient, config: dict[str, Any], candidates: list[dict[str, Any]], roles: list[tuple[str, str, str]]) -> list[dict[str, Any]]:
    html_task = is_html_task(config)
    candidate_text = compact_candidates(candidates, 6000 if html_task else 700)

    def one_vote(role: tuple[str, str, str]) -> dict[str, Any]:
        if html_task:
            prompt = (
                f"任务：{config['problem']}\n\n"
                f"以下是 {len(candidates)} 个 HTML 候选：\n{candidate_text}\n\n"
                f"请独立投票。只能选择 1 到 {len(candidates)}，优先选择 HTML 结构完整、包含可见鹈鹕和自行车、"
                "动画机制正确且最可能在浏览器中运行、没有明显 JavaScript 选择器错误或语法错误的候选。"
                "仅返回 JSON：{\"choice\": 1, \"score\": 0-100, \"reason\": \"一句话\"}"
            )
        else:
            prompt = (
                f"问题：{config['problem']}\n\n{candidate_text}\n\n"
                f"请独立投票。只能选择 1 到 {len(candidates)}，按约束覆盖、可执行性、风险控制评分。"
                "仅返回 JSON：{\"choice\": 1, \"score\": 0-100, \"reason\": \"一句话\"}"
            )
        raw = client.chat(role[1], artifact_role_system(role[1], role[2], "html") if html_task else role_system(role[1], role[2]), prompt, temperature=0.1, json_mode=True, max_tokens=1024)
        data = safe_json(raw)
        choice = int(data.get("choice") or 1)
        return {"agent": role[1], "choice": max(1, min(len(candidates), choice)), "score": data.get("score"), "reason": data.get("reason", "")}

    votes = []
    with ThreadPoolExecutor(max_workers=min(5, len(roles))) as pool:
        futures = [pool.submit(one_vote, role) for role in roles]
        for future in as_completed(futures):
            try:
                votes.append(future.result())
            except Exception as exc:
                client.ctx.event("vote_error", str(exc), "投票 Agent")
    client.ctx.event("vote", f"收到 {len(votes)} 份有效投票", "投票器", payload=votes)
    return votes


def choose_by_votes(candidates: list[dict[str, Any]], votes: list[dict[str, Any]]) -> dict[str, Any]:
    if not votes:
        return candidates[0]
    counts: dict[int, int] = {}
    for vote in votes:
        choice = int(vote.get("choice") or 1)
        counts[choice] = counts.get(choice, 0) + 1
    winner = max(counts, key=lambda choice: (counts[choice], -choice))
    return candidates[winner - 1]


def judge_candidates(client: ModelClient, config: dict[str, Any], candidates: list[dict[str, Any]], votes: list[dict[str, Any]]) -> str:
    if is_html_task(config):
        prompt = (
            f"任务：{config['problem']}\n\n"
            f"投票记录：{json_text(votes)}\n\n"
            f"候选 HTML（每个候选可能被截断，请优先保留结构和动画最完整的版本）：\n{compact_candidates(candidates, 6000)}\n\n"
            "你是最终工件裁判。请基于投票和候选，输出一个最终 HTML 文档。"
            "如果候选存在明显问题，你可以修复它们，但必须直接输出修复后的完整 HTML 源码。"
            "不要输出分析、选择理由、风险、执行步骤或退出条件，不要使用 Markdown 代码块。"
        )
        return client.chat("裁判 Agent", artifact_role_system("裁判 Agent", "独立裁决并整合团队 HTML 工件", "html"), prompt, temperature=0.15)
    prompt = (
        f"研究问题：{config['problem']}\n\n{compact_candidates(candidates, 900)}\n\n"
        f"投票记录：{json_text(votes)}\n\n"
        "你是最终决策裁判。综合候选而不是简单拼接，给出一个可独立阅读的最终答案。"
        "必须包括：明确选择、关键权衡、执行步骤、风险控制、退出条件。"
    )
    return client.chat("裁判 Agent", role_system("裁判 Agent", "独立裁决并整合团队产出"), prompt, temperature=0.15)


def evaluate_output(client: ModelClient, config: dict[str, Any], final_answer: str, test_report: dict[str, Any] | None = None) -> dict[str, Any]:
    success_criteria = str(config.get("success_criteria") or "未提供客观成功标准，请仅按通用质量标准判断").strip()
    prompt = (
        f"问题：{config['problem']}\n\n待评估答案：\n{final_answer[:10000]}\n\n"
        f"任务的客观成功标准：\n{success_criteria}\n\n"
        "作为独立评估器，按四项各 0-25 分评分：约束覆盖、可执行性、一致性、解释充分度。"
        "不要因文风或长度奖励答案。仅返回 JSON："
        "{\"success\":true或false,\"score\":0-100,\"confidence\":0-1,\"dimensions\":{"
        "\"constraint_coverage\":0-25,\"actionability\":0-25,\"consistency\":0-25,\"explanation\":0-25},"
        "\"summary\":\"一句话评语\"}。"
        "success 表示最终答案是否达到客观成功标准。"
    )
    reference = str(config.get("reference_answer_text") or "").strip()
    if reference:
        prompt += f"\n\n参考答案（仅评估器可见，被测系统从未见过）：\n{reference[:3000]}"
    if test_report and test_report.get("available") and test_report.get("total"):
        failures = "；".join(
            f"{t['name']}: {t.get('error', '')}" for t in test_report.get("tests", []) if not t.get("ok")
        ) or "无"
        prompt += (
            f"\n\n自动化测试客观结果（必须作为评分的主要依据）："
            f"通过 {test_report.get('passed', 0)}/{test_report.get('total', 0)}；失败明细：{failures[:800]}。"
            "评分规则：测试未全部通过时 success 必须为 false；"
            "constraint_coverage 最高不得超过 25 × 测试通过率。"
        )
        if test_report.get("pass_rate") is not None:
            prompt += f"\n测试通过率：{test_report['pass_rate']:.2f}。"
        if test_report.get("fixed_depth") is not None:
            prompt += (f"\n依赖链修复深度（fixed_depth）：{test_report['fixed_depth']}/{test_report.get('stage_total', '?')} "
                       "（从入口阶段起连续修复正确的层数，是核心过程指标）。")
    elif test_report and test_report.get("available") and test_report.get("kind") != "match":
        # 代码任务但测试未能执行（代码无法加载/无测试运行）：视为客观失败，而非退回盲评
        reason = test_report.get("load_error") or test_report.get("error") or "未能运行任何测试"
        prompt += (
            f"\n\n自动化测试客观结果（必须作为评分的主要依据）：答案中的代码无法执行：{str(reason)[:400]}。"
            "这属于客观失败：success 必须为 false，constraint_coverage 不得超过 5。"
        )
    if test_report and test_report.get("kind") == "match":
        prompt += (
            f"\n\n答案精确匹配客观结果（必须作为评分的主要依据）："
            f"匹配得分 {test_report.get('score', 0):.0f}/100；"
            f"完全正确：{'是' if test_report.get('all_correct') else '否'}；明细：{str(test_report.get('detail'))[:800]}。"
            "评分规则：all_correct 为否时 success 必须为 false。"
        )
    raw = client.chat("独立评估器", role_system("独立评估器", "使用固定量表盲评最终答案"), prompt, temperature=0, json_mode=True, max_tokens=2048)
    data = safe_json(raw)
    score = data.get("score")
    if score is None:
        # 推理模型可能把输出截断成不完整 JSON，尝试从文本中抢救 score / success
        match = re.search(r'"score"\s*:\s*(\d+(?:\.\d+)?)', raw)
        if match:
            score = match.group(1)
        if "success" not in data:
            succ = re.search(r'"success"\s*:\s*(true|false)', raw, re.IGNORECASE)
            if succ:
                data["success"] = succ.group(1).lower() == "true"
    try:
        data["score"] = max(0.0, min(100.0, float(score)))
    except (TypeError, ValueError):
        data["score"] = None
    if "success" not in data or not isinstance(data.get("success"), bool):
        data["success"] = None
    return data


CODE_TEST_RUNNER = r"""
import contextlib
import io
import json
import random
import signal

def load(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()

def _timeout_handler(*_args):
    raise TimeoutError("解答代码加载超时（>10s），疑似模块级死循环或演示调用")

ns = {"__name__": "__test__"}
# 模块级副作用防护：加载阶段丢弃 print 输出，并用 SIGALRM 限制加载时长
#（被测代码若保留战斗/演示等顶层调用且未完全修复，可能死循环）。
signal.signal(signal.SIGALRM, _timeout_handler)
signal.alarm(10)
try:
    with contextlib.redirect_stdout(io.StringIO()):
        exec(compile(load("solution.py"), "solution.py", "exec"), ns)
except Exception as exc:
    print(json.dumps({"load_error": f"solution: {type(exc).__name__}: {exc}"}, ensure_ascii=False))
    raise SystemExit(0)
finally:
    signal.alarm(0)

before = set(ns)
try:
    exec(compile(load("tests.py"), "tests.py", "exec"), ns)
except Exception as exc:
    print(json.dumps({"load_error": f"tests: {type(exc).__name__}: {exc}"}, ensure_ascii=False))
    raise SystemExit(0)

# 只运行任务自带测试文件中的 test_* 函数；答案里模型自己写的测试不计入
# 每个测试单独 15s 超时保护，防止未修好的代码死循环拖垮评分
results = []
for name in sorted(n for n in set(ns) - before if n.startswith("test_") and callable(ns[n])):
    signal.alarm(15)
    try:
        ns[name]()
        results.append({"name": name, "ok": True})
    except Exception as exc:
        results.append({"name": name, "ok": False, "error": f"{type(exc).__name__}: {exc}"})
    finally:
        signal.alarm(0)
print(json.dumps({"tests": results}, ensure_ascii=False))
"""


def extract_python_code(answer: str) -> str:
    """从最终答案中提取实现代码。

    模型答案通常包含实现块、它自己写的测试块、以及示例输入/输出片段块。
    策略：剔除测试块后，在剩余块中挑选**能独立编译**且含 def/class 定义的最长块
    （示例 CSV/输出文本块要么无法编译、要么没有函数定义，均被排除）；
    没有可编译块时退化为最长非测试块，再退化为原文。
    """
    blocks = re.findall(r"```(?:python|py)?[^\S\n]*\n([\s\S]*?)```", answer)
    blocks = [block.strip() for block in blocks if block.strip()]
    if not blocks:
        return answer.strip() if "def " in answer else ""
    impl = [b for b in blocks if "def test_" not in b and "import pytest" not in b] or blocks

    def compilable(code: str) -> bool:
        try:
            compile(code, "<block>", "exec")
            return True
        except SyntaxError:
            return False

    good = [b for b in impl if compilable(b)]
    with_defs = [b for b in good if "def " in b or "class " in b]
    if with_defs:
        return max(with_defs, key=len)
    if good:
        return max(good, key=len)
    return max(impl, key=len)


def run_code_tests(final_answer: str, test_code: str, timeout: int = 30) -> dict[str, Any]:
    """在隔离子进程中执行任务附带的断言测试。

    test_code 由若干 test_* 函数组成；解答代码与测试代码分别写入独立文件，
    在 `python -I` 隔离模式下先后执行，只统计测试文件中定义的 test_* 函数
    （答案里模型自带的测试不计入）。
    """
    solution = extract_python_code(final_answer)
    if not solution:
        return {"available": False, "reason": "未能从最终答案中提取到代码"}
    with tempfile.TemporaryDirectory(prefix="emergence_test_") as tmp:
        tmp_path = Path(tmp)
        (tmp_path / "solution.py").write_text(solution + "\n", encoding="utf-8")
        (tmp_path / "tests.py").write_text(test_code + "\n", encoding="utf-8")
        (tmp_path / "runner.py").write_text(CODE_TEST_RUNNER, encoding="utf-8")
        try:
            proc = subprocess.run(
                [sys.executable, "-I", "runner.py"],
                cwd=tmp, capture_output=True, text=True, timeout=timeout,
                env={"PATH": "/usr/bin:/bin", "PYTHONHASHSEED": "0"},
            )
        except subprocess.TimeoutExpired:
            return {"available": True, "error": f"测试执行超时（{timeout}s）", "passed": 0, "failed": 0, "total": 0, "pass_rate": 0.0}
    last_line = (proc.stdout or "").strip().splitlines()[-1] if proc.stdout.strip() else ""
    try:
        report = json.loads(last_line)
    except json.JSONDecodeError:
        return {"available": True, "error": f"测试进程未返回结果：{(proc.stderr or '')[:300]}",
                "passed": 0, "failed": 0, "total": 0, "pass_rate": 0.0}
    if "load_error" in report:
        return {"available": True, "load_error": report["load_error"],
                "passed": 0, "failed": 0, "total": 0, "pass_rate": 0.0}
    tests = report.get("tests", [])
    passed = sum(1 for t in tests if t.get("ok"))
    total = len(tests)
    return {
        "available": True,
        "tests": tests,
        "passed": passed,
        "failed": total - passed,
        "total": total,
        "pass_rate": round(passed / total, 4) if total else 0.0,
    }


def compute_fixed_depth(tests: list[dict[str, Any]]) -> tuple[int, int]:
    """依赖链题目的修复深度：test_stage_1..N 中从 1 起连续通过的最长前缀。"""
    stages = []
    for t in tests:
        m = re.fullmatch(r"test_stage_(\d+)", str(t.get("name") or ""))
        if m:
            stages.append((int(m.group(1)), bool(t.get("ok"))))
    stages.sort()
    depth = 0
    for num, ok in stages:
        if num == depth + 1 and ok:
            depth += 1
        elif num == depth + 1:
            break
    return depth, (stages[-1][0] if stages else 0)


def find_chrome_binary() -> str:
    env = os.getenv("CHROME_BIN")
    candidates = [
        env,
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        "/Applications/Chromium.app/Contents/MacOS/Chromium",
        "/usr/bin/google-chrome",
        "/usr/bin/chromium",
        "/usr/bin/chromium-browser",
    ]
    for candidate in candidates:
        if candidate and Path(candidate).exists():
            return candidate
    return ""


def extract_html_document(text: str) -> str:
    if not text:
        return ""
    blocks = re.findall(r"```(?:html|xml|svg)?\s*([\s\S]*?)```", text, re.I)
    bodies = [b.strip() for b in blocks if "<" in b] or [text.strip()]
    body = max(bodies, key=len)
    low = body.lower()
    pos = -1
    for tag in ("<html", "<svg"):
        found = low.find(tag)
        if found >= 0 and (pos < 0 or found < pos):
            pos = found
    if pos < 0:
        return ""
    body = body[pos:].strip()
    if body.lower().startswith("<svg"):
        body = '<html><head><meta charset="utf-8"><title>artifact</title></head><body>' + body + "</body></html>"
    return body


def html_feature_report(html_text: str) -> dict[str, int]:
    low = html_text.lower()

    def count(pattern: str) -> int:
        return len(re.findall(pattern, low))

    return {
        "html": count(r"<html\b"),
        "svg": count(r"<svg\b"),
        "circle": count(r"<circle\b"),
        "ellipse": count(r"<ellipse\b"),
        "path": count(r"<path\b"),
        "line": count(r"<line\b"),
        "polygon": count(r"<polygon\b"),
        "animate": count(r"<animate\b"),
        "animate_transform": count(r"<animatetransform\b"),
        "script": count(r"<script\b"),
        "css_keyframes": count(r"@keyframes"),
        "css_animation": count(r"animation\s*:"),
        "css_transition": count(r"transition\s*:"),
        "image": count(r"<image\b"),
        "external_refs": len(re.findall(r"(?:src|href)\s*=\s*[\"']https?://", low)),
    }


def _run_chrome_capture(chrome: str, html_path: Path, png_path: Path, budget_ms: int, log_console: bool = False, headless_mode: str = "new") -> tuple[bool, str]:
    cmd = [
        chrome, f"--headless={headless_mode}", "--disable-gpu", "--no-sandbox", "--hide-scrollbars",
        "--window-size=900,650", f"--virtual-time-budget={budget_ms}",
        f"--screenshot={png_path}", html_path.as_uri(),
    ]
    if log_console:
        cmd.extend(["--enable-logging=stderr", "--v=1", "--log-level=0"])
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        return proc.returncode == 0 and png_path.exists(), proc.stderr or ""
    except Exception as exc:
        return False, str(exc)


def validate_html_artifact(final_answer: str) -> dict[str, Any]:
    html_text = extract_html_document(final_answer)
    if not html_text:
        return {"kind": "html_artifact", "available": False, "reason": "未能从最终答案中提取 HTML/SVG",
                "score": 0.0, "technical_score": 0.0, "quality_score": None, "gate_passed": False, "success": False}
    feature = html_feature_report(html_text)
    score = 0
    reasons: list[str] = []
    if feature["html"] > 0 and feature["svg"] > 0:
        score += 10
    else:
        reasons.append("缺少完整的 HTML/SVG 文档结构")
    if feature["circle"] >= 2:
        score += 10
    else:
        reasons.append("车轮/圆形元素少于 2 个")
    if feature["path"] + feature["ellipse"] >= 4:
        score += 10
    else:
        reasons.append("路径/椭圆元素过少，鹈鹕或自行车结构可能不完整")
    animation_mechanisms = (feature["animate"] + feature["animate_transform"] + feature["script"]
                            + feature["css_keyframes"] + feature["css_animation"])
    if animation_mechanisms > 0:
        score += 15
    else:
        reasons.append("未发现 animate/animateTransform/script/CSS 动画机制")
    if feature["external_refs"] == 0 and feature["image"] == 0:
        score += 10
    else:
        reasons.append("引用了外部资源或图片，无法独立运行")
    if len(html_text) >= 800:
        score += 5
    chrome = find_chrome_binary()
    animated = False
    console_errors: list[str] = []
    if chrome:
        with tempfile.TemporaryDirectory(prefix="emergence_html_") as tmp:
            tmp_path = Path(tmp)
            html_path = tmp_path / "artifact.html"
            html_path.write_text(html_text, encoding="utf-8")
            png1 = tmp_path / "t1.png"
            png2 = tmp_path / "t2.png"
            err2 = ""
            capture_ok = False
            # 优先使用 old headless：新版 headless 对部分 SMIL 动画的截图检测不稳定
            for headless_mode in ("old", "new"):
                png1.unlink(missing_ok=True)
                png2.unlink(missing_ok=True)
                ok1, _err1 = _run_chrome_capture(chrome, html_path, png1, 700, log_console=False, headless_mode=headless_mode)
                ok2, err2 = _run_chrome_capture(chrome, html_path, png2, 2500, log_console=True, headless_mode=headless_mode)
                if ok1 and ok2:
                    capture_ok = True
                    break
            if capture_ok:
                h1 = hashlib.sha256(png1.read_bytes()).hexdigest()
                h2 = hashlib.sha256(png2.read_bytes()).hexdigest()
                animated = h1 != h2
            for line in err2.splitlines():
                if "INFO:CONSOLE" in line and any(marker in line for marker in ("Uncaught", "TypeError", "ReferenceError", "SyntaxError")):
                    console_errors.append(line.strip()[:500])
    else:
        reasons.append("未找到可用的 Chrome/Chromium 可执行文件，无法做浏览器验证")
    if animated:
        score += 25
    else:
        reasons.append("浏览器两次截图一致，未检测到动画变化")
    if chrome and not console_errors:
        score += 15
    elif console_errors:
        reasons.append("浏览器控制台存在 JavaScript 错误")
    score = float(max(0, min(100, score)))
    gate_passed = bool(animated and not console_errors and feature["svg"] > 0 and feature["circle"] >= 2)
    return {
        "kind": "html_artifact",
        "available": True,
        "score": score,
        "technical_score": score,
        "quality_score": None,
        "gate_passed": gate_passed,
        "success": gate_passed,
        "animated": animated,
        "console_errors": console_errors,
        "features": feature,
        "animation_mechanism_count": (feature["animate"] + feature["animate_transform"] + feature["script"]
                                     + feature["css_keyframes"] + feature["css_animation"]),
        "html_size": len(html_text),
        "html_preview": html_text[:400],
        "reasons": reasons,
    }


def contains_usable_html(text: str) -> bool:
    return bool(extract_html_document(text))


def extract_final_json(answer: str) -> Any:
    """从答案中提取最后一个含「最终答案」键的 JSON 对象，返回其「最终答案」值。"""
    blocks = re.findall(r"```(?:json)?[^\S\n]*\n([\s\S]*?)```", answer)
    for block in reversed(blocks):
        try:
            value = json.loads(block.strip())
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict) and "最终答案" in value:
            return value["最终答案"]
    # 兜底：在原文中定位「最终答案」，从其前方的 { 起尝试解码
    idx = answer.rfind("最终答案")
    if idx >= 0:
        for pos in range(idx, max(-1, idx - 4000), -1):
            if answer[pos] == "{":
                try:
                    value, _ = json.JSONDecoder().raw_decode(answer[pos:])
                except json.JSONDecodeError:
                    continue
                if isinstance(value, dict) and "最终答案" in value:
                    return value["最终答案"]
    return None


def _norm_scalar(value: Any) -> Any:
    """归一化标量：数字串转 float，字符串去空白/常见标点并小写。"""
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        text = value.strip()
        try:
            return float(text)
        except ValueError:
            return re.sub(r"[\s，。、；：:,.·\-_（）()\"'“”]", "", text).lower()
    return value


def _scalar_equal(expected: Any, actual: Any) -> bool:
    """标量宽松相等：归一化后相等，或一方是数字、另一方是仅含一个数字的字符串（如 "20万元" vs 20）。"""
    exp, act = _norm_scalar(expected), _norm_scalar(actual)
    if exp == act:
        return True
    for num_side, str_side in ((exp, act), (act, exp)):
        if isinstance(num_side, float) and isinstance(str_side, str):
            nums = re.findall(r"-?\d+(?:\.\d+)?", str_side)
            if len(nums) == 1 and float(nums[0]) == num_side:
                return True
    return False


def _compare_spec(expected: Any, actual: Any) -> tuple[int, int, list[str]]:
    """递归比对 answer_spec 与模型答案，返回 (正确叶子数, 总叶子数, 差异明细)。"""
    if isinstance(expected, dict) and isinstance(actual, dict):
        correct = total = 0
        details: list[str] = []
        for key, exp_val in expected.items():
            act_val = actual.get(key)
            if act_val is None:
                c, t = _compare_spec(exp_val, None)
                details.append(f"缺少键 {key}")
            else:
                c, t, sub = _compare_spec(exp_val, act_val)
                details.extend(f"{key}.{s}" for s in sub)
            correct += c
            total += t
        return correct, total, details
    if isinstance(expected, (list, tuple)):
        ok = isinstance(actual, (list, tuple)) and [str(x) for x in actual] == [str(x) for x in expected]
        return (1 if ok else 0), 1, [] if ok else [f"期望 {expected}，实际 {actual}"]
    ok = _scalar_equal(expected, actual)
    return (1 if ok else 0), 1, [] if ok else [f"期望 {expected!r}，实际 {actual!r}"]


def exact_match_report(final_answer: str, answer_spec: dict[str, Any]) -> dict[str, Any]:
    """生成式谜题的精确匹配评分：提取「最终答案」并与 answer_spec 结构化比对。"""
    actual = extract_final_json(final_answer)
    if actual is None:
        return {"kind": "match", "available": True, "score": 0.0, "all_correct": False,
                "detail": "未能从答案中提取「最终答案」JSON"}
    kind = answer_spec.get("kind")
    if kind == "value":
        ok = _scalar_equal(answer_spec.get("value"), actual)
        score, detail = (100.0, "值匹配") if ok else (0.0, f"期望 {answer_spec.get('value')!r}，实际 {actual!r}")
        return {"kind": "match", "available": True, "score": score, "all_correct": ok, "detail": detail}
    if kind == "mapping":
        if not isinstance(actual, dict):
            return {"kind": "match", "available": True, "score": 0.0, "all_correct": False,
                    "detail": f"期望映射对象，实际 {actual!r}"}
        correct, total, details = _compare_spec(answer_spec.get("mapping") or {}, actual)
        score = round(100.0 * correct / total, 1) if total else 0.0
        return {"kind": "match", "available": True, "score": score,
                "all_correct": correct == total and total > 0,
                "detail": f"叶子字段 {correct}/{total} 正确" + ("；差异：" + "；".join(details[:10]) if details else "")}
    if kind == "schedule":
        if not isinstance(actual, dict):
            return {"kind": "match", "available": True, "score": 0.0, "all_correct": False,
                    "detail": f"期望对象（最短工期/关键路径），实际 {actual!r}"}
        makespan_ok = _scalar_equal(answer_spec.get("makespan"), actual.get("最短工期"))
        path = actual.get("关键路径")
        legal_paths = answer_spec.get("all_paths") or [answer_spec.get("critical_path")]
        path_ok = any(isinstance(path, list) and [str(x) for x in path] == [str(x) for x in legal]
                      for legal in legal_paths if legal)
        score = (50.0 if makespan_ok else 0.0) + (50.0 if path_ok else 0.0)
        makespan_detail = "正确" if makespan_ok else f"错误（期望 {answer_spec.get('makespan')}，实际 {actual.get('最短工期')}）"
        path_detail = "正确" if path_ok else f"错误（实际 {path}）"
        detail = f"最短工期{makespan_detail}；关键路径{path_detail}"
        return {"kind": "match", "available": True, "score": score, "all_correct": makespan_ok and path_ok,
                "detail": detail}
    return {"kind": "match", "available": False, "reason": f"未知 answer_spec 类型: {kind}"}


def run_single_experiment(config: dict[str, Any], client: ModelClient) -> dict[str, Any]:
    call_start = len(client.call_records)
    baseline = str(config.get("baseline") or "B4")
    capabilities = set(config.get("capabilities") or [])
    agent_count = max(5, min(10, int(config.get("agent_count") or 5)))
    roles = ROLES[:agent_count]
    client.ctx.event("phase", f"启动 {baseline} · {agent_count} 个 Agent", "编排器")
    candidates: list[dict[str, Any]] = []
    votes: list[dict[str, Any]] = []
    reassignment: dict[str, Any] | None = None

    if baseline == "B0":
        candidates = [call_role(client, config, ("generalist", "通用 Agent", "独立完成整个任务"))]
    elif baseline == "B1":
        sample_role = ("generalist", "采样 Agent", "独立完成整个任务，不参考其他样本")
        candidates = parallel_roles(client, config, [sample_role for _ in range(agent_count)])
    elif baseline == "B2":
        homogeneous = [("peer", f"同质 Agent {i + 1}", "独立完成任务，不与其他 Agent 共享过程") for i in range(agent_count)]
        candidates = parallel_roles(client, config, homogeneous)
        roles = homogeneous
    elif baseline == "B3":
        previous = ""
        injected = False
        midpoint = max(1, len(roles) // 2)
        for index, role in enumerate(roles):
            extra = f"固定流水线上一节点的输出：\n{previous[-4500:]}" if previous else "你是固定流水线的第一个节点。"
            # 扰动在流水线中途（过半节点处）真实注入，而不是预先写进初始提示
            reveal = bool(config.get("perturbation_message")) and not injected and bool(previous) and index >= midpoint
            if reveal:
                injected = True
                client.ctx.event("perturbation", f"流水线第 {index + 1} 个节点处注入中途扰动", "实验控制器",
                                 payload={"perturbation": config.get("perturbation_message")})
            try:
                item = call_role(client, config, role, extra, reveal_perturbation=reveal)
            except Exception as exc:
                if not candidates:
                    raise
                client.ctx.event("stage_skip", f"固定流水线节点失败，保留上一节点产出：{exc}", role[1])
                continue
            candidates.append(item)
            previous = item["content"]
    elif baseline == "B4":
        plan = call_role(client, config, roles[0])
        candidates.append(plan)
        shared = f"共享任务板中的初始计划：\n{plan['content'][:5000]}" if "memory" in capabilities else "只依据原始问题独立工作。"
        specialists = parallel_roles(client, config, roles[1:], shared)
        candidates.extend(specialists)
        # 扰动在初始计划与专家产出完成后注入，辩论与动态重分配机制需要对它作出反应
        candidates = inject_perturbation(client, config, candidates)
        if "debate" in capabilities and len(candidates) > 1:
            debate_context = compact_candidates(candidates, 800)
            critic_roles = [role for role in roles if role[0] in {"critic", "risk", "reviewer"}][:2] or roles[-2:]
            try:
                critiques = parallel_roles(client, config, critic_roles, "请批判以下候选并指出必须修正之处：\n" + debate_context)
                candidates.extend(critiques)
                client.ctx.event("debate", f"完成 {len(critiques)} 份交叉批判", "辩论器")
            except Exception as exc:
                client.ctx.event("stage_skip", f"辩论阶段失败，继续使用已有候选：{exc}", "辩论器")
        if "redistribute" in capabilities:
            prompt = (
                f"问题：{config['problem']}\n\n当前团队产出：\n{compact_candidates(candidates, 650)}\n\n"
                "识别尚未覆盖或质量最弱的一个方面，并选择最适合补位的角色。"
                "仅返回 JSON：{\"needed\":true,\"gap\":\"...\",\"role\":\"风险/成本/执行/验证\"}。若无需补位，needed=false。"
            )
            try:
                raw = client.chat("协调 Agent", role_system("协调 Agent", "根据局部状态发现缺口并动态转交任务"), prompt, temperature=0.1, json_mode=True, max_tokens=1024)
                reassignment = safe_json(raw)
                if reassignment.get("needed", True):
                    gap = str(reassignment.get("gap") or "补充当前方案最薄弱的部分")
                    backup = ("backup", f"动态补位·{reassignment.get('role', '验证')}", f"补齐缺口：{gap}")
                    extra = "这是运行中动态发现的缺口，请只处理该缺口，并给出可合并的修正：\n" + compact_candidates(candidates, 750)
                    try:
                        candidates.append(call_role(client, config, backup, extra))
                        client.ctx.event("reassign", f"动态重分配：{gap}", "协调 Agent", payload=reassignment)
                    except Exception as exc:
                        client.ctx.event("stage_skip", f"动态补位失败，继续使用已有候选：{exc}", backup[1])
            except Exception as exc:
                client.ctx.event("stage_skip", f"协调阶段失败，跳过动态重分配：{exc}", "协调 Agent")
    else:
        raise ValueError(f"不支持的基线条件: {baseline}")

    # B0/B1/B2 在候选生成完成后统一注入中途扰动（B3 在流水线中途注入，B4 在辩论前注入）
    if baseline in {"B0", "B1", "B2"}:
        candidates = inject_perturbation(client, config, candidates)

    if is_html_task(config):
        html_candidates = [item for item in candidates if contains_usable_html(str(item.get("content") or ""))]
        if html_candidates:
            candidates = html_candidates
            client.ctx.event("artifact_filter", f"已过滤出 {len(candidates)} 个可解析 HTML 候选", "实验控制器")

    if "vote" in capabilities and len(candidates) > 1:
        votes = vote_candidates(client, config, candidates, roles)

    if "judge" in capabilities:
        try:
            final_answer = judge_candidates(client, config, candidates, votes)
        except Exception as exc:
            final_answer = choose_by_votes(candidates, votes)["content"]
            client.ctx.event("stage_skip", f"裁判调用失败，改用投票胜出候选：{exc}", "裁判 Agent")
    else:
        final_answer = choose_by_votes(candidates, votes)["content"]

    if "reflect" in capabilities:
        if is_html_task(config):
            prompt = (
                f"任务：{config['problem']}\n\n当前 HTML 输出：\n{final_answer[:20000]}\n\n"
                "请修复其中明显的问题：HTML/SVG 结构、元素选择器、动画循环、变量作用域、括号与标签闭合。"
                "直接输出修复后的完整 HTML 源码；不要输出解释、分析或 Markdown 代码块。"
            )
            reflect_system = artifact_role_system("反思 Agent", "只做一次有边界的最终修复", "html")
        else:
            prompt = (
                f"原问题：{config['problem']}\n\n当前最终答案：\n{final_answer[:10000]}\n\n"
                "进行一次最终反思修订。保留正确内容，修复遗漏、矛盾和不可执行之处。直接输出修订后的完整答案。"
            )
            reflect_system = role_system("反思 Agent", "只做一次有边界的最终修订")
        try:
            final_answer = client.chat("反思 Agent", reflect_system, prompt, temperature=0.15)
            client.ctx.event("reflect", "完成一次最终反思修订", "反思 Agent")
        except Exception as exc:
            client.ctx.event("stage_skip", f"反思调用失败，保留裁判答案：{exc}", "反思 Agent")

    # 客观评分：代码类任务执行真实自动化测试；HTML 工件执行浏览器验证；生成式谜题做精确匹配
    test_report = None
    artifact_report = None
    task_type = config.get("task_type")
    if is_html_task(config):
        try:
            artifact_report = validate_html_artifact(final_answer)
            if artifact_report.get("available"):
                client.ctx.event(
                    "html_artifact",
                    f"浏览器验证：结构分 {artifact_report.get('score', 0):.0f}/100 · "
                    f"动画={'是' if artifact_report.get('animated') else '否'} · "
                    f"JS错误={len(artifact_report.get('console_errors') or [])}",
                    "浏览器验证器",
                    payload={k: v for k, v in artifact_report.items() if k not in {"html_preview"}},
                )
            else:
                client.ctx.event("html_artifact", f"HTML 工件验证未通过：{artifact_report.get('reason')}", "浏览器验证器", payload=artifact_report)
        except Exception as exc:
            artifact_report = {"kind": "html_artifact", "available": False, "error": str(exc), "score": 0.0, "success": False}
            client.ctx.event("stage_skip", f"HTML 工件验证失败：{exc}", "浏览器验证器")
    if task_type in {"code_repair", "chain_repair"} and str(config.get("test_code") or "").strip():
        try:
            test_report = run_code_tests(final_answer, str(config["test_code"]))
            if task_type == "chain_repair" and test_report.get("tests"):
                depth, stage_total = compute_fixed_depth(test_report["tests"])
                test_report["fixed_depth"] = depth
                test_report["stage_total"] = stage_total
            if test_report.get("available"):
                depth_note = f"，fixed_depth={test_report['fixed_depth']}/{test_report['stage_total']}" if test_report.get("fixed_depth") is not None else ""
                client.ctx.event(
                    "code_test",
                    f"自动化测试：{test_report.get('passed', 0)}/{test_report.get('total', 0)} 通过{depth_note}",
                    "测试执行器", payload=test_report)
            else:
                client.ctx.event("code_test", f"自动化测试未能执行：{test_report.get('reason') or test_report.get('error')}",
                                 "测试执行器", payload=test_report)
        except Exception as exc:
            test_report = {"available": False, "error": str(exc)}
            client.ctx.event("stage_skip", f"自动化测试执行失败，退回纯裁判评分：{exc}", "测试执行器")
    elif config.get("answer_spec"):
        try:
            test_report = exact_match_report(final_answer, config["answer_spec"])
            client.ctx.event(
                "exact_match",
                f"精确匹配：{test_report.get('score', 0):.0f}/100 · 完全正确={'是' if test_report.get('all_correct') else '否'}",
                "匹配评分器", payload=test_report)
        except Exception as exc:
            test_report = {"kind": "match", "available": False, "error": str(exc)}
            client.ctx.event("stage_skip", f"精确匹配评分失败，退回纯裁判评分：{exc}", "匹配评分器")

    try:
        evaluation = evaluate_output(client, config, final_answer, test_report)
        # 有客观评分时，success 以客观结果为准（测试全过 / 完全匹配 / HTML 验证通过），不采用裁判的主观判断
        if test_report and test_report.get("available"):
            if test_report.get("kind") == "match":
                evaluation["success"] = bool(test_report.get("all_correct"))
            else:
                # 测试未能执行（total=0）同样视为客观失败
                evaluation["success"] = bool(test_report.get("total")) and test_report.get("passed") == test_report.get("total")
        if artifact_report and artifact_report.get("available"):
            evaluation["judge_score"] = evaluation.get("score")
            evaluation["technical_score"] = artifact_report.get("technical_score")
            evaluation["artifact_gate_passed"] = bool(artifact_report.get("gate_passed"))
            evaluation["quality_score"] = config.get("human_quality_score")
            # 最终 Q 不再直接采用技术分；技术验证只作为门禁，视觉质量需人工或视觉模型评分
            evaluation["score"] = evaluation.get("quality_score")
            evaluation["success"] = bool(artifact_report.get("gate_passed"))
            evaluation["artifact"] = artifact_report
            client.ctx.event(
                "artifact_score",
                f"浏览器技术分={artifact_report.get('technical_score', 0):.0f}"
                f" · 门禁={'通过' if artifact_report.get('gate_passed') else '未通过'}"
                f" · 视觉质量分={'未评' if evaluation.get('quality_score') is None else evaluation.get('quality_score')}",
                "浏览器验证器",
                payload={"technical_score": artifact_report.get("technical_score"),
                         "gate_passed": artifact_report.get("gate_passed"),
                         "quality_score": evaluation.get("quality_score"),
                         "animated": artifact_report.get("animated"),
                         "console_errors": artifact_report.get("console_errors")},
            )
        client.ctx.event("evaluation", f"独立评估完成 · Q={evaluation.get('score')}", "独立评估器", payload=evaluation)
    except Exception as exc:
        evaluation = {"score": None, "confidence": None, "dimensions": {}, "summary": "独立评估未完成", "error": str(exc)}
        client.ctx.event("stage_skip", f"独立评估失败，任务仍保留最终答案：{exc}", "独立评估器", payload=evaluation)
    evidence = {
        "non_preconfigured": bool(reassignment and reassignment.get("needed", True)),
        "system_gain": None,
        "interaction_dependency": None,
        "repeatability": None,
        "traceable": True,
    }
    return {
        "baseline": baseline,
        "task_id": config.get("task_id"),
        "task_type": config.get("task_type"),
        "task_title": config.get("task_title"),
        "final_answer": final_answer,
        "evaluation": evaluation,
        "success": evaluation.get("success"),
        "votes": votes,
        "candidate_count": len(candidates),
        "candidates": candidates,
        "reassignment": reassignment,
        "code_tests": test_report,
        "artifact_report": artifact_report,
        "emergence_evidence": evidence,
        "calls": client.call_records[call_start:],
    }


def run_job(run_id: str, config: dict[str, Any], mode: str) -> None:
    ctx = RunContext(run_id)
    started = time.perf_counter()
    client = ModelClient(config, ctx)
    try:
        # 生成式谜题：每次运行用新 seed 生成实例，使题目无法被记忆；compare 模式下所有条件共享同一实例保证公平
        if config.get("generator"):
            import generators
            seed = int(uuid.uuid4().hex[:8], 16)
            kind = config["generator"] if isinstance(config["generator"], str) else str(config["generator"].get("kind"))
            instance = generators.generate(kind, seed)
            config = dict(config)
            config["problem"] = instance["problem"]
            config["answer_spec"] = instance["answer_spec"]
            config["reference_answer_text"] = instance["reference_answer"]
            config["generator_seed"] = seed
            ctx.event("generate", f"生成式任务实例已生成（kind={kind}, seed={seed}）", "实验控制器",
                      payload={"kind": kind, "seed": seed, "meta": instance.get("meta")})
        with JOBS_LOCK:
            JOBS[run_id]["status"] = "running"
        db_execute("UPDATE runs SET status=?, updated_at=? WHERE id=?", ("running", utc_now(), run_id))
        if mode == "compare":
            comparisons = []
            for baseline in ["B0", "B1", "B2", "B3", "B4"]:
                ctx.event("condition_start", f"开始条件 {baseline}", "实验控制器")
                condition_started = time.perf_counter()
                before_usage = (client.prompt_tokens, client.completion_tokens, client.total_tokens, client.usage_missing)
                child_config = dict(config)
                child_config["baseline"] = baseline
                if baseline == "B0":
                    child_config["capabilities"] = []
                elif baseline == "B1":
                    child_config["capabilities"] = ["vote"]
                elif baseline == "B2":
                    child_config["capabilities"] = ["vote"]
                elif baseline == "B3":
                    child_config["capabilities"] = ["reflect", "judge"]
                result = run_single_experiment(child_config, client)
                result["_usage"] = {
                    "prompt_tokens": client.prompt_tokens - before_usage[0],
                    "completion_tokens": client.completion_tokens - before_usage[1],
                    "total_tokens": client.total_tokens - before_usage[2],
                    "usage_missing": client.usage_missing - before_usage[3],
                }
                result["_duration_seconds"] = time.perf_counter() - condition_started
                comparisons.append(result)
                ctx.event("condition_complete", f"完成条件 {baseline} · Q={result['evaluation'].get('score')}", "实验控制器")
            b4 = comparisons[-1]
            scores = [item.get("evaluation", {}).get("score") for item in comparisons]
            numeric = [score for score in scores[:-1] if isinstance(score, (int, float))]
            b4_score = scores[-1]
            b4["emergence_evidence"]["system_gain"] = bool(numeric and isinstance(b4_score, (int, float)) and b4_score > max(numeric))
            b4["emergence_evidence"]["interaction_dependency"] = bool(isinstance(b4_score, (int, float)) and isinstance(scores[3], (int, float)) and b4_score > scores[3])
            result: dict[str, Any] = {"comparisons": comparisons, "final_answer": b4["final_answer"], "evaluation": b4["evaluation"], "emergence_evidence": b4["emergence_evidence"], "calls": b4.get("calls", [])}
        else:
            result = run_single_experiment(config, client)
        duration = time.perf_counter() - started
        score = result.get("evaluation", {}).get("score")
        with JOBS_LOCK:
            JOBS[run_id].update({"status": "completed", "result": result, "usage": {"prompt_tokens": client.prompt_tokens, "completion_tokens": client.completion_tokens, "total_tokens": client.total_tokens, "usage_missing": client.usage_missing}, "duration_seconds": duration, "score": score, "updated_at": utc_now()})
        db_execute(
            "UPDATE runs SET status=?, updated_at=?, result_json=?, prompt_tokens=?, completion_tokens=?, total_tokens=?, usage_missing=?, duration_seconds=?, score=? WHERE id=?",
            ("completed", utc_now(), json_text(result), client.prompt_tokens, client.completion_tokens, client.total_tokens, client.usage_missing, duration, score, run_id),
        )
        ctx.event("run_complete", f"实验完成 · {duration:.1f}s · {client.total_tokens} Token", "系统")
    except Exception as exc:
        duration = time.perf_counter() - started
        message = str(exc)
        partial_result = {"partial": True, "final_answer": None, "evaluation": {"score": None}, "calls": list(client.call_records), "error": message}
        with JOBS_LOCK:
            JOBS[run_id].update({"status": "failed", "error": message, "result": partial_result, "usage": {"prompt_tokens": client.prompt_tokens, "completion_tokens": client.completion_tokens, "total_tokens": client.total_tokens, "usage_missing": client.usage_missing}, "duration_seconds": duration, "updated_at": utc_now()})
        db_execute(
            "UPDATE runs SET status=?, updated_at=?, error=?, result_json=?, duration_seconds=?, prompt_tokens=?, completion_tokens=?, total_tokens=?, usage_missing=? WHERE id=?",
            ("failed", utc_now(), message, json_text(partial_result), duration, client.prompt_tokens, client.completion_tokens, client.total_tokens, client.usage_missing, run_id),
        )
        ctx.event("run_error", message, "系统")


def create_run(config: dict[str, Any], mode: str) -> str:
    problem = str(config.get("problem") or "").strip()
    if not problem:
        raise ValueError("研究问题不能为空")
    if not str(config.get("model") or os.getenv("OPENAI_MODEL") or "").strip():
        raise ValueError("必须设置模型名称")
    run_id = "RUN-" + uuid.uuid4().hex[:10].upper()
    baseline = "B0-B4" if mode == "compare" else str(config.get("baseline") or "B4")
    stored_config = {key: value for key, value in config.items() if key != "api_key"}
    now = utc_now()
    job = {"id": run_id, "created_at": now, "updated_at": now, "status": "queued", "mode": mode, "baseline": baseline, "problem": problem, "config": stored_config, "events": [], "calls": [], "result": None, "error": None}
    with JOBS_LOCK:
        JOBS[run_id] = job
    db_execute(
        "INSERT INTO runs(id, created_at, updated_at, status, mode, baseline, problem, config_json) VALUES(?,?,?,?,?,?,?,?)",
        (run_id, now, now, "queued", mode, baseline, problem, json_text(stored_config)),
    )
    threading.Thread(target=run_job, args=(run_id, config, mode), daemon=True).start()
    return run_id


def create_batch(config: dict[str, Any], mode: str, repeats: int) -> list[str]:
    repeats = max(1, min(100, int(repeats or 1)))
    run_ids: list[str] = []
    for _ in range(repeats):
        run_id = create_run(config, mode)
        run_ids.append(run_id)
    return run_ids


def export_runs_json() -> list[dict[str, Any]]:
    rows = db_query("SELECT * FROM runs ORDER BY created_at DESC")
    return [serialize_db_run(row) for row in rows]


def export_runs_csv() -> str:
    rows = db_query("SELECT * FROM runs ORDER BY created_at DESC")
    header = [
        "run_id", "status", "mode", "baseline", "problem", "task_id", "task_type",
        "agent_count", "model", "score", "success", "prompt_tokens", "completion_tokens",
        "total_tokens", "usage_missing", "duration_seconds", "created_at", "updated_at", "error",
    ]

    def field(row: sqlite3.Row, name: str) -> str:
        if name == "run_id":
            return str(row["id"])
        if name in {"prompt_tokens", "completion_tokens", "total_tokens", "usage_missing"}:
            return str(row[name])
        if name == "score":
            return "" if row["score"] is None else str(row["score"])
        if name == "success":
            result = json.loads(row["result_json"]) if row["result_json"] else {}
            value = result.get("success")
            return "" if value is None else ("true" if value else "false")
        if name in {"agent_count", "model"}:
            config = json.loads(row["config_json"] or "{}")
            return str(config.get(name, ""))
        if name == "task_id":
            config = json.loads(row["config_json"] or "{}")
            return str(config.get("task_id", ""))
        if name == "task_type":
            config = json.loads(row["config_json"] or "{}")
            return str(config.get("task_type", ""))
        return str(row[name] or "")

    lines = [",".join(f'"{h}"' for h in header)]
    for row in rows:
        lines.append(",".join(f'"{field(row, h).replace(chr(34), chr(34) + chr(34))}"' for h in header))
    return "\n".join(lines)


def serialize_db_run(row: sqlite3.Row, include_events: bool = False) -> dict[str, Any]:
    config = json.loads(row["config_json"] or "{}")
    result_json = json.loads(row["result_json"]) if row["result_json"] else {}
    result = {
        "id": row["id"], "created_at": row["created_at"], "updated_at": row["updated_at"], "status": row["status"], "mode": row["mode"], "baseline": row["baseline"], "problem": row["problem"],
        "task_id": config.get("task_id"), "task_type": config.get("task_type"), "task_title": config.get("task_title"),
        "config": config, "result": result_json,
        "usage": {"prompt_tokens": row["prompt_tokens"], "completion_tokens": row["completion_tokens"], "total_tokens": row["total_tokens"], "usage_missing": row["usage_missing"]},
        "duration_seconds": row["duration_seconds"], "score": row["score"], "success": result_json.get("success") if isinstance(result_json, dict) else None, "error": row["error"],
    }
    if include_events:
        event_rows = db_query("SELECT created_at, kind, agent, message, tokens, payload_json FROM events WHERE run_id=? ORDER BY id", (row["id"],))
        result["events"] = [{"created_at": e["created_at"], "kind": e["kind"], "agent": e["agent"], "message": e["message"], "tokens": e["tokens"], "payload": json.loads(e["payload_json"]) if e["payload_json"] else None} for e in event_rows]
    return result


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args: Any, **kwargs: Any):
        super().__init__(*args, directory=str(ROOT), **kwargs)

    def log_message(self, fmt: str, *args: Any) -> None:
        print(f"[{self.log_date_time_string()}] {fmt % args}")

    def send_json(self, status: int, data: Any) -> None:
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def read_json(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length") or 0)
        if length > 1_000_000:
            raise ValueError("请求体过大")
        return json.loads(self.rfile.read(length).decode("utf-8")) if length else {}

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path == "/api/health":
            self.send_json(200, {"ok": True, "service": "Emergence Lab", "database": DB_PATH.name})
            return
        if path == "/api/config":
            default_model = os.getenv("OPENAI_MODEL") or "gpt-4.1-mini"
            profile = model_profile(default_model)
            self.send_json(200, {"base_url": os.getenv("OPENAI_BASE_URL") or "https://api.openai.com/v1", "model": default_model, "max_input_tokens": int(os.getenv("OPENAI_MAX_INPUT_TOKENS") or profile["max_input_tokens"]), "max_output_tokens": int(os.getenv("OPENAI_MAX_OUTPUT_TOKENS") or profile["default_output_tokens"]), "has_server_key": bool(os.getenv("OPENAI_API_KEY"))})
            return
        if path == "/api/tasks":
            self.send_json(200, task_summaries())
            return
        task_match = re.fullmatch(r"/api/tasks/([A-Z0-9-]+)", path)
        if task_match:
            task = TASK_BY_ID.get(task_match.group(1))
            if not task:
                self.send_json(404, {"error": "任务不存在"})
                return
            self.send_json(200, task)
            return
        if path == "/api/export":
            format_name = (urlparse(self.path).query or "").split("format=")[-1].split("&")[0].lower()
            if format_name == "json":
                self.send_json(200, export_runs_json())
            else:
                csv_text = "\ufeff" + export_runs_csv()
                body = csv_text.encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/csv; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Content-Disposition", "attachment; filename=emergence-runs.csv")
                self.end_headers()
                self.wfile.write(body)
            return
        if path == "/api/runs":
            rows = db_query("SELECT * FROM runs ORDER BY created_at DESC LIMIT 50")
            self.send_json(200, [serialize_db_run(row) for row in rows])
            return
        match = re.fullmatch(r"/api/runs/([A-Z0-9-]+)", path)
        if match:
            run_id = match.group(1)
            with JOBS_LOCK:
                job = JOBS.get(run_id)
                live = dict(job) if job else None
                if live:
                    live["events"] = list(job["events"])
            if live:
                self.send_json(200, live)
                return
            rows = db_query("SELECT * FROM runs WHERE id=?", (run_id,))
            if not rows:
                self.send_json(404, {"error": "运行记录不存在"})
                return
            self.send_json(200, serialize_db_run(rows[0], include_events=True))
            return
        super().do_GET()

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        try:
            body = self.read_json()
            if path == "/api/runs":
                task_id = str(body.get("task_id") or "").strip()
                config = apply_task_to_config(body, task_id) if task_id else body
                self.send_json(202, {"run_id": create_run(config, "single")})
                return
            if path == "/api/runs/batch":
                task_id = str(body.get("task_id") or "").strip()
                config = apply_task_to_config(body, task_id) if task_id else body
                mode = str(body.get("mode") or "single")
                if mode not in {"single", "compare"}:
                    raise ValueError("mode 只能是 single 或 compare")
                repeats = int(body.get("repeats") or 1)
                run_ids = create_batch(config, mode, repeats)
                self.send_json(202, {"run_ids": run_ids, "repeats": len(run_ids), "mode": mode})
                return
            if path == "/api/compare":
                task_id = str(body.get("task_id") or "").strip()
                config = apply_task_to_config(body, task_id) if task_id else body
                self.send_json(202, {"run_id": create_run(config, "compare")})
                return
            self.send_json(404, {"error": "接口不存在"})
        except (ValueError, json.JSONDecodeError) as exc:
            self.send_json(400, {"error": str(exc)})
        except Exception as exc:
            self.send_json(500, {"error": str(exc)})


if __name__ == "__main__":
    init_db()
    host = os.getenv("EMERGENCE_HOST", "127.0.0.1")
    port = int(os.getenv("EMERGENCE_PORT", "4173"))
    server = ThreadingHTTPServer((host, port), Handler)
    print(f"Emergence Lab running at http://{host}:{port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
