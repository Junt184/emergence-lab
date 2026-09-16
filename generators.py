"""生成式多跳谜题模块：参数化随机生成 + 平台可知唯一解。

每次实验运行以不同 seed 调用 generate(kind, seed) 生成新实例，
使被测 LLM 无法依赖训练语料记忆，只能进行多跳推理 / 长链计算。

每个生成器配套独立求解器（solve_* / simulate_*），求解器只读取
实例的结构化数据（即题目文本所编码的信息），不复用生成内部状态。
直接运行 `python3.13 generators.py` 会对 seeds 0..49 逐一生成并自验。
"""

from __future__ import annotations

import itertools
import random
from typing import Any

KINDS = ("multi_hop", "logic_grid", "inventory_sim", "dag_schedule")

# ---------------------------------------------------------------------------
# 共享语料池
# ---------------------------------------------------------------------------

NAME_POOL = [
    "张伟", "李静", "王磊", "赵敏", "陈浩", "刘洋", "杨帆", "周洁",
    "吴迪", "孙鹏", "韩雪", "马超", "朱琳", "郭涛", "何欢", "罗成",
    "郑凯", "冯倩", "蒋峰", "沈岚", "曹骏", "谢宁", "邓婕", "许巍",
    "高翔", "林蓉",
]

PROJECT_POOL = [
    "猎户座", "天枢", "燎原", "昆仑", "青鸟", "玄武",
    "曙光", "长风", "紫微", "听潮", "沧海", "凌云",
    "北斗", "烽火", "瑶光", "白泽", "鸿鹄", "惊蛰",
]

DEPT_POOL = [
    "平台研发部", "数据智能部", "数据平台部", "数据运营部",
    "市场拓展部", "品牌市场部", "供应链管理部", "物流调度部",
    "财务共享中心", "客户成功部", "质量保障部", "战略规划部",
]

TOPIC_POOL = [
    "人工智能落地", "供应链优化", "财务审计", "市场营销复盘", "产品路线规划",
    "风险控制", "客户成功体系", "数据治理", "组织效能", "出海战略",
    "云原生架构", "合规体系建设",
]

WEEKDAYS = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
ROOMS = ["1", "2", "3", "4", "5", "6", "7"]
WEEK_ORDER = {w: i for i, w in enumerate(WEEKDAYS)}

LEVEL_COEF = {"甲": 2, "乙": 1.5, "丙": 1}
PROMO_CAP = 30  # 促销加倍出库量封顶（件）


def _fmt_instruction(skeleton: str) -> str:
    return (
        '在回答最后用 ```json 代码块输出：{"最终答案": ...}\n'
        "示例骨架：\n```json\n" + skeleton + "\n```"
    )


# ===========================================================================
# 1. multi_hop：多文档多跳针毡
# ===========================================================================

def solve_multi_hop(facts: dict[str, Any], question: dict[str, Any]) -> int:
    """独立求解器：沿问题指定的关联链逐步查找，返回预算缺口（万元）。"""
    owner = facts["project_owner"][question["project"]]
    manager = facts["person_manager"][owner]
    dept = facts["manager_dept"][manager]
    if question["hops"] == 4:
        budget = facts["dept_budget"][dept]["上季度"]
    else:  # 5 跳：部门 → 归口成本中心 → 成本中心预算
        cost_center = facts["dept_costcenter"][dept]
        budget = facts["costcenter_budget"][cost_center]["上季度"]
    return int(budget["actual"] - budget["budget"])


