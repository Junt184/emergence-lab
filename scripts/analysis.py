# Emergence Lab analysis script (run with python3 scripts/analysis.py)
"""Generate paper-style tables from experiments.db.

Usage:
    python scripts/analysis.py [path-to-db]
"""
from __future__ import annotations

import json
import math
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "experiments.db"
REPORT_DIR = ROOT / "reports"
TASKS_FILE = ROOT / "tasks.json"
BASELINES = ["B0", "B1", "B2", "B3", "B4"]


def load_task_meta():
    if not TASKS_FILE.exists():
        return {}
    try:
        data = json.loads(TASKS_FILE.read_text(encoding="utf-8"))
        tasks = data.get("tasks", data if isinstance(data, list) else [])
        return {t.get("id"): t for t in tasks if isinstance(t, dict) and t.get("id")}
    except (json.JSONDecodeError, OSError):
        return {}


def mean(values):
    return sum(values) / len(values) if values else float("nan")


def sd(values):
    if len(values) < 2:
        return float("nan")
    m = mean(values)
    return math.sqrt(sum((v - m) ** 2 for v in values) / (len(values) - 1))


def ci(values):
    if len(values) < 2:
        return (float("nan"), float("nan"))
    n = len(values)
    se = sd(values) / math.sqrt(n)
    m = mean(values)
    return (m - 1.96 * se, m + 1.96 * se)


def ranks_with_ties(values):
    indexed = sorted((v, i) for i, v in enumerate(values))
    n = len(indexed)
    ranks = [0.0] * n
    tie_corr = 1.0
    i = 0
    tie_sums = []
    while i < n:
        j = i
        while j + 1 < n and indexed[j + 1][0] == indexed[i][0]:
            j += 1
        avg = (i + 1 + j + 1) / 2.0
        for k in range(i, j + 1):
            ranks[indexed[k][1]] = avg
        if j > i:
            t = j - i + 1
            tie_sums.append(t ** 3 - t)
        i = j + 1
    if n > 1:
        tie_corr = 1.0 - sum(tie_sums) / (n ** 3 - n)
    return ranks, tie_corr


def norm_cdf(z):
    return 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))


def gser(a, x):
    if x == 0:
        return 0.0
    gln = math.lgamma(a)
    ap = a
    total = 1.0 / a
    delta = total
    for _ in range(200):
        ap += 1
        delta *= x / ap
        total += delta
        if abs(delta) < abs(total) * 1e-12:
            break
    return total * math.exp(-x + a * math.log(x) - gln)


def gcf(a, x):
    gln = math.lgamma(a)
    b = x + 1.0 - a
    c = 1.0 / 1e-30
    d = 1.0 / b
    h = d
    for i in range(1, 201):
        an = -i * (i - a)
        b += 2.0
        d = an * d + b
        if abs(d) < 1e-30:
            d = 1e-30
        c = b + an / c
        if abs(c) < 1e-30:
            c = 1e-30
        d = 1.0 / d
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < 1e-12:
            break
    return math.exp(-x + a * math.log(x) - gln) * h


def gammq(a, x):
    if x < 0 or a <= 0:
        return float("nan")
    if x < a + 1.0:
        return 1.0 - gser(a, x)
    return gcf(a, x)


def chisq_sf(x, df):
    if x <= 0:
        return 1.0
    return gammq(df / 2.0, x / 2.0)


def kruskal_wallis(groups):
    groups = [g for g in groups if g]
    k = len(groups)
    if k < 2:
        return (float("nan"), 0, float("nan"))
    combined = [v for g in groups for v in g]
    n = len(combined)
    ranks, tie_corr = ranks_with_ties(combined)
    pos = 0
    sum_ranks = []
    for g in groups:
        sum_ranks.append(sum(ranks[pos:pos + len(g)]))
        pos += len(g)
    h = 12.0 / (n * (n + 1)) * sum(r ** 2 / len(g) for r, g in zip(sum_ranks, groups)) - 3 * (n + 1)
    if tie_corr > 0:
        h = h / tie_corr
    return (h, k - 1, chisq_sf(h, k - 1))


def mann_whitney(a, b):
    n1, n2 = len(a), len(b)
    if n1 == 0 or n2 == 0:
        return (float("nan"), float("nan"), float("nan"))
    combined = list(a) + list(b)
    ranks, tie_corr = ranks_with_ties(combined)
    r1 = sum(ranks[:n1])
    u1 = r1 - n1 * (n1 + 1) / 2.0
    u2 = n1 * n2 - u1
    u = min(u1, u2)
    var_u = n1 * n2 * (n1 + n2 + 1) / 12.0
    if tie_corr > 0:
        var_u *= tie_corr
    z = (u1 - n1 * n2 / 2.0) / math.sqrt(var_u) if var_u > 0 else 0.0
    p = 2.0 * (1.0 - norm_cdf(abs(z)))
    return (u, z, p)


