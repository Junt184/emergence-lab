import unittest

import server
from server import (
    compute_fixed_depth,
    exact_match_report,
    extract_final_json,
    extract_python_code,
)


class ExtractPythonCodeTests(unittest.TestCase):
    def test_prefers_implementation_over_model_tests(self):
        answer = (
            "实现：\n```python\ndef add(a, b):\n    return a + b\n```\n"
            "我的测试：\n```python\ndef test_a():\n    assert add(1, 1) == 2\n"
            "def test_b():\n    assert add(2, 2) == 4\ndef test_c():\n    assert True\n```"
        )
        extracted = extract_python_code(answer)
        self.assertIn("def add", extracted)
        self.assertNotIn("def test_", extracted)

    def test_multiple_impl_blocks_picks_best_compilable(self):
        answer = "```python\ndef a():\n    return 1\n```\n文字\n```python\ndef b():\n    return a() + 1\n```"
        extracted = extract_python_code(answer)
        self.assertIn("def b", extracted)

    def test_skips_example_io_blocks(self):
        answer = (
            "实现：\n```python\ndef run_pipeline(x):\n    return x\n```\n"
            "示例输入：\n```\nid,price,qty\nA,6,1\n```\n"
            "预期输出：\n```\ncount=0 total=0.00\n```"
        )
        extracted = extract_python_code(answer)
        self.assertIn("def run_pipeline", extracted)
        self.assertNotIn("id,price,qty", extracted)
        compile(extracted, "<extracted>", "exec")


class FixedDepthTests(unittest.TestCase):
    def test_prefix(self):
        tests = [
            {"name": "test_stage_1", "ok": True},
            {"name": "test_stage_2", "ok": True},
            {"name": "test_stage_3", "ok": False},
            {"name": "test_stage_4", "ok": True},
        ]
        self.assertEqual(compute_fixed_depth(tests), (2, 4))

    def test_zero_when_first_stage_fails(self):
        tests = [{"name": "test_stage_1", "ok": False}, {"name": "test_stage_2", "ok": True}]
        self.assertEqual(compute_fixed_depth(tests), (0, 2))

    def test_ignores_non_stage_tests(self):
        tests = [{"name": "test_stage_1", "ok": True}, {"name": "test_helper", "ok": False}]
        self.assertEqual(compute_fixed_depth(tests), (1, 1))


class ExtractFinalJsonTests(unittest.TestCase):
    def test_json_block(self):
        self.assertEqual(extract_final_json('分析\n```json\n{"最终答案": 42}\n```'), 42)

    def test_last_block_wins(self):
        answer = '```json\n{"最终答案": 1}\n```\n修正：\n```json\n{"最终答案": 2}\n```'
        self.assertEqual(extract_final_json(answer), 2)

    def test_fallback_raw_text(self):
        self.assertEqual(extract_final_json('所以最终答案是 {"最终答案": "灯塔"} 无误'), "灯塔")

    def test_none_when_absent(self):
        self.assertIsNone(extract_final_json("没有任何 JSON"))


class ExactMatchReportTests(unittest.TestCase):
    def test_value_numeric_with_unit(self):
        r = exact_match_report('```json\n{"最终答案": "20万元"}\n```', {"kind": "value", "value": 20})
        self.assertTrue(r["all_correct"])
        self.assertEqual(r["score"], 100.0)

    def test_value_mismatch(self):
        r = exact_match_report('```json\n{"最终答案": 15}\n```', {"kind": "value", "value": 20})
        self.assertFalse(r["all_correct"])
        self.assertEqual(r["score"], 0.0)

    def test_mapping_partial_credit(self):
        spec = {"kind": "mapping", "mapping": {"A": 1, "B": {"x": 2, "y": 3}}}
        r = exact_match_report('```json\n{"最终答案": {"A": 1, "B": {"x": 2, "y": 9}}}\n```', spec)
        self.assertFalse(r["all_correct"])
        self.assertAlmostEqual(r["score"], 66.7, places=1)

    def test_schedule_accepts_alternative_critical_path(self):
        spec = {"kind": "schedule", "makespan": 18, "critical_path": ["T1", "T3"],
                "all_paths": [["T1", "T3"], ["T2", "T4"]]}
        r = exact_match_report('```json\n{"最终答案": {"最短工期": 18, "关键路径": ["T2", "T4"]}}\n```', spec)
        self.assertTrue(r["all_correct"])

    def test_schedule_wrong_makespan(self):
        spec = {"kind": "schedule", "makespan": 18, "critical_path": ["T1"]}
        r = exact_match_report('```json\n{"最终答案": {"最短工期": 20, "关键路径": ["T1"]}}\n```', spec)
        self.assertFalse(r["all_correct"])
        self.assertEqual(r["score"], 50.0)

    def test_missing_final_answer(self):
        r = exact_match_report("没有输出 JSON", {"kind": "value", "value": 1})
        self.assertFalse(r["all_correct"])
        self.assertEqual(r["score"], 0.0)


if __name__ == "__main__":
    unittest.main()
