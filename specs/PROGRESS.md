# 项目进度

## 当前焦点

F20 — data_cleaning 节点从 pandas REPL 重构为 DuckDB SQL-first：LLM 不再裸写 pandas，而是对预注册的 `src_<source_table>` 视图写 SQL；节点 deterministic 导出 final_view 并自动 dump 所有中间 view 到 `_stage/` 供调试。

MVP 前 25 个 feature (F01-F25) 已 `passes=true`；F26（probe list_databases 预拉）与本次 F20 重构均已过 9/9 质量门禁。

## 当前上下文

<!-- 每次任务完成覆写此部分。保持简洁。 -->

- 本次会话重构 — F20 数据清洗节点改用 DuckDB SQL：
  - 动机：DataFrame 命令式清洗心智负担重、中间态无名难审计；换成 SQL + 命名 VIEW 后每一步都是可查询的声明式中间态。
  - 架构决策：
    - **D1 底座**：DuckDB 内存连接（`duckdb>=1.1.0`），源 CSV 走 `conn.read_csv(path).create_view(src_<source_table>)` 预注册；终产物由 node `conn.sql('SELECT * FROM "<final_view>"').write_csv(...)` 导出（路径不拼 SQL）。
    - **D2 工具粒度**：单一 `run_sql(query)`；SELECT/DESCRIBE 返回前 20 行 + 总行数，DDL/DML/SET 折叠为 "OK"，错误返回 "ERROR: ..." 字符串（不抛异常，由 ReAct 自行修复）。
    - **D3 预注入**：HumanMessage 中附 schema + 3 行样本，省掉 LLM 摸底回合。
    - **D4 契约变更**：终态 JSON 从 `{file_path, primary_key}` 改为 `{final_view, primary_key}`；LLM 只声明 view，COPY 由 node 执行。
    - **D5 中间产物**：node 在最终导出前扫描 main schema 下非 `src_` 前缀的所有 tables/views，best-effort dump 到 `<session>/_stage/<name>.csv`（含失败尝试），开发期调试用。
    - **D6 注入防御**：`_IDENT_RE = ^[A-Za-z_][A-Za-z0-9_]*$` 白名单校验 `source_table` 与 `final_view`；路径通过 DuckDB Python relation API 传入，不拼 SQL；`information_schema` 查询用 `?` 参数化。
    - **D7 xlsx 预占**：`_register_sources` 按 suffix 分支，`.xlsx/.xls` 本期 `NotImplementedError`。
  - 变更：
    - `src/harness_stata/nodes/data_cleaning.py`：重写为 DuckDB 版本，docstring 全中文。
    - `src/harness_stata/prompts/data_cleaning.md`：重写为 DuckDB SQL 数据工程师角色 + 新终态契约。
    - `src/harness_stata/config.py`：`Settings` 新增 `cleaning_coverage_threshold: float`，读 `.env` 的 `HARNESS_CLEANING_COVERAGE_THRESHOLD`（默认 0.8，边界 (0, 1]）。
    - `pyproject.toml`：加 `duckdb>=1.1.0`；`[tool.ruff.lint]` 忽略 `RUF001/RUF002/RUF003`（中文 docstring/注释里的全角标点为预期用法）。
    - `tests/nodes/test_data_cleaning.py`：fake subgraph 改为真实调用 `run_sql` 执行测试预设 SQL；新增中间产物 dump / final_view 缺失 / 非法 identifier / 非法 source_table / xlsx NotImplementedError 等测试。
  - 质量门禁 9/9 通过。

- 既往会话 bug fix — probe 子图 ReAct 上下文补充时间范围:
  - 根因:`_variable_react` 的 HumanMessage 仅携带变量定义 + 已购库清单,丢失 EmpiricalSpec 的
    `time_range_start` / `time_range_end` / `data_frequency` / `sample_scope`;Agent 调 csmar-mcp
    的 probe_query / 样本拉取类工具时不会传时间过滤,无法正确判断"目标时间范围下是否可得"
  - 变更:`src/harness_stata/subgraphs/probe_subgraph.py::_variable_react` HumanMessage 注入
    Sample scope / Time range / Data frequency 三行,放在变量定义与已购库清单之间
  - 未改 prompt、未改 ProbeState schema、未改 nodes/data_probe.py(spec 已通过 initial 传入)
  - 质量门禁 9/9 通过