def cohens_d(a, b):
    n1, n2 = len(a), len(b)
    if n1 < 2 or n2 < 2:
        return float("nan")
    pooled = math.sqrt(((n1 - 1) * sd(a) ** 2 + (n2 - 1) * sd(b) ** 2) / (n1 + n2 - 2))
    if pooled == 0:
        return 0.0
    return (mean(a) - mean(b)) / pooled


def load_records(db_path):
    task_meta = load_task_meta()
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT * FROM runs WHERE status='completed' ORDER BY created_at").fetchall()
    conn.close()
    records = []
    for row in rows:
        config = json.loads(row["config_json"] or "{}")
        result = json.loads(row["result_json"]) if row["result_json"] else {}
        if row["mode"] == "compare" and isinstance(result.get("comparisons"), list):
            for comp in result["comparisons"]:
                evaluation = comp.get("evaluation") or {}
                usage = comp.get("_usage") or {}
                meta = task_meta.get(config.get("task_id"), {})
                records.append({
                    "run_id": row["id"],
                    "baseline": comp.get("baseline") or "B?",
                    "task_id": config.get("task_id"),
                    "task_type": config.get("task_type"),
                    "task_coupling": meta.get("coupling"),
                    "task_role": meta.get("role"),
                    "task_difficulty": meta.get("difficulty"),
                    "score": evaluation.get("score"),
                    "success": evaluation.get("success"),
                    "total_tokens": usage.get("total_tokens"),
                    "duration_seconds": comp.get("_duration_seconds"),
                    "problem": row["problem"],
                })
        else:
            evaluation = result.get("evaluation") or {}
            meta = task_meta.get(config.get("task_id"), {})
            records.append({
                "run_id": row["id"],
                "baseline": row["baseline"] or config.get("baseline") or "B?",
                "task_id": config.get("task_id"),
                "task_type": config.get("task_type"),
                "task_coupling": meta.get("coupling"),
                "task_role": meta.get("role"),
                "task_difficulty": meta.get("difficulty"),
                "score": row["score"],
                "success": result.get("success") if isinstance(result, dict) else None,
                "total_tokens": row["total_tokens"],
                "duration_seconds": row["duration_seconds"],
                "problem": row["problem"],
            })
    return records


def table_main(records):
    lines = ["| 条件 | n | Q（均值±SD） | 95% CI | SR（%） | Cost（k Token） | Time（s） | Stab CV（%） |",
             "|---|---:|---:|---:|---:|---:|---:|---:|"]
    for b in BASELINES:
        recs = [r for r in records if r["baseline"] == b]
        scores = [r["score"] for r in recs if isinstance(r["score"], (int, float))]
        successes = [r["success"] for r in recs if isinstance(r["success"], bool)]
        costs = [r["total_tokens"] / 1000 for r in recs if isinstance(r["total_tokens"], (int, float))]
        times = [r["duration_seconds"] for r in recs if isinstance(r["duration_seconds"], (int, float))]
        if not recs:
            lines.append(f"| {b} | 0 | — | — | — | — | — | — |")
            continue
        q_mean, q_sd = mean(scores), sd(scores)
        lo, hi = ci(scores)
        sr = (sum(successes) / len(successes) * 100) if successes else float("nan")
        cost_mean, cost_sd = mean(costs), sd(costs)
        time_mean, time_sd = mean(times), sd(times)
        cv = (q_sd / q_mean * 100) if q_mean and not math.isnan(q_sd) else float("nan")

        def fmt(m, s):
            if not isinstance(m, float) or math.isnan(m):
                return "—"
            if isinstance(s, float) and not math.isnan(s):
                return f"{m:.2f}±{s:.2f}"
            return f"{m:.2f}"

        def fmt_ci(m_lo, m_hi):
            if not isinstance(m_lo, float) or math.isnan(m_lo) or not isinstance(m_hi, float) or math.isnan(m_hi):
                return "—"
            return f"[{m_lo:.2f}, {m_hi:.2f}]"

        cv_text = f"{cv:.1f}" if not math.isnan(cv) else "—"
        sr_text = f"{sr:.1f}" if not math.isnan(sr) else "—"
        lines.append(f"| {b} | {len(scores)} | {fmt(q_mean, q_sd)} | {fmt_ci(lo, hi)} | {sr_text} | {fmt(cost_mean, cost_sd)} | {fmt(time_mean, time_sd)} | {cv_text} |")
    return "\n".join(lines)


