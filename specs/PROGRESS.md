# 项目进度

## 当前焦点

F27 MCP 子模块迁移：移除主仓库内旧 `packages/csmar-mcp` / `packages/stata-executor` 源码，改用 `packages/CSMAR-Data-MCP` 与 `packages/Stata-Executor-MCP` 两个 git submodule；同步修正主项目 uv workspace、MCP 客户端说明、质量门禁路径与项目文档。

## 当前上下文

<!-- 每次任务完成覆写此部分，删除之前会话的内容。保持简洁。 -->

- 本次会话 — MCP 子模块迁移：
  - `packages/CSMAR-Data-MCP` 已本地提交 `1035a64`；ruff check、ruff format、pyright 通过。
  - `packages/Stata-Executor-MCP` 已本地提交 `6b63709`；保留既有 ruff/pytest 技术债，暂不纳入主仓库质量门禁。
  - 主仓库新增 `.gitmodules`，删除旧 MCP 源码目录，uv workspace 指向两个新 submodule。
  - 主仓库 `uv sync --extra dev` 已确认从新 submodule 安装 `csmar-mcp` 与 `stata-executor`；`uv run scripts/check.py` 9/9 通过。
  - 推送两个子仓库时当前环境缺少 GitHub HTTPS 凭据，子仓库 commit 仍需在有凭据环境补推。

## 下一步

- 完成 F27 收尾：
  - 在有 GitHub 凭据的环境推送 `packages/CSMAR-Data-MCP` 与 `packages/Stata-Executor-MCP` 的本地 commits。
  - 推送后重新运行 `git submodule status`，确认主仓库 submodule 指针均可从远端拉取。
- **MVP 本地 CLI 已完成**。所有 25+F26 个 feature passes=true，ReAct 子图已迁移到 `create_agent`。下一阶段方向由用户决定:
  - (a) 真实端到端联调:启动 CSMAR-Data-MCP / Stata-Executor-MCP 服务,配 DashScope API key,跑真实 UserRequest 验证 LLM + MCP 链路
  - (b) 技术债清理:probe_subgraph.py 487 行拆分 / stata-executor ruff+pyright 收口 / tests/ 纳入 ruff format 门禁
  - (c) Web 迭代启动:把 CLI 换成 HTTP/WS 适配层,checkpointer 升级为 SqliteSaver 以跨进程 resume
  - (d) 功能扩展:稳健性回归 / 异质性分析 / 可视化等非 MVP feature

## 未解决/卡点

- pyright strict 下 ChatTongyi 缺少部分类型桩, clients/llm.py 中有 type: ignore 注释
- WorkflowState total=False 导致所有 state key 访问需要 type: ignore[reportTypedDictNotRequiredAccess]
- langgraph 1.1.6 缺少公开类型桩, `StateGraph.add_node` / `.add_edge` / `.compile()`, `BaseChatModel.bind_tools()` / `.with_structured_output()`, `BaseTool.invoke()` / `.ainvoke()`, `Runnable.invoke()` / `ainvoke` / `aget_state` 被 pyright strict 判 reportUnknownMemberType, 统一通过 `# pyright: ignore[reportUnknownMemberType]` 压制
- `@tool` 装饰器在 pyright strict 下需 `# pyright: ignore[reportUntypedFunctionDecorator, reportUnknownVariableType, reportUnknownArgumentType]` 压制
- pandas 在 pyright strict 下大量 reportUnknownMemberType,F20 采用 `cast("Any", ...)` + `# pyright: ignore` 组合
- ruff RUF001/RUF002/RUF003 已在 pyproject 中 ignore（中文 docstring/注释采用全角标点为项目约定）；但 Field description 仍要避免同形希腊字母 α/β/γ
- 当前环境缺少 GitHub HTTPS 凭据，两个 MCP 子仓库 push 失败：`could not read Username for 'https://github.com'`
- `packages/Stata-Executor-MCP/` 的 ruff/pyright 收口尚未做；临时 ruff 检查暴露 import 排序、相对导入、SIM103 等既有问题
- `packages/Stata-Executor-MCP/` 临时 pytest 在当前环境触发 pytest capture 临时文件 `FileNotFoundError`，需后续在子仓库单独定位
- `subgraphs/probe_subgraph.py` 当前 487 行触发 check_file_size warn,下一次实质性扩展前应拆出 `_probe_helpers.py`
- F20 数据清洗节点已从 pandas REPL 重构为 DuckDB SQL；覆盖率阈值已提到 `Settings.cleaning_coverage_threshold`（不再硬编码）
- F22 `actual_sign` 由 LLM 从 log 抽取,节点不 parse log;若发现 LLM 误读可改为节点端正则抽取 Stata 回归表格
- F22/F21 要求 do 文件内部用 `log using` 显式指定绝对路径,否则节点在 `_assert_file_exists` 阶段 raise (prompt 已强制约束)
- `scripts/check.py` 的 ruff format 仅检查 `src/harness_stata`,不覆盖 `tests/` 与 `scripts/`,导致测试代码可能存在格式漂移
- F23 用 `InMemorySaver` 作为 MVP 本地单进程 checkpointer;若后续需要跨进程 resume 可替换为 SqliteSaver
- F24 CLI 为单进程阻塞模式,不支持长会话/Web 场景 (与 D2 决策一致);Web 迭代需要重新设计 HITL 适配层
