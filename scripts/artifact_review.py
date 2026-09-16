"""Compare automatic HTML artifact metrics with human visual scores.

Usage:
    python scripts/artifact_review.py
Inputs:
    reports/artifact_review/human_scores.json
Outputs:
    reports/artifact_review/review.md
"""
from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import server  # noqa: E402

SCORES_FILE = ROOT / "reports" / "artifact_review" / "human_scores.json"
OUT = ROOT / "reports" / "artifact_review" / "review.md"


def main() -> None:
    scores = json.loads(SCORES_FILE.read_text(encoding="utf-8"))["scores"]
    conn = sqlite3.connect(ROOT / "experiments.db")
    conn.row_factory = sqlite3.Row
    rows = []
    for item in scores:
        row = conn.execute("SELECT * FROM runs WHERE id=?", (item["run_id"],)).fetchone()
        if row is None:
            rows.append({**item, "missing": True})
            continue
        result = json.loads(row["result_json"]) if row["result_json"] else {}
        answer = str(result.get("final_answer") or "")
        report = server.validate_html_artifact(answer)
        rows.append({
            **item,
            "missing": False,
            "auto_technical": report.get("technical_score", report.get("score")),
            "auto_gate": bool(report.get("gate_passed", report.get("success"))),
            "auto_animated": bool(report.get("animated")),
            "auto_console_errors": len(report.get("console_errors") or []),
            "human_score": item.get("human_score"),
            "delta": (item.get("human_score") or 0) - (report.get("technical_score") or 0),
        })
    conn.close()

    lines = ["# Artifact 评分校准：自动技术分 vs 人工视觉分", ""]
    lines.append("- 自动分只检查 HTML/SVG 结构、动画是否存在、JS 错误、外部资源等“技术可运行性”。")
    lines.append("- 人工分用于评价图形是否像鹈鹕、像自行车，以及整体视觉效果。")
    lines.append("")
    lines.append("| 模型 | 条件 | Run ID | 自动技术分 | 技术门禁 | 动画 | JS 错误 | 人工视觉分 | 差值（人工-自动） |")
    lines.append("|---|---:|---|---:|---|---:|---:|---:|---:|")
    for r in rows:
        if r.get("missing"):
            lines.append(f"| {r['model']} | {r['condition']} | {r['run_id']} | 缺失 | — | — | — | {r.get('human_score')} | — |")
            continue
        lines.append(
            f"| {r['model']} | {r['condition']} | {r['run_id']} | {r['auto_technical']:.0f} | "
            f"{'通过' if r['auto_gate'] else '未通过'} | {'是' if r['auto_animated'] else '否'} | "
            f"{r['auto_console_errors']} | {r['human_score']} | {r['delta']:.0f} |"
        )
    lines.append("")
    lines.append("## 初步修正建议")
    lines.append("")
    lines.append("1. 自动分应改名为 `technical_score`，不能直接当作最终 Q。")
    lines.append("2. `animated=False` 或存在 JS 错误时，技术门禁应直接失败，最终 Q 不能因此拿到中等分数。")
    lines.append("3. 最终质量分必须加入人工盲评或视觉模型评分：鹈鹕结构、自行车结构、动画协调、整体观感。")
    lines.append("4. 论文主表建议同时报告 `technical_pass`、`visual_Q`、Token 和 Time，而不是只写一个混合 Q。")
    lines.append("")
    OUT.write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines))
    print(f"\n[report] {OUT}")


if __name__ == "__main__":
    main()
