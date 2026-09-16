"""Qwen3-VL visual scoring for generated HTML/SVG artifacts.

Usage:
    SILICONFLOW_API_KEY=... python scripts/visual_review.py

Inputs:
    reports/artifact_review/human_scores.json
    experiments.db
Outputs:
    reports/artifact_review/visual_scores.json
    reports/artifact_review/visual_review.md
"""
from __future__ import annotations

import base64
import json
import os
import sqlite3
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import server  # noqa: E402

SCORES_FILE = ROOT / "reports" / "artifact_review" / "human_scores.json"
OUT_JSON = ROOT / "reports" / "artifact_review" / "visual_scores.json"
OUT_MD = ROOT / "reports" / "artifact_review" / "visual_review.md"
API_URL = "https://api.siliconflow.cn/v1/chat/completions"
MODEL = os.getenv("QWEN_VL_MODEL", "Qwen/Qwen3-VL-32B-Instruct")


def api_key() -> str:
    return (os.getenv("SILICONFLOW_API_KEY") or os.getenv("SF_KEY")
            or os.getenv("OPENAI_API_KEY") or "")


def post_vlm(images: list[str]) -> dict:
    key = api_key()
    if not key:
        raise RuntimeError("缺少 SILICONFLOW_API_KEY / SF_KEY / OPENAI_API_KEY")
    prompt = (
        "你是严格的视觉评审员。下面两张图是同一个 HTML/SVG 动画在不同时间点的截图。"
        "请只根据实际看到的画面，按四个维度打分，不要因为 HTML 能运行就给高分：\n"
        "1. 鹈鹕结构 0-30：长喙、喉囊、身体、翅膀、眼睛是否可辨认；\n"
        "2. 自行车结构 0-25：两个车轮、车架、车把、坐垫、脚踏是否可辨认；\n"
        "3. 动画协调 0-20：两帧之间车轮、脚踏、翅膀或身体是否有合理的运动变化；\n"
        "4. 整体观感 0-25：构图、比例、清晰度、可接受程度。\n"
        "如果只是几何图形堆叠、看不出鹈鹕或自行车，请给低分。"
        '只返回 JSON：{"pelican":0,"bicycle":0,"animation":0,"aesthetics":0,'
        '"total":0,"summary":"一句话评语"}'
    )
    content: list[dict] = [{"type": "text", "text": prompt}]
    for index, image in enumerate(images):
        content.append({"type": "text", "text": f"第 {index + 1} 帧："})
        content.append({"type": "image_url", "image_url": {"url": "data:image/png;base64," + image}})
    payload = {"model": MODEL, "messages": [{"role": "user", "content": content}],
               "max_tokens": 700, "stream": False}
    headers = {"Content-Type": "application/json", "Authorization": "Bearer " + key}
    req = urllib.request.Request(API_URL, data=json.dumps(payload).encode(), headers=headers, method="POST")
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    last_error = ""
    for attempt in range(3):
        try:
            with opener.open(req, timeout=240) as resp:
                data = json.loads(resp.read().decode())
            message = data["choices"][0]["message"]
            raw = str(message.get("content") or "").strip()
            parsed = server.safe_json(raw)
            if not parsed:
                match = __import__("re").search(r"\{[\s\S]*\}", raw)
                parsed = server.safe_json(match.group(0)) if match else {}
            if not parsed:
                raise RuntimeError("VLM 未返回可解析 JSON：" + raw[:300])
            for key_name in ("pelican", "bicycle", "animation", "aesthetics"):
                try:
                    parsed[key_name] = max(0.0, float(parsed.get(key_name) or 0))
                except (TypeError, ValueError):
                    parsed[key_name] = 0.0
            parsed["total"] = round(parsed["pelican"] + parsed["bicycle"] + parsed["animation"] + parsed["aesthetics"], 1)
            parsed["raw"] = raw[:1000]
            return parsed
        except urllib.error.HTTPError as exc:
            last_error = f"HTTP {exc.code}: {exc.read().decode('utf-8', errors='replace')[:300]}"
        except Exception as exc:  # noqa: BLE001
            last_error = f"{type(exc).__name__}: {exc}"
        time.sleep(5 * (attempt + 1))
    raise RuntimeError(f"Qwen3-VL 调用失败：{last_error}")


