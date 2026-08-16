# 自涌现研究台

这是一个可实际调用大模型的本地多 Agent 实验平台。后端只使用 Python 标准库，无需安装第三方依赖。

## 目录结构

- `server.py`：HTTP 后端、多 Agent 编排、SQLite 持久化
- `index.html`、`app.js`：实验台前端
- `tests/`：响应解析、Token 记录和截断重试测试
- `docs/`：研究论文和项目参考资料
- `experiments.db`：本地运行历史，不提交到 Git

## 启动

双击 `start.bat`，或在当前目录运行：

```powershell
python server.py
```

浏览器访问 `http://127.0.0.1:4173`。

## 模型连接

在页面右上角打开“模型设置”，填写：

- API Base URL，例如 `https://api.openai.com/v1`
- 模型名称，例如 `gpt-4.1-mini`
- API Key

接口需兼容 `POST /chat/completions`。API Key 不写入 SQLite；也可以通过环境变量 `OPENAI_API_KEY`、`OPENAI_BASE_URL`、`OPENAI_MODEL` 在服务端配置。

`deepseek-v4-flash` 使用 128,000 Token 最大输入上下文和 8,192 Token 单次最大输出。遇到 `finish_reason=length` 时，系统会记录截断响应，并自动提高一次输出上限重试。

## 实验数据

- `experiments.db`：SQLite 运行记录，首次启动自动创建
- Token：直接读取供应商响应中的 `usage`
- 质量 Q：由独立裁判模型按固定四维量表评分
- 原始候选、投票、动态重分配、事件日志与最终输出均保存在数据库中
- 页面中的“Agent 输出明细”可以逐条查看每次调用的输入上下文、完整输出、Token、耗时和请求地址
- 可在模型设置中设置最大输入上下文和单次最大输出 Token；可选协作阶段超时会保留已有答案并继续，而不是让整次实验失败

“运行 B0-B4 对比”会实际执行五组条件，消耗明显高于单次运行。
