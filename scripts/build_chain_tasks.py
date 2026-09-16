"""构建"依赖链代码修复"题目（CHAIN-01/02/03）并做正确性自验。

用法（cwd 必须为 emergence-lab 根目录）：
    python3.13 scripts/build_chain_tasks.py

自验内容：
  1. reference 修复版跑 server.run_code_tests，passed == total；
  2. buggy 版 fixed_depth == 0（test_stage_1 失败）；
  3. 链结构验证：生成"只修前 k 个阶段"的中间版本（k=1..N-1），断言 fixed_depth
     序列与每题依赖图定义的预期一致（CHAIN-02 含负反馈陷阱验证）；
  4. 全部通过后写入 reports/chain_tasks.json。
"""

import json
import re
import sys
from pathlib import Path

LAB_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(LAB_ROOT))

import server  # noqa: E402  （import 无副作用）

# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------


def run_tests(source, test_code):
    """用评分执行器跑测试；final_answer 包装为 python 代码块。"""
    return server.run_code_tests("```python\n" + source + "\n```", test_code)


def fixed_depth(result):
    """从 stage 1 起连续通过的最长前缀。

    注意：runner 按名字字典序排序，test_stage_10/11/12 会排在 test_stage_2 之前，
    因此这里必须按数字后缀排序。
    """
    def stage_no(t):
        m = re.search(r"(\d+)$", t["name"])
        return int(m.group(1)) if m else 0
    depth = 0
    for t in sorted(result["tests"], key=stage_no):
        if t["ok"]:
            depth += 1
        else:
            break
    return depth


def make_variant(reference, bugs, fixed):
    """由 reference 生成中间版本：fixed 集合内的阶段保持修复，其余回退为 buggy。

    bugs: [(fixed_anchor, buggy_anchor), ...]，按阶段 1..N 排列。
    """
    src = reference
    for i, (good, bad) in enumerate(bugs, start=1):
        assert src.count(good) == 1, "锚点不唯一: %r" % good
        if i not in fixed:
            src = src.replace(good, bad)
    return src


def exec_pipeline(source, *args):
    """直接在本进程执行管道源码并调用 run_pipeline（仅用于生成示例输出/陷阱验证）。"""
    ns = {}
    exec(compile(source, "<variant>", "exec"), ns)
    return ns["run_pipeline"](*args)


# ---------------------------------------------------------------------------
# CHAIN-01 订单记录处理管道（线性链，6 阶段，入门校准题）
# 依赖图：1 -> 2 -> 3 -> 4 -> 5 -> 6（纯线性）
# ---------------------------------------------------------------------------

CHAIN01_REFERENCE = r'''# 订单记录处理管道
# 流程: 解析 CSV -> 校验 -> 单位换算 -> 过滤 -> 聚合 -> 格式化输出

MIN_TOTAL_CENTS = 100  # 最低订单单价（分），低于该单价的订单不计入统计


def money(cents):
    """把以分为单位的整数格式化为 '元.分分' 字符串，如 205 -> '2.05'。"""
    return "{}.{:02d}".format(cents // 100, cents % 100)


def stage1_parse(csv_text):
    """解析 CSV 文本（首行为表头，列为 订单号,单价(元),数量），返回记录列表。"""
    lines = [ln for ln in csv_text.strip().splitlines() if ln.strip()]
    rows = lines[1:]
    records = []
    for ln in rows:
        oid, price, qty = ln.split(",")
        records.append({"id": oid.strip(), "price": price.strip(), "qty": int(qty)})
    return records


def stage2_validate(records):
    """校验：数量必须为正整数，数量为 0 的记录作废。"""
    return [r for r in records if r["qty"] > 0]


def stage3_convert(records):
    """单位换算：单价由元换算为分，并计算每单总额（分）。"""
    for r in records:
        r["price_cents"] = int(round(float(r["price"]) * 100))
        r["total_cents"] = r["price_cents"] * r["qty"]
    return records


def stage4_filter(records):
    """过滤：只保留单价不低于 MIN_TOTAL_CENTS 的订单（含边界）。"""
    return [r for r in records if r["price_cents"] >= MIN_TOTAL_CENTS]


def stage5_aggregate(records):
    """聚合：统计订单数与订单总金额（分）。"""
    total = sum(r["total_cents"] for r in records)
    return {"count": len(records), "total_cents": total}


def stage6_format(records, summary):
    """格式化：订单行按总额从高到低排列，末尾追加汇总行。"""
    ordered = sorted(records, key=lambda r: r["total_cents"], reverse=True)
    lines = ["{}:{}".format(r["id"], money(r["total_cents"])) for r in ordered]
    lines.append("count={} total={}".format(summary["count"], money(summary["total_cents"])))
    return "\n".join(lines)


def run_pipeline(csv_text):
    records = stage1_parse(csv_text)
    records = stage2_validate(records)
    records = stage3_convert(records)
    records = stage4_filter(records)
    summary = stage5_aggregate(records)
    return stage6_format(records, summary)
'''

# (fixed_anchor, buggy_anchor)，按阶段 1..6
CHAIN01_BUGS = [
    ("rows = lines[1:]", "rows = lines[1:-1]"),                              # S1 off-by-one 丢最后一行
    ("if r[\"qty\"] > 0", "if r[\"qty\"] >= 0"),                              # S2 边界：放行零数量记录
    ("int(round(float(r[\"price\"]) * 100))", "int(float(r[\"price\"]) * 100)"),  # S3 缺 round，2.05 元被算成 204 分
    ("r[\"price_cents\"] >= MIN_TOTAL_CENTS", "r[\"price_cents\"] > MIN_TOTAL_CENTS"),  # S4 边界：漏掉恰为 100 分的订单
    ("sum(r[\"total_cents\"] for r in records)", "sum(r[\"price_cents\"] for r in records)"),  # S5 错按单价求和，忽略数量
    ("key=lambda r: r[\"total_cents\"], reverse=True", "key=lambda r: r[\"total_cents\"]"),  # S6 排序方向反了
]

CHAIN01_TESTS = r'''def _sorted_lines(text):
    return sorted(text.splitlines())


def test_stage_1():
    out = run_pipeline("id,price,qty\nA,2,1")
    assert _sorted_lines(out) == _sorted_lines("A:2.00\ncount=1 total=2.00"), out


def test_stage_2():
    out = run_pipeline("id,price,qty\nA,2,1\nB,3,0\nC,4,1")
    assert _sorted_lines(out) == _sorted_lines("A:2.00\nC:4.00\ncount=2 total=6.00"), out


def test_stage_3():
    out = run_pipeline("id,price,qty\nA,2.05,1\nB,5,0\nC,2.50,1")
    assert _sorted_lines(out) == _sorted_lines("A:2.05\nC:2.50\ncount=2 total=4.55"), out


def test_stage_4():
    out = run_pipeline("id,price,qty\nA,1,1\nB,9,0\nC,2.05,1")
    assert _sorted_lines(out) == _sorted_lines("A:1.00\nC:2.05\ncount=2 total=3.05"), out


def test_stage_5():
    out = run_pipeline("id,price,qty\nA,2,2\nB,9,0\nC,1,1\nD,2.05,1")
    assert _sorted_lines(out) == _sorted_lines("A:4.00\nC:1.00\nD:2.05\ncount=3 total=7.05"), out


def test_stage_6():
    out = run_pipeline("id,price,qty\nP,2,1\nQ,3,0\nR,2.05,1\nS,1,1\nT,2,2")
    assert out == "T:4.00\nR:2.05\nP:2.00\nS:1.00\ncount=4 total=9.05", out
'''

