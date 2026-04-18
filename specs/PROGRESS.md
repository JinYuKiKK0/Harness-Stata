# 项目进度

## 当前焦点

F20 完成: `nodes/data_cleaning.py` 作为第二个 async 节点,借 F19 `build_react_subgraph` 驱动 ReAct 循环,绑定单一 `run_python` REPL 工具(持久 namespace 预置 `pd`/`Path`),让 LLM 完成跨表主键对齐 + 宽长转换 + snake_case 列名规范,产出单一 `merged.csv` 到 `<session_dir>/merged.csv`;节点本身负责 JSON 解析 + 分层 post-condition(主键重复 raise、覆盖率<0.8 写入新字段 `MergedDataset.warnings`)。F23 主图装配的剩余阻塞只剩 F21/F22。下一步 F21(描述性统计)或 F22(基准回归),均依赖 F20 的 `MergedDataset`,两者可并行推进。

## 当前上下文

<!-- 每个会话覆盖此部分。保持简洁。 -->

- F20 完成:
  - `src/harness_stata/state.py` 的 `MergedDataset` TypedDict 新增 `warnings: list[str]` 字段;`docs/state.md` 同步补小节说明(主键重复属硬错误 raise 不入 warnings;覆盖率不足/列缺失入 warnings)
  - `src/harness_stata/prompts/data_cleaning.md` 撰写完整 system prompt(角色 + 任务 + 可用工具 + 工作流程建议 + 终止契约);终止契约要求 LLM 最后一条消息不再发起 tool_call,content 直接输出 JSON `{"file_path": ..., "primary_key": [...]}`
  - `src/harness_stata/nodes/data_cleaning.py` (252 行, <300 warn 阈值):
    - 模块常量:`_MAX_ITERATIONS = 30` / `_MERGED_FILENAME = "merged.csv"` / `_COVERAGE_THRESHOLD = 0.8` / `_FENCE_RE`(剥离 markdown 围栏的正则)
    - `_make_python_tool()`: 闭包工厂,每次调用产生新的持久 namespace dict(预置 `pd` + `Path`),@tool 装饰的 `run_python(code)` 用 `exec(code, namespace)` + `redirect_stdout` 执行并捕获 stdout/异常消息,无沙箱(MVP 本地单机)
    - `_validate` / `_derive_output_path`(从 `DownloadedFile.path` 的 parents[1] 推导,不新增 config) / `_build_human_prompt`(拼装 EmpiricalSpec + 源文件清单 + 输出路径给 HumanMessage)
    - `_extract_final_json` / `_extract_primary_key`: 解析 AIMessage.content 的 JSON(支持 markdown 围栏)
    - `_check_post_conditions`: pandas 读 csv 后,先查 primary_key 列存在性与唯一性(raise),再逐变量做 `_find_variable_column` 归一化匹配(lower + 去下划线)+ 非空率覆盖(<0.8 入 warnings;列缺失入 warnings)
    - 主函数 `async def data_cleaning(state) -> dict[str, Any]`: 验证 → 构造工具+子图 → `await subgraph.ainvoke` → 检查 messages 非空 + 最后 AIMessage + tool_calls 为空(非空 raise max_iterations) → 解析 JSON → post-condition → 写 `merged_dataset`
  - `tests/nodes/test_data_cleaning.py` (6 用例全过):
    - 3 条 success (single_table / multi_source_files / coverage_and_missing_column_warn 分层软告警)
    - 3 条 failure (duplicate_primary_key_raises / react_truncation_raises / missing_downloaded_files_raises)
    - Mock 方案:patch `harness_stata.nodes.data_cleaning.build_react_subgraph` 返回一个 MagicMock,其 `.ainvoke` 是 AsyncMock 返回 `{"messages": [AIMessage(...)], "iteration_count": 1}`;测试内预先用 pandas 在 `tmp_path` 下按 F18 session 布局(`downloads/session1/<db_table>/`)创建源 csv + 预期输出 `merged.csv`,让 `_derive_output_path` 与 post-condition 能跑
    - async 测试统一 `asyncio.run(data_cleaning(state))` 包同步,延续 F18 约定
  - 设计取舍 (plan 拍板):
    - **D1 Python 工具形态**:单一 `run_python` REPL 工具(vs 声明式 read_csv/merge/write_csv 工具集 / 混合方案);优先 prompt 最短 + LLM 最灵活
    - **D2 工具代码位置**:`@tool` 装饰器就地写在 `nodes/data_cleaning.py` 内(vs 新建 tools/ 包);控制文件 ≤300 行
    - **D3 失败分层**:主键重复/缺列/ReAct 截断/LLM 未落盘 → `RuntimeError`;变量覆盖率<0.8/变量列缺失 → 写入 `MergedDataset.warnings`,下游 F21/F22 决策是否继续
    - **D4 输出落盘**:从 `DownloadedFile.path` 推导 `parents[1] / "merged.csv"` 落在 F18 session 目录根部,不新增 config
    - **D5 max_iterations = 30**:F15 per_variable_max_calls=8,F20 跨表更复杂,初值 30
  - pyright strict 处理:`@tool` 装饰器被识别为 partial-unknown → 行级 `# pyright: ignore[reportUntypedFunctionDecorator, reportUnknownVariableType, reportUnknownArgumentType]`;pandas `pd.read_csv` / `.duplicated().sum()` / `.notna().sum()` 同样 pyright ignore + `cast("Any", ...)` 包一层再 `int(...)`;`AIMessage.content` 类型是 `str | list[str|dict[Unknown,Unknown]]`,用 `cast("Any", last.content)` + isinstance 收敛
- 质量门禁 9/9 通过 (全仓 61/61 pytest, 新增 6 用例)
- 先前 F25 / F18 完成内容见 git log d11faa1 / b7c300b

## 下一步

1. F21: 描述性统计节点 (借 F19 generic_react + stata-executor,读 F20 产出的 MergedDataset)
2. F22: 基准回归节点 (借 F19 generic_react + stata-executor,对照 ModelPlan.core_hypothesis.expected_sign 做符号校验)
3. F23: 主图装配 (等 F21 / F22 就绪, F25 已完成; 需绑定 checkpointer 以支持 F17 interrupt)
4. F24: CLI 入口 (等 F23 就绪, 需用 asyncio.run + graph.ainvoke 适配 F18/F20 引入的 async 模式, 并处理 HITL interrupt resume)

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
- F18 + F20 引入的 async 节点模式需在 F24 CLI 统一入口用 `asyncio.run(graph.ainvoke(...))`,并在 F21 / F22 两个节点保持一致的 async def 签名
- F20 `run_python` 工具当前直接用 `exec` + 闭包 namespace,无沙箱/子进程隔离(MVP 本地单机可接受);若将来走服务端需替换为子进程或 Docker,但不在当前范围
- F20 `_COVERAGE_THRESHOLD = 0.8` 当前硬编码,若需按场景调整后续再提到 config