- F26 — 数据探针 list_databases 缓存:
  - 根因:`csmar_list_databases` 是零参数确定性枚举,当前由每个变量的 ReAct 单独调用,浪费 1 轮 per_variable_max_calls 预算;csmar-mcp 服务端已有 30 min SQLite cache 但省不了 LLM token 与轮次
  - 架构决策:caller-side 预拉(不加 preamble 节点保持 subgraph 3 节点不漂移);存会话内存 ProbeState.available_databases (str);`data_probe.py` 节点过滤 + `build_probe_subgraph` 防御性再过滤
  - 变更:
    - `src/harness_stata/subgraphs/probe_subgraph.py`:ProbeState 增 `available_databases: str`;`bound_tools` 过滤 `csmar_list_databases` 且空列表时 ValueError;`_variable_react` HumanMessage 追加 "Purchased databases: ..." 块 + "Do NOT call any list_databases tool" 指令
    - `src/harness_stata/nodes/data_probe.py`:进入 subgraph 前 `list_tool.ainvoke({})` 拉一次,`str(raw)` 注入 initial state;工具缺失硬抛 RuntimeError
    - `src/harness_stata/prompts/data_probe.md`:工具说明段移除 "列举数据库";探测策略步骤 1 改为 "从用户消息中列出的已购数据库清单里选"
    - `tests/nodes/test_data_probe.py`:`_patch_csmar` 默认带 list_databases mock(return '..."CSMAR", "RESSET"..."');新增 `test_data_probe_prefetches_list_databases_once` 与 `test_data_probe_raises_when_list_databases_tool_missing`
    - `tests/subgraphs/test_probe_subgraph.py`:新增 `TestAvailableDatabasesInjection` (注入/回落两例) 与 `TestToolFiltering` (bind_tools 参数不含 list_databases / 仅 list_databases 时 ValueError)
    - `CLAUDE.md` 技术栈段追加 SqliteSaver + DuckDB 两行战略决策
    - `specs/feature_list.json` 新增 F26 (depends_on: F15, F25)
  - 设计取舍:
    - **D1 存储层**:ProbeState 会话内存 + csmar-mcp TTL 兜底(拒绝在主应用引入 PG 二级缓存,避免 ownership 分裂)
    - **D2 拉取位置**:caller-side (data_probe.py) 而非 subgraph preamble — 保持 subgraph 3 节点 FSM 不漂移 `docs/empirical-analysis-workflow.md`
    - **D3 字段类型**:`available_databases: str` 原始工具输出字符串(YAGNI,不做 JSON 解析;LLM 能直接读)
    - **D4 工具过滤**:caller 过滤 + subgraph 防御性再过滤(双保险)
    - **D5 失败语义**:list_databases 调用失败直接 RuntimeError(CSMAR 不可达时降级无意义)

## 下一步

- **MVP 本地 CLI 已完成**。所有 25 个 feature (F01-F25) passes=true。下一阶段方向由用户决定:
  - (a) 真实端到端联调:启动 csmar-mcp / stata-executor-mcp 服务,配 DashScope API key,跑真实 UserRequest 验证 LLM + MCP 链路
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
- 主 `.venv` 缺 `prettytable` (csmarapi 的运行时依赖),scripts/check.py 已 9/9 通过但手动跑 csmar-mcp 子包单测会 ImportError
- `packages/stata-executor/` 的 ruff/pyright 收口尚未做 (类比 csmar-mcp 已完成的技术债)
- `subgraphs/probe_subgraph.py` 当前 487 行触发 check_file_size warn,下一次实质性扩展前应拆出 `_probe_helpers.py`
- F20 数据清洗节点已从 pandas REPL 重构为 DuckDB SQL；覆盖率阈值已提到 `Settings.cleaning_coverage_threshold`（不再硬编码）
- F22 `actual_sign` 由 LLM 从 log 抽取,节点不 parse log;若发现 LLM 误读可改为节点端正则抽取 Stata 回归表格
- F22/F21 要求 do 文件内部用 `log using` 显式指定绝对路径,否则节点在 `_assert_file_exists` 阶段 raise (prompt 已强制约束)
- `scripts/check.py` 的 ruff format 仅检查 `src/harness_stata`,不覆盖 `tests/` 与 `scripts/`,导致测试代码可能存在格式漂移
- F23 用 `InMemorySaver` 作为 MVP 本地单进程 checkpointer;若后续需要跨进程 resume 可替换为 SqliteSaver
- F24 CLI 为单进程阻塞模式,不支持长会话/Web 场景 (与 D2 决策一致);Web 迭代需要重新设计 HITL 适配层