CHAIN01_SAMPLE_INPUT = "id,price,qty\nA1001,2,1\nA1002,3,0\nA1003,2.05,1\nA1004,1,1\nA1005,2,2"

CHAIN01_PROBLEM = """【订单处理管道修复（线性依赖链）】

下面是一个订单记录处理管道：输入 CSV 文本（首行为表头，各列为 订单号,单价(元),数量），数据依次流经 6 个阶段：

  stage1_parse      解析 CSV 为记录列表
  stage2_validate   校验：数量必须为正，数量为 0 的记录作废
  stage3_convert    单位换算：单价由元换算为分，并计算每单总额（分）
  stage4_filter     过滤：只保留单价不低于 1 元（100 分）的订单（含边界）
  stage5_aggregate  聚合：统计订单数与订单总金额
  stage6_format     格式化：订单行按总额从高到低排列，末尾追加汇总行

当前程序的 6 个阶段各被引入了一个 bug，端到端输出不正确。完整源码：

```python
@SOURCE@
```

示例输入：
@SAMPLE_INPUT@

当前（错误的）端到端输出：
@BUGGY_OUTPUT@

期望的正确输出：
@EXPECTED_OUTPUT@

要求：定位并修复全部 6 个阶段的 bug，输出完整的修复后程序（保持各阶段函数接口与 run_pipeline 签名不变）。
"""

# ---------------------------------------------------------------------------
# CHAIN-02 门店销售日报表管道（乱序依赖链，8 阶段，核心题）
# 语义依赖图（不告诉模型）：
#   4 依赖 6：stage4 生成的 label 分隔符必须与 stage6 的拆列约定一致；
#             只把 stage4 按注释改成 '#' 而不改 stage6 的 split('-') 会直接
#             抛 ValueError —— 比全坏时（输出数值错但格式可解析）更糟（负反馈陷阱）。
#   6 依赖 5：stage6 渲染的是 stage5 去重后的记录，去重键（label）约定决定行内容。
#   5 依赖 3：stage5 去重键中的 tier 由 stage3 计算，tier 语义错了去重结果就错。
# 机械前缀序列（修前 k 个阶段的 fixed_depth）：[0,1,2,4,0,0,6,7,8]
#   注意 k=3 时深度为 4（bug4 与 bug6 同为 '-' 相互抵消，端到端不可见），
#   k=4/5 时深度跌到 0（stage4 已修 '#' 而 stage6 仍 split('-')，全线崩溃）。
# ---------------------------------------------------------------------------

CHAIN02_REFERENCE = r'''# 门店销售日报表管道
# 流程: 解析 -> 校验 -> 富化(金额/档位) -> 生成标签 -> 去重 -> 渲染行 -> 分档小计 -> 汇总定稿
# 输出格式：
#   首行 "rows=N"，随后每行 "<sku>|<tier>|<金额>"（tier 为 A/B/C，金额为元，两位小数），
#   末尾按 tier 字母序追加 "subtotal[<tier>]=<小计>" 行。

TIER_A_MIN = 10000  # A 档订单总额下限（分）
TIER_B_MIN = 4000   # B 档订单总额下限（分）


def money(cents):
    """把以分为单位的整数格式化为 '元.分分' 字符串，如 10000 -> '100.00'。"""
    return "{}.{:02d}".format(cents // 100, cents % 100)


def tier_of(amount_cents):
    """按订单总额（分）划分档位：A >= 100 元，B >= 40 元，其余 C。"""
    if amount_cents >= TIER_A_MIN:
        return "A"
    if amount_cents >= TIER_B_MIN:
        return "B"
    return "C"


def stage1_parse(csv_text):
    """解析 CSV 文本（首行为表头，列为 sku,单价(元),数量），返回记录列表。"""
    lines = [ln for ln in csv_text.strip().splitlines() if ln.strip()]
    rows = lines[1:]
    records = []
    for ln in rows:
        sku, price, qty = ln.split(",")
        records.append({"sku": sku.strip(), "price": price.strip(), "qty": qty.strip()})
    return records


def stage2_validate(records):
    """校验：数量必须为正整数，数量不为正的记录作废。"""
    return [r for r in records if int(r["qty"]) > 0]


def stage3_enrich(records):
    """富化：单价换算为分，计算订单总额，并按总额划分档位 tier。"""
    for r in records:
        r["qty"] = int(r["qty"])
        r["price_cents"] = int(round(float(r["price"]) * 100))
        r["amount_cents"] = r["price_cents"] * r["qty"]
        r["tier"] = tier_of(r["amount_cents"])
    return records


def stage4_label(records):
    """生成报表标签：格式为 '<sku>#<tier>'（stage6 按 '#' 拆列渲染）。"""
    for r in records:
        r["label"] = r["sku"] + "#" + r["tier"]
    return records


def stage5_dedupe(records):
    """按 label 去重：同一标签的重复记录只保留第一次出现的那条。"""
    seen = {}
    for r in records:
        if r["label"] not in seen:
            seen[r["label"]] = r
    return list(seen.values())


def stage6_render_rows(records):
    """渲染订单行：把 label 按 '#' 拆回 sku 与 tier，输出 '<sku>|<tier>|<金额>'。"""
    lines = []
    for r in records:
        sku, tier = r["label"].split("#")
        lines.append("{}|{}|{}".format(sku, tier, money(r["amount_cents"])))
    return lines


def stage7_subtotals(records):
    """分档小计：按 tier 累计订单总额（分），返回 {tier: 小计}。"""
    sub = {}
    for r in records:
        t = r["tier"]
        sub[t] = sub.get(t, 0) + r["amount_cents"]
    return sub


def stage8_finalize(row_lines, sub):
    """定稿：首行 rows=N，随后订单行，末尾按 tier 字母序追加小计行。"""
    out = ["rows={}".format(len(row_lines))]
    out.extend(row_lines)
    for t in sorted(sub):
        out.append("subtotal[{}]={}".format(t, money(sub[t])))
    return "\n".join(out)


def run_pipeline(csv_text):
    records = stage1_parse(csv_text)
    records = stage2_validate(records)
    records = stage3_enrich(records)
    records = stage4_label(records)
    records = stage5_dedupe(records)
    row_lines = stage6_render_rows(records)
    sub = stage7_subtotals(records)
    return stage8_finalize(row_lines, sub)
'''