def _gen_multi_hop(seed: int) -> dict[str, Any]:
    rng = random.Random(f"multi_hop:{seed}")
    hops = 5 if seed % 3 == 2 else 4

    projects = rng.sample(PROJECT_POOL, 14)
    names = rng.sample(NAME_POOL, 19)
    owners, managers = names[:14], names[14:19]
    depts = rng.sample(DEPT_POOL, 8)
    cost_centers = [f"CC-{code}" for code in rng.sample(range(101, 399), 6)]

    project_owner = dict(zip(projects, owners))
    person_dept = {p: rng.choice(depts) for p in owners}
    person_manager = {p: rng.choice(managers) for p in owners}
    manager_dept = dict(zip(managers, rng.sample(depts, 5)))
    dept_costcenter = {d: rng.choice(cost_centers) for d in depts}

    target_project = rng.choice(projects)
    target_owner = project_owner[target_project]
    # 保证关联链每一跳都真实存在：负责人所在部门 ≠ 其上级分管的部门
    for _ in range(50):
        if person_dept[target_owner] != manager_dept[person_manager[target_owner]]:
            break
        person_manager[target_owner] = rng.choice(managers)
        person_dept[target_owner] = rng.choice(depts)
    target_dept = manager_dept[person_manager[target_owner]]
    target_cc = dept_costcenter[target_dept]

    def budget_rows(key: str, keys: list[str]) -> dict[str, dict[str, dict[str, int]]]:
        rows: dict[str, dict[str, dict[str, int]]] = {}
        for item in keys:
            last_budget = rng.randrange(120, 900, 10)
            if item == key:
                actual = last_budget + rng.randrange(20, 200, 5)
            else:
                actual = last_budget + rng.randrange(-80, 160, 5)
                if actual == last_budget:
                    actual += 15
            cur_budget = rng.randrange(120, 900, 10)
            cur_actual = cur_budget + rng.randrange(-80, 160, 5)
            rows[item] = {
                "上季度": {"budget": last_budget, "actual": actual},
                "本季度": {"budget": cur_budget, "actual": cur_actual},
            }
        return rows

    dept_budget = budget_rows(target_dept, depts)
    costcenter_budget = budget_rows(target_cc, cost_centers)

    facts = {
        "project_owner": project_owner,
        "person_dept": person_dept,
        "person_manager": person_manager,
        "manager_dept": manager_dept,
        "dept_costcenter": dept_costcenter,
        "dept_budget": dept_budget,
        "costcenter_budget": costcenter_budget,
    }
    question = {"project": target_project, "hops": hops}
    answer = solve_multi_hop(facts, question)

    doc_meeting = ["【项目立项会议纪要】本次会议确认以下项目正式立项："]
    for proj in projects:
        doc_meeting.append(
            f"· 「{proj}」项目，负责人：{project_owner[proj]}，拟定 {rng.randint(1, 12)} 月 {rng.randint(1, 28)} 日启动。"
        )
    doc_meeting.append("会议要求各负责人两周内提交里程碑计划。")

    doc_hr = ["【人力资源部邮件：人员任职与汇报关系更新】"]
    for person in owners:
        doc_hr.append(
            f"· {person}：现任{person_dept[person]}高级经理，直属上级为 {person_manager[person]}。"
        )
    doc_hr.append("以上汇报关系自本月起生效。")

    doc_manage = ["【管理层分管安排通知】经管理层决议，分管安排如下："]
    for mgr in managers:
        doc_manage.append(f"· {mgr} 分管{manager_dept[mgr]}。")
    doc_manage.append("未列明的部门维持原分管安排不变。")

    doc_fin = ["【季度财务快报】各部门预算执行情况（单位：万元）："]
    for dept in depts:
        b = dept_budget[dept]
        doc_fin.append(
            f"· {dept}：上季度批复 {b['上季度']['budget']}，支出 {b['上季度']['actual']}；"
            f"本季度批复 {b['本季度']['budget']}，支出 {b['本季度']['actual']}。"
        )

    doc_cc = ["【成本中心台账】各成本中心费用归集情况（单位：万元）："]
    for cc in cost_centers:
        b = costcenter_budget[cc]
        doc_cc.append(
            f"· {cc}：上季度额度 {b['上季度']['budget']}，归集 {b['上季度']['actual']}；"
            f"本季度额度 {b['本季度']['budget']}，归集 {b['本季度']['actual']}。"
        )

    doc_map = ["【财务归口对照表】各部门费用归口的成本中心如下："]
    for dept in depts:
        doc_map.append(f"· {dept} → {dept_costcenter[dept]}。")

    doc_weekly = ["【项目周报汇总】"]
    for proj in projects:
        doc_weekly.append(
            f"· 「{proj}」本周进度 {rng.randint(15, 95)}%，下一里程碑预计 {rng.randint(1, 12)} 月 {rng.randint(1, 28)} 日。"
        )

    docs = [doc_meeting, doc_hr, doc_manage, doc_fin, doc_cc, doc_map, doc_weekly]
    rng.shuffle(docs)
    doc_text = "\n\n".join("\n".join(lines) for lines in docs)

    mgr = person_manager[target_owner]
    chain_desc = (
        f"「{target_project}」项目负责人是 {target_owner}，其直属上级是 {mgr}，{mgr} 分管的部门是{target_dept}"
    )
    if hops == 4:
        b = dept_budget[target_dept]["上季度"]
        q_line = (
            f"问题：「{target_project}」项目的负责人的直属上级所分管的部门，"
            "其上季度的预算缺口是多少万元？"
        )
        ref_tail = (
            f"{target_dept}上季度批复预算 {b['budget']} 万元，实际支出 {b['actual']} 万元"
        )
        def_line = "（预算缺口 = 该部门上季度实际支出 − 上季度批复预算；答案为一个整数，单位万元，最终 JSON 中只填数值本身。）"
    else:
        b = costcenter_budget[target_cc]["上季度"]
        chain_desc += f"，{target_dept}的费用归口成本中心是 {target_cc}"
        q_line = (
            f"问题：「{target_project}」项目的负责人的直属上级所分管的部门，"
            "其费用归口的成本中心，上季度的预算缺口是多少万元？"
        )
        ref_tail = (
            f"{target_cc} 上季度额度 {b['budget']} 万元，归集费用 {b['actual']} 万元"
        )
        def_line = "（预算缺口 = 该成本中心上季度归集费用 − 上季度额度；答案为一个整数，单位万元，最终 JSON 中只填数值本身。）"
    reference = f"{chain_desc}；{ref_tail}，预算缺口 = {b['actual']} - {b['budget']} = {answer} 万元。"

    problem = (
        f"以下是 {len(docs)} 份公司内部文档片段（顺序已随机打乱）。文档中埋有大量名称相近但彼此无关的"
        "项目、人员、部门、成本中心与数字（例如名字相似的多个部门），只有沿着正确的关联链逐步定位"
        "才能得到答案，请勿凭印象猜测。\n\n"
        f"{doc_text}\n\n"
        f"{q_line}\n"
        f"{def_line}\n\n"
        + _fmt_instruction('{"最终答案": "123"}')
    )
    return {
        "problem": problem,
        "reference_answer": reference,
        "answer_spec": {"kind": "value", "value": answer},
        "meta": {"hops": hops, "instance": {"facts": facts, "question": question}},
    }


# ===========================================================================
# 2. logic_grid：5 人逻辑网格（唯一解）
# ===========================================================================

def _get_value(assign: dict[str, dict[str, str]], person: str, attr: str) -> str | None:
    return assign.get(person, {}).get(attr)


def _atom_state(atom: dict[str, Any], assign: dict[str, dict[str, str]]) -> bool | None:
    value = _get_value(assign, atom["person"], atom["attr"])
    if value is None:
        return None
    eq = value == atom["value"]
    return eq if atom["type"] == "attr_eq" else not eq


