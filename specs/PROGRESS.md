# 项目进度

## 当前焦点

F22 完成: `nodes/regression.py` 作为终端产物节点,复用 F19 `build_react_subgraph` 驱动 ReAct 循环,绑定 `clients/stata.get_stata_tools()` 产出的 stata MCP 工具集(`doctor` / `run_do` / `run_inline`),让 LLM 按 `ModelPlan.model_type/equation` 自由编写并执行 do 文件,产出 `<session_dir>/regression.do` + `regression.log`;节点负责 JSON 解析 + do/log 存在性校验 + 对照 `core_hypothesis.expected_sign` 组装结构化 `SignCheck`,符号不一致不 raise 仅写入 `consistent=False` 并正常写 `workflow_status="success"`。F23 主图装配的剩余阻塞只剩 F21。

## 当前上下文

<!-- 每个会话覆盖此部分。保持简洁。 -->

- F22 完成:
  - `src/harness_stata/prompts/regression.md` 撰写完整 system prompt(角色 + 任务上下文 + 可用工具 + 工作流程建议 + 终止契约);终止契约要求 LLM 最后一条消息不再发起 tool_call,content 直接输出 JSON `{"do_file_path", "log_file_path", "actual_sign", "summary"}`,actual_sign 严格取 `+` / `-` / `0` 三者之一
  - `src/harness_stata/nodes/regression.py` (226 行, <300 warn 阈值):
    - 模块常量:`_MAX_ITERATIONS = 20` / `_DO_FILENAME = "regression.do"` / `_LOG_FILENAME = "regression.log"` / `_VALID_ACTUAL_SIGNS = {"+", "-", "0"}` / `_FENCE_RE`(复用 F20 markdown 围栏剥离正则)
    - `_validate` 校验三个必备 state 切片(merged_dataset / model_plan / empirical_spec)
    - `_derive_session_dir(merged_path)`: `Path(merged_path).resolve().parent` 复用 F20 session_dir 约定,零新增 config
    - `_build_human_prompt`: 拼接研究上下文 + 模型方程 + 核心假设(`variable_name` + `expected_sign` + `rationale`)+ merged 元信息(file_path/row_count/columns/warnings)+ 输出 do/log 绝对路径
    - `_extract_final_json` / `_require_str` / `_validate_payload`: 解析并严格校验 4 个字段,actual_sign 不在集合内直接 raise
    - `_assert_file_exists`: do 或 log 文件不存在即 raise(LLM 声称但未落盘)
    - `_compute_sign_check`: `consistent = (expected == "ambiguous") or (expected == actual_sign)`
    - 主函数 `async def regression(state) -> dict[str, Any]`: 验证 → `async with get_stata_tools() as tools` → 构造子图 → `await subgraph.ainvoke` → 终止校验(messages 非空 + AIMessage + tool_calls 为空) → 解析 JSON → 校验字段 → 校验 do/log 存在 → 组装 `RegressionResult` + `workflow_status="success"`
  - `tests/nodes/test_regression.py` (7 用例全过):
    - 3 条 success (sign_consistent / sign_inconsistent_does_not_raise / sign_ambiguous_always_consistent)
    - 4 条 failure (react_truncation / invalid_actual_sign / log_file_missing / missing_merged_dataset)
    - Mock 方案:patch `harness_stata.nodes.regression.build_react_subgraph` 返回 MagicMock 的 `.ainvoke` AsyncMock + 同时 patch `get_stata_tools` 为返回 `@asynccontextmanager` 产出空 list(子图被 mock 掉,工具不会被真调);测试在 `tmp_path` 下预写 `merged.csv` + `regression.do` + `regression.log` 让 `_derive_session_dir` 推导的路径校验通过
    - async 测试统一 `asyncio.run(regression(state))` 包同步,延续 F18/F20 约定
  - 设计取舍 (plan 拍板):
    - **D1 do-file 生成**:LLM 自由生成(vs 节点硬编码模板);prompt 内要求 LLM 用 `file write` / `run_inline` 落盘 do 再 `run_do` 执行
    - **D2 符号不一致分层**:不 raise,只写 `sign_check.consistent=False`;符号不一致本身是有价值的实证结论,不应视为错误
    - **D3 产物位置**:复用 F20 session_dir(从 `merged_dataset.file_path` 的 parent 推导),`<session_dir>/regression.do` + `regression.log`,零 config 新增
    - **D4 actual_sign 三值**:`+` / `-` / `0` 三选一(0 表示系数约等于 0 或不显著);节点严格校验
    - **D5 max_iterations = 20**:F20=30 做跨表合并更复杂,回归只做一次建模 + 可能几次 doctor/run_inline 试错,20 够用
  - pyright strict 处理:复用 F20 模式对 `subgraph.ainvoke` 加 `# pyright: ignore[reportUnknownMemberType]`,对 `AIMessage.content` 用 `cast("Any", ...)` + isinstance 收敛;WorkflowState 切片访问统一 `# pyright: ignore[reportTypedDictNotRequiredAccess]`