CHAIN02_BUGS = [
    ("rows = lines[1:]", "rows = lines[2:]"),                                # S1 off-by-one 丢第一条数据
    ("int(r[\"qty\"]) > 0", "int(r[\"qty\"]) >= 0"),                          # S2 边界：放行零数量记录
    ("tier_of(r[\"amount_cents\"])", "tier_of(r[\"price_cents\"])"),          # S3 错按单价分档而非总额
    ("r[\"sku\"] + \"#\" + r[\"tier\"]", "r[\"sku\"] + \"-\" + r[\"tier\"]"),  # S4 label 分隔符与注释/下游约定不符
    ("        if r[\"label\"] not in seen:\n            seen[r[\"label\"]] = r",
     "        seen[r[\"label\"]] = r"),                                       # S5 去重错留最后一次出现
    ("r[\"label\"].split(\"#\")", "r[\"label\"].split(\"-\")"),               # S6 拆列分隔符与 S4 注释约定不符
    ("sub[t] = sub.get(t, 0) + r[\"amount_cents\"]", "sub[t] = r[\"amount_cents\"]"),  # S7 覆盖而非累加
    ("for t in sorted(sub):", "for t in sub:"),                              # S8 小计行未按字母序
]

CHAIN02_TESTS = r'''def test_stage_1():
    out = run_pipeline("sku,price,qty\nSKU1,30.00,1")
    assert out == "rows=1\nSKU1|C|30.00\nsubtotal[C]=30.00", out


def test_stage_2():
    out = run_pipeline("sku,price,qty\nSKU1,50.00,1\nSKU2,20.00,0")
    assert out == "rows=1\nSKU1|B|50.00\nsubtotal[B]=50.00", out


def test_stage_3():
    out = run_pipeline("sku,price,qty\nSKU1,50.00,2\nSKU2,10.00,0")
    assert out == "rows=1\nSKU1|A|100.00\nsubtotal[A]=100.00", out


def test_stage_4():
    out = run_pipeline("sku,price,qty\nSKU4,50.00,2\nSKU2,10.00,0")
    assert out == "rows=1\nSKU4|A|100.00\nsubtotal[A]=100.00", out


def test_stage_5():
    out = run_pipeline("sku,price,qty\nSKU5,50.00,2\nSKU5,60.00,2\nSKU3,10.00,0")
    assert out == "rows=1\nSKU5|A|100.00\nsubtotal[A]=100.00", out


def test_stage_6():
    out = run_pipeline("sku,price,qty\nSKU6,50.00,2\nSKU6B,50.00,1\nSKU0,1.00,0")
    assert out == "rows=2\nSKU6|A|100.00\nSKU6B|B|50.00\nsubtotal[A]=100.00\nsubtotal[B]=50.00", out


def test_stage_7():
    out = run_pipeline("sku,price,qty\nSKU7,50.00,2\nSKU8,60.00,2\nSKU0,1.00,0")
    assert out == "rows=2\nSKU7|A|100.00\nSKU8|A|120.00\nsubtotal[A]=220.00", out


def test_stage_8():
    out = run_pipeline("sku,price,qty\nSKU9,10.00,1\nSKU10,50.00,2\nSKU0,1.00,0")
    assert out == "rows=2\nSKU9|C|10.00\nSKU10|A|100.00\nsubtotal[A]=100.00\nsubtotal[C]=10.00", out
'''

CHAIN02_SAMPLE_INPUT = "sku,price,qty\nSKU1001,10.00,1\nSKU1002,50.00,2\nSKU1003,60.00,2\nSKU1004,1.00,0\nSKU1005,50.00,2\nSKU1006,30.00,1"

CHAIN02_PROBLEM = """【门店销售日报表管道修复（高耦合依赖链）】

下面是一个门店销售日报表管道：输入 CSV 文本（首行为表头，各列为 sku,单价(元),数量），数据依次流经 8 个阶段（解析 -> 校验 -> 富化 -> 生成标签 -> 去重 -> 渲染行 -> 分档小计 -> 汇总定稿），最终输出日报表文本。

当前程序的 8 个阶段各被引入了一个 bug，端到端输出不正确。各阶段的设计意图见源码中的 docstring 与文件头部注释。完整源码：

```python
@SOURCE@
```

示例输入：
@SAMPLE_INPUT@

当前（错误的）端到端输出：
@BUGGY_OUTPUT@

注意：某些阶段的 bug 相互纠缠，孤立地"按注释修复"单个阶段可能让端到端输出变得更糟；修复时需要通盘考虑上下游阶段之间的数据约定。

要求：定位并修复全部 8 个阶段的 bug，输出完整的修复后程序（保持各阶段函数接口与 run_pipeline 签名不变）。
"""

# ---------------------------------------------------------------------------
# CHAIN-03 国内/国际订单双链汇合（7 阶段，多 Agent 并行题）
# 结构：国内分支 S1->S2->S3，国际分支 S4->S5->S6，S7 汇合。
# 依赖图：S7 的正确修法依赖两条分支各自的输出约定
#   （国内分类 {A,B}，国际分类 {A,B,C}，C 为国际特有的大额报关档）；
#   两条分支内部各自线性，分支之间独立（适合并行修复）。
# 机械前缀序列：[0,1,2,3,4,5,6,7]
# ---------------------------------------------------------------------------