def table_statistics(records):
    groups = [[r["score"] for r in records if r["baseline"] == b and isinstance(r["score"], (int, float))] for b in BASELINES]
    present = [g for g in groups if len(g) >= 1]
    lines = []
    if len(present) >= 2:
        h, df, p = kruskal_wallis(present)
        lines.append(f"- Kruskal–Wallis: H = {h:.2f}, df = {df}, p = {p:.4f}")
    lines.append("")
    lines.append("| 比较 | U | z | p | rank-biserial r | Cohen's d |")
    lines.append("|---|---:|---:|---:|---:|---:|")
    for i, a in enumerate(BASELINES):
        for b in BASELINES[i + 1:]:
            ga = groups[i]
            gb = groups[BASELINES.index(b)]
            if len(ga) < 2 or len(gb) < 2:
                continue
            u, z, p = mann_whitney(ga, gb)
            r_effect = 1.0 - 2.0 * u / (len(ga) * len(gb))
            d = cohens_d(ga, gb)
            lines.append(f"| {a} vs {b} | {u:.1f} | {z:.2f} | {p:.4f} | {r_effect:.2f} | {d:.2f} |")
    return "\n".join(lines)


def table_dimension(records, key, label):
    groups = [[r[key] for r in records if r["baseline"] == b and isinstance(r[key], (int, float))] for b in BASELINES]
    present = [g for g in groups if len(g) >= 2]
    if len(present) < 2:
        return f"{label}：数据不足，无法检验。"
    h, df, p = kruskal_wallis(present)
    return f"{label} Kruskal–Wallis: H = {h:.2f}, df = {df}, p = {p:.4f}"


def build_report(records):
    lines = ["# Emergence Lab 自动分析报告", ""]
    lines.append(f"- 有效 completed 样本组数：{len(records)}")
    lines.append("- 说明：SR 需要评估器返回 success；Rob/Adapt 需要扰动前后对照实验，本脚本不强行估计。")
    lines.append("- 分层口径：core=高耦合核心判别任务；boundary=低耦合边界任务（用于检验协作收益边界）。")
    lines.append("")
    lines.append("## 全样本主结果（表 5-1 风格）")
    lines.append("")
    lines.append(table_main(records))
    lines.append("")
    lines.append("## 全样本任务质量 Q 统计检验（表 5-7 风格）")
    lines.append("")
    lines.append(table_statistics(records))
    lines.append("")
    lines.append("## 分层一：按任务角色分层")
    for role, role_label in (("core", "核心判别任务（高耦合，role=core）"), ("boundary", "边界任务（低耦合，role=boundary）")):
        subset = [r for r in records if r.get("task_role") == role]
        lines.append("")
        lines.append(f"### {role_label}（n={len(subset)}）")
        lines.append("")
        lines.append(table_main(subset))
        lines.append("")
        lines.append(table_statistics(subset))
    lines.append("")
    lines.append("## 分层二：按任务类型分层")
    for type_key, type_label in (("code_repair", "代码修复（CR）"), ("information_integration", "信息整合与规划（II）"), ("dynamic_perturbation", "动态扰动（DT）")):
        subset = [r for r in records if r.get("task_type") == type_key]
        lines.append("")
        lines.append(f"### {type_label}（n={len(subset)}）")
        lines.append("")
        lines.append(table_main(subset))
    lines.append("")
    lines.append("## 全样本其他维度的组间检验")
    lines.append("")
    lines.append("- " + table_dimension(records, "total_tokens", "Cost（总 Token）"))
    lines.append("- " + table_dimension(records, "duration_seconds", "Time（秒）"))
    lines.append("")
    lines.append("## 分层三：按难度分层（主结果）")
    for diff in ("easy", "medium", "hard"):
        subset = [r for r in records if r.get("task_difficulty") == diff]
        lines.append("")
        lines.append(f"### {diff}（n={len(subset)}）")
        lines.append("")
        lines.append(table_main(subset))
    return "\n".join(lines)


def main():
    db_path = Path(sys.argv[1]) if len(sys.argv) > 1 else DB_PATH
    if not db_path.exists():
        print(f"数据库不存在: {db_path}")
        sys.exit(1)
    records = load_records(db_path)
    if not records:
        print("没有已完成的运行记录。")
        sys.exit(0)
    report = build_report(records)
    REPORT_DIR.mkdir(exist_ok=True)
    out = REPORT_DIR / "analysis_tables.md"
    out.write_text(report, encoding="utf-8")
    print(report)
    print(f"\n[报告已写入] {out}")


if __name__ == "__main__":
    main()
