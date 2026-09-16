#!/usr/bin/env python3
"""一次性迁移：向任务库追加 12 个能力边缘任务（5 CR + 4 II + 3 DT，全部 hard/high/core）。"""
import json
from pathlib import Path

TASKS = Path(__file__).resolve().parent.parent / "tasks.json"

NEW_TASKS = [
    {
        "id": "CR-13",
        "type": "code_repair",
        "title": "LRU 缓存容量与 TTL 交互",
        "difficulty": "hard",
        "coupling": "high",
        "problem": "实现一个带过期时间的 LRU 缓存类 `LRUCache(capacity, ttl_seconds, clock)`：\n1. `put(key, value)` 写入；超出容量时驱逐最久未使用的条目；重复 put 同一 key 视为更新并刷新其热度。\n2. `get(key)` 返回值并刷新热度；不存在或已过期返回 None。\n3. 条目在写入 ttl_seconds 秒后过期；过期条目视为不存在，且不再占用容量。\n4. `clock` 是一个无参函数，返回当前秒数（float），用于测试注入；不得直接调用 time.time()。\n请给出完整类实现和测试用例。",
        "success_criteria": "通过测试：容量驱逐顺序正确、重复 put 刷新热度、TTL 过期后 get 返回 None、过期条目释放容量。",
        "perturbation": None,
        "perturbation_message": None,
        "reference_answer": None,
        "test_code": '''
def test_capacity_eviction():
    clock = [0.0]
    c = LRUCache(2, 100, clock=lambda: clock[0])
    c.put("a", 1)
    c.put("b", 2)
    assert c.get("a") == 1
    c.put("c", 3)
    assert c.get("b") is None
    assert c.get("c") == 3

def test_ttl_expiry():
    clock = [0.0]
    c = LRUCache(2, 10, clock=lambda: clock[0])
    c.put("a", 1)
    clock[0] = 11.0
    assert c.get("a") is None

def test_expired_frees_capacity():
    clock = [0.0]
    c = LRUCache(1, 10, clock=lambda: clock[0])
    c.put("a", 1)
    clock[0] = 20.0
    c.put("b", 2)
    assert c.get("b") == 2

def test_update_refreshes_recency():
    clock = [0.0]
    c = LRUCache(2, 100, clock=lambda: clock[0])
    c.put("a", 1)
    c.put("b", 2)
    c.put("a", 9)
    c.put("c", 3)
    assert c.get("b") is None
    assert c.get("a") == 9
''',
        "role": "core",
    },
    {
        "id": "CR-14",
        "type": "code_repair",
        "title": "分页查询过滤排序交互",
        "difficulty": "hard",
        "coupling": "high",
        "problem": "实现 `paginate(items, page, page_size, key=None, reverse=False, filter_fn=None)`，返回 dict：{\"items\", \"total\", \"page\", \"pages\"}。要求：\n1. page 从 1 开始；total 是过滤后的总数；pages 为过滤后的总页数（空数据 pages=0）。\n2. 先过滤、再排序、后分页；排序必须稳定（相等元素保持原相对顺序）。\n3. page < 1 或（total > 0 且 page > pages）时抛出 ValueError；page_size < 1 同样抛 ValueError。\n4. key 为 None 时保持原始顺序。\n请给出完整函数和测试用例。",
        "success_criteria": "通过测试：过滤后分页计数正确、排序稳定、非法页码抛 ValueError、空数据 pages=0。",
        "perturbation": None,
        "perturbation_message": None,
        "reference_answer": None,
        "test_code": '''
DATA = [{"name": n, "v": v} for n, v in [("a", 3), ("b", 1), ("c", 2), ("d", 2), ("e", 5)]]

def test_basic_page():
    r = paginate(DATA, 1, 2, key=lambda x: x["v"])
    assert [x["name"] for x in r["items"]] == ["b", "c"]
    assert r["total"] == 5 and r["pages"] == 3

def test_stable_sort():
    r = paginate(DATA, 1, 5, key=lambda x: x["v"])
    assert [x["name"] for x in r["items"]] == ["b", "c", "d", "a", "e"]

def test_filter_then_paginate():
    r = paginate(DATA, 1, 10, filter_fn=lambda x: x["v"] >= 2)
    assert r["total"] == 4
    assert [x["name"] for x in r["items"]] == ["a", "c", "d", "e"]
    r2 = paginate(DATA, 2, 3, key=lambda x: x["v"], filter_fn=lambda x: x["v"] >= 2)
    assert [x["name"] for x in r2["items"]] == ["e"]

def test_invalid_page():
    for p in (0, 4):
        try:
            paginate(DATA, p, 2)
        except ValueError:
            continue
        raise AssertionError(f"expected ValueError for page={p}")

def test_empty():
    r = paginate([], 1, 10)
    assert r == {"items": [], "total": 0, "page": 1, "pages": 0}
''',
        "role": "core",
    },
    {
        "id": "CR-15",
        "type": "code_repair",
        "title": "订单状态机非法转移",
        "difficulty": "hard",
        "coupling": "high",
        "problem": "实现一个订单状态机，核心函数 `next_state(state, event)`：\n1. 合法转移：created --pay--> paid；paid --ship--> shipped；shipped --complete--> done；created --cancel--> cancelled；paid --cancel--> refunded。\n2. 其他任何状态与事件组合（包括终态上的操作、未知事件）都必须抛出自定义异常 `InvalidTransition`（需在模块中定义）。\n3. 另实现 `history(events)`：从 created 开始依次应用事件序列，返回状态序列；任一事件非法时抛出 InvalidTransition 且不产生部分结果。\n请给出完整代码和测试用例。",
        "success_criteria": "通过测试：合法路径正确、取消分支正确（paid 取消为 refunded）、非法转移与未知事件均抛 InvalidTransition、history 中途非法不产生部分结果。",
        "perturbation": None,
        "perturbation_message": None,
        "reference_answer": None,
        "test_code": '''
def test_happy_path():
    assert next_state("created", "pay") == "paid"
    assert next_state("paid", "ship") == "shipped"
    assert next_state("shipped", "complete") == "done"

def test_cancel_branches():
    assert next_state("created", "cancel") == "cancelled"
    assert next_state("paid", "cancel") == "refunded"

def test_invalid_transitions():
    for state, event in [("done", "cancel"), ("created", "ship"), ("cancelled", "pay"), ("shipped", "pay"), ("paid", "complete"), ("refunded", "pay")]:
        try:
            next_state(state, event)
        except InvalidTransition:
            continue
        raise AssertionError(f"expected InvalidTransition for {state}+{event}")

def test_unknown_event():
    try:
        next_state("created", "explode")
    except InvalidTransition:
        pass
    else:
        raise AssertionError("expected InvalidTransition")

def test_history():
    assert history(["pay", "ship"]) == ["created", "paid", "shipped"]
    try:
        history(["pay", "complete"])
    except InvalidTransition:
        pass
    else:
        raise AssertionError("expected InvalidTransition")
''',
        "role": "core",
    },
    {
        "id": "CR-16",
        "type": "code_repair",
        "title": "滑动窗口速率限制器",
        "difficulty": "hard",
        "coupling": "high",
        "problem": "实现 `RateLimiter(limit, window_seconds, clock)`：\n1. `allow()` 在滑动窗口内调用次数未达到 limit 时返回 True，否则 False；每次 allow() 调用都计入窗口计数（包括被拒绝的调用不计入）。\n2. 使用滑动窗口（不是固定时间桶）：任意时刻 t，窗口为 (t - window_seconds, t]。\n3. `clock` 是无参函数返回当前秒数（float），用于测试注入；不得直接调用 time.time()。\n4. limit <= 0 或 window_seconds <= 0 时构造抛 ValueError。\n请给出完整类实现和测试用例。",
        "success_criteria": "通过测试：窗口内计数正确、窗口滑动后恢复、恰好 window_seconds 后视为已滑出、被拒绝的调用不计数、非法参数抛 ValueError。",
        "perturbation": None,
        "perturbation_message": None,
        "reference_answer": None,
        "test_code": '''
def test_limit():
    clock = [0.0]
    r = RateLimiter(3, 10, clock=lambda: clock[0])
    assert [r.allow() for _ in range(4)] == [True, True, True, False]

def test_window_slides():
    clock = [0.0]
    r = RateLimiter(2, 10, clock=lambda: clock[0])
    assert r.allow() and r.allow()
    clock[0] = 5.0
    assert r.allow() == False
    clock[0] = 10.1
    assert r.allow() == True

def test_boundary_exact():
    clock = [100.0]
    r = RateLimiter(1, 10, clock=lambda: clock[0])
    assert r.allow() == True
    clock[0] = 110.0
    assert r.allow() == True

def test_rejected_not_counted():
    clock = [0.0]
    r = RateLimiter(1, 10, clock=lambda: clock[0])
    assert r.allow() == True
    assert r.allow() == False
    assert r.allow() == False
    clock[0] = 10.5
    assert r.allow() == True

def test_invalid_args():
    for args in ((0, 10), (3, 0), (-1, 5)):
        try:
            RateLimiter(*args, clock=lambda: 0.0)
        except ValueError:
            continue
        raise AssertionError(f"expected ValueError for {args}")
''',
        "role": "core",
    },
    {
        "id": "CR-17",
        "type": "code_repair",
        "title": "四则运算表达式解析器",
        "difficulty": "hard",
        "coupling": "high",
        "problem": "实现 `evaluate(expr)`：计算含 + - * / 和括号的算术表达式字符串。要求：\n1. 支持运算优先级、括号、一元负号（如 \"-3+5\"、\"-(2+3)*2\"）和小数。\n2. 除零抛出 ZeroDivisionError。\n3. 语法错误（如 \"1+\"、\"(1\"、\"abc\"、空串）抛出 ValueError。\n4. 不得使用 eval 或 exec。\n请给出完整实现和测试用例。",
        "success_criteria": "通过测试：优先级与括号正确、一元负号正确、除零抛 ZeroDivisionError、非法表达式抛 ValueError、未使用 eval/exec。",
        "perturbation": None,
        "perturbation_message": None,
        "reference_answer": None,
        "test_code": '''
def test_arithmetic():
    assert evaluate("1+2*3") == 7
    assert evaluate("(1+2)*3") == 9
    assert abs(evaluate("10/4") - 2.5) < 1e-9
    assert evaluate("2*3+4*5") == 26

def test_unary_minus():
    assert evaluate("-3+5") == 2
    assert evaluate("-(2+3)*2") == -10
    assert evaluate("3*-2") == -6

def test_division_by_zero():
    try:
        evaluate("1/0")
    except ZeroDivisionError:
        pass
    else:
        raise AssertionError("expected ZeroDivisionError")

def test_invalid_syntax():
    for bad in ("1+", "(1", "abc", "", "1 2"):
        try:
            evaluate(bad)
        except ValueError:
            continue
        raise AssertionError(f"expected ValueError for {bad!r}")
''',
        "role": "core",
    },
    {
        "id": "II-11",
        "type": "information_integration",
        "title": "三来源用户数据口径冲突",
        "difficulty": "hard",
        "coupling": "high",
        "problem": "公司要上报产品的月活跃用户数（MAU），三个来源给出矛盾数据：\n1. 埋点平台：上月 MAU 120 万（基于设备去重日志）；\n2. 应用商店后台：上月活跃设备 80 万（仅统计通过商店安装的渠道）；\n3. 客服系统：注册账号总数 300 万（累计注册口径，含历史流失）。\n管理层要求明天给出\"一个数字\"用于对外披露。请分析三个数字的关系与口径差异，给出披露建议、核实步骤和不能做的事。",
        "success_criteria": "识别三者统计口径不同（活跃设备/渠道子集/累计注册），不得简单取平均或任选其一；给出披露口径建议、核实步骤与合规风险说明。",
        "perturbation": None,
        "perturbation_message": None,
        "reference_answer": None,
        "role": "core",
    },
    {
        "id": "II-12",
        "type": "information_integration",
        "title": "培训排期隐藏不可行",
        "difficulty": "hard",
        "coupling": "high",
        "problem": "安排下周的三场培训，约束如下：\n1. 课程《安全合规》必须由讲师 A 讲授，A 仅周二上午有空，且必须占用教室（线上不可）；\n2. 课程《代码评审》由讲师 B 讲授，B 周三全天或周四下午有空；\n3. 课程《数据库优化》由讲师 C 讲授，C 周二下午或周四上午有空；\n4. 教室 X 周二全天、周三上午可用；教室 Y 仅周四全天可用；每场培训占用一个教室半天；\n5. 《安全合规》和《数据库优化》的听众高度重合，不得安排在同一半天。\n请给出排期方案。",
        "success_criteria": "正确发现《安全合规》只能排在周二上午教室 X，《数据库优化》受讲师 C 与听众冲突约束后与《安全合规》冲突，识别出三个课程在给定约束下无法全部排入，并给出至少两个明确的松弛方案（如调整听众重合约束或更换讲师）。",
        "perturbation": None,
        "perturbation_message": None,
        "reference_answer": None,
        "role": "core",
    },
    {
        "id": "II-13",
        "type": "information_integration",
        "title": "故障时间线跨源重建",
        "difficulty": "hard",
        "coupling": "high",
        "problem": "一次线上故障后拿到三份日志，时间戳基准不一致：\n1. 应用日志（服务器 A，本地时间 UTC+8）：09:12 开始大量超时，09:15 服务不可用；\n2. 数据库慢查询日志（服务器 B，UTC）：01:05 起慢查询激增，01:10 连接池耗尽；\n3. 部署系统记录（服务器 C，UTC+8，但事后发现该机器时钟快 6 分钟）：09:20 有一次配置变更上线。\n请重建故障因果时间线，给出对齐假设、最可能的根因链，以及每条结论的置信度。",
        "success_criteria": "显式处理时区差异（UTC vs UTC+8）和时钟漂移（+6 分钟），将三源事件对齐到统一时间轴；识别部署变更实际发生在 09:14（对齐后），介于超时开始与服务不可用之间；因果链排序合理并标注置信度。",
        "perturbation": None,
        "perturbation_message": None,
        "reference_answer": None,
        "role": "core",
    },
    {
        "id": "II-14",
        "type": "information_integration",
        "title": "预算硬约束不可行识别",
        "difficulty": "hard",
        "coupling": "high",
        "problem": "部门年度预算 100 万元，必须覆盖三个刚性支出：\n1. 云服务合同续约：最低 55 万（已签三年框架协议，违约金 80 万）；\n2. 等保合规整改：最低 40 万（监管死线，不可延期）；\n3. 核心系统维保：最低 30 万（停止维保则厂商不再提供支持）。\n请给出预算分配方案。",
        "success_criteria": "明确识别三项刚性支出合计 125 万超出 100 万预算，问题在给定约束下无可行解；不得给出三项全满足且总额 ≤100 万的虚假方案；给出至少两个可行的松弛路径（如申请追加预算、谈判续约条款、承担违约金的经济性比较）。",
        "perturbation": None,
        "perturbation_message": None,
        "reference_answer": None,
        "role": "core",
    },
    {
        "id": "DT-09",
        "type": "dynamic_perturbation",
        "title": "新品发布会策划（预算中途腰斩）",
        "difficulty": "hard",
        "coupling": "high",
        "problem": "为某消费电子新品策划发布会。初始要求：预算 50 万元，筹备期 6 周，线下 300 人规模 + 线上直播，需覆盖媒体邀请、场地、物料、直播推流与应急预案。请给出完整执行方案。",
        "success_criteria": "扰动注入后，最终方案必须在 30 万元与 4 周的新约束下重新规划（不得沿用原 50 万/6 周方案的关键假设），明确哪些环节被削减或替换，并保留应急预案。",
        "perturbation": "D1",
        "perturbation_message": "执行中途新增关键约束：公司财务通知预算从 50 万元削减至 30 万元，且发布会必须提前至 4 周后举行（配合电商大促档期）。",
        "reference_answer": None,
        "role": "core",
    },
    {
        "id": "DT-10",
        "type": "dynamic_perturbation",
        "title": "灾备演练方案（RTO 数据冲突）",
        "difficulty": "hard",
        "coupling": "high",
        "problem": "为核心支付系统设计年度灾备演练方案。初始资料：系统 RTO 目标 4 小时、RPO 5 分钟，已有异地双活架构。请给出演练范围、故障注入方式、验收标准和回退方案。",
        "success_criteria": "识别两份监控报告关于历史恢复时间（4 小时 vs 15 分钟）的矛盾，显式说明在原始日志不可用时的处理策略（如按保守口径设计验收标准），演练方案与验收标准完整。",
        "perturbation": "D2",
        "perturbation_message": "执行中途关键资料出现冲突：运维团队的监控报告称去年故障实际恢复时间为 4 小时 10 分钟；但 SRE 团队的复盘报告称同类故障 15 分钟内恢复。两份报告口径矛盾，且原始日志因保留策略已不可查。",
        "reference_answer": None,
        "role": "core",
    },
    {
        "id": "DT-11",
        "type": "dynamic_perturbation",
        "title": "渗透测试方案（资产清单缺网段）",
        "difficulty": "hard",
        "coupling": "high",
        "problem": "为一家互联网金融公司设计年度渗透测试方案。初始要求：覆盖全部对外暴露资产，周期 3 周，预算 40 万元，需符合监管要求并产出整改闭环。请给出测试范围、方法、分工与交付物。",
        "success_criteria": "识别出资产清单缺少支付系统网段这一关键缺口，说明继续按旧清单执行的风险，给出补全清单或调整范围的恢复措施；最终方案覆盖范围、方法、交付物与合规要求。",
        "perturbation": "D3",
        "perturbation_message": "执行中途发现某关键 Agent 的阶段产出低质：安全工程师提交的资产清单遗漏了支付系统所在网段（10.20.0.0/16），且将两个已下线的测试域名列为重点目标。需要识别问题并恢复。",
        "reference_answer": None,
        "role": "core",
    },
]


def main():
    data = json.loads(TASKS.read_text(encoding="utf-8"))
    existing = {t["id"] for t in data["tasks"]}
    added = 0
    for task in NEW_TASKS:
        if task["id"] in existing:
            continue
        if task.get("test_code"):
            task["test_code"] = task["test_code"].strip() + "\n"
        data["tasks"].append(task)
        added += 1
    TASKS.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"新增 {added} 个任务，总数 {len(data['tasks'])}")


if __name__ == "__main__":
    main()