def _clue_violated(clue: dict[str, Any], assign: dict[str, dict[str, str]]) -> bool:
    """线索在部分赋值下是否已被确定违反（引用值未赋值时不视为违反）。"""
    ctype = clue["type"]
    if ctype in ("attr_eq", "attr_ne"):
        state = _atom_state(clue, assign)
        if state is None:
            return False
        return not state
    if ctype == "pair_eq":
        for person in assign:
            va = _get_value(assign, person, clue["attr_a"])
            vb = _get_value(assign, person, clue["attr_b"])
            if va == clue["value_a"] and vb is not None and vb != clue["value_b"]:
                return True
            if vb == clue["value_b"] and va is not None and va != clue["value_a"]:
                return True
        return False
    if ctype == "pair_ne":
        for person in assign:
            if (
                _get_value(assign, person, clue["attr_a"]) == clue["value_a"]
                and _get_value(assign, person, clue["attr_b"]) == clue["value_b"]
            ):
                return True
        return False
    if ctype == "before":
        wa = _get_value(assign, clue["a"], "星期")
        wb = _get_value(assign, clue["b"], "星期")
        if wa is not None and wb is not None:
            return WEEK_ORDER[wa] >= WEEK_ORDER[wb]
        return False
    if ctype == "adjacent":
        ra = _get_value(assign, clue["a"], "会议室")
        rb = _get_value(assign, clue["b"], "会议室")
        if ra is not None and rb is not None:
            adjacent = abs(int(ra) - int(rb)) == 1
            return adjacent if clue["neg"] else not adjacent
        return False
    if ctype == "cond":
        return _atom_state(clue["if"], assign) is True and _atom_state(clue["then"], assign) is False
    raise ValueError(f"未知线索类型: {ctype}")


def _clue_attrs(clue: dict[str, Any]) -> set[str]:
    """线索涉及的属性集合（用于按属性索引，只在相关赋值节点检查）。"""
    ctype = clue["type"]
    if ctype in ("pair_eq", "pair_ne"):
        return {clue["attr_a"], clue["attr_b"]}
    if ctype == "before":
        return {"星期"}
    if ctype == "adjacent":
        return {"会议室"}
    if ctype == "cond":
        return {clue["if"]["attr"], clue["then"]["attr"]}
    return {clue["attr"]}


def solve_logic_grid(
    people: list[str],
    domains: dict[str, list[str]],
    clues: list[dict[str, Any]],
    limit: int = 2,
) -> list[dict[str, dict[str, str]]]:
    """独立求解器：按属性分层回溯枚举（每层内全排列剪枝），返回至多 limit 组完整解。

    attr_eq/attr_ne 线索静态吸收进 forced/banned；其余线索按涉及属性索引，
    每个赋值节点只检查与该属性相关的线索。
    """
    forced: dict[tuple[str, str], str] = {}
    banned: dict[str, dict[str, set[str]]] = {p: {a: set() for a in domains} for p in people}
    by_attr: dict[str, list[dict[str, Any]]] = {a: [] for a in domains}
    for clue in clues:
        if clue["type"] == "attr_eq":
            forced[(clue["person"], clue["attr"])] = clue["value"]
        elif clue["type"] == "attr_ne":
            banned[clue["person"]][clue["attr"]].add(clue["value"])
        else:
            for attr in _clue_attrs(clue):
                by_attr[attr].append(clue)

    attr_order = ["星期", "会议室", "主题"]
    solutions: list[dict[str, dict[str, str]]] = []
    assign: dict[str, dict[str, str]] = {p: {} for p in people}
    used: dict[str, set[str]] = {a: set() for a in domains}

    def backtrack(attr_index: int, person_index: int) -> None:
        if len(solutions) >= limit:
            return
        if attr_index == len(attr_order):
            solutions.append({p: dict(assign[p]) for p in people})
            return
        attr = attr_order[attr_index]
        person = people[person_index]
        active = by_attr[attr]
        pin = forced.get((person, attr))
        for value in domains[attr]:
            if value in used[attr] or value in banned[person][attr]:
                continue
            if pin is not None and value != pin:
                continue
            assign[person][attr] = value
            used[attr].add(value)
            if not any(_clue_violated(c, assign) for c in active):
                if person_index + 1 == len(people):
                    backtrack(attr_index + 1, 0)
                else:
                    backtrack(attr_index, person_index + 1)
            used[attr].discard(value)
            del assign[person][attr]
            if len(solutions) >= limit:
                return

    backtrack(0, 0)
    return solutions


def _eq_phrase(person: str, attr: str, value: str) -> str:
    if attr == "星期":
        return f"{person}安排在{value}"
    if attr == "会议室":
        return f"{person}使用{value}号会议室"
    return f"{person}负责「{value}」主题"


def _ne_phrase(person: str, attr: str, value: str) -> str:
    if attr == "星期":
        return f"{person}不安排在{value}"
    if attr == "会议室":
        return f"{person}不使用{value}号会议室"
    return f"{person}不负责「{value}」主题"


def _holder_phrase(attr: str, value: str) -> str:
    if attr == "星期":
        return f"安排在{value}的人"
    if attr == "会议室":
        return f"使用{value}号会议室的人"
    return f"负责「{value}」主题的人"


def _pred_phrase(attr: str, value: str, neg: bool = False) -> str:
    if attr == "星期":
        return f"{'不' if neg else ''}安排在{value}"
    if attr == "会议室":
        return f"{'不' if neg else ''}使用{value}号会议室"
    return f"{'不' if neg else ''}负责「{value}」主题"


def _atom_text(atom: dict[str, Any]) -> str:
    if atom["type"] == "attr_eq":
        return _eq_phrase(atom["person"], atom["attr"], atom["value"])
    return _ne_phrase(atom["person"], atom["attr"], atom["value"])


