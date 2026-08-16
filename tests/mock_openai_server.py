import json
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_args):
        return

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(length).decode("utf-8"))
        prompt = body.get("messages", [{}, {}])[-1].get("content", "")
        if body.get("model") == "timeout-model" and "进行一次最终反思修订" in prompt:
            time.sleep(2)
        if '"dimensions"' in prompt:
            content = json.dumps({"score": 84, "confidence": 0.81, "dimensions": {"constraint_coverage": 21, "actionability": 22, "consistency": 20, "explanation": 21}, "summary": "测试评估通过"}, ensure_ascii=False)
        elif '"needed"' in prompt:
            content = json.dumps({"needed": True, "gap": "补充退出条件", "role": "风险"}, ensure_ascii=False)
        elif '"choice"' in prompt and '只能选择' in prompt:
            content = json.dumps({"choice": 1, "score": 82, "reason": "测试投票"}, ensure_ascii=False)
        else:
            content = "建议采用分阶段试点。关键权衡是成本与迁移风险；先由负责人执行两周试点，以采用率和缺陷率验收，未达阈值则退出。"
        prompt_tokens = max(1, len(prompt) // 4)
        completion_tokens = max(1, len(content) // 3)
        message = {"role": "assistant", "content": content}
        if body.get("model") == "reasoning-content-model":
            message = {"role": "assistant", "content": "", "reasoning_content": content}
        elif body.get("model") == "content-parts-model":
            message = {"role": "assistant", "content": [{"type": "text", "text": content}]}
        elif body.get("model") == "empty-content-model":
            message = {"role": "assistant", "content": ""}
        response = {"choices": [{"message": message, "finish_reason": "stop"}], "usage": {"prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens, "total_tokens": prompt_tokens + completion_tokens}}
        data = json.dumps(response, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


if __name__ == "__main__":
    ThreadingHTTPServer(("127.0.0.1", 4199), Handler).serve_forever()