CHAIN03_REFERENCE = r'''# 国内/国际订单合并统计管道
# 国内分支: d1_parse -> d2_convert -> d3_classify  （价格单位：元人民币）
# 国际分支: i1_parse -> i2_convert -> i3_classify  （价格单位：美元）
# 汇合:    merge_report 按类别合并两条分支的统计结果
# 分类约定：国内订单分 A/B 两类；国际订单分 A/B/C 三类（C 为大额需报关档，国际特有）。
# 输出格式：按类别字母序每行 "<cat>: domestic=<d> intl=<i> total=<t>"，末尾 "grand=<总计>"（金额单位：元，两位小数）。

DOM_A_MIN = 20000      # 国内 A 类下限（分）
INTL_A_MIN = 20000     # 国际 A 类下限（分）
INTL_C_MIN = 50000     # 国际 C 类（大额需报关）下限（分）
USD_RATE_CENTS = 800   # 汇率：1 美元 = 800 分人民币


def money(cents):
    """把以分为单位的整数格式化为 '元.分分' 字符串，如 15199 -> '151.99'。"""
    return "{}.{:02d}".format(cents // 100, cents % 100)


def d1_parse(csv_text):
    """解析国内订单 CSV（首行为表头，列为 订单号,单价(元),数量）。"""
    lines = [ln for ln in csv_text.strip().splitlines() if ln.strip()]
    data_lines = lines[1:]
    records = []
    for ln in data_lines:
        parts = [p.strip() for p in ln.split(",")]
        records.append({"id": parts[0], "price": parts[1], "qty": int(parts[2])})
    return records


def d2_convert(records):
    """国内订单单位换算：单价元 -> 分，计算每单总额（分）。"""
    for r in records:
        r["total_cents"] = int(round(float(r["price"]) * 100)) * r["qty"]
    return records


def d3_classify(records):
    """国内订单分类汇总：总额 >= DOM_A_MIN 为 A 类，否则 B 类，返回 {类别: 总额(分)}。"""
    totals = {}
    for r in records:
        cat = "A" if r["total_cents"] >= DOM_A_MIN else "B"
        totals[cat] = totals.get(cat, 0) + r["total_cents"]
    return totals


def i1_parse(csv_text):
    """解析国际订单 CSV（首行为表头，列为 订单号,单价(美元),数量）。"""
    lines = [ln for ln in csv_text.strip().splitlines() if ln.strip()]
    rows = lines[1:]
    records = []
    for ln in rows:
        parts = [p.strip() for p in ln.split(",")]
        records.append({"id": parts[0], "price_usd": parts[1], "qty": int(parts[2])})
    return records


def i2_convert(records):
    """国际订单单位换算：单价美元 -> 人民币分，计算每单总额（分）。"""
    for r in records:
        r["total_cents"] = int(round(float(r["price_usd"]) * USD_RATE_CENTS)) * r["qty"]
    return records


def i3_classify(records):
    """国际订单分类汇总：>= INTL_C_MIN 为 C 类，>= INTL_A_MIN 为 A 类，否则 B 类。"""
    totals = {}
    for r in records:
        if r["total_cents"] >= INTL_C_MIN:
            cat = "C"
        elif r["total_cents"] >= INTL_A_MIN:
            cat = "A"
        else:
            cat = "B"
        totals[cat] = totals.get(cat, 0) + r["total_cents"]
    return totals


def merge_report(d_totals, i_totals):
    """汇合：按类别合并两条分支的统计（类别取两边并集），末尾追加总计行。"""
    lines = []
    grand = 0
    for cat in sorted(set(d_totals) | set(i_totals)):
        dv = d_totals.get(cat, 0)
        iv = i_totals.get(cat, 0)
        grand += dv + iv
        lines.append("{}: domestic={} intl={} total={}".format(cat, money(dv), money(iv), money(dv + iv)))
    lines.append("grand={}".format(money(grand)))
    return "\n".join(lines)


def run_pipeline(domestic_csv, intl_csv):
    d = d3_classify(d2_convert(d1_parse(domestic_csv)))
    i = i3_classify(i2_convert(i1_parse(intl_csv)))
    return merge_report(d, i)
'''

CHAIN03_BUGS = [
    ("records.append({\"id\": parts[0], \"price\": parts[1], \"qty\": int(parts[2])})",
     "records.append({\"id\": parts[0], \"price\": parts[1], \"qty\": 1})"),  # S1 数量列被忽略，硬编码为 1
    ("int(round(float(r[\"price\"]) * 100)) * r[\"qty\"]",
     "int(float(r[\"price\"])) * 100 * r[\"qty\"]"),                          # S2 截断小数元（1.99 元变 1 元）
    ("\"A\" if r[\"total_cents\"] >= DOM_A_MIN else \"B\"",
     "\"A\" if r[\"total_cents\"] >= 10000 else \"B\""),                      # S3 分类阈值错（10000 而非 20000）
    ("rows = lines[1:]", "rows = lines[2:]"),                                # S4 off-by-one 丢第一条国际订单
    ("int(round(float(r[\"price_usd\"]) * USD_RATE_CENTS)) * r[\"qty\"]",
     "round(float(r[\"price_usd\"])) * USD_RATE_CENTS * r[\"qty\"]"),         # S5 先把美元四舍五入成整数再换算
    ("elif r[\"total_cents\"] >= INTL_A_MIN:", "elif r[\"total_cents\"] > INTL_A_MIN:"),  # S6 边界：恰好 200 元的不算 A 类
    ("for cat in sorted(set(d_totals) | set(i_totals)):", "for cat in sorted(d_totals):"),  # S7 只遍历国内类别，丢掉国际特有类别
]

CHAIN03_TESTS = r'''INTL_EMPTY = "oid,price_usd,qty"


def test_stage_1():
    out = run_pipeline("oid,price,qty\nD1,10.00,3", INTL_EMPTY)
    assert out == "B: domestic=30.00 intl=0.00 total=30.00\ngrand=30.00", out


def test_stage_2():
    out = run_pipeline("oid,price,qty\nD1,10.00,2\nD2,1.99,1", INTL_EMPTY)
    assert out == "B: domestic=21.99 intl=0.00 total=21.99\ngrand=21.99", out


def test_stage_3():
    out = run_pipeline("oid,price,qty\nD1,50.00,3\nD2,1.99,1", INTL_EMPTY)
    assert out == "B: domestic=151.99 intl=0.00 total=151.99\ngrand=151.99", out


def test_stage_4():
    out = run_pipeline("oid,price,qty\nD1,200.00,1", "oid,price_usd,qty\nI1,30.00,1")
    assert out == "A: domestic=200.00 intl=240.00 total=440.00\ngrand=440.00", out


def test_stage_5():
    out = run_pipeline("oid,price,qty\nD1,200.00,1", "oid,price_usd,qty\nI1,25.75,1")
    assert out == "A: domestic=200.00 intl=206.00 total=406.00\ngrand=406.00", out


def test_stage_6():
    out = run_pipeline("oid,price,qty\nD1,200.00,1", "oid,price_usd,qty\nI1,25.00,1")
    assert out == "A: domestic=200.00 intl=200.00 total=400.00\ngrand=400.00", out


def test_stage_7():
    out = run_pipeline(
        "oid,price,qty\nD1,200.00,1",
        "oid,price_usd,qty\nI1,25.00,1\nI2,100.00,1",
    )
    assert out == (
        "A: domestic=200.00 intl=200.00 total=400.00\n"
        "C: domestic=0.00 intl=800.00 total=800.00\n"
        "grand=1200.00"
    ), out
'''

CHAIN03_SAMPLE_DOM = "oid,price,qty\nD1001,50.00,3\nD1002,1.99,1"
CHAIN03_SAMPLE_INTL = "oid,price_usd,qty\nI1001,25.00,1\nI1002,100.00,1"