def clue_text(clue: dict[str, Any]) -> str:
    ctype = clue["type"]
    if ctype == "attr_eq":
        return _eq_phrase(clue["person"], clue["attr"], clue["value"]) + "。"
    if ctype == "attr_ne":
        return _ne_phrase(clue["person"], clue["attr"], clue["value"]) + "。"
    if ctype == "pair_eq":
        return _holder_phrase(clue["attr_a"], clue["value_a"]) + _pred_phrase(clue["attr_b"], clue["value_b"]) + "。"
    if ctype == "pair_ne":
        return _holder_phrase(clue["attr_a"], clue["value_a"]) + _pred_phrase(clue["attr_b"], clue["value_b"], neg=True) + "。"
    if ctype == "before":
        return f"{clue['a']}的汇报安排早于{clue['b']}。"
    if ctype == "adjacent":
        return f"{clue['a']}与{clue['b']}的会议室编号{'不' if clue['neg'] else ''}相邻。"
    if ctype == "cond":
        return f"若{_atom_text(clue['if'])}，则{_atom_text(clue['then'])}。"
    raise ValueError(f"未知线索类型: {ctype}")


def _clue_key(clue: dict[str, Any]) -> str:
    if clue["type"] == "cond":
        return f"cond|{_clue_key(clue['if'])}|{_clue_key(clue['then'])}"
    return "|".join(f"{k}={clue[k]}" for k in sorted(clue) if k != "text")


def _build_clues(
    rng: random.Random,
    people: list[str],
    solution: dict[str, dict[str, str]],
    weekdays: list[str],
    rooms: list[str],
    topics: list[str],
) -> list[dict[str, Any]] | None:
    """构造式生成：主干线索结构性保证唯一解，再用经求解器验证的替换注入条件式/否定式。

    主干（3(n-1) 条）：n-1 条 before 构成星期全序链；n-1 条 pair_eq（星期→会议室）钉住
    n-1 间会议室；n-1 条 pair_eq（星期/会议室→主题）钉住 n-1 个主题；每个属性的最后一个
    值由互异性自动推出。随后把部分主干线索逐条换成对真解成立的条件式/否定式线索，
    每次替换都用求解器复核唯一性。
    """
    n = len(people)
    domains = {"星期": weekdays, "会议室": rooms, "主题": topics}

    def unique(clues: list[dict[str, Any]]) -> bool:
        return len(solve_logic_grid(people, domains, clues, limit=2)) == 1

    # ---- 主干：星期全序链 ----
    by_weekday = sorted(people, key=lambda p: WEEK_ORDER[solution[p]["星期"]])
    backbone: list[dict[str, Any]] = [
        {"type": "before", "a": by_weekday[i], "b": by_weekday[i + 1]} for i in range(n - 1)
    ]
    # ---- 主干：星期→会议室（n-1 个不同星期值钉住 n-1 间会议室，第 n 间由互异性推出）----
    room_anchor = rng.sample(by_weekday, n - 1)
    backbone += [
        {"type": "pair_eq", "attr_a": "星期", "value_a": solution[p]["星期"],
         "attr_b": "会议室", "value_b": solution[p]["会议室"]}
        for p in room_anchor
    ]
    # ---- 主干：星期或会议室→主题 ----
    topic_anchor_attr = rng.choice(["星期", "会议室"])
    topic_anchor = rng.sample(by_weekday, n - 1)
    backbone += [
        {"type": "pair_eq", "attr_a": topic_anchor_attr, "value_a": solution[p][topic_anchor_attr],
         "attr_b": "主题", "value_b": solution[p]["主题"]}
        for p in topic_anchor
    ]
    if not unique(backbone):
        return None

    # ---- 配额替换（靶向）：把主干 pair_eq 换成等价的条件式 / 排除式否定线索 ----
    # 条件式：若<持有者><attr_a>=value_a，则<持有者><attr_b>=value_b —— 前件被星期链锁死，
    # 在剩余解空间中恒真，故与原 pair_eq 约束等价；否定式：pair_ne 排除一个错误值，
    # 当上下文只剩两个自由值时排除其一即锁定。每次替换都用求解器复核唯一性。
    chosen = list(backbone)
    pin_slots = [i for i, c in enumerate(chosen) if c["type"] == "pair_eq"]
    rng.shuffle(pin_slots)

    def holder_of(attr: str, value: str) -> str:
        return next(p for p in people if solution[p][attr] == value)

    def try_swap(slot: int, candidate: dict[str, Any]) -> bool:
        trial = chosen[:slot] + [candidate] + chosen[slot + 1:]
        if unique(trial):
            chosen[slot] = candidate
            return True
        return False

    cond_have = 0
    for slot in pin_slots:
        if cond_have >= 4:
            break
        pin = chosen[slot]
        person = holder_of(pin["attr_a"], pin["value_a"])
        candidate = {
            "type": "cond",
            "if": {"type": "attr_eq", "person": person, "attr": pin["attr_a"], "value": pin["value_a"]},
            "then": {"type": "attr_eq", "person": person, "attr": pin["attr_b"], "value": pin["value_b"]},
        }
        if try_swap(slot, candidate):
            cond_have += 1

    neg_have = 0
    for slot in pin_slots:
        if neg_have >= 2:
            break
        pin = chosen[slot]
        if pin["type"] != "pair_eq":
            continue
        person = holder_of(pin["attr_a"], pin["value_a"])
        wrongs = [v for v in domains[pin["attr_b"]] if v != pin["value_b"]]
        rng.shuffle(wrongs)
        swapped = False
        for wrong in wrongs[:4]:
            if rng.random() < 0.5:
                candidate = {
                    "type": "pair_ne", "attr_a": pin["attr_a"], "value_a": pin["value_a"],
                    "attr_b": pin["attr_b"], "value_b": wrong,
                }
            else:
                candidate = {"type": "attr_ne", "person": person, "attr": pin["attr_b"], "value": wrong}
            if try_swap(slot, candidate):
                swapped = True
                break
        if swapped:
            neg_have += 1

    cond_count = sum(1 for c in chosen if c["type"] == "cond")
    neg_count = sum(1 for c in chosen if c["type"] in ("attr_ne", "pair_ne"))
    if len(chosen) != 3 * (n - 1) or cond_count < 4 or neg_count < 2 or not unique(chosen):
        return None
    rng.shuffle(chosen)
    return chosen