- 质量门禁 9/9 通过 (全仓 68/68 pytest, 新增 7 用例)
- 先前 F20 / F25 / F18 完成内容见 git log 857d865 / d11faa1 / b7c300b

## 下一步

1. F21: 描述性统计节点 (借 F19 generic_react + stata-executor,读 F20 产出的 MergedDataset;可直接复刻 F22 的 stata 工具绑定与 ReAct 骨架,是 F22 的"简化版"终止契约——无符号校验,只返 do/log/summary)
2. F23: 主图装配 (等 F21 就绪;F22 + F25 已完成; 需绑定 checkpointer 以支持 F17 interrupt)
3. F24: CLI 入口 (等 F23 就绪, 需用 asyncio.run + graph.ainvoke 适配 F18/F20/F22 引入的 async 模式, 并处理 HITL interrupt resume)

## 未解决/卡点

- pyright strict 下 ChatTongyi 缺少部分类型桩, clients/llm.py 中有 type: ignore 注释
- WorkflowState total=False 导致所有 state key 访问需要 type: ignore[reportTypedDictNotRequiredAccess]
- langgraph 1.1.6 缺少公开类型桩, `StateGraph.add_node` / `.compile()`, `BaseChatModel.bind_tools()` / `.with_structured_output()`, `BaseTool.invoke()` / `.ainvoke()`, `Runnable.invoke()` 被 pyright strict 判 reportUnknownMemberType, 统一通过 `# pyright: ignore[reportUnknownMemberType]` 压制
- `@tool` 装饰器在 pyright strict 下也需 `# pyright: ignore[reportUntypedFunctionDecorator, reportUnknownVariableType, reportUnknownArgumentType]` 压制(F20 引入的新模式,后续节点若再写 inline @tool 沿用)
- pandas 在 pyright strict 下大量 reportUnknownMemberType (`.read_csv` / `.duplicated().sum()` / `.notna().sum()` / `.columns` 遍历),F20 采用 `cast("Any", ...)` + `# pyright: ignore` 的组合,后续 F21/F22 若直接用 pandas 可沿用
- ruff RUF001/RUF002 对中文全角标点与同形希腊字母的检查: docstring 与 Field description 中避免使用全角标点 (逗号/句号/括号等) 与 α/β/γ
- 主 `.venv` 缺 `prettytable` (csmarapi 的运行时依赖): `scripts/check.py` 已 9/9 通过, 但若要手动跑 csmar-mcp 子包单元测试会 ImportError; 修复方案待定
- `packages/stata-executor/` 的 ruff/pyright 收口尚未做 (类比 csmar-mcp 已完成的技术债), 留给独立会话
- `subgraphs/probe_subgraph.py` 当前 487 行触发 check_file_size warn (>300, <500 fail). 下一次本文件实质性扩展前应拆出 `_probe_helpers.py`
- F18 + F20 + F22 引入的 async 节点模式需在 F24 CLI 统一入口用 `asyncio.run(graph.ainvoke(...))`,并在 F21 节点保持一致的 async def 签名
- F20 `run_python` 工具当前直接用 `exec` + 闭包 namespace,无沙箱/子进程隔离(MVP 本地单机可接受);若将来走服务端需替换为子进程或 Docker,但不在当前范围
- F20 `_COVERAGE_THRESHOLD = 0.8` 当前硬编码,若需按场景调整后续再提到 config
- F22 的 `actual_sign` 由 LLM 自己从 log 中抽取核心系数正负填回,节点不独立 parse log;MVP 信任 LLM,未来若发现 LLM 误读可改为节点端正则抽取 Stata 回归表格
- F22 要求 do 文件内部用 `log using` 显式指定绝对路径;如果 LLM 忘写 `log using`,节点在 `_assert_file_exists(log_file_path)` 阶段 raise——prompt 已强制约束此点