CHAIN03_PROBLEM = """【国内/国际订单合并统计管道修复（双链汇合）】

下面是一个订单合并统计管道，包含两条独立的处理分支和一个汇合阶段：

  国内分支  d1_parse -> d2_convert -> d3_classify   （价格单位：元人民币，分 A/B 两类）
  国际分支  i1_parse -> i2_convert -> i3_classify   （价格单位：美元，分 A/B/C 三类，C 为大额需报关档）
  汇合      merge_report：按类别合并两条分支的统计结果并输出报表

两条分支各自独立，可以并行排查；但汇合阶段的正确性依赖两条分支各自的输出约定。

当前程序的 7 个阶段（国内 3 个 + 国际 3 个 + 汇合 1 个）各被引入了一个 bug，端到端输出不正确。完整源码：

```python
@SOURCE@
```

示例输入（国内订单）：
@SAMPLE_DOM@

示例输入（国际订单）：
@SAMPLE_INTL@

当前（错误的）端到端输出：
@BUGGY_OUTPUT@

期望的正确输出：
@EXPECTED_OUTPUT@

要求：定位并修复全部 7 个阶段的 bug，输出完整的修复后程序（保持各阶段函数接口与 run_pipeline 签名不变）。
"""

# ---------------------------------------------------------------------------
# CHAIN-04 物流运单对账管道（12 阶段长链 + 双陷阱）
# 结构：主体为长线性链 s01..s12，含两处乱序语义依赖（生产者/消费者约定对）：
#   陷阱 A（S5 生产 -> S9 消费）：fee_tag 形如 '<线路>#<级别>'，S9 按 '#' 拆分解组。
#     buggy 版两端同为 '|'，相互抵消、端到端不可见；只修任一端 -> split 解包
#     抛 ValueError，全线崩溃（比全坏时"数值错但格式可解析"更糟）。
#   陷阱 B（S7 生产 -> S11 消费）：batch_id 形如 'B<账期>-<序号>'，S11 按 '-' 拆出
#     序号排序并渲染为 batchNNN。buggy 版两端同为 '_'；只修任一端 -> rsplit 拆
#     不出序号，抛 IndexError，全线崩溃。
# 修复语义依赖（题面不给出）：S9 的正确修法依赖 S5 的 fee_tag 约定，S11 的正确
#   修法依赖 S7 的 batch_id 约定；且 S9/S11 都必须在对应上游修好后才可被验证。
# 机械前缀序列（只修前 k 个阶段的 fixed_depth，k=0..12）：
#   [0,1,2,3,5,0,0,0,0,0,0,11,12]
#   k=4 时深度为 5（S5 的分隔符 bug 在 S9 同坏时不可见）；k=5..8 因陷阱 A 失配
#   全线崩溃归零；k=9..10 陷阱 A 已对齐但陷阱 B 失配，仍归零；k=11 双陷阱解除。
# ---------------------------------------------------------------------------

CHAIN04_REFERENCE = r'''# 物流运单对账管道
# 流程: 解析 -> 校验 -> 线路规范化 -> 计费重量/分级 -> 运费计算 -> VIP折扣
#       -> 批次号 -> 返点 -> 线路汇总 -> 排名 -> 批次行渲染 -> 定稿
#
# 输出报表格式：
#   批次行（按批次序号升序）: batch<序号3位>|<运单号>|<线路>|<级别>|fee=<净运费>
#   线路汇总（按 rank 升序）: rank<r> <线路>: count=<票数> fee=<净运费合计>
#   末行: grand=<总净运费>
# 跨阶段约定：
#   fee_tag（s05 生成，s09 消费）形如 '<线路>#<级别>'；
#   batch_id（s07 生成，s11 消费）形如 'B<账期yyyymm>-<序号3位>'。

import math

RATES = {"小型": 2, "中型": 3, "大型": 5}  # 费率：分/(kg·km)
VIP_ROUTES = {"SH-GZ", "BJ-SH"}            # VIP 线路运费 9 折
REBATE_MIN = 20000   # 返点门槛（分）：折后运费达到 200 元返 5%
REBATE_RATE = 0.05


def money(cents):
    """把以分为单位的整数格式化为 '元.分分' 字符串，如 12300 -> '123.00'。"""
    return "{}.{:02d}".format(cents // 100, cents % 100)


def s01_parse(csv_text):
    """解析 CSV（表头: 运单号,日期,线路,车型,重量kg,里程km,状态）。"""
    lines = [ln for ln in csv_text.strip().splitlines() if ln.strip()]
    records = []
    for ln in lines[1:]:
        parts = [p.strip() for p in ln.split(",")]
        records.append({
            "oid": parts[0],
            "date": parts[1],
            "route": parts[2],
            "vehicle": parts[3],
            "weight": float(parts[4]),
            "km": float(parts[5]),
            "status": parts[6],
        })
    return records


def s02_validate(records):
    """校验：作废运单不参与对账。"""
    return [r for r in records if r["status"] != "作废"]


def s03_normalize_route(records):
    """线路编码规范化：去空白并统一大写。"""
    for r in records:
        r["route"] = r["route"].strip().upper()
    return records


def s04_weight_class(records):
    """计费重量（向上取整到 100kg）与级别：计费重量 >= 500kg 为 H（重件），否则 L（轻件）。"""
    for r in records:
        r["billable"] = int(math.ceil(r["weight"] / 100.0)) * 100
        r["wclass"] = "H" if r["billable"] >= 500 else "L"
    return records


def s05_freight(records):
    """运费（分）= 计费重量 × 里程 × 车型费率；并生成 fee_tag（'<线路>#<级别>'，供 s09 分组）。"""
    for r in records:
        r["fee"] = int(round(r["billable"] * r["km"] * RATES[r["vehicle"]]))
        r["fee_tag"] = r["route"] + "#" + r["wclass"]
    return records


def s06_discount(records):
    """VIP 线路运费 9 折。"""
    for r in records:
        if r["route"] in VIP_ROUTES:
            r["fee"] = int(round(r["fee"] * 0.9))
    return records


def s07_batch(records):
    """生成批次号：'B<账期yyyymm>-<序号3位>'（账期取自日期，序号按记录顺序，s11 按序号排序）。"""
    for i, r in enumerate(records, start=1):
        period = r["date"].replace("-", "")[:6]
        r["batch_id"] = "B{}-{:03d}".format(period, i)
    return records


def s08_rebate(records):
    """返点：折后运费达到 REBATE_MIN（含边界）返 5%，得到净运费。"""
    for r in records:
        if r["fee"] >= REBATE_MIN:
            r["fee"] = r["fee"] - int(round(r["fee"] * REBATE_RATE))
    return records


def s09_route_summary(records):
    """按线路汇总：解析 fee_tag（'<线路>#<级别>'）取线路，累计票数与净运费。"""
    summary = {}
    for r in records:
        route, wclass = r["fee_tag"].split("#")
        entry = summary.setdefault(route, {"count": 0, "fee": 0})
        entry["count"] += 1
        entry["fee"] += r["fee"]
    return summary


def s10_rank(summary):
    """按净运费合计从高到低排名，返回 [(rank, route, count, fee), ...]。"""
    ordered = sorted(summary.items(), key=lambda kv: kv[1]["fee"], reverse=True)
    return [(i, route, e["count"], e["fee"]) for i, (route, e) in enumerate(ordered, start=1)]


def s11_batch_lines(records):
    """渲染批次行并按批次序号升序排列（序号从 batch_id 末尾按 '-' 拆出）。"""
    def seq_of(r):
        return int(r["batch_id"].rsplit("-", 1)[1])
    lines = []
    for r in sorted(records, key=seq_of):
        lines.append("batch{:03d}|{}|{}|{}|fee={}".format(
            seq_of(r), r["oid"], r["route"], r["wclass"], money(r["fee"])))
    return lines


def s12_finalize(batch_lines, ranked):
    """定稿：批次行 + 线路汇总（按 rank 升序）+ 总计行。"""
    out = list(batch_lines)
    grand = 0
    for rank, route, count, fee in ranked:
        grand += fee
        out.append("rank{} {}: count={} fee={}".format(rank, route, count, money(fee)))
    out.append("grand={}".format(money(grand)))
    return "\n".join(out)


def run_pipeline(csv_text):
    records = s01_parse(csv_text)
    records = s02_validate(records)
    records = s03_normalize_route(records)
    records = s04_weight_class(records)
    records = s05_freight(records)
    records = s06_discount(records)
    records = s07_batch(records)
    records = s08_rebate(records)
    summary = s09_route_summary(records)
    ranked = s10_rank(summary)
    batch_lines = s11_batch_lines(records)
    return s12_finalize(batch_lines, ranked)
'''