def _gen_logic_grid(seed: int) -> dict[str, Any]:
    rng = random.Random(f"logic_grid:{seed}")
    for _ in range(300):
        n = rng.randint(6, 7)
        people = rng.sample(NAME_POOL, n)
        topics = rng.sample(TOPIC_POOL, n)
        # 属性域与人数等势：before 全序链 + 互异性才能锁死绝对位置
        weekdays = WEEKDAYS[:n]
        rooms = ROOMS[:n]
        domains = {"星期": weekdays, "会议室": rooms, "主题": topics}
        weekday_assign = rng.sample(weekdays, n)
        room_assign = rng.sample(rooms, n)
        topic_assign = rng.sample(topics, n)
        solution = {
            person: {"星期": weekday_assign[i], "会议室": room_assign[i], "主题": topic_assign[i]}
            for i, person in enumerate(people)
        }
        clues = _build_clues(rng, people, solution, weekdays, rooms, topics)
        if clues is None:
            continue
        check = solve_logic_grid(people, domains, clues, limit=2)
        if len(check) != 1 or check[0] != solution:
            continue

        clue_lines = "\n".join(f"{i}. {clue_text(c)}" for i, c in enumerate(clues, 1))
        topic_list = "、".join(f"「{t}」" for t in topics)
        problem = (
            f"{n} 位同事 {('、'.join(people))} 要在{weekdays[0]}至{weekdays[-1]}各安排一次专题汇报，每人一天、互不重复；"
            f"每人各使用一间会议室（1 至 {n} 号，互不重复），并各负责一个主题"
            f"（{topic_list}，互不重复）。\n"
            "已知以下线索：\n"
            f"{clue_lines}\n\n"
            "请推理出每个人的汇报星期、会议室编号与负责主题。注意线索中包含否定式与条件式，"
            "条件式（若……则……）只在前件成立时约束后件。\n\n"
            + _fmt_instruction(
                '{"最终答案": {"张三": {"星期": "周二", "会议室": "3", "主题": "主题示例"}, '
                '"李四": {"星期": "...", "会议室": "...", "主题": "..."}}}'
            )
        )
        reference = "；".join(
            f"{p}：{solution[p]['星期']}，{solution[p]['会议室']}号会议室，负责「{solution[p]['主题']}」"
            for p in people
        )
        return {
            "problem": problem,
            "reference_answer": reference + "。",
            "answer_spec": {"kind": "mapping", "mapping": solution},
            "meta": {
                "people_count": n,
                "clue_count": len(clues),
                "instance": {"people": people, "domains": domains, "clues": clues},
            },
        }
    raise RuntimeError(f"logic_grid seed={seed}: 300 次尝试内未能生成唯一解实例")


# ===========================================================================
# 3. inventory_sim：库存长链模拟
# ===========================================================================

def simulate_inventory(instance: dict[str, Any]) -> dict[str, int]:
    """独立求解器：按题目规则逐条结算操作并触发补货，返回各 SKU 最终库存。"""
    params = instance["params"]
    stock = {sku: p["init"] for sku, p in params.items()}
    for op in instance["ops"]:
        kind = op["op"]
        affected: list[str] = []
        if kind == "sale":
            stock[op["sku"]] -= op["qty"]
            affected = [op["sku"]]
        elif kind == "return":
            stock[op["sku"]] += op["qty"]
            affected = [op["sku"]]
        elif kind == "scrap":
            stock[op["sku"]] -= op["qty"]
            affected = [op["sku"]]
        elif kind == "promo":
            sku, qty = op["sku"], op["qty"]
            out = min(2 * qty, PROMO_CAP) if stock[sku] >= 2 * qty else qty
            stock[sku] -= out
            affected = [sku]
        elif kind == "group":
            sku = op["sku"]
            if stock[sku] >= params[sku]["safety"]:
                stock[sku] -= op["qty"]
            affected = [sku]
        elif kind == "transfer":
            src, dst, qty = op["src"], op["dst"], op["qty"]
            if stock[src] - qty >= params[src]["safety"]:
                stock[src] -= qty
                stock[dst] += qty
                affected = [src, dst]
            # 调拨保护：执行后调出方会跌破安全线 ⇒ 整笔取消，双方均不变、不检查补货
        elif kind == "audit":
            stock[op["sku"]] = op["qty"]
            affected = [op["sku"]]
        else:
            raise ValueError(f"未知操作类型: {kind}")
        for sku in affected:
            p = params[sku]
            if stock[sku] < p["safety"]:
                stock[sku] += int(p["base"] * LEVEL_COEF[p["level"]])
    return stock


def _op_text(index: int, op: dict[str, Any]) -> str:
    kind = op["op"]
    if kind == "sale":
        body = f"销售出库：{op['sku']} 出库 {op['qty']} 件"
    elif kind == "return":
        body = f"客户退货入库：{op['sku']} 入库 {op['qty']} 件"
    elif kind == "scrap":
        body = f"报废出库：{op['sku']} 出库 {op['qty']} 件"
    elif kind == "promo":
        body = (
            f"促销出库：{op['sku']} 若出库前库存不低于 {2 * op['qty']} 件，"
            f"则出库 {2 * op['qty']} 件，否则出库 {op['qty']} 件"
        )
    elif kind == "group":
        body = (
            f"团购订单：{op['sku']} 若出库前库存低于其安全线，则取消本次出库；"
            f"否则出库 {op['qty']} 件"
        )
    elif kind == "transfer":
        body = f"库存调拨：从 {op['src']} 调拨 {op['qty']} 件到 {op['dst']}（{op['src']} 出库、{op['dst']} 入库）"
    else:
        body = f"盘点校正：{op['sku']} 库存直接校正为 {op['qty']} 件"
    return f"{index}. {body}。"


