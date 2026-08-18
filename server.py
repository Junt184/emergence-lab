from __future__ import annotations

import json
import os
import re
import sqlite3
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
    return merged

MODEL_PROFILES: dict[str, dict[str, int]] = {
    "deepseek-v4-flash": {"max_input_tokens": 128000, "default_output_tokens": 8192, "max_output_tokens": 8192},
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


def _open_url(request: urllib.request.Request, timeout: int):
    """Open a request, bypassing HTTP(S) proxy for loopback addresses.

    Many local OpenAI-compatible servers (Ollama, vLLM, LM Studio) listen on
    127.0.0.1. Python's urllib would otherwise route localhost through the
    system proxy in some environments and return 502.
    """
    if _is_loopback_url(request.full_url):
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        return opener.open(request, timeout=timeout)
    return urllib.request.urlopen(request, timeout=timeout)



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


def task_prompt(config: dict[str, Any], extra: str = "") -> str:
    coupling = "高耦合、存在跨步骤依赖" if config.get("coupling") == "high" else "低耦合、子任务可并行"
    perturbation = PERTURBATIONS.get(str(config.get("perturbation") or "none"), "无额外扰动")
    perturbation_message = str(config.get("perturbation_message") or "").strip()
    success_criteria = str(config.get("success_criteria") or "").strip()
    prompt = (
        f"研究问题：\n{config['problem']}\n\n"
        f"任务结构：{coupling}\n"
        f"扰动条件：{perturbation}\n"
        "请给出具体、可验证的分析。最终建议必须包含选择、关键权衡、执行步骤和失败退出条件。"
    )
    if perturbation_message:
        prompt += f"\n\n扰动注入说明（实验处理）：\n{perturbation_message}"
    if success_criteria:
        prompt += f"\n\n任务的客观成功标准（供产出时对照）：\n{success_criteria}"
    if extra:
        prompt += f"\n\n补充上下文：\n{extra}"
    return prompt


def call_role(client: ModelClient, config: dict[str, Any], role: tuple[str, str, str], extra: str = "") -> dict[str, Any]:
    role_id, role_name, specialty = role
    content = client.chat(role_name, role_system(role_name, specialty), task_prompt(config, extra), temperature=0.35)
    return {"role": role_id, "agent": role_name, "content": content}


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
    candidate_text = compact_candidates(candidates, 700)

    def one_vote(role: tuple[str, str, str]) -> dict[str, Any]:
        prompt = (
            f"问题：{config['problem']}\n\n{candidate_text}\n\n"
            f"请独立投票。只能选择 1 到 {len(candidates)}，按约束覆盖、可执行性、风险控制评分。"
            "仅返回 JSON：{\"choice\": 1, \"score\": 0-100, \"reason\": \"一句话\"}"
        )
        raw = client.chat(role[1], role_system(role[1], role[2]), prompt, temperature=0.1, json_mode=True, max_tokens=300)
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
    prompt = (
        f"研究问题：{config['problem']}\n\n{compact_candidates(candidates, 900)}\n\n"
        f"投票记录：{json_text(votes)}\n\n"
        "你是最终决策裁判。综合候选而不是简单拼接，给出一个可独立阅读的最终答案。"
        "必须包括：明确选择、关键权衡、执行步骤、风险控制、退出条件。"
    )
    return client.chat("裁判 Agent", role_system("裁判 Agent", "独立裁决并整合团队产出"), prompt, temperature=0.15)


def evaluate_output(client: ModelClient, config: dict[str, Any], final_answer: str) -> dict[str, Any]:
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
    raw = client.chat("独立评估器", role_system("独立评估器", "使用固定量表盲评最终答案"), prompt, temperature=0, json_mode=True, max_tokens=600)
    data = safe_json(raw)
    score = data.get("score")
    try:
        data["score"] = max(0.0, min(100.0, float(score)))
    except (TypeError, ValueError):
        data["score"] = None
    if "success" not in data or not isinstance(data.get("success"), bool):
        data["success"] = None
    return data


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
        for role in roles:
            extra = f"固定流水线上一节点的输出：\n{previous[-4500:]}" if previous else "你是固定流水线的第一个节点。"
            try:
                item = call_role(client, config, role, extra)
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
                raw = client.chat("协调 Agent", role_system("协调 Agent", "根据局部状态发现缺口并动态转交任务"), prompt, temperature=0.1, json_mode=True, max_tokens=350)
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
        prompt = (
            f"原问题：{config['problem']}\n\n当前最终答案：\n{final_answer[:10000]}\n\n"
            "进行一次最终反思修订。保留正确内容，修复遗漏、矛盾和不可执行之处。直接输出修订后的完整答案。"
        )
        try:
            final_answer = client.chat("反思 Agent", role_system("反思 Agent", "只做一次有边界的最终修订"), prompt, temperature=0.15)
            client.ctx.event("reflect", "完成一次最终反思修订", "反思 Agent")
        except Exception as exc:
            client.ctx.event("stage_skip", f"反思调用失败，保留裁判答案：{exc}", "反思 Agent")

    try:
        evaluation = evaluate_output(client, config, final_answer)
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
        "emergence_evidence": evidence,
        "calls": client.call_records[call_start:],
    }


def run_job(run_id: str, config: dict[str, Any], mode: str) -> None:
    ctx = RunContext(run_id)
    started = time.perf_counter()
    client = ModelClient(config, ctx)
    try:
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