CHAIN04_BUGS = [
    ('"km": float(parts[5]),', '"km": int(parts[5]),'),                        # S1 里程被截断为整数（20.5 -> 20）
    ('r["status"] != "作废"', 'r["status"] != "取消"'),                          # S2 状态字面量写错，作废单被放行
    ('r["route"].strip().upper()', 'r["route"].strip()'),                      # S3 线路未统一大写
    ('"H" if r["billable"] >= 500 else "L"', '"H" if r["billable"] > 500 else "L"'),  # S4 边界：恰好 500kg 不算重件
    ('r["route"] + "#" + r["wclass"]', 'r["route"] + "|" + r["wclass"]'),      # S5 fee_tag 分隔符与约定不符（陷阱 A 上游）
    ('int(round(r["fee"] * 0.9))', 'int(round(r["fee"] * 0.95))'),             # S6 VIP 折扣率错（95 折而非 9 折）
    ('"B{}-{:03d}".format(period, i)', '"B{}_{:03d}".format(period, i)'),      # S7 batch_id 分隔符与约定不符（陷阱 B 上游）
    ('if r["fee"] >= REBATE_MIN:', 'if r["fee"] > REBATE_MIN:'),               # S8 边界：恰好 200 元不返点
    ('r["fee_tag"].split("#")', 'r["fee_tag"].split("|")'),                    # S9 拆分组标签的分隔符与约定不符（陷阱 A 下游）
    ('key=lambda kv: kv[1]["fee"], reverse=True', 'key=lambda kv: kv[1]["fee"]'),  # S10 排名方向反了
    ('r["batch_id"].rsplit("-", 1)', 'r["batch_id"].rsplit("_", 1)'),          # S11 拆批次号的分隔符与约定不符（陷阱 B 下游）
    ('for rank, route, count, fee in ranked:',
     'for rank, route, count, fee in sorted(ranked, key=lambda x: x[1]):'),    # S12 汇总行错按线路名排序而非 rank
]

CHAIN04_TESTS = r'''HEADER = "运单号,日期,线路,车型,重量kg,里程km,状态"


def test_stage_1():
    out = run_pipeline(HEADER + "\nW1,2024-01-15,GZ-SZ,小型,300,20.5,正常")
    assert out == "batch001|W1|GZ-SZ|L|fee=123.00\nrank1 GZ-SZ: count=1 fee=123.00\ngrand=123.00", out


def test_stage_2():
    out = run_pipeline(HEADER + "\nW1,2024-01-15,GZ-SZ,小型,300,20,正常\nW2,2024-01-15,GZ-SZ,小型,300,20,作废")
    assert out == "batch001|W1|GZ-SZ|L|fee=120.00\nrank1 GZ-SZ: count=1 fee=120.00\ngrand=120.00", out


def test_stage_3():
    out = run_pipeline(HEADER + "\nW1,2024-01-15,gz-sz,小型,300,20,正常")
    assert out == "batch001|W1|GZ-SZ|L|fee=120.00\nrank1 GZ-SZ: count=1 fee=120.00\ngrand=120.00", out


def test_stage_4():
    out = run_pipeline(HEADER + "\nW1,2024-01-15,GZ-SZ,小型,450,10,正常")
    assert out == "batch001|W1|GZ-SZ|H|fee=100.00\nrank1 GZ-SZ: count=1 fee=100.00\ngrand=100.00", out


def test_stage_5():
    out = run_pipeline(HEADER + "\nW1,2024-01-15,GZ-SZ,中型,600,30,正常")
    assert out == "batch001|W1|GZ-SZ|H|fee=513.00\nrank1 GZ-SZ: count=1 fee=513.00\ngrand=513.00", out


def test_stage_6():
    out = run_pipeline(HEADER + "\nW1,2024-01-15,SH-GZ,小型,500,20,正常")
    assert out == "batch001|W1|SH-GZ|H|fee=180.00\nrank1 SH-GZ: count=1 fee=180.00\ngrand=180.00", out


def test_stage_7():
    out = run_pipeline(HEADER + "\nW1,2024-03-05,GZ-SZ,小型,300,20,正常\nW2,2024-03-06,GZ-SZ,小型,300,20,正常")
    assert out == "batch001|W1|GZ-SZ|L|fee=120.00\nbatch002|W2|GZ-SZ|L|fee=120.00\nrank1 GZ-SZ: count=2 fee=240.00\ngrand=240.00", out


def test_stage_8():
    out = run_pipeline(HEADER + "\nW1,2024-01-15,GZ-SZ,小型,500,20,正常")
    assert out == "batch001|W1|GZ-SZ|H|fee=190.00\nrank1 GZ-SZ: count=1 fee=190.00\ngrand=190.00", out


def test_stage_9():
    out = run_pipeline(HEADER + "\nW1,2024-01-15,GZ-SZ,小型,300,20,正常\nW2,2024-01-15,GZ-SZ,小型,300,20,正常")
    assert out == "batch001|W1|GZ-SZ|L|fee=120.00\nbatch002|W2|GZ-SZ|L|fee=120.00\nrank1 GZ-SZ: count=2 fee=240.00\ngrand=240.00", out


def test_stage_10():
    out = run_pipeline(HEADER + "\nW1,2024-01-15,A-01,中型,600,30,正常\nW2,2024-01-15,B-02,小型,300,20,正常")
    assert out == (
        "batch001|W1|A-01|H|fee=513.00\nbatch002|W2|B-02|L|fee=120.00\n"
        "rank1 A-01: count=1 fee=513.00\nrank2 B-02: count=1 fee=120.00\ngrand=633.00"
    ), out


def test_stage_11():
    out = run_pipeline(HEADER + "\nW1,2024-03-05,GZ-SZ,小型,300,20,正常\nW2,2024-01-15,SZ-HK,小型,300,20,正常")
    assert out == (
        "batch001|W1|GZ-SZ|L|fee=120.00\nbatch002|W2|SZ-HK|L|fee=120.00\n"
        "rank1 GZ-SZ: count=1 fee=120.00\nrank2 SZ-HK: count=1 fee=120.00\ngrand=240.00"
    ), out


def test_stage_12():
    out = run_pipeline(HEADER + "\nW1,2024-01-15,B-01,中型,600,30,正常\nW2,2024-01-15,A-02,小型,300,20,正常")
    assert out == (
        "batch001|W1|B-01|H|fee=513.00\nbatch002|W2|A-02|L|fee=120.00\n"
        "rank1 B-01: count=1 fee=513.00\nrank2 A-02: count=1 fee=120.00\ngrand=633.00"
    ), out
'''

