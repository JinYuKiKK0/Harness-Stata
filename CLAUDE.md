# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目简介

Harness-Stata 是一个 Stata 实证分析 Agent，通过显式状态机组织实证分析流程，解析用户的实证需求并调用 CSMAR-Data-MCP 与 Stata-Executor-MCP 完成数据获取、清洗以及实证分析，并返回实证结果。

本期 MVP 为本地 CLI，Web 端后续迭代。

## 技术栈

- 运行时与编排：Python 3.12、langgraph、langchain、langchain-openai
- MCP 集成：langchain-mcp-adapters（通过 MCP 协议调用 csmar-mcp 与 stata-executor submodule，禁止直接 import services 层）
- 数据处理：pandas
- CLI：typer
- LLM：OpenAI API（兼容协议，具体模型运行时配置）
- 测试与治理：pytest、pyright、import-linter、ruff、pre-commit
- 包管理：UV
- 状态持久化：MVP 本地 CLI 使用 LangGraph InMemorySaver；Web 化后切换 PostgresSaver
- 数据清洗引擎：DuckDB（将散落 csv/xlsx 挂载为表，库内 SQL 声明式清洗，导出 csv 给 Stata）
- 可观测性：LangSmith tracing

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
│   ├── subgraphs/                # 可复用子图工厂（实现细节）
│   │   └── probe/                # data_probe 子图（节点级 colocation + 共享 pure 纯逻辑）
│   ├── prompts/                  # Markdown 格式 system prompt
│   └── clients/                  # 外部依赖统一入口（contextmanager 管理生命周期）
│       ├── csmar.py              # CSMAR-Data-MCP 客户端适配
│       ├── stata.py              # Stata-Executor-MCP 客户端适配
│       ├── mcp.py                # MCP 工具调用 helper（structured_content 解码）
│       └── llm.py                # LLM 客户端封装
├── csmar-mcp/                    # 外部 MCP submodule，主应用通过 stdio MCP 协议调用
├── stata-executor/               # 外部 MCP submodule，主应用通过 stdio MCP 协议调用
├── docs/
│   ├── empirical-analysis-workflow.md # 关键文档：实证工作流链路设计文档，包含状态机的节点设计、拓扑结构以及节点输入输出切片
│   ├── state.md # 各节点输入输出的切片schema结构和字段定义
│   └── pitfalls.md # 三方依赖踩坑/调试卡点/代码债知识库（Claude Code 维护）
└── tests/
```

## Session 流程

### 会话开始

1. 读取 `git log` 与 `specs/PROGRESS.md`，结合用户本次诉求确认目标。
2. 在编写任何代码前，宣布本次任务的目标。

### 会话结束
- 完成任何文件变更后运行`uv run scripts/check.py`统一质量门禁。一次性跑完 pytest、ruff lint、ruff format、pyright、import-linter、custom-lint 全部检查。**调用时不加任何 `| tail` 或 `| head` 截断，直接读取完整输出**
- 完成任何实质进展后更新 `specs/PROGRESS.md`：
  1. 推进"当前焦点"和"当前上下文"
  2. 从"下一步"移走已做完的项
- 完成一次可交付任务后，必须进行Git提交

### 维护 `docs/pitfalls.md`

`docs/pitfalls.md` 是项目的踩坑/卡点知识库,由 Claude Code 主动维护。

**何时查阅**:
- 涉及三方依赖(DashScope / langchain-mcp-adapters / MCP / LangGraph 等)行为时,先扫一遍 `docs/pitfalls.md` 对应章节,避免重复踩坑。

**何时写入**:
- 用户反馈调试卡点/故障并讨论出根因后 → 增加 `[调试卡点]` 条目(已解决勾 `[x]`,未解决留 `[ ]` 且方案段空)。
- 探索代码时发现技术债、错误、隐患 → 增加 `[代码债]` 条目(`问题` + `影响` 两段)。
- 撞到三方依赖反直觉行为 → 增加 `[依赖坑]` 条目。

