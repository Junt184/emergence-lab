import json
import unittest
from unittest.mock import patch

import server
from server import ModelClient, RunContext, extract_response_content, response_usage


class FakeHTTPResponse:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self):
        return json.dumps(self.payload).encode("utf-8")


class ResponseParsingTests(unittest.TestCase):
    def test_standard_content(self):
        self.assertEqual(
            extract_response_content({"choices": [{"message": {"content": "answer"}}]}),
            ("answer", "choices[0].message.content"),
        )

    def test_reasoning_content_fallback(self):
        self.assertEqual(
            extract_response_content({"choices": [{"message": {"content": "", "reasoning_content": "answer"}}]}),
            ("answer", "choices[0].message.reasoning_content"),
        )

    def test_reasoning_field_fallback(self):
        self.assertEqual(
            extract_response_content({"choices": [{"message": {"content": "", "reasoning": "answer"}}]}),
            ("answer", "choices[0].message.reasoning"),
        )

    def test_content_parts(self):
        data = {"choices": [{"message": {"content": [{"type": "text", "text": "part one"}, {"type": "text", "text": {"value": "part two"}}]}}]}
        self.assertEqual(extract_response_content(data), ("part one\npart two", "choices[0].message.content"))

    def test_legacy_and_top_level_output(self):
        self.assertEqual(extract_response_content({"choices": [{"text": "legacy"}]}), ("legacy", "choices[0].text"))
        self.assertEqual(extract_response_content({"output_text": "response"}), ("response", "output_text"))

    def test_usage_aliases(self):
        self.assertEqual(response_usage({"usage": {"input_tokens": 10, "output_tokens": 4}}), (10, 4, 14, 0))

    def test_deepseek_flash_uses_standard_limits_by_default(self):
        client = ModelClient({"model": "deepseek-v4-flash"}, RunContext("TEST-DS-LIMITS"))
        self.assertEqual(client.max_input_tokens, 128000)
        self.assertEqual(client.max_output_tokens, 8192)
        oversized = ModelClient({"model": "deepseek-v4-flash", "max_input_tokens": 999999, "max_output_tokens": 999999}, RunContext("TEST-DS-LIMITS-CAP"))
        self.assertEqual(oversized.max_input_tokens, 128000)
        self.assertEqual(oversized.max_output_tokens, 8192)

    def test_empty_response_is_retried_and_usage_is_recorded(self):
        empty = {"choices": [{"message": {"content": ""}, "finish_reason": "stop"}], "usage": {"prompt_tokens": 7, "completion_tokens": 3, "total_tokens": 10}}
        context = RunContext("TEST-EMPTY-RETRY")
        client = ModelClient({"base_url": "http://model.test/v1", "model": "test", "timeout": 1}, context)
        with patch("server.db_execute"), patch("server.urllib.request.urlopen", return_value=FakeHTTPResponse(empty)):
            with self.assertRaisesRegex(RuntimeError, "连续两次"):
                client.chat("测试 Agent", "system", "prompt")
        self.assertEqual(len(client.call_records), 2)
        self.assertEqual([call["status"] for call in client.call_records], ["empty", "empty"])
        self.assertEqual(client.total_tokens, 20)
        self.assertEqual(client.prompt_tokens, 14)
        self.assertEqual(client.completion_tokens, 6)

    def test_length_retry_expands_limit_and_requires_final_content(self):
        truncated = {"choices": [{"message": {"content": "", "reasoning": "unfinished analysis"}, "finish_reason": "length"}], "usage": {"prompt_tokens": 8, "completion_tokens": 1600, "total_tokens": 1608}}
        completed = {"choices": [{"message": {"content": "final answer", "reasoning": "analysis"}, "finish_reason": "stop"}], "usage": {"prompt_tokens": 9, "completion_tokens": 100, "total_tokens": 109}}
        requests = []

        def urlopen(request, timeout):
            requests.append(json.loads(request.data.decode("utf-8")))
            return FakeHTTPResponse(truncated if len(requests) == 1 else completed)

        context = RunContext("TEST-LENGTH-RETRY")
        client = ModelClient({"base_url": "http://model.test/v1", "model": "test", "timeout": 1, "max_output_tokens": 1600}, context)
        with patch("server.db_execute"), patch("server.urllib.request.urlopen", side_effect=urlopen):
            output = client.chat("测试 Agent", "system", "prompt")
        self.assertEqual(output, "final answer")
        self.assertEqual([request["max_tokens"] for request in requests], [1600, 3200])
        self.assertIn("直接给出简洁", requests[1]["messages"][-1]["content"])
        self.assertEqual([call["requested_max_tokens"] for call in client.call_records], [1600, 3200])
        self.assertEqual([call["status"] for call in client.call_records], ["empty", "completed"])
        self.assertEqual(client.total_tokens, 1717)


if __name__ == "__main__":
    unittest.main()
