#!/usr/bin/env python3
"""构建并自验 CHAIN-05「武侠对战游戏」：6 级遮蔽链一次性修复题。

遮蔽链：B1 语法(无法启动) → B2 终止(永不结束) → B3 计数(回合号错乱)
       → B4 随机(先手写死) → B5 数值(防御未生效) → B6 表现(先手文本/目标颠倒)
每一级只有前序修好后才可观测。评分用分层测试 test_stage_1..6。

运行（LAB 根目录）：python3.13 scripts/build_chain05_task.py
自验：reference 6/6；buggy fixed_depth=0；累积修复版本 v1..v5 深度序列 [1,2,3,4,5,6]。
通过后把 CHAIN-05 并入 tasks.json（幂等）。
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from server import compute_fixed_depth, run_code_tests  # noqa: E402

BUGGY = '''import random

class People():
    def __init__(self, name, hp=100, act=10, defense=5):
        self.name = name
        self.hp = hp
        self.act = act
        self.defense = defense

    def 被攻击(self, act):
        # 规则：实际伤害 = max(1, act - defense)，HP 归零即阵亡
        if self.hp <= 0:
            return
        self.hp -= act


def 战斗(hero1, hero2):
    i = 0
    q = random.randint(0-)

    while hero1.hp >= 0 or hero2.hp >= 0:
        print(f"===第{i}回合===")
        i = random.randint(0, 1)
        first = 0
        if first == 0:
            print(f"{hero2.name} 先手")
            hero2.被攻击(hero1.act)
            hero1.被攻击(hero2.act)
        else:
            print(f"{hero2.name} 先手")
            hero1.被攻击(hero2.act)
            hero2.被攻击(hero1.act)
        i += 1
        print(f"h1 {hero1.hp}\\nh2 {hero2.hp}")


h1 = People("张三")
h2 = People("李四")
战斗(h1, h2)
'''

REFERENCE = '''import random

class People():
    def __init__(self, name, hp=100, act=10, defense=5):
        self.name = name
        self.hp = hp
        self.act = act
        self.defense = defense
        self.alive = True

    def 被攻击(self, act):
        if not self.alive:
            return 0
        actual = max(1, act - self.defense)
        self.hp = max(0, self.hp - actual)
        if self.hp == 0:
            self.alive = False
        return actual


def 战斗(hero1, hero2):
    i = 1
    while hero1.alive and hero2.alive:
        print(f"===第{i}回合===")
        first = random.randint(0, 1)
        if first == 0:
            print(f"{hero1.name} 先手")
            hero2.被攻击(hero1.act)
        else:
            print(f"{hero2.name} 先手")
            hero1.被攻击(hero2.act)
        print(f"{hero1.name} 剩余HP: {hero1.hp}，{hero2.name} 剩余HP: {hero2.hp}")
        i += 1
    winner = hero1 if hero1.alive else hero2
    print(f"{winner.name} 获胜")
    return winner.name
'''

def _strip_demo(code: str) -> str:
    """去掉程序末尾的演示调用块（模型提交版不应包含，否则模块加载即触发战斗）。"""
    return code.replace('h1 = People("张三")\nh2 = People("李四")\n战斗(h1, h2)\n', "")


# 累积修复版本：v1 只修 B1，v2 加修 B2，依此类推；v5 即 reference。
def _variant(fix_b2=False, fix_b3=False, fix_b4=False, fix_b5=False) -> str:
    code = _strip_demo(BUGGY).replace("q = random.randint(0-)", "q = 0")  # B1 必修
    if fix_b5:
        code = code.replace(
            """    def 被攻击(self, act):
        # 规则：实际伤害 = max(1, act - defense)，HP 归零即阵亡
        if self.hp <= 0:
            return
        self.hp -= act""",
            '''    def 被攻击(self, act):
        # 规则：实际伤害 = max(1, act - defense)，HP 归零即阵亡
        if self.hp <= 0:
            return 0
        actual = max(1, act - self.defense)
        self.hp = max(0, self.hp - actual)
        return actual''')
    if fix_b2:
        code = code.replace(
            "while hero1.hp >= 0 or hero2.hp >= 0:",
            "while hero1.hp > 0 and hero2.hp > 0:")
        code = code.replace(
            '''        i += 1
        print(f"h1 {hero1.hp}\\nh2 {hero2.hp}")''',
            '''        i += 1
        print(f"h1 {hero1.hp}\\nh2 {hero2.hp}")
    winner = hero1 if hero1.hp > 0 else hero2
    print(f"{winner.name} 获胜")
    return winner.name''')
    if fix_b3:
        code = code.replace("    i = 0\n", "    i = 1\n")
        code = code.replace("        i = random.randint(0, 1)\n", "")
    if fix_b4:
        code = code.replace("        first = 0\n", "        first = random.randint(0, 1)\n")
        code = code.replace(
            '''        if first == 0:
            print(f"{hero2.name} 先手")
            hero2.被攻击(hero1.act)
            hero1.被攻击(hero2.act)
        else:
            print(f"{hero2.name} 先手")
            hero1.被攻击(hero2.act)
            hero2.被攻击(hero1.act)''',
            '''        if first == 0:
            print(f"{hero1.name} 先手")
            hero2.被攻击(hero1.act)
        else:
            print(f"{hero2.name} 先手")
            hero1.被攻击(hero2.act)''')
    return code

V1 = _variant()
V2 = _variant(fix_b2=True)
V3 = _variant(fix_b2=True, fix_b3=True)
V4 = _variant(fix_b2=True, fix_b3=True, fix_b4=True)

TEST_CODE = '''
import contextlib
import io
import random


def _run_battle(seq):
    """用固定序列替换 random.randint 跑一场战斗，返回 (输出文本, 胜者)。"""
    it = iter(seq)
    original = random.randint
    random.randint = lambda a, b: next(it)
    try:
        h1 = People("张三")
        h2 = People("李四")
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            winner = 战斗(h1, h2)
        return buf.getvalue(), winner
    finally:
        random.randint = original


def test_stage_1():
    p = People("测试")
    assert p.hp == 100 and p.act == 10 and p.defense == 5
    assert callable(战斗)


def test_stage_2():
    out, winner = _run_battle([0] * 400)
    assert "获胜" in out, out[-200:]
    assert winner in ("张三", "李四")


def test_stage_3():
    out, _ = _run_battle([0] * 400)
    assert "===第0回合===" not in out
    i1 = out.find("===第1回合===")
    i2 = out.find("===第2回合===")
    i3 = out.find("===第3回合===")
    assert -1 < i1 < i2 < i3, out[:300]


def test_stage_4():
    out0, _ = _run_battle([0] * 400)
    assert "张三 先手" in out0
    out1, _ = _run_battle([1] * 400)
    assert "李四 先手" in out1


def test_stage_5():
    p = People("测试")
    p.被攻击(10)
    assert p.hp == 95, p.hp
    p.被攻击(3)
    assert p.hp == 94, p.hp
    for _ in range(300):
        p.被攻击(10)
    assert p.hp == 0, p.hp


def test_stage_6():
    out, winner = _run_battle([0] * 400)
    assert winner == "张三"
    assert "===第20回合===" in out
    assert "===第21回合===" not in out
    assert "张三 剩余HP: 100，李四 剩余HP: 0" in out
    assert "张三 获胜" in out
'''

PROBLEM = '''【武侠对战游戏修复（6 级遮蔽链 · 一次性修复）】

下面是一个武侠对战小游戏。注意：程序当前**根本无法启动**；且即使能启动，后续每层 bug 也只有在前一层修好后才会暴露症状。请通读规则约定与源码，**一次性**给出完整修复版程序（不允许运行后再改）。

规则约定（修复目标）：
1. 每回合随机一方先手：必须调用 `random.randint(0, 1)`，0 表示 hero1 先手、1 表示 hero2 先手；每回合只有先手方攻击一次。
2. 实际伤害 = max(1, 攻击方 act − 受击方 defense)；HP 最低为 0，归零即阵亡，阵亡后不再受击。
3. 任一方阵亡战斗立即结束，打印 `<胜者名> 获胜`，并由 `战斗` 函数返回胜者名字。
4. 回合从第 1 回合开始连续编号；每回合依次打印 `===第N回合===`、`<先手名> 先手`、`<hero1名> 剩余HP: x，<hero2名> 剩余HP: y`。
5. 保持 `People` 类与 `战斗(hero1, hero2)` 的对外接口不变。

完整源码：

```python
''' + BUGGY + '''```

要求：输出修复后的完整程序（单个 python 代码块），只包含 `People` 类与 `战斗` 函数的定义，**不要包含任何示例调用代码**（如末尾的 `h1 = People("张三")`、`战斗(h1, h2)`），也不要输出测试代码。'''

TASK = {
    "id": "CHAIN-05",
    "type": "chain_repair",
    "title": "武侠对战游戏六级遮蔽链修复",
    "difficulty": "hard",
    "coupling": "high",
    "problem": PROBLEM,
    "success_criteria": "分层测试 test_stage_1..6 全部通过；每级症状只有前序 bug 修好后才可观测，fixed_depth 反映真实修复进度。",
    "perturbation": None,
    "perturbation_message": None,
    "reference_answer": REFERENCE,
    "test_code": TEST_CODE.strip() + "\n",
    "role": "core",
}


def depth_of(code: str) -> tuple[int, int, int]:
    report = run_code_tests(f"```python\n{code}\n```", TEST_CODE)
    depth, total = compute_fixed_depth(report.get("tests", []))
    return depth, report.get("passed", 0), total


def main() -> None:
    checks = [
        ("buggy", BUGGY, 0),
        ("v1(只修B1)", V1, 1),
        ("v2(+B2)", V2, 2),
        ("v3(+B3)", V3, 3),
        ("v4(+B4+B6)", V4, 4),
        ("reference", REFERENCE, 6),
    ]
    for name, code, expect in checks:
        depth, passed, total = depth_of(code)
        status = "OK" if depth == expect else f"FAIL（期望深度 {expect}）"
        print(f"{name:16s} fixed_depth={depth} passed={passed}/{total} {status}")
        if depth != expect:
            sys.exit(1)
    print("CHAIN-05 遮蔽链自验通过")

    tasks_path = ROOT / "tasks.json"
    data = json.loads(tasks_path.read_text(encoding="utf-8"))
    for i, task in enumerate(data["tasks"]):
        if task["id"] == "CHAIN-05":
            data["tasks"][i] = TASK
            break
    else:
        data["tasks"].append(TASK)
    tasks_path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"tasks.json 现有 {len(data['tasks'])} 个任务")


if __name__ == "__main__":
    main()