def _gen_inventory_sim(seed: int) -> dict[str, Any]:
    rng = random.Random(f"inventory_sim:{seed}")
    count = rng.randint(6, 8)
    codes = rng.sample(range(100, 999), count)
    skus = [f"SKU-{chr(ord('A') + i)}{codes[i]}" for i in range(count)]

    params: dict[str, dict[str, Any]] = {}
    for sku in skus:
        init = rng.randint(60, 180)
        params[sku] = {
            "init": init,
            "safety": rng.randint(15, 45),
            "base": rng.choice([20, 24, 28, 32, 36, 40]),
            "level": rng.choice(list(LEVEL_COEF)),
        }

    op_count = rng.randint(50, 60)

    def rand_sku(exclude: str = "") -> str:
        choices = [s for s in skus if s != exclude]
        return rng.choice(choices)

    ops: list[dict[str, Any]] = []
    for _ in range(6):
        ops.append({"op": "promo", "sku": rand_sku(), "qty": rng.randint(8, 20)})
    for _ in range(4):
        ops.append({"op": "group", "sku": rand_sku(), "qty": rng.randint(10, 25)})
    for _ in range(3):
        src = rand_sku()
        ops.append({"op": "transfer", "src": src, "dst": rand_sku(src), "qty": rng.randint(5, 25)})
    for _ in range(2):
        ops.append({"op": "audit", "sku": rand_sku(), "qty": rng.randint(20, 150)})
    while len(ops) < op_count:
        kind = rng.choice(["sale", "sale", "sale", "return", "return", "scrap"])
        if kind == "sale":
            ops.append({"op": "sale", "sku": rand_sku(), "qty": rng.randint(5, 30)})
        elif kind == "return":
            ops.append({"op": "return", "sku": rand_sku(), "qty": rng.randint(3, 15)})
        else:
            ops.append({"op": "scrap", "sku": rand_sku(), "qty": rng.randint(2, 10)})
    rng.shuffle(ops)

    instance = {"params": params, "ops": ops}
    final = simulate_inventory(instance)

    param_lines = "\n".join(
        f"· {sku}：初始库存 {p['init']} 件，安全线 {p['safety']} 件，"
        f"基准补货量 {p['base']} 件，供应商等级 {p['level']}（系数 {LEVEL_COEF[p['level']]}）"
        for sku, p in params.items()
    )
    op_lines = "\n".join(_op_text(i, op) for i, op in enumerate(ops, 1))

    problem = (
        "某仓库的库存结算规则如下，请严格按规则逐步模拟：\n"
        "1. 操作按编号顺序逐条执行；出库扣减库存，入库增加库存。\n"
        "2. 每条操作结算完成后，立即检查该操作影响的 SKU：若其库存 < 安全线，"
        "立即触发一次补货，补货量 = 基准补货量 × 供应商等级系数（甲=2，乙=1.5，丙=1），"
        "补货入库后即使仍低于安全线，本条操作也不再重复补货。\n"
        "3. 调拨视为一条操作：先结算调出方出库与调入方入库，再依次检查调出方、调入方是否补货。\n"
        "4. 库存允许为负（记为欠货）；除明确写有取消条件的操作外，出库不因库存不足而取消。\n"
        "5. 促销、团购等带条件的操作，条件一律以该条操作执行前的库存为准。\n"
        f"6. 促销出库的实际出库量以 {PROMO_CAP} 件封顶：操作描述中加倍后的数量超过 "
        f"{PROMO_CAP} 件的，按 {PROMO_CAP} 件出库。\n"
        "7. 调拨保护：若按描述数量调拨后调出方库存会低于其安全线，则整笔调拨取消"
        "（双方库存均不变，也不再检查补货）。\n\n"
        f"各 SKU 参数：\n{param_lines}\n\n"
        f"操作序列（共 {len(ops)} 步）：\n{op_lines}\n\n"
        "请给出全部操作执行完毕后每个 SKU 的最终库存（整数，单位件）。\n\n"
        + _fmt_instruction('{"最终答案": {"SKU-XXXX": 12, "SKU-YYYY": 34}}')
    )
    reference = "；".join(f"{sku} 最终库存 {final[sku]} 件" for sku in skus) + "。"
    return {
        "problem": problem,
        "reference_answer": reference,
        "answer_spec": {"kind": "mapping", "mapping": final},
        "meta": {"op_count": len(ops), "sku_count": count, "instance": instance},
    }


# ===========================================================================
# 4. dag_schedule：依赖图排期（含资源冲突陷阱）
# ===========================================================================

def _dag_longest(preds: dict[str, set[str]], dur: dict[str, int]) -> dict[str, int] | None:
    """各节点最早完工时间（最长路径）；有环返回 None。"""
    color = {t: 0 for t in preds}
    dist: dict[str, int] = {}

    def dfs(task: str) -> int | None:
        if color[task] == 1:
            return None
        if color[task] == 2:
            return dist[task]
        color[task] = 1
        best = 0
        for pre in sorted(preds[task]):
            d = dfs(pre)
            if d is None:
                return None
            best = max(best, d)
        color[task] = 2
        dist[task] = best + dur[task]
        return dist[task]

    for task in sorted(preds):
        if dfs(task) is None:
            return None
    return dist


