#!/usr/bin/env python3
"""一次性整合：把 CHAIN/GEN/DCHAIN 共 9 个组合依赖型任务并入 tasks.json。

输入：reports/chain_tasks.json（build_chain_tasks.py 产物）、reports/dchain01_tests.json
输出：tasks.json（就地更新，幂等：按 id 覆盖或追加）
"""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TASKS = ROOT / "tasks.json"

chain_tasks = json.loads((ROOT / "reports" / "chain_tasks.json").read_text(encoding="utf-8"))["tasks"]
dchain01_tests = json.loads((ROOT / "reports" / "dchain01_tests.json").read_text(encoding="utf-8"))
c1 = next(t for t in chain_tasks if t["id"] == "CHAIN-01")
ref500 = c1["reference_answer"].replace("MIN_TOTAL_CENTS = 100", "MIN_TOTAL_CENTS = 500")
assert ref500 != c1["reference_answer"]

GEN_TEMPLATES = [
    ("GEN-01", "multi_hop", "多文档多跳针毡（生成式）",
     "5 份含大量干扰实体的公司内部文档，答案需要 3–4 跳关联（如：某项目负责人所在部门上季度的预算缺口）。每次运行随机生成全新实例，无法靠记忆作答。"),
    ("GEN-02", "logic_grid", "逻辑网格谜题（生成式）",
     "5 人 × 3 属性排期谜题，12 条线索（含否定式与条件式），唯一解。每次运行随机生成全新实例。"),
    ("GEN-03", "inventory_sim", "库存长链模拟（生成式）",
     "4–6 个 SKU、25–35 步含条件分支的出入库操作序列，问最终库存。一步算错全链错。每次运行随机生成全新实例。"),
    ("GEN-04", "dag_schedule", "依赖图排期与关键路径（生成式）",
     "12–15 个任务的 DAG + 资源冲突约束（含看似可并行实则冲突的陷阱），求最短工期与关键路径。每次运行随机生成全新实例。"),
]

gen_tasks = [
    {
        "id": tid,
        "type": "exact_match",
        "title": title,
        "difficulty": "hard",
        "coupling": "high",
        "problem": f"【生成式任务】{desc}\n（实际题目材料在每次运行时由平台按随机种子生成，此处仅为题型说明。）",
        "success_criteria": "最终答案与生成实例的唯一解完全一致（由精确匹配评分器客观判定）。",
        "perturbation": None,
        "perturbation_message": None,
        "reference_answer": None,
        "generator": kind,
        "role": "core",
    }
    for tid, kind, title, desc in GEN_TEMPLATES
]

dchain01 = {
    "id": "DCHAIN-01",
    "type": "chain_repair",
    "title": "订单管道修复（中途变更过滤阈值）",
    "difficulty": "hard",
    "coupling": "high",
    "problem": c1["problem"],
    "success_criteria": "扰动注入后，最终修复版必须按新门槛（单价≥5元/500分）过滤且全部分层测试通过；沿用旧门槛（100 分）的修复视为未适应扰动。",
    "perturbation": "D1",
    "perturbation_message": "执行中途需求变更：过滤阶段的最低单价门槛从 1 元（100 分）提高到 5 元（500 分），其余规则不变。请按新门槛完成全部修复，并显式说明受影响的阶段。",
    "reference_answer": ref500,
    "test_code": dchain01_tests,
    "role": "core",
}

DCHAIN02_DOCS = """以下是五份公司内部文档片段：

【项目周报摘录】「灯塔」项目本周完成数据迁移第一阶段，负责人王珊汇报了进度风险；「曙光」项目负责人李强汇报了推广计划；「磐石」项目负责人赵磊汇报延期原因。

【人事通知】王珊，数据平台部高级经理；李强，市场拓展部总监；赵磊，供应链管理部经理；陈晨，质量保障部主管。

【分管关系通知】数据平台部向 CTO 线汇报；市场拓展部向销售 VP 线汇报。

【季度财务快报】各部门上季度预算执行（单位：万元）：数据平台部：批复预算 620，实际支出 680；市场拓展部：批复 540，实际支出 550；供应链管理部：批复 490，实际支出 510；质量保障部：批复 710，实际支出 685。

【行政通知】本年度会议室预定规则调整……（与问题无关的行政细节若干）

问题：「灯塔」项目负责人所在部门，上季度的预算缺口（实际支出 − 批复预算）是多少万元？若实际支出低于批复预算，缺口为负数。

在回答最后用 ```json 代码块输出：{"最终答案": <数字>}"""

dchain02 = {
    "id": "DCHAIN-02",
    "type": "dynamic_perturbation",
    "title": "多跳问答（中途文档撤回更正）",
    "difficulty": "hard",
    "coupling": "high",
    "problem": DCHAIN02_DOCS,
    "success_criteria": "扰动注入后，最终答案必须使用更正后的财务数据（实际支出 590 万元），即答案为 -30；沿用撤回前数据（60）视为未适应扰动；过程需显式说明撤回文档的处理。",
    "perturbation": "D2",
    "perturbation_message": "执行中途关键资料撤回更正：【季度财务快报】被财务部门撤回，更正版为——数据平台部上季度实际支出为 590 万元（原 680 万元为口径错误），其余部门数字不变。请基于更正后的资料修订你的答案。",
    "reference_answer": "「灯塔」负责人是王珊（数据平台部）；更正后该部门上季度实际支出 590 万元、批复预算 620 万元，缺口 = 590 - 620 = -30 万元（即结余 30 万元）。",
    "answer_spec": {"kind": "value", "value": -30},
    "role": "core",
}

new_tasks = chain_tasks + gen_tasks + [dchain01, dchain02]


def main():
    data = json.loads(TASKS.read_text(encoding="utf-8"))
    by_id = {t["id"]: i for i, t in enumerate(data["tasks"])}
    for task in new_tasks:
        if task["id"] in by_id:
            data["tasks"][by_id[task["id"]]] = task
        else:
            data["tasks"].append(task)
    data["version"] = "2.0"
    TASKS.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"tasks.json 现有 {len(data['tasks'])} 个任务（新增/更新 {len(new_tasks)} 个）")


if __name__ == "__main__":
    main()
