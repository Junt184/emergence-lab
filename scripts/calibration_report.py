#!/usr/bin/env python3
"""从 experiments.db 聚合全部 B0 校准运行，生成最终报告。

用法：python3 scripts/calibration_report.py
输出：reports/calibration_final.md
"""
from __future__ import annotations

import json
import sqlite3
import statistics
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "experiments.db"
OUT = ROOT / "reports" / "calibration_final.md"

TASK_META = {t["id"]: t for t in json.loads((ROOT / "tasks.json").read_text(encoding="utf-8"))["tasks"]}


def main() -> None:
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT id, status, score, result_json, config_json, total_tokens, duration_seconds FROM runs ORDER BY created_at"
    ).fetchall()

    records = []
    for row in rows:
        config = json.loads(row["config_json"] or "{}")
        task_id = config.get("task_id")
        if not task_id:
            continue
        result = json.loads(row["result_json"] or "{}")
        meta = TASK_META.get(task_id, {})
        records.append({
            "run_id": row["id"],
            "task_id": task_id,
            "baseline": config.get("baseline") or "B0",
            "type": meta.get("type", "?"),
            "role": meta.get("role", "?"),
            "difficulty": meta.get("difficulty", "?"),
            "coupling": meta.get("coupling", "?"),
            "status": row["status"],
            "score": row["score"],
            "success": result.get("success"),
            "tokens": row["total_tokens"],
            "duration": row["duration_seconds"],
        })

    scored = [r for r in records if isinstance(r["score"], (int, float))]
    failed = [r for r in records if r["status"] != "completed"]
    no_score = [r for r in records if r["status"] == "completed" and not isinstance(r["score"], (int, float))]

    def stats(scores):
        if not scores:
            return "-"
        return (f"mean={statistics.mean(scores):.1f} sd={statistics.stdev(scores):.1f}"
                if len(scores) > 1 else f"mean={scores[0]:.1f}") + f" min={min(scores):.0f} max={max(scores):.0f} n={len(scores)}"

    def group(key):
        groups: dict[str, list[float]] = {}
        for r in scored:
            groups.setdefault(str(r[key]), []).append(r["score"])
        return groups

    lines = ["# 校准实验最终报告（按基线分组）", "",
             f"- 模型：deepseek-v4-flash",
             f"- 总运行 {len(records)} 次：有效评分 {len(scored)}，运行失败 {len(failed)}，完成但评估缺失 {len(no_score)}",
             f"- 全体有效 Q：{stats([r['score'] for r in scored])}", ""]

    # 按基线 × 角色交叉汇总
    lines += ["## 按基线 × 任务角色", "",
              "| 基线 | 角色 | n | mean | sd | min | max | success 率 | 平均 Token |",
              "|---|---|---|---|---|---|---|---|---|"]
    combo: dict[tuple, list[dict]] = {}
    for r in scored:
        combo.setdefault((r["baseline"], r["role"]), []).append(r)
    for (baseline, role), rs in sorted(combo.items()):
        scores = [r["score"] for r in rs]
        succ = [r["success"] for r in rs if isinstance(r["success"], bool)]
        tokens = [r["tokens"] for r in rs if isinstance(r["tokens"], (int, float)) and r["tokens"]]
        lines.append(
            f"| {baseline} | {role} | {len(scores)} | {statistics.mean(scores):.1f} | "
            f"{statistics.stdev(scores):.1f} | {min(scores):.0f} | {max(scores):.0f} | "
            f"{f'{100 * sum(succ) / len(succ):.0f}%' if succ else '-'} | "
            f"{f'{statistics.mean(tokens):.0f}' if tokens else '-'} |")
    lines.append("")

    # 逐任务 × 基线对照表
    lines += ["## 逐任务 × 基线对照", "",
              "| 任务 | 类型 | 难度 | 角色 | B0 mean Q (n) | B4 mean Q (n) | Δ(B4-B0) |",
              "|---|---|---|---|---|---|---|"]
    cell: dict[tuple, list[float]] = {}
    for r in scored:
        cell.setdefault((r["task_id"], r["baseline"]), []).append(r["score"])
    task_ids = sorted({r["task_id"] for r in records})
    for task_id in task_ids:
        meta = TASK_META.get(task_id, {})
        b0 = cell.get((task_id, "B0"), [])
        b4 = cell.get((task_id, "B4"), [])
        b0s = f"{statistics.mean(b0):.1f} ({len(b0)})" if b0 else "-"
        b4s = f"{statistics.mean(b4):.1f} ({len(b4)})" if b4 else "-"
        delta = f"{statistics.mean(b4) - statistics.mean(b0):+.1f}" if b0 and b4 else "-"
        lines.append(f"| {task_id} | {meta.get('type', '?')} | {meta.get('difficulty', '?')} "
                     f"| {meta.get('role', '?')} | {b0s} | {b4s} | {delta} |")
    lines.append("")

    for key, title in [("role", "按角色"), ("type", "按任务类型"), ("difficulty", "按难度"), ("coupling", "按耦合度")]:
        lines += [f"## {title}", "", "| 分组 | " + " | ".join(["n", "mean", "sd", "min", "max", "Q≥85占比"]) + " |",
                  "|---|---|---|---|---|---|---|"]
        for k, v in sorted(group(key).items()):
            lines.append(f"| {k} | {len(v)} | {statistics.mean(v):.1f} | "
                         f"{statistics.stdev(v):.1f} | {min(v):.0f} | {max(v):.0f} | "
                         f"{100 * sum(1 for s in v if s >= 85) / len(v):.0f}% |")
        lines.append("")

    lines += ["## 逐任务明细", "", "| 任务 | 类型 | 难度 | 耦合 | 角色 | 各次 Q | mean | success 率 |",
              "|---|---|---|---|---|---|---|---|"]
    by_task: dict[str, list[dict]] = {}
    for r in records:
        by_task.setdefault(r["task_id"], []).append(r)
    for task_id in sorted(by_task):
        rs = by_task[task_id]
        scores = [r["score"] for r in rs if isinstance(r["score"], (int, float))]
        succ = [r["success"] for r in rs if isinstance(r["success"], bool)]
        first = rs[0]
        lines.append(
            f"| {task_id} | {first['type']} | {first['difficulty']} | {first['coupling']} | {first['role']} "
            f"| {', '.join(f'{s:.0f}' for s in scores) or '-'} "
            f"| {f'{statistics.mean(scores):.1f}' if scores else '-'} "
            f"| {f'{100 * sum(succ) / len(succ):.0f}%' if succ else '-'} |")

    OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"已写入 {OUT}")
    print(f"有效评分 {len(scored)} / {len(records)}；全体：{stats([r['score'] for r in scored])}")


if __name__ == "__main__":
    main()