def _enum_critical_paths(
    preds: dict[str, set[str]], dur: dict[str, int], dist: dict[str, int], cap: int = 500
) -> list[tuple[str, ...]]:
    makespan = max(dist.values())
    paths: list[tuple[str, ...]] = []

    def back(task: str, suffix: tuple[str, ...]) -> None:
        if len(paths) >= cap:
            return
        path = (task,) + suffix
        if not preds[task]:
            paths.append(path)  # 到达源节点：完整的源→汇最长链
            return
        for pre in sorted(preds[task]):
            if dist[pre] + dur[task] == dist[task]:
                back(pre, path)

    for task in sorted(dist):
        if dist[task] == makespan:
            back(task, ())
    return paths


def solve_dag(tasks: dict[str, dict[str, Any]], conflicts: list[tuple[str, str]]) -> dict[str, Any]:
    """独立求解器：枚举资源冲突对的所有先后取向，取增广 DAG 最长路径的最小值。

    除冲突对外资源无限，因此给定取向后最早开始排期即最优；最优 makespan
    等于所有可行取向中最小的最长路径。返回 makespan 与全部等价关键路径。
    """
    dur = {t: tasks[t]["duration"] for t in tasks}
    base_preds = {t: set(tasks[t]["preds"]) for t in tasks}
    best: int | None = None
    best_paths: set[tuple[str, ...]] = set()
    for mask in range(1 << len(conflicts)):
        preds = {t: set(p) for t, p in base_preds.items()}
        for i, (a, b) in enumerate(conflicts):
            first, second = (a, b) if (mask >> i) & 1 == 0 else (b, a)
            preds[second].add(first)
        dist = _dag_longest(preds, dur)
        if dist is None:
            continue  # 该取向与已有依赖构成环，不可行
        makespan = max(dist.values())
        if best is None or makespan < best:
            best = makespan
            best_paths = set(_enum_critical_paths(preds, dur, dist))
        elif makespan == best:
            best_paths.update(_enum_critical_paths(preds, dur, dist))
    if best is None:
        raise RuntimeError("所有资源冲突取向均不可行")
    return {"makespan": best, "paths": sorted(best_paths)}


def _reachable(preds: dict[str, set[str]]) -> dict[str, set[str]]:
    reach: dict[str, set[str]] = {t: set() for t in preds}

    def ancestors(task: str) -> set[str]:
        if not reach[task]:
            for pre in preds[task]:
                reach[task].add(pre)
                reach[task] |= ancestors(pre)
        return reach[task]

    for task in preds:
        ancestors(task)
    return reach


def _conflict_combos(
    candidates: list[tuple[str, str]], rng: random.Random, max_combos: int = 30
) -> list[tuple[tuple[str, str], ...]]:
    combos = list(itertools.combinations(candidates, 3)) + list(itertools.combinations(candidates, 4))
    rng.shuffle(combos)
    return combos[:max_combos]


def _gen_dag_schedule(seed: int) -> dict[str, Any]:
    rng = random.Random(f"dag_schedule:{seed}")
    for _ in range(300):
        count = rng.randint(18, 22)
        ids = [f"T{i + 1}" for i in range(count)]
        tasks: dict[str, dict[str, Any]] = {}
        for i, task in enumerate(ids):
            preds = [ids[j] for j in range(i) if rng.random() < 0.22]
            if i > 0 and not preds and rng.random() < 0.65:
                preds = [ids[i - 1]]
            tasks[task] = {"duration": rng.randint(2, 9), "preds": preds}

        preds_map = {t: set(tasks[t]["preds"]) for t in tasks}
        reach = _reachable(preds_map)
        incomparable = [
            (ids[i], ids[j])
            for i in range(count) for j in range(i + 1, count)
            if ids[j] not in reach[ids[i]] and ids[i] not in reach[ids[j]]
        ]
        if len(incomparable) < 4:
            continue
        relaxed = solve_dag(tasks, [])
        # 只保留“单独加入就会推高工期”的冲突对：依赖上不可比（看似可并行）且真实绑定
        binding = [
            pair for pair in incomparable
            if solve_dag(tasks, [pair])["makespan"] > relaxed["makespan"]
        ]
        if len(binding) < 3:
            continue
        rng.shuffle(binding)
        # 从绑定对中组合 3–4 条冲突约束，优选等价关键路径数最少的组合
        solved = None
        conflicts: list[tuple[str, str]] = []
        for combo in _conflict_combos(binding[:8], rng):
            candidate = solve_dag(tasks, list(combo))
            if 1 <= len(candidate["paths"]) <= 20 and (
                solved is None or len(candidate["paths"]) < len(solved["paths"])
            ):
                solved, conflicts = candidate, list(combo)
        if solved is None:
            continue
        # 组合约束只增不减，且每对单独绑定 ⇒ 组合 makespan 必大于松弛解（陷阱成立）

        critical = list(solved["paths"][0])
        all_paths = [list(p) for p in solved["paths"]]

        task_lines = "\n".join(
            f"· {t}：工期 {tasks[t]['duration']} 天，前置任务：{('、'.join(tasks[t]['preds']) if tasks[t]['preds'] else '无')}"
            for t in ids
        )
        conflict_lines = "\n".join(
            f"· {a} 与 {b} 共用同一台专用设备，任何时刻不得同时进行（必须一前一后完成）。"
            for a, b in conflicts
        )
        multi_note = "；若存在多条等长关键路径，输出其中任意一条即可"
        problem = (
            "某项目由下列任务组成。工期从第 0 天起算；任务一旦开始不可中断；"
            "前置任务全部完成后任务才能开始；除下列资源冲突外人力充足，无依赖关系的任务可任意并行。\n\n"
            f"任务清单：\n{task_lines}\n\n"
            f"资源冲突约束：\n{conflict_lines}\n\n"
            "注意：有些任务从依赖关系上看似乎可以并行，但资源冲突可能迫使它们串行，"
            "从而推高总工期。\n\n"
            f"请计算最短工期（总天数，整数）以及一条关键路径（按执行先后给出任务编号序列{multi_note}）。\n\n"
            + _fmt_instruction('{"最终答案": {"最短工期": 18, "关键路径": ["T1", "T3", "T5"]}}')
        )
        reference = (
            f"最短工期 {solved['makespan']} 天；关键路径：{' → '.join(critical)}"
            + (f"（共 {len(all_paths)} 条等价关键路径）" if len(all_paths) > 1 else "")
            + "。"
        )
        return {
            "problem": problem,
            "reference_answer": reference,
            "answer_spec": {
                "kind": "schedule",
                "makespan": solved["makespan"],
                "critical_path": critical,
                "all_paths": all_paths,
            },
            "meta": {
                "task_count": count,
                "relaxed_makespan": relaxed["makespan"],
                "instance": {"tasks": tasks, "conflicts": [list(c) for c in conflicts]},
            },
        }
    raise RuntimeError(f"dag_schedule seed={seed}: 300 次尝试内未能生成含陷阱的实例")