CHAIN04_SAMPLE_INPUT = (
    "运单号,日期,线路,车型,重量kg,里程km,状态\n"
    "WB001,2024-01-15,sh-gz,中型,620,30,正常\n"
    "WB002,2024-01-15,GZ-SZ,小型,450,10,正常\n"
    "WB003,2024-02-03,BJ-SH,大型,500,20,作废\n"
    "WB004,2024-02-03,gz-sz,小型,300,25,正常\n"
    "WB005,2024-02-03,SH-GZ,小型,500,20,正常"
)

CHAIN04_PROBLEM = """【物流运单对账管道修复（12 阶段长链 · 双陷阱）】

下面是一个物流运单对账管道：输入 CSV 文本（首行为表头，各列为 运单号,日期,线路,车型,重量kg,里程km,状态），数据依次流经 12 个阶段：

  s01_parse            解析 CSV 为运单记录
  s02_validate         校验：作废运单不参与对账
  s03_normalize_route  线路编码规范化
  s04_weight_class     计费重量（向上取整到 100kg）与轻重件分级
  s05_freight          运费计算，并生成供下游分组使用的 fee_tag
  s06_discount         VIP 线路折扣
  s07_batch            生成对账批次号
  s08_rebate           大客户返点，得到净运费
  s09_route_summary    按线路汇总
  s10_rank             线路排名
  s11_batch_lines      渲染批次行并排序
  s12_finalize         报表定稿

当前程序的 12 个阶段各被引入了一个 bug，端到端输出不正确。各阶段的设计意图与跨阶段数据约定见源码中的 docstring 和文件头注释。完整源码：

```python
@SOURCE@
```

示例输入：
@SAMPLE_INPUT@

当前（错误的）端到端输出：
@BUGGY_OUTPUT@

注意：本管道的阶段之间存在多处隐式数据约定，孤立地"按注释修复"某个阶段可能不仅无效，反而让端到端输出变得更糟（例如从数值错误恶化为完全无法产出报表）。修复时必须通盘考虑上下游阶段之间的约定，建议先整体推演数据流再动手。

要求：定位并修复全部 12 个阶段的 bug，输出完整的修复后程序（保持各阶段函数接口与 run_pipeline 签名不变）。
"""

# ---------------------------------------------------------------------------
# 题目装配与自验
# ---------------------------------------------------------------------------

TASKS = [
    {
        "id": "CHAIN-01",
        "title": "订单处理管道六阶段线性链修复",
        "difficulty": "medium",
        "reference": CHAIN01_REFERENCE,
        "bugs": CHAIN01_BUGS,
        "test_code": CHAIN01_TESTS,
        "problem_tpl": CHAIN01_PROBLEM,
        "sample_args": (CHAIN01_SAMPLE_INPUT,),
        "tpl_mapping": {"@SAMPLE_INPUT@": CHAIN01_SAMPLE_INPUT},
        "expected_depths": [0, 1, 2, 3, 4, 5, 6],
    },
    {
        "id": "CHAIN-02",
        "title": "门店销售日报表管道乱序依赖链修复",
        "difficulty": "hard",
        "reference": CHAIN02_REFERENCE,
        "bugs": CHAIN02_BUGS,
        "test_code": CHAIN02_TESTS,
        "problem_tpl": CHAIN02_PROBLEM,
        "sample_args": (CHAIN02_SAMPLE_INPUT,),
        "tpl_mapping": {"@SAMPLE_INPUT@": CHAIN02_SAMPLE_INPUT},
        "expected_depths": [0, 1, 2, 4, 0, 0, 6, 7, 8],
    },
    {
        "id": "CHAIN-03",
        "title": "国内国际订单双链汇合管道修复",
        "difficulty": "hard",
        "reference": CHAIN03_REFERENCE,
        "bugs": CHAIN03_BUGS,
        "test_code": CHAIN03_TESTS,
        "problem_tpl": CHAIN03_PROBLEM,
        "sample_args": (CHAIN03_SAMPLE_DOM, CHAIN03_SAMPLE_INTL),
        "tpl_mapping": {"@SAMPLE_DOM@": CHAIN03_SAMPLE_DOM, "@SAMPLE_INTL@": CHAIN03_SAMPLE_INTL},
        "expected_depths": [0, 1, 2, 3, 4, 5, 6, 7],
    },
    {
        "id": "CHAIN-04",
        "title": "物流运单对账管道十二阶段长链修复（双陷阱）",
        "difficulty": "hard",
        "reference": CHAIN04_REFERENCE,
        "bugs": CHAIN04_BUGS,
        "test_code": CHAIN04_TESTS,
        "problem_tpl": CHAIN04_PROBLEM,
        "sample_args": (CHAIN04_SAMPLE_INPUT,),
        "tpl_mapping": {"@SAMPLE_INPUT@": CHAIN04_SAMPLE_INPUT},
        "expected_depths": [0, 1, 2, 3, 5, 0, 0, 0, 0, 0, 0, 11, 12],
    },
]


