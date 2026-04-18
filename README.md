# Harness-Stata

基于 LangGraph 的 Stata 实证分析 Agent。通过显式状态机组织实证流程，解析用户需求并调用 csmar-mcp 与 stata-executor-mcp 完成数据获取、清洗与回归分析，返回实证结果。


## 工作流总览

8 节点线性主图，节点间仅通过共享状态切片耦合：

```
需求解析 → 模型与基准线构建 → 数据探针 ─┬─ hard_failure → END
                                      └─ success → HITL ─┬─ rejected → END
                                                         └─ approved → 数据批量获取
                                                                     → 数据清洗
                                                                     → 描述性统计
                                                                     → 基准回归 → END
```

| 节点             | 形态      | 输出切片                                   |
| ---------------- | --------- | ------------------------------------------ |
| 需求解析         | 单轮 LLM  | `EmpiricalSpec`                            |
| 模型与基准线构建 | 单轮 LLM  | `ModelPlan`                                |
| 数据探针         | ReAct LLM | `ProbeReport` + `DownloadManifest`         |
| HITL             | 纯代码    | `hitl_decision`（langgraph interrupt）     |
| 数据批量获取     | 纯代码    | `DownloadedFiles`                          |
| 数据清洗         | ReAct LLM | `MergedDataset`（单一分析长表）            |
| 描述性统计       | ReAct LLM | `DescStatsReport`                          |
| 基准回归         | ReAct LLM | `RegressionResult`（含预期符号一致性校验） |

详细设计见 [`docs/empirical-analysis-workflow.md`](docs/empirical-analysis-workflow.md)，状态切片见 [`docs/state.md`](docs/state.md)。

## 技术栈

- **运行时**：Python 3.12
- **编排**：langgraph、langchain、langchain-core
- **LLM**：DashScope ChatTongyi（qwen-plus），通过 `langchain-community`
- **MCP 集成**：`langchain-mcp-adapters`，经 MCP 协议调用 `packages/csmar-mcp` 与 `packages/stata-executor`
- **数据处理**：pandas
- **CLI**：typer
- **包管理**：UV
- **治理**：pytest、pyright（strict）、ruff、import-linter、pre-commit

## 目录结构

```
src/harness_stata/
├── state.py         # 共享 state 切片的 TypedDict
├── graph.py         # 主图装配（仅 import nodes/）
├── config.py        # 配置
├── cli.py           # typer CLI 入口
├── nodes/           # 8 个主图节点
├── subgraphs/       # 可复用 ReAct 子图工厂
│   ├── generic_react.py   # build_react_subgraph(tools, prompt, max_iters)
│   └── probe_subgraph.py  # build_probe_subgraph(tools, per_variable_max_calls)
├── prompts/         # Markdown system prompts
└── clients/         # 外部依赖统一入口（csmar / stata / llm）

packages/
├── csmar-mcp/        # CSMAR 数据获取 MCP 服务
└── stata-executor/   # Stata 执行 MCP 服务
```

### 分层约束（import-linter 强制）

`cli > graph > nodes > subgraphs > clients`，低层不得反向依赖高层。

- `graph.py` 不得直接 import `subgraphs/`
- `subgraphs/` 不得 import `nodes/`
- `nodes/` 与 `subgraphs/` 不得直连 `csmar_mcp` / `stata_executor` / `csmarapi`，必须经 `clients/` 的 MCP 协议入口
- 仅 `clients/llm.py` 可 import `langchain_community`

## 快速开始

### 1. 安装依赖

```bash
uv sync --all-extras
```

### 2. 配置环境

在项目根创建 `.env`：

```
DASHSCOPE_API_KEY=sk-xxx
```

> 配置仅从 `.env` 注入，不从系统环境变量回退读取。

### 3. 质量门禁

```bash
.venv/Scripts/python.exe scripts/check.py
```

一次跑完 pytest、ruff、pyright、import-linter、custom-lint。

### 4. 运行 CLI

```bash
harness-stata ...
```

> CLI 入口（F24）尚未落地，命令签名待定。

## 开发约定

- 会话开始：运行 `scripts/init.py` 摸清现状，从 `specs/feature_list.json` 选择 `passes:false` 且依赖全绿的 feature。
- 会话结束：`scripts/check.py` 5/5 全过后翻转 `passes`，更新 `specs/PROGRESS.md`，提交 git。
- Feature 的结构性变更（id/description/steps/depends_on）需用户确认；`passes` 翻转无需确认。
- 详见 [`CLAUDE.md`](CLAUDE.md)。
