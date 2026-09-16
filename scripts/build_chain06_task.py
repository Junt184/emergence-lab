#!/usr/bin/env python3
"""构建并自验 CHAIN-06「幸运数字转盘」：补偿性耦合 bug（双错互消）最小范式。

bug1（生产端）：抽取数字本应 0~9，实际恒定返回 0；
bug2（消费端）：数码管渲染本应支持 0~9，实际只有 0 有段码。
两端同错时端到端输出"看起来正常"；只修 bug1 → 系统反而输出空白（更糟）；
只修 bug2 → 无可见变化。必须同时修复。

自验（全部本地、零模型调用）：
- buggy：端到端输出"正常"但 test_stage_1 失败（fixed_depth=0）
- 只修 bug1：stage_1 过，但端到端比 buggy 更糟（输出为空白）且 stage_2/3 挂
- 只修 bug2：fixed_depth=0（stage_1 仍挂）
- 双端都修：3/3 全过

运行（LAB 根目录）：python3.13 scripts/build_chain06_task.py
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from server import compute_fixed_depth, run_code_tests  # noqa: E402

SEGMENTS_FULL = {0: "abcefg", 1: "cf", 2: "acdeg", 3: "acdfg", 4: "bcdf",
                 5: "abdfg", 6: "abdefg", 7: "acf", 8: "abcdefg", 9: "abcdfg"}

BUGGY = '''import random

# 幸运数字转盘：抽取 0~9 的数字并用七段数码管渲染

段码表 = {
    0: "abcefg",
    # 1~9 的段码待补全
}


def 抽取数字():
    """从 0~9 中等概率抽取一个数字。"""
    return random.randint(0, 0)


def 渲染数字(digit):
    """把 0~9 的数字渲染为七段数码管段码字符串。"""
    return 段码表.get(digit, "")


def 转盘():
    """抽取一个幸运数字并返回其数码管段码。"""
    return 渲染数字(抽取数字())


if __name__ == "__main__":
    print(转盘())
'''

REFERENCE = f'''import random

# 幸运数字转盘：抽取 0~9 的数字并用七段数码管渲染

段码表 = {SEGMENTS_FULL!r}


def 抽取数字():
    """从 0~9 中等概率抽取一个数字。"""
    return random.randint(0, 9)


def 渲染数字(digit):
    """把 0~9 的数字渲染为七段数码管段码字符串。"""
    if digit not in 段码表:
        raise ValueError(f"无法渲染的数字: {{digit!r}}")
    return 段码表[digit]


def 转盘():
    """抽取一个幸运数字并返回其数码管段码。"""
    return 渲染数字(抽取数字())
'''

TEST_CODE = '''
import random


def _spy_randint(result_when_open):
    """替换 random.randint：若范围上限>0 则返回指定值，否则按原范围逻辑返回 0。"""
    original = random.randint

    def spy(a, b):
        if b > 0:
            return result_when_open
        return 0

    random.randint = spy
    return original


def test_stage_1():
    original = _spy_randint(5)
    try:
        assert 抽取数字() == 5, "范围上限为 9 时应能返回 5（恒定返回 0 说明范围写错）"
    finally:
        random.randint = original


def test_stage_2():
    rendered = set()
    for d in range(10):
        out = 渲染数字(d)
        assert isinstance(out, str) and out, f"数字 {d} 渲染结果为空"
        rendered.add(out)
    assert len(rendered) == 10, "0~9 的段码应当互不相同"


def test_stage_3():
    # 必须断言到具体段码字符串，不能用 渲染数字() 做参照（两端同错时两头都是空串会假性通过）
    original = _spy_randint(3)
    try:
        assert 转盘() == "acdfg", 转盘()
    finally:
        random.randint = original
    original = _spy_randint(7)
    try:
        assert 转盘() == "acf", 转盘()
    finally:
        random.randint = original
'''


def _fix_b1(code: str) -> str:
    return code.replace("random.randint(0, 0)", "random.randint(0, 9)")


def _fix_b2(code: str) -> str:
    return code.replace(
        '''段码表 = {
    0: "abcefg",
    # 1~9 的段码待补全
}''',
        f"段码表 = {SEGMENTS_FULL!r}")


def _depth(code: str) -> tuple[int, int, int, str]:
    report = run_code_tests(f"```python\n{code}\n```", TEST_CODE)
    depth, total = compute_fixed_depth(report.get("tests", []))
    return depth, report.get("passed", 0), total, str(report.get("load_error") or "")


def _end_to_end(code: str) -> str:
    ns = {"__name__": "probe"}
    exec(compile(code, "<probe>", "exec"), ns)
    return ns["转盘"]()


def main() -> None:
    # 0. 双错互消确认：buggy 端到端输出"看起来正常"（0 的段码）
    assert _end_to_end(BUGGY) == "abcefg", "buggy 端到端应输出 0 的段码"

    # 1. buggy：stage_1 挂，深度 0
    depth, passed, total, err = _depth(BUGGY)
    print(f"buggy        depth={depth} passed={passed}/{total} {err}")
    assert depth == 0

    # 2. 只修 bug1：stage_1 过，但端到端反而更糟（输出空白），stage_2/3 挂
    only_b1 = _fix_b1(BUGGY)
    depth, passed, total, err = _depth(only_b1)
    print(f"只修 bug1    depth={depth} passed={passed}/{total} {err}")
    assert depth == 1 and passed == 1
    outcomes = set()
    import random
    for _ in range(50):
        outcomes.add(_end_to_end(only_b1))
    assert "" in outcomes, "只修 bug1 后应出现空白渲染（比 buggy 更糟）"
    print("             端到端实测：buggy 恒为 'abcefg'，只修 bug1 后出现 ''（空白）→ 更糟确认")

    # 3. 只修 bug2：无可见变化，stage_1 仍挂
    only_b2 = _fix_b2(BUGGY)
    depth, passed, total, err = _depth(only_b2)
    print(f"只修 bug2    depth={depth} passed={passed}/{total} {err}")
    assert depth == 0
    assert _end_to_end(only_b2) == "abcefg", "只修 bug2 端到端应与 buggy 完全一致"

    # 4. 双端都修：全过
    depth, passed, total, err = _depth(_fix_b1(_fix_b2(BUGGY)))
    print(f"双端都修    depth={depth} passed={passed}/{total} {err}")
    assert depth == 3 and passed == 3

    # 5. reference 全过
    depth, passed, total, err = _depth(REFERENCE)
    print(f"reference    depth={depth} passed={passed}/{total} {err}")
    assert passed == 3
    print("CHAIN-06 补偿性耦合自验通过")

    task = {
        "id": "CHAIN-06",
        "type": "chain_repair",
        "title": "幸运数字转盘双错互消修复",
        "difficulty": "hard",
        "coupling": "high",
        "problem": '''【幸运数字转盘修复（补偿性耦合 · 一次性修复）】

下面是一个"幸运数字转盘"程序：从 0~9 抽取一个数字，用七段数码管段码渲染输出。程序当前能运行，输出看起来也"正常"——但它里面有两个相互抵消的 bug。请通读规则约定与源码，**一次性**给出完整修复版程序。

规则约定（修复目标）：
1. `抽取数字()` 必须从 0~9 中等概率抽取（即 `random.randint(0, 9)`）。
2. `渲染数字(digit)` 必须能对 0~9 全部给出互不相同的非空段码；对超出范围的输入应抛出 ValueError，而不是静默返回空串。
3. `转盘()` 返回抽取数字的段码；`段码表` 的 0 段码为 "abcefg"，1~9 依次为 "cf"、"acdeg"、"acdfg"、"bcdf"、"abdfg"、"abdefg"、"acf"、"abcdefg"、"abcdfg"。
4. 保持三个函数的对外接口不变。

完整源码：

```python
''' + BUGGY + '''```

提示：不要只看端到端输出——它恰好是正确的假象。请逐个函数对照约定检查。
要求：输出修复后的完整程序（单个 python 代码块），保留 `if __name__ == "__main__"` 演示块与否均可，不要输出测试代码。''',
        "success_criteria": "test_stage_1..3 全部通过：生产端范围正确、消费端 0~9 全覆盖、端到端在强制抽取 3 和 7 时输出正确段码。",
        "perturbation": None,
        "perturbation_message": None,
        "reference_answer": REFERENCE,
        "test_code": TEST_CODE.strip() + "\n",
        "role": "core",
    }

    tasks_path = ROOT / "tasks.json"
    data = json.loads(tasks_path.read_text(encoding="utf-8"))
    for i, t in enumerate(data["tasks"]):
        if t["id"] == "CHAIN-06":
            data["tasks"][i] = task
            break
    else:
        data["tasks"].append(task)
    tasks_path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"tasks.json 现有 {len(data['tasks'])} 个任务")


if __name__ == "__main__":
    main()