def verify_task(task):
    """对单个题目执行全部自验，返回 (buggy_source, actual_depths)。"""
    ref = task["reference"]
    bugs = task["bugs"]
    tests = task["test_code"]
    n = len(bugs)
    expected = task["expected_depths"]
    assert len(expected) == n + 1, "%s: expected_depths 长度应为 N+1" % task["id"]
    for k in range(1, n + 1):
        assert ("def test_stage_%d(" % k) in tests, "%s: 缺少 test_stage_%d" % (task["id"], k)

    # 1) reference 全通过
    res = run_tests(ref, tests)
    assert res.get("passed") == res.get("total") == n, \
        "%s: reference 未全通过: %s" % (task["id"], res.get("tests", res))

    # 2) buggy 版 fixed_depth == 0
    buggy = make_variant(ref, bugs, set())
    res = run_tests(buggy, tests)
    assert fixed_depth(res) == 0, "%s: buggy 版 fixed_depth 应为 0，实际 %s" % (task["id"], res["tests"])

    # 3) 链结构验证：只修前 k 个阶段（k=1..N-1）的 fixed_depth 序列
    depths = [0]
    for k in range(1, n):
        variant = make_variant(ref, bugs, set(range(1, k + 1)))
        depths.append(fixed_depth(run_tests(variant, tests)))
    depths.append(n)  # reference
    assert depths == expected, \
        "%s: fixed_depth 序列不符，预期 %s 实际 %s" % (task["id"], expected, depths)

    return buggy, depths


def verify_chain02_trap(buggy_source):
    """CHAIN-02 负反馈陷阱：只修阶段 4（label 改回 '#'）而不修阶段 6（仍 split('-')），
    端到端应从"数值错但格式可解析"恶化为"直接抛异常"。"""
    trap = make_variant(CHAIN02_REFERENCE, CHAIN02_BUGS, {4})
    # 全坏版本：输出格式仍可解析（每行 body 形如 sku|tier|金额 或为汇总行）
    out_buggy = exec_pipeline(buggy_source, CHAIN02_SAMPLE_INPUT)
    assert isinstance(out_buggy, str) and "subtotal[" in out_buggy
    for ln in out_buggy.splitlines():
        assert re.match(r"^(rows=\d+|\w+\|[ABC]\|[\d.]+\w*|subtotal\[[ABC]\]=[\d.]+)$", ln), ln
    # 陷阱版本：直接抛 ValueError（拆列失败），比全坏更糟
    try:
        exec_pipeline(trap, CHAIN02_SAMPLE_INPUT)
    except ValueError:
        pass
    else:
        raise AssertionError("CHAIN-02 陷阱版本未抛 ValueError")
    # 陷阱版本在评分器下 fixed_depth 也为 0（与全坏相同，但输出质量更差）
    assert fixed_depth(run_tests(trap, CHAIN02_TESTS)) == 0
    print("CHAIN-02 负反馈陷阱验证通过：全坏=格式可解析的错值，只修阶段4=ValueError 崩溃")


def verify_chain04_traps(buggy_source):
    """CHAIN-04 双陷阱：陷阱对 (S5,S9) 与 (S7,S11) 只修任一端都会失配崩溃。

    对每个陷阱的上下游各构造一个"单边修复"版本，断言：
      - 端到端直接抛异常（ValueError/IndexError），而全坏版输出格式可解析；
      - 评分器下 fixed_depth 为 0。
    """
    traps = {
        "只修阶段5（下游S9仍用旧约定）": {5},
        "只修阶段9（上游S5仍用旧约定）": {9},
        "只修阶段7（下游S11仍用旧约定）": {7},
        "只修阶段11（上游S7仍用旧约定）": {11},
    }
    # 全坏版本：输出格式仍可解析（批次行/汇总行/总计行均成形，只是数值与级别错误）
    out_buggy = exec_pipeline(buggy_source, CHAIN04_SAMPLE_INPUT)
    assert isinstance(out_buggy, str) and out_buggy.strip()
    for ln in out_buggy.splitlines():
        assert re.match(
            r"^(batch\d{3}\|[^|]+\|[^|]+\|[HL]\|fee=\d+\.\d{2}"
            r"|rank\d+ [^:]+: count=\d+ fee=\d+\.\d{2}"
            r"|grand=\d+\.\d{2})$", ln), ln
    for name, fixed in traps.items():
        trap = make_variant(CHAIN04_REFERENCE, CHAIN04_BUGS, fixed)
        try:
            exec_pipeline(trap, CHAIN04_SAMPLE_INPUT)
        except (ValueError, IndexError):
            pass
        else:
            raise AssertionError("CHAIN-04 陷阱版本（%s）未崩溃" % name)
        assert fixed_depth(run_tests(trap, CHAIN04_TESTS)) == 0, name
    print("CHAIN-04 双陷阱验证通过：全坏=格式可解析的错值；4 个单边修复版本均崩溃、fixed_depth=0")


def build_task_dict(task, buggy_source):
    """组装题目 dict：题面嵌入 buggy 源码 + 当前错误输出（+ 期望输出，除 CHAIN-02 外）。"""
    buggy_output = exec_pipeline(buggy_source, *task["sample_args"])
    expected_output = exec_pipeline(task["reference"], *task["sample_args"])
    problem = task["problem_tpl"].replace("@SOURCE@", buggy_source)
    for token, value in task["tpl_mapping"].items():
        problem = problem.replace(token, value)
    problem = problem.replace("@BUGGY_OUTPUT@", buggy_output)
    problem = problem.replace("@EXPECTED_OUTPUT@", expected_output)
    for token in ("@SOURCE@", "@BUGGY_OUTPUT@", "@EXPECTED_OUTPUT@", "@SAMPLE_INPUT@", "@SAMPLE_DOM@", "@SAMPLE_INTL@"):
        assert token not in problem, "%s: 题面占位符 %s 未替换" % (task["id"], token)
    return {
        "id": task["id"],
        "type": "chain_repair",
        "title": task["title"],
        "difficulty": task["difficulty"],
        "coupling": "high",
        "problem": problem,
        "success_criteria": "端到端测试全部通过",
        "perturbation": None,
        "perturbation_message": None,
        "reference_answer": task["reference"],
        "test_code": task["test_code"],
        "role": "core",
    }


def main():
    results = {}
    task_dicts = []
    for task in TASKS:
        buggy, depths = verify_task(task)
        results[task["id"]] = depths
        task_dicts.append(build_task_dict(task, buggy))
        print("%s 自验通过：fixed_depth 序列 %s" % (task["id"], depths))

    verify_chain02_trap(make_variant(CHAIN02_REFERENCE, CHAIN02_BUGS, set()))
    verify_chain04_traps(make_variant(CHAIN04_REFERENCE, CHAIN04_BUGS, set()))

    out_path = LAB_ROOT / "reports" / "chain_tasks.json"
    out_path.write_text(
        json.dumps({"version": "1.0", "tasks": task_dicts}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print("已写入 %s（%d 个题目）" % (out_path, len(task_dicts)))
    print("ALL CHAIN TASKS OK")


if __name__ == "__main__":
    main()