def capture_frames(html_text: str) -> tuple[list[str], str]:
    chrome = server.find_chrome_binary()
    if not chrome:
        return [], "未找到 Chrome/Chromium"
    images: list[str] = []
    with tempfile.TemporaryDirectory(prefix="visual_review_") as tmp:
        tmp_path = Path(tmp)
        html_path = tmp_path / "artifact.html"
        html_path.write_text(html_text, encoding="utf-8")
        for index, budget in enumerate((700, 2200)):
            png = tmp_path / f"frame_{index}.png"
            ok = False
            last_error = ""
            for mode in ("old", "new"):
                ok, last_error = server._run_chrome_capture(chrome, html_path, png, budget,
                                                            log_console=(index == 1), headless_mode=mode)
                if ok:
                    break
            if not ok:
                return images, f"Chrome 截图失败：{last_error[:300]}"
            images.append(base64.b64encode(png.read_bytes()).decode())
    return images, ""


def main() -> None:
    if not SCORES_FILE.exists():
        sys.exit(f"缺少人工评分文件：{SCORES_FILE}")
    entries = json.loads(SCORES_FILE.read_text(encoding="utf-8"))["scores"]
    cached = {}
    if OUT_JSON.exists():
        try:
            for item in json.loads(OUT_JSON.read_text(encoding="utf-8")):
                if item.get("run_id"):
                    cached[item["run_id"]] = item
        except Exception:
            cached = {}
    conn = sqlite3.connect(ROOT / "experiments.db")
    conn.row_factory = sqlite3.Row
    results = []
    for entry in entries:
        row = conn.execute("SELECT * FROM runs WHERE id=?", (entry["run_id"],)).fetchone()
        record = {**entry, "technical_score": None, "gate_passed": None, "vlm_total": None,
                  "pelican": None, "bicycle": None, "animation": None, "aesthetics": None,
                  "summary": "", "error": ""}
        if row is None:
            record["error"] = "数据库中找不到该运行"
            results.append(record)
            continue
        result = json.loads(row["result_json"]) if row["result_json"] else {}
        report = result.get("artifact_report") or (result.get("evaluation") or {}).get("artifact") or {}
        record["technical_score"] = report.get("technical_score", report.get("score"))
        record["gate_passed"] = report.get("gate_passed", report.get("success"))
        answer = str(result.get("final_answer") or "")
        html_text = server.extract_html_document(answer)
        if not html_text:
            record["error"] = "未能从最终答案中提取 HTML"
            results.append(record)
            continue
        previous = cached.get(entry["run_id"]) or {}
        if previous.get("vlm_total") is not None and not previous.get("error"):
            record.update({k: previous.get(k) for k in ("vlm_total", "pelican", "bicycle", "animation", "aesthetics", "summary")})
            results.append(record)
            continue
        images, error = capture_frames(html_text)
        if error:
            record["error"] = error
            results.append(record)
            continue
        try:
            scored = post_vlm(images)
            record.update({k: scored.get(k) for k in ("vlm_total", "pelican", "bicycle", "animation", "aesthetics", "summary")})
            record["vlm_total"] = scored.get("total")
            record["summary"] = scored.get("summary", "")
        except Exception as exc:  # noqa: BLE001
            record["error"] = str(exc)
        results.append(record)
    conn.close()

    OUT_JSON.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = ["# Qwen3-VL 视觉评审结果", ""]
    lines.append(f"- 模型：`{MODEL}`")
    lines.append("- 每个工件渲染两帧截图，交给 Qwen3-VL 按鹈鹕结构/自行车结构/动画协调/整体观感评分。")
    lines.append("")
    lines.append("| 模型 | 条件 | Run ID | 技术门禁 | 人工视觉分 | VLM 总分 | 鹈鹕 | 自行车 | 动画 | 观感 | 差值(VLM-人工) |")
    lines.append("|---|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|")
    def num(value):
        return "—" if value is None else f"{value:.0f}"

    for r in results:
        human = r.get("human_score")
        vlm = r.get("vlm_total")
        diff = "" if vlm is None or human is None else f"{vlm - human:.0f}"
        lines.append(
            f"| {r['model']} | {r['condition']} | {r['run_id']} | "
            f"{'通过' if r.get('gate_passed') else '未通过'} | {human} | "
            f"{num(vlm)} | {num(r.get('pelican'))} | {num(r.get('bicycle'))} | "
            f"{num(r.get('animation'))} | {num(r.get('aesthetics'))} | {diff or '—'} |"
        )
        if r.get("error"):
            lines.append(f"|  |  |  |  |  |  |  |  |  |  | 错误：{r['error'][:120]} |")
    lines.append("")
    lines.append("## 说明")
    lines.append("")
    lines.append("- VLM 总分由四个子项相加得出，满分 100。")
    lines.append("- 该分数仍应与人工盲评做一致性校验，不能单独作为最终结论。")
    OUT_MD.write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines))
    print(f"\n[visual scores] {OUT_JSON}")
    print(f"[visual report] {OUT_MD}")


if __name__ == "__main__":
    main()
