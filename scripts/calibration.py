#!/usr/bin/env python3
"""B0 校准实验：单 Agent 跑全部任务，检验任务难度是否存在天花板效应。

用法：
    python scripts/calibration.py [--repeats 3] [--tasks CR-01,II-03] [--base http://127.0.0.1:4173]

模型配置通过环境变量传入（服务端也可自行配置）：
    OPENAI_BASE_URL / OPENAI_MODEL / OPENAI_API_KEY

输出：
    reports/calibration.md    人读报告
    reports/calibration.json  原始汇总数据
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REPORTS = ROOT / "reports"


def api(base: str, method: str, path: str, payload: dict | None = None):
    url = base.rstrip("/") + path
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    # 本地服务需要绕过系统代理，否则 macOS 上 localhost 请求会被代理拦截返回 502
    host = urllib.parse.urlparse(url).hostname or ""
    opener = (urllib.request.build_opener(urllib.request.ProxyHandler({}))
              if host in {"localhost", "::1"} or host.startswith("127.")
              else urllib.request.build_opener())
    with opener.open(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def wait_run(base: str, run_id: str, timeout: int = 900) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        run = api(base, "GET", f"/api/runs/{run_id}")
        if run.get("status") in {"completed", "failed"}:
            return run
        time.sleep(3)
    return {"id": run_id, "status": "timeout"}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="http://127.0.0.1:4173")
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--tasks", default="", help="逗号分隔的任务 id，默认全部")
    parser.add_argument("--baseline", default="B0", help="B0/B1/B2/B3/B4")
    parser.add_argument("--capabilities", default="",
                        help="逗号分隔，如 vote,debate,reflect,memory,judge,redistribute；默认 B0 为空、其他全开")
    args = parser.parse_args()

    tasks = api(args.base, "GET", "/api/tasks")
    if args.tasks:
        wanted = set(args.tasks.split(","))
        tasks = [t for t in tasks if t["id"] in wanted]
    if not tasks:
        sys.exit("没有匹配的任务")

    model_config = {
        "base_url": os.getenv("OPENAI_BASE_URL", ""),
        "model": os.getenv("OPENAI_MODEL", ""),
        "api_key": os.getenv("OPENAI_API_KEY", ""),
    }
    print(f"模型: {model_config['model'] or '(服务端配置)'}  任务数: {len(tasks)}  每任务重复: {args.repeats}  基线: {args.baseline}")

    capabilities = ([c for c in args.capabilities.split(",") if c] if args.capabilities
                    else ([] if args.baseline == "B0"
                          else ["vote", "debate", "reflect", "memory", "judge", "redistribute"]))

    results: list[dict] = []
    run_counter = 0
    total = len(tasks) * args.repeats
    # 逐任务提交并等待，避免 90 次运行同时并发触发限流
    for task in tasks:
        payload = {
            **model_config,
            "task_id": task["id"],
            "baseline": args.baseline,
            "capabilities": capabilities,
            "agent_count": 5,
            "mode": "single",
            "repeats": args.repeats,
        }
        resp = api(args.base, "POST", "/api/runs/batch", payload)
        run_ids = resp.get("run_ids", [])
        print(f"已提交 {task['id']} {task['title']} -> {len(run_ids)} 次运行", flush=True)
        for run_id in run_ids:
            run = wait_run(args.base, run_id)
            result = run.get("result") or {}
            evaluation = result.get("evaluation") or {}
            score = run.get("score")
            results.append({
                "run_id": run_id,
                "task_id": task["id"],
                "type": task["type"],
                "role": task["role"],
                "difficulty": task["difficulty"],
                "coupling": task["coupling"],
                "status": run.get("status"),
                "score": score,
                "success": result.get("success"),
                "total_tokens": (run.get("usage") or {}).get("total_tokens"),
                "duration_seconds": run.get("duration_seconds"),
                "error": run.get("error"),
            })
            run_counter += 1
            print(f"[{run_counter}/{total}] {task['id']} status={run.get('status')} Q={score} success={result.get('success')}", flush=True)

    REPORTS.mkdir(exist_ok=True)
    (REPORTS / "calibration.json").write_text(
        json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")

    # 汇总：按任务聚合，再按维度分组
    def group_stats(key):
        groups: dict[str, list[float]] = {}
        for r in results:
            if isinstance(r["score"], (int, float)):
                groups.setdefault(str(r[key]), []).append(r["score"])
        return {k: {"n": len(v), "mean": round(statistics.mean(v), 1),
                    "sd": round(statistics.stdev(v), 1) if len(v) > 1 else 0.0,
                    "min": min(v), "max": max(v)}
                for k, v in sorted(groups.items())}

    all_scores = [r["score"] for r in results if isinstance(r["score"], (int, float))]
    failed = [r for r in results if r["status"] != "completed"]
    ceiling = [r for r in results if isinstance(r["score"], (int, float)) and r["score"] >= 85]

    lines = ["# B0 单 Agent 校准实验报告", ""]
    lines.append(f"- 模型: {model_config['model'] or '(服务端配置)'}")
    lines.append(f"- 总运行: {len(results)} 次（{len(tasks)} 任务 × {args.repeats} 重复），失败 {len(failed)} 次")
    if all_scores:
        lines.append(f"- 全体 Q: mean={statistics.mean(all_scores):.1f}, "
                     f"sd={statistics.stdev(all_scores):.1f}, "
                     f"median={statistics.median(all_scores):.1f}, "
                     f"min={min(all_scores)}, max={max(all_scores)}")
        lines.append(f"- Q>=85 的运行占比: {len(ceiling)}/{len(all_scores)} "
                     f"({100 * len(ceiling) / len(all_scores):.0f}%)")
    for key, title in [("role", "按角色 (core/boundary)"), ("type", "按任务类型"),
                       ("difficulty", "按难度"), ("coupling", "按耦合度")]:
        lines += ["", f"## {title}", "", "| 分组 | n | mean | sd | min | max |", "|---|---|---|---|---|---|"]
        for k, s in group_stats(key).items():
            lines.append(f"| {k} | {s['n']} | {s['mean']} | {s['sd']} | {s['min']} | {s['max']} |")
    lines += ["", "## 逐任务均值", "", "| 任务 | 类型 | 难度 | 角色 | mean Q | success 率 |",
              "|---|---|---|---|---|---|"]
    by_task: dict[str, list[dict]] = {}
    for r in results:
        by_task.setdefault(r["task_id"], []).append(r)
    for task_id, rs in sorted(by_task.items()):
        scores = [r["score"] for r in rs if isinstance(r["score"], (int, float))]
        succ = [r["success"] for r in rs if isinstance(r["success"], bool)]
        mean = f"{statistics.mean(scores):.1f}" if scores else "-"
        sr = f"{100 * sum(succ) / len(succ):.0f}%" if succ else "-"
        first = rs[0]
        lines.append(f"| {task_id} | {first['type']} | {first['difficulty']} | {first['role']} | {mean} | {sr} |")

    (REPORTS / "calibration.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"\n报告已写入 {REPORTS / 'calibration.md'}")


if __name__ == "__main__":
    main()
