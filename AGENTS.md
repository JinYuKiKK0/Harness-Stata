# AGENTS.md

This file provides guidance to Codex (Codex.ai/code) when working with code in this repository.

## 项目简介

Harness-Stata 是一个 Stata 实证分析 Agent，通过显式状态机组织实证分析流程，解析用户的实证需求并调用 CSMAR-Data-MCP 与 Stata-Executor-MCP 完成数据获取、清洗以及实证分析，并返回实证结果。

本期 MVP 为本地 CLI，Web 端后续迭代。

## 技术栈

- 运行时与编排：Python 3.12、langgraph、langchain、langchain-openai
- MCP 集成：langchain-mcp-adapters（通过 MCP 协议调用 packages/CSMAR-Data-MCP 与 packages/Stata-Executor-MCP submodule，禁止直接 import services 层）
- 数据处理：pandas
- CLI：typer
- LLM：OpenAI API（兼容协议，具体模型运行时配置）
- 测试与治理：pytest、pyright、import-linter、ruff、pre-commit
- 包管理：UV
- 状态持久化：LangGraph SqliteSaver（MVP 阶段）；Web 化后切换 PostgresSaver
- 数据清洗引擎：DuckDB（将散落 csv/xlsx 挂载为表，库内 SQL 声明式清洗，导出 csv 给 Stata）

## 项目组织架构
若项目实际架构与此处文档架构不一致，应当立刻修正防止架构漂移
```
harness-stata/
├── src/harness_stata/            # 主应用（本仓库的核心）
│   ├── state.py                  # 共享 state 切片的 TypedDict 定义
│   ├── graph.py                  # 主图装配（仅 import nodes/）
│   ├── config.py                 # 配置
│   ├── cli.py                    # typer CLI 入口
│   ├── __main__.py               # python -m harness_stata 入口
│   ├── studio.py                 # LangSmith Studio / langgraph dev 入口
│   ├── nodes/                    # 主图视角下的 8 个节点
│   │   ├── _writes.py            # @writes_to 装饰器，单切片节点返回 bare slice 的包装
│   │   ├── requirement_analysis.py
│   │   ├── model_construction.py
│   │   ├── data_probe.py         # 内部调用 subgraphs/probe_subgraph 工厂
│   │   ├── hitl.py
│   │   ├── data_download.py
│   │   ├── data_cleaning.py      # 内联 create_agent（DuckDB SQL-first）
│   │   ├── descriptive_stats.py  # 内联 create_agent
│   │   └── regression.py         # 内联 create_agent
│   ├── subgraphs/                # 可复用子图工厂（实现细节）
│   │   └── probe_subgraph.py     # build_probe_subgraph(tools, per_variable_max_calls)
│   ├── prompts/                  # Markdown 格式 system prompt
│   │   └── __init__.py           # 提供 load_prompt(name)
│   └── clients/                  # 外部依赖统一入口（contextmanager 管理生命周期）
│       ├── _mcp_interceptors.py  # MCP 拦截器
│       ├── csmar.py              # CSMAR-Data-MCP 客户端适配
│       ├── stata.py              # Stata-Executor-MCP 客户端适配
│       └── llm.py                # LLM 客户端封装
├── packages/
│   ├── CSMAR-Data-MCP/           # 外部 MCP submodule，主应用通过 stdio MCP 协议调用
│   └── Stata-Executor-MCP/       # 外部 MCP submodule，主应用通过 stdio MCP 协议调用
├── docs/
│   ├── empirical-analysis-workflow.md # 关键文档：实证工作流链路设计文档，包含状态机的节点设计、拓扑结构以及节点输入输出切片
│   └── state.md # 各节点输入输出的切片schema结构和字段定义
└── tests/
```

## Session 流程

### 会话开始

1. 运行 uv run scripts/init.py 跑一遍质量门禁并了解项目现状**调用时不加任何 `| tail` 或 `| head` 截断，直接读取完整输出**。
2. 读取 `specs/PROGRESS.md`和`specs/feature_list.json`，挑选一个 `passes:false` 且 `depends_on` 全部已 `passes:true` 的 feature 作为本次会话目标。若多项可选，结合 `specs/PROGRESS.md` 当前焦点与 MVP 价值推断当前最重要者。
3. 在编写任何代码前，宣布将要处理的 feature id 与目标。

### 会话结束
- 完成任何文件变更后运行`uv run scripts/check.py`统一质量门禁。一次性跑完 pytest、ruff、pyright、import-linter、custom-lint 全部检查。**调用时不加任何 `| tail` 或 `| head` 截断，直接读取完整输出**
- 自检本次目标 feature 的 `steps` 全部走通且 `scripts/check.py` 5/5 通过后，将该 feature 的 `passes` 改为 true（`passes` 翻转无需用户确认）
- 完成任何实质进展后更新 `specs/PROGRESS.md`：
  1. 推进"当前焦点"和"当前上下文"
  2. 从"下一步"移走已做完的项
  3. 发现/解决的卡点进"未解决/卡点"
  4. 某个 section 长期空着 → 删除该 section；需要时再加回来
- 完成一次可交付任务后，必须进行Git提交

## 测试约定

本项目是 Agent 项目，LLM 输出不稳定，mock 模型输出的测试是"先射箭再画靶子"——只证明"如果模型恰好按预期输出，代码能正确处理"，不证明端到端链路正确。

**有价值的测试**：
- 纯逻辑：路由函数、格式化辅助、输入校验，完全不涉及 LLM
- 确定性后处理：mock 仅用于驱动状态机到达特定分支，被测逻辑本身是确定性代码（sign_check 计算、manifest 合并、hard/soft 路由、文件存在性校验）
- 真实副作用：mock agent 但执行真实 DuckDB SQL / 文件系统操作

**禁止写的测试**：mock LLM / create_agent 返回预设的"正确"结构化输出，然后验证节点解包逻辑——这等价于不测试。

### feature 增改约定

`specs/feature_list.json` 由 Codex 主导维护。实现过程中发现需求遗漏、需要拆分或合并 feature 时，由 Codex 主动提议（说明动因与建议的 id/description/steps/depends_on），用户确认后才能修改 feature 的结构性内容。已存在的 `id` 永不重排（保证 depends_on 引用稳定），新增 feature 取递增编号。`passes` 字段的翻转不属于结构性修改，无需确认。
