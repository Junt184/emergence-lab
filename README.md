# 自涌现研究台

这是一个可实际调用大模型的本地多 Agent 实验平台。后端只使用 Python 标准库，无需安装第三方依赖。

## 目录结构

- `server.py`：HTTP 后端、多 Agent 编排、SQLite 持久化
- `index.html`、`app.js`：实验台前端
- `tests/`：响应解析、Token 记录和截断重试测试
- `docs/`：研究论文和项目参考资料
- `experiments.db`：本地运行历史，不提交到 Git
- `tasks.json`：论文任务库（30 个任务）
- `scripts/analysis.py`：统计检验与论文表格生成脚本
- `scripts/calibration.py`、`scripts/calibration_report.py`：批量校准运行与按基线聚合报告
- `generators.py`：生成式谜题（GEN）的实例生成器与独立求解器自验
- `scripts/build_chain_tasks.py`：依赖链题目（CHAIN）构建与链结构自验
- `reports/`：分析报告输出目录

## 启动

macOS / Linux：

```bash
python3 server.py
```

Windows 可双击 `start.bat`，或在当前目录运行 `python server.py`。

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

## 论文任务库

`tasks.json` 内置 54 个任务：

- 代码修复（CR）：17 个，附带 `test_code` 可执行断言测试（`test_*` 函数），评分时真实执行
- 信息整合与规划（II）：14 个，多来源归纳、约束下方案设计与隐藏不可行识别
- 动态扰动（DT）：12 个，D1–D4 扰动在**执行中途真实注入**（初始提示不泄露扰动与成功标准）
- 依赖链修复（CHAIN / DCHAIN）：6 个，遮蔽链（前 bug 不修后 bug 不暴露，含无法启动的语法层）、乱序依赖、双链汇合、补偿性耦合（双错互消，修一端反而更糟）、中途需求变更，分层测试报告 `fixed_depth`（连续修复深度）作为过程指标
- 生成式多跳谜题（GEN）：4 个，每次运行由 `generators.py` 按随机种子生成全新实例（多文档针毡、逻辑网格、库存长链模拟、DAG 排期），平台持有唯一解并用 `exact_match` 评分器客观比对，彻底排除训练语料记忆

任务按角色分为：

- `core`：37 个高耦合核心判别任务，用于检验多 Agent 协作机制的真实增益
- `boundary`：14 个低耦合边界任务，用于检验“动态协作并非普遍最优”的边界条件（校准实验显示其对强模型已饱和，仅适合成本/开销对比）

难度分布：`hard` 37 个、`medium` 11 个、`easy` 3 个。

评估规则：

- 代码修复与依赖链任务：从最终答案提取实现代码（自动排除模型自带的测试块），在 `python -I` 隔离子进程中执行 `test_code`；依赖链任务额外报告 `fixed_depth`
- 生成式谜题：`exact_match` 评分器提取答案末尾的 `{"最终答案": ...}` JSON，与生成器持有的唯一解结构化比对（数字容差、映射按叶子字段计分、关键路径接受等价解）；有客观评分时 success 以客观结果为准
- 成功标准（success_criteria）与参考答案只提供给独立评估器，不出现在被测 Agent 的提示中
- 扰动任务的扰动内容在运行中途注入：B0/B1/B2 在候选产出后修订，B3 在流水线过半节点注入，B4 在辩论与动态重分配前注入

在实验配置中选择任务后，研究问题、耦合度与扰动条件会自动填充。也可以留空使用自定义问题。

## 校准与统计分析脚本

```bash
# B0/B4 等任意条件的批量校准（示例：B0 全部任务 ×3）
python3 scripts/calibration.py --repeats 3 --baseline B0
# 从 experiments.db 聚合生成按基线 × 角色分组的报告
python3 scripts/calibration_report.py
# 论文表格与统计检验
python3 scripts/analysis.py
```

## 批量运行与导出

- 页面中设置“批量重复运行次数”后点击“批量运行”，会创建多个真实运行任务
- 点击“导出记录”会下载完整 CSV（包含 task_id、success 等扩展字段）
- 服务端接口：`POST /api/runs/batch`、`GET /api/export?format=csv|json`、`GET /api/tasks`

## 统计检验与论文表格

```powershell
python scripts/analysis.py
```

脚本读取 `experiments.db` 中所有 `completed` 记录，自动生成：

- 表 5-1 风格的主结果表：Q、SR、Cost、Time、Stab CV
- 表 5-7 风格的任务质量 Q 统计检验：Kruskal–Wallis 与成对 Mann–Whitney U、效应量
- 分层分析：按角色（core / boundary）、任务类型（CR / II / DT）、难度（easy / medium / hard）
- Cost / Time 的组间 Kruskal–Wallis 检验

报告写入 `reports/analysis_tables.md`，并在终端输出。Rob / Adapt 需要专门的扰动前后对照实验，当前脚本不强行估计。