# ===========================================================================
# 调度入口与自验
# ===========================================================================

_GENERATORS = {
    "multi_hop": _gen_multi_hop,
    "logic_grid": _gen_logic_grid,
    "inventory_sim": _gen_inventory_sim,
    "dag_schedule": _gen_dag_schedule,
}


def generate(kind: str, seed: int) -> dict[str, Any]:
    """生成一个谜题实例。kind ∈ {"multi_hop", "logic_grid", "inventory_sim", "dag_schedule"}。"""
    if kind not in _GENERATORS:
        raise ValueError(f"未知题型: {kind}（可选：{', '.join(KINDS)}）")
    return _GENERATORS[kind](seed)


def _verify(kind: str, result: dict[str, Any]) -> None:
    """用独立求解器从实例数据重算答案，与 answer_spec 精确比对。"""
    spec = result["answer_spec"]
    instance = result["meta"]["instance"]
    if kind == "multi_hop":
        got = solve_multi_hop(instance["facts"], instance["question"])
        assert spec["kind"] == "value" and spec["value"] == got, (
            f"answer_spec={spec['value']} 求解={got}"
        )
        assert result["meta"]["hops"] in (4, 5), f"跳数 {result['meta']['hops']} 不在 4–5"
        assert len(result["problem"]) <= 6000, f"problem 长度 {len(result['problem'])} 超过 6000 字"
    elif kind == "logic_grid":
        sols = solve_logic_grid(instance["people"], instance["domains"], instance["clues"], limit=3)
        assert len(sols) == 1, f"解不唯一或无解：共 {len(sols)} 组解"
        assert spec["kind"] == "mapping" and spec["mapping"] == sols[0], (
            f"answer_spec 与求解结果不一致：{spec['mapping']} != {sols[0]}"
        )
        clue_count = len(instance["clues"])
        people_count = len(instance["people"])
        assert 6 <= people_count <= 7, f"人数 {people_count} 不在 6–7"
        assert 15 <= clue_count <= 18, f"线索数 {clue_count} 不在 15–18"
        cond = sum(1 for c in instance["clues"] if c["type"] == "cond")
        neg = sum(1 for c in instance["clues"] if c["type"] in ("attr_ne", "pair_ne") or (c["type"] == "adjacent" and c["neg"]))
        assert cond >= 4 and neg >= 2, f"条件式 {cond} 条 / 否定式 {neg} 条，未达到配额"
    elif kind == "inventory_sim":
        got = simulate_inventory(instance)
        assert spec["kind"] == "mapping" and spec["mapping"] == got, (
            f"answer_spec={spec['mapping']} 模拟={got}"
        )
        assert 50 <= len(instance["ops"]) <= 60, f"操作数 {len(instance['ops'])} 不在 50–60"
        assert 6 <= len(instance["params"]) <= 8, f"SKU 数 {len(instance['params'])} 不在 6–8"
    elif kind == "dag_schedule":
        conflicts = [tuple(c) for c in instance["conflicts"]]
        solved = solve_dag(instance["tasks"], conflicts)
        assert spec["kind"] == "schedule" and spec["makespan"] == solved["makespan"], (
            f"makespan answer_spec={spec['makespan']} 求解={solved['makespan']}"
        )
        paths = [list(p) for p in solved["paths"]]
        assert spec["critical_path"] in paths, "critical_path 不在合法关键路径集合中"
        assert spec["all_paths"] == paths, "all_paths 与求解器枚举结果不一致"
        dur = {t: instance["tasks"][t]["duration"] for t in instance["tasks"]}
        for path in paths:
            assert sum(dur[t] for t in path) == solved["makespan"], f"关键路径 {path} 长度不等于 makespan"
        relaxed = solve_dag(instance["tasks"], [])
        assert solved["makespan"] > relaxed["makespan"], "资源冲突未构成真实陷阱"
        assert 3 <= len(conflicts) <= 4, f"冲突约束数 {len(conflicts)} 不在 3–4"
        assert 18 <= len(instance["tasks"]) <= 22, f"任务数 {len(instance['tasks'])} 不在 18–22"
    # 公共形状检查
    assert result["problem"].strip().endswith("```"), "problem 未以输出格式要求结尾"
    assert '{"最终答案"' in result["problem"], "problem 缺少最终答案输出指令"


def self_check(seeds: range = range(50)) -> None:
    for seed in seeds:
        for kind in KINDS:
            try:
                result = generate(kind, seed)
                _verify(kind, result)
            except (AssertionError, RuntimeError, ValueError, KeyError) as exc:
                print(f"[FAIL] kind={kind} seed={seed}: {exc}")
                raise SystemExit(1)
    print("ALL GENERATORS OK")


if __name__ == "__main__":
    self_check()
