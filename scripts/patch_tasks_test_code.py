#!/usr/bin/env python3
"""一次性迁移：为 12 个 CR 任务补充可执行的 test_code（test_* 断言函数）。"""
import json
from pathlib import Path

TASKS = Path(__file__).resolve().parent.parent / "tasks.json"

TEST_CODE = {
    "CR-01": '''
def test_values():
    assert fizzbuzz(15) == "FizzBuzz"
    assert fizzbuzz(-9) == "Fizz"
    assert fizzbuzz(7) == "7"
    assert fizzbuzz(5) == "Buzz"

def test_range():
    assert fizzbuzz_range(3, 5) == ["Fizz", "4", "Buzz"]
    assert fizzbuzz_range(5, 3) == []

def test_type_error():
    for bad in ("3", 3.5, None):
        try:
            fizzbuzz(bad)
        except TypeError:
            continue
        raise AssertionError(f"expected TypeError for {bad!r}")
''',
    "CR-02": '''
def test_cases():
    assert is_palindrome("A man, a plan, a canal: Panama") == True
    assert is_palindrome("race a car") == False
    assert is_palindrome("上海自来水来自海上") == True
    assert is_palindrome("hello") == False
''',
    "CR-03": '''
def test_nested():
    assert flatten([1, [2, [3, 4]], 5]) == [1, 2, 3, 4, 5]
    assert flatten([]) == []
    assert flatten([[[]]]) == []
    assert flatten([[[1], 2], [3]]) == [1, 2, 3]
''',
    "CR-04": '''
def test_search():
    assert binary_search([1, 2, 3, 4, 5], 3) == 2
    assert binary_search([1, 2, 3, 4, 5], 6) == -1
    assert binary_search([], 1) == -1
    assert binary_search([1], 1) == 0
    assert binary_search([1, 3], 3) == 1
''',
    "CR-05": '''
def test_parse():
    assert parse_jsonl('{"a":1}\\n\\n  \\n{"a":2}') == [{"a": 1}, {"a": 2}]
    assert parse_jsonl("") == []

def test_invalid_line_number():
    try:
        parse_jsonl('{"a":1}\\nnot json')
    except ValueError as exc:
        assert "line 2" in str(exc), str(exc)
    else:
        raise AssertionError("expected ValueError")

def test_load_missing():
    try:
        load_jsonl("/nonexistent/path/x.jsonl")
    except FileNotFoundError:
        pass
    else:
        raise AssertionError("expected FileNotFoundError")
''',
    "CR-06": '''
def test_dedupe():
    assert dedupe([3, 1, 2, 1, 3]) == [3, 1, 2]
    assert dedupe([]) == []
    assert dedupe([1, 1, 1]) == [1]
''',
    "CR-07": '''
def test_group():
    assert group_by_first(["apple", "Avocado", "banana"]) == {"a": ["apple", "Avocado"], "b": ["banana"]}
    assert group_by_first([]) == {}
    assert group_by_first([""]) == {"_": [""]}
''',
    "CR-08": '''
def test_divide():
    assert safe_divide(5, 2) == 2.5
    assert safe_divide(1, 0) is None
    assert safe_divide(10, 3) == 3.33

def test_mod():
    assert safe_mod(7, 3) == 1
    assert safe_mod(5, 0) is None

def test_average():
    assert safe_average([]) is None
    assert safe_average([None, None]) is None
    assert safe_average([2, None, 4]) == 3.0
''',
    "CR-09": '''
def test_merge():
    assert merge_intervals([[1, 3], [2, 6], [8, 10], [15, 18]]) == [[1, 6], [8, 10], [15, 18]]
    assert merge_intervals([[1, 4], [4, 5]]) == [[1, 5]]
    assert merge_intervals([]) == []

def test_overlap():
    assert has_overlap([[1, 2], [3, 4]]) == False
    assert has_overlap([[1, 3], [2, 4]]) == True

def test_uncovered():
    assert uncovered_range([[1, 3], [6, 9]], 1, 10) == [4, 5]
    assert uncovered_range([[1, 10]], 1, 10) is None

def test_invalid():
    for bad in ("x", [[1, 2, 3]], [[1]]):
        try:
            merge_intervals(bad)
        except ValueError:
            continue
        raise AssertionError(f"expected ValueError for {bad!r}")
''',
    "CR-10": '''
def test_slug():
    assert slugify("Hello World 2024") == "hello-world-2024"
    assert slugify("  A B  ") == "a-b"
    assert slugify("Python 3.12!") == "python-3-12"
    assert slugify("") == ""
''',
    "CR-11": '''
def test_most():
    assert most_frequent(["a", "b", "a", "b", "a"]) == "a"
    assert most_frequent(["x", "y", "y", "x"]) == "x"
    assert most_frequent([]) is None

def test_least():
    assert least_frequent(["x", "y", "y", "x"]) == "x"
    assert least_frequent([]) is None
    assert least_frequent([1, 1, 2, 3, 3]) == 2
''',
    "CR-12": '''
def test_chunked():
    assert list(chunked([1, 2, 3, 4, 5], 2)) == [[1, 2], [3, 4], [5]]
    assert list(chunked([], 3)) == []
    try:
        list(chunked([1], 0))
    except ValueError:
        pass
    else:
        raise AssertionError("expected ValueError")

def test_windowed():
    assert list(windowed([1, 2, 3], 2)) == [[1, 2], [2, 3]]
    assert list(windowed([1], 2)) == []

def test_batch_map():
    def tf(batch):
        if 99 in batch:
            raise RuntimeError("boom")
        return sum(batch)
    assert list(batch_map([1, 2, 99, 3, 4], 2, tf)) == [3, None, 4]

def test_lazy():
    import types
    assert isinstance(chunked([1, 2], 1), types.GeneratorType)
    assert isinstance(windowed([1, 2, 3], 2), types.GeneratorType)
''',
}


def main():
    data = json.loads(TASKS.read_text(encoding="utf-8"))
    patched = 0
    for task in data["tasks"]:
        code = TEST_CODE.get(task["id"])
        if code:
            task["test_code"] = code.strip() + "\n"
            patched += 1
    TASKS.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"已为 {patched} 个任务补充 test_code")


if __name__ == "__main__":
    main()
