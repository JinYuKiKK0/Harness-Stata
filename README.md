# Harness-Stata

基于 LangGraph 的 Stata 实证分析 Agent。通过显式状态机组织实证流程，解析用户需求并调用 CSMAR-Data-MCP 与 Stata-Executor-MCP 完成数据获取、清洗与回归分析，返回实证结果。


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
- **观测 / 调试**：LangSmith Studio（本地 Agent Server）
- **LLM**：通过 `langchain-openai` 的 `ChatOpenAI` 调用 DashScope/OpenAI 兼容接口
- **MCP 集成**：`langchain-mcp-adapters`，经 stdio MCP 协议调用 `csmar-mcp` 与 `stata-executor` submodule
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
├── subgraphs/       # 数据探针子图工厂
│   └── probe_subgraph.py  # build_probe_subgraph(tools, per_variable_max_calls)
├── prompts/         # Markdown system prompts
└── clients/         # 外部依赖统一入口（csmar / stata / llm）

csmar-mcp/          # CSMAR 数据获取 MCP submodule
stata-executor/     # Stata 执行 MCP submodule
```

### 分层约束（import-linter 强制）

`cli > graph > nodes > subgraphs > clients`，低层不得反向依赖高层。

- `graph.py` 不得直接 import `subgraphs/`
- `subgraphs/` 不得 import `nodes/`
- `nodes/` 与 `subgraphs/` 不得直连 `csmar_mcp` / `stata_executor` / `csmarapi`，必须经 `clients/` 的 MCP 协议入口
- 仅 `clients/llm.py` 可 import `langchain_openai`

## 快速开始

### 1. 安装依赖

```bash
uv sync --all-extras
```

### 2. 配置环境

在项目根创建 `.env`：

```
必填
DASHSCOPE_API_KEY=
LLM_BASE_URL=https://your-openai-compatible-endpoint/v1
CSMAR_ACCOUNT=      # CSMAR账号
CSMAR_PASSWORD=     # CSMAR密码
STATA_EXECUTOR_STATA_EXECUTABLE='C:/Program Files/Stata17/StataMP-64.exe'   Stata执行程序路径
选填
STATA_EXECUTOR_EDITION=mp   # Stata版本
LANGSMITH_API_KEY=   # LangSmith key
LLM_MODEL=      # 模型编号 
LLM_TEMPERATURE=0.5     # 模型温度
HARNESS_DOWNLOADS_ROOT=/downloads   # CSMAR数据下载解压路径
HARNESS_PER_VARIABLE_MAX_CALLS=6    # 单个变量CSMAR api最大调用次数，避免Agent无限调用触发账号日限流

```

> 配置仅从 `.env` 注入，不从系统环境变量回退读取。
> 若只想使用 Studio 本地调试界面而不上传 tracing，可额外设置 `LANGSMITH_TRACING=false`。

### 3. 质量门禁

```bash
uv run scripts/check.py
```

一次跑完 pytest、ruff、pyright、import-linter、custom-lint。


### 4. 连接 LangSmith Studio

项目已包含 Studio 所需的 `langgraph.json`，指向 `src/harness_stata/studio.py` 暴露的编译图对象。

启动本地 Agent Server：

```bash
uv run langgraph dev
```

## 开发约定

- 会话开始：运行 `scripts/init.py` 摸清现状，从 `specs/feature_list.json` 选择 `passes:false` 且依赖全绿的 feature。
- 会话结束：`scripts/check.py` 5/5 全过后翻转 `passes`，更新 `specs/PROGRESS.md`，提交 git。
- Feature 的结构性变更（id/description/steps/depends_on）需用户确认；`passes` 翻转无需确认。
- 详见 [`CLAUDE.md`](CLAUDE.md)。
