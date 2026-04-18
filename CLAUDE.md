# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目简介

Harness-Stata 是一个 Stata 实证分析 Agent，通过显式状态机组织实证分析流程，解析用户的实证需求并调用 csmar_mcp 与 stata-executor-mcp 完成数据获取、清洗以及实证分析，并返回实证结果。

本期 MVP 为本地 CLI，Web 端后续迭代。

## 技术栈

- 运行时与编排：Python 3.12、langgraph、langchain、langchain-openai
- MCP 集成：langchain-mcp-adapters（通过 MCP 协议调用 packages/csmar-mcp 与 packages/stata-executor，禁止直接 import services 层）
- 数据处理：pandas
- CLI：typer
- LLM：OpenAI API（兼容协议，具体模型运行时配置）
- 测试与治理：pytest、pyright、import-linter、ruff、pre-commit

## 项目组织架构
若项目实际架构与此处文档架构不一致，应当立刻修正防止架构漂移
```
harness-stata/
├── src/harness_stata/            # 主应用（本仓库的核心）
│   ├── state.py                  # 共享 state 切片的 TypedDict 定义
│   ├── graph.py                  # 主图装配（仅 import nodes/）
│   ├── config.py                 # 配置
│   ├── cli.py                    # typer CLI 入口
│   ├── nodes/                    # 主图视角下的 8 个节点
│   │   ├── requirement_analysis.py
│   │   ├── model_construction.py
│   │   ├── data_probe.py         # 内部调用 subgraphs/probe_subgraph 工厂
│   │   ├── hitl.py
│   │   ├── data_download.py
│   │   ├── data_cleaning.py      # 内部调用 subgraphs/generic_react 工厂
│   │   ├── descriptive_stats.py  # 内部调用 subgraphs/generic_react 工厂
│   │   └── regression.py         # 内部调用 subgraphs/generic_react 工厂
│   ├── subgraphs/                # 可复用子图工厂（实现细节）
│   │   ├── generic_react.py      # build_react_subgraph(tools, prompt, max_iters)
│   │   └── probe_subgraph.py     # build_probe_subgraph(tools, per_variable_max_calls)
│   ├── prompts/                  # Markdown 格式 system prompt
│   │   └── __init__.py           # 提供 load_prompt(name)
│   └── clients/                  # 外部依赖统一入口（contextmanager 管理生命周期）
│       ├── csmar.py              # csmar-mcp 客户端适配
│       ├── stata.py              # stata-executor-mcp 客户端适配
│       └── llm.py                # OpenAI 兼容 LLM 客户端封装
├── packages/
│   ├── csmar-mcp/                # 独立 MCP 服务，主应用通过 MCP 协议调用
│   └── stata-executor/           # 同上
├── docs/
│   ├── empirical-analysis-workflow.md
│   └── state.md
└── tests/
```

## Session 流程

### 会话开始

1. 运行 scripts/init.py 跑一遍质量门禁并了解项目现状。
2. 在编写任何代码前，宣布将要处理的功能。

### 会话结束
- 完成任何文件变更后运行`.venv/Scripts/python.exe scripts/check.py`统一质量门禁。一次性跑完 pytest、ruff、pyright、import-linter、custom-lint 全部检查
- 完成任何实质进展后更新 `PROGRESS.md`：
  1. 推进"当前焦点"和"已完成"
  2. 从"下一步"移走已做完的项
  3. 发现/解决的卡点进"未解决/卡点"
  4. 某个 section 长期空着 → 删除该 section；需要时再加回来
- 完成一次可交付任务后，必须进行Git提交
