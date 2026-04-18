# 项目进度

## 当前焦点

F25 完成: `nodes/data_probe.py` 用 async 外壳包装 `build_probe_subgraph()`——`async with get_csmar_tools()` 拿工具、`load_prompt("data_probe")` 注入 system prompt、`get_settings().per_variable_max_calls` 提供预算,构造 `ProbeState` 初值后同步 `.invoke()` 子图,再把 `probe_report` / `download_manifest` (必出) + `empirical_spec` (soft 替代时) + `workflow_status` (hard_failure 时) 回传 WorkflowState。F23 主图装配的剩余阻塞只剩 F20/F21/F22。下一步推进 F20 (`data_cleaning` 借 F19 generic_react 产出 `MergedDataset`) 作为 F21/F22 的前置依赖。

## 当前上下文

<!-- 每个会话覆盖此部分。保持简洁。 -->

- F25 完成:
  - `src/harness_stata/config.py` Settings 追加 `per_variable_max_calls: int`,从 `.env` 的 `HARNESS_PER_VARIABLE_MAX_CALLS` 读取,默认 4;非法值(非整数或 <1)抛 RuntimeError,与其他字段校验风格一致
  - `src/harness_stata/nodes/data_probe.py` (83 行, 远低于 300 warn 阈值):
    - `async def data_probe(state) -> dict[str, Any]`: 外层 `async with get_csmar_tools()` 管理 MCP 生命周期,内层用工厂编译子图后**同步** `.invoke(initial)` (非 ainvoke,子图内部 ReAct 是手写同步循环)
    - 字段映射最小化原则:probe_report/download_manifest 必回传;empirical_spec **仅在子图重建了新对象时**回传 (用 `final_spec is not spec` 判断);workflow_status 仅在 `failed_hard_contract` 时回传
    - 不主动 raise hard_failure——子图已置 `workflow_status`,节点只透传,由 F23 主图 conditional edge 负责路由到 END
    - `_validate` 捕获 empirical_spec/model_plan 缺失与 variables 空列表,抛 ValueError (与 F18 data_download 风格一致)
  - `tests/nodes/test_data_probe.py` (230 行, 7 用例全过):
    - Mock 到 subgraph 层(per F25 step 4): patch `build_probe_subgraph` 返回 `MagicMock`,预设 `.invoke.return_value = <fake ProbeState>`;不重复 F15/F16 已覆盖的 subgraph 内部行为
    - 3 条 happy path (all found / soft substitute / hard failure) 验证字段映射;3 条 validation 用例 (missing spec/plan/empty variables) → ValueError;1 条异常透传用例验证 subgraph 抛错时节点不吞错且 csmar tools async cm 正常退出
  - 设计取舍:
    - **per_variable_max_calls 放 config.Settings** 而非硬编码常量:延续 F18 downloads_root 的 pattern,后续调优走 `.env` 而非改代码;`.env` 未显式设置时默认 4 (与 data_probe.md prompt 里 "三四步内没有结论" 的预算意识对齐)
    - **测试粒度 mock 到 subgraph**:F25 是纯外壳层,F15/F16 已对子图内部做过充分单元覆盖,重复编排 LLM/tools mock 只会加测试脆弱性
    - **empirical_spec 用 `is not` 判断回写**:避免原样透传当前虽无 reducer 影响,但语义上明确区分 "未变更 = 不回传" vs "变更 = 显式覆写",给未来引入 reducer 留余地
  - 质量门禁 9/9 通过 (pytest 55/55 含新增 7 用例, 全部 lint + pyright + import-linter 通过)
- 先前 F18 完成内容见 git log b7c300b
  - `src/harness_stata/config.py` 新增 `downloads_root: Path` 字段,默认 `<repo>/downloads`,可由 `.env` 的 `HARNESS_DOWNLOADS_ROOT` 覆盖;`.resolve()` 规范化为绝对路径
  - `src/harness_stata/nodes/data_download.py` (194 行, < 300 warn 阈值):
    - 3 个模块常量 (`_PROBE_TOOL_NAME` / `_MATERIALIZE_TOOL_NAME` / `_SESSION_TS_FORMAT`)
    - 9 个纯辅助函数:`_validate` / `_make_session_dir` / `_make_task_dir` / `_tools_by_name` / `_build_probe_payload` / `_coerce_dict` / `_extract_validation_id` / `_extract_file_paths` / `_make_downloaded_files`
    - 主函数 `async def data_download(state) -> dict[str, Any]`: 外层 `async with get_csmar_tools()`,内层串行遍历 DownloadTask;每 task 先 probe(校验 `can_materialize` + 取 `validation_id`)再 materialize(物化到 `<session_dir>/<database>_<table>/`)
    - filters 当前只透传 `start_date` / `end_date`,其它键忽略(docstring 注明 TODO 留给 F20 暴露具体场景再补 CSMAR condition 字符串)
    - 同 task 返回多文件时 `variable_names` 全量复制到每个 DownloadedFile(跨文件拼接责任下沉到 F20 data_cleaning)
  - `tests/nodes/test_data_download.py` (240 行, 6 用例 全过):
    - 3 条 success 用例 (single_task / multi_tasks / multi_files_per_task)
    - 3 条 failure 用例 (probe_cannot_materialize_raises / materialize_raises_propagates / empty_manifest_raises)
    - Mock 方案:patch `harness_stata.nodes.data_download.get_csmar_tools` 的 `side_effect` 为本地 `@asynccontextmanager`,内部 yield `MagicMock` 包装的 tool(`.name` + `AsyncMock` 的 `.ainvoke`);patch `get_settings` 返回 `MagicMock(downloads_root=tmp_path)` 以避免污染真实文件系统
    - async 测试一律用 `asyncio.run(data_download(state))` 包裹成同步,不新增 pytest-asyncio 依赖(延续既有测试同步风格)
  - `tests/nodes/conftest.py` 新增 `make_download_manifest` factory fixture (默认单 task 指向 CSMAR.FS_COMBAS),可被 F20 data_cleaning 测试复用
  - `docs/state.md` 的 `DownloadedFiles` 小节补 F18 产出语义:materialize 每个 file path → 独立 DownloadedFile;`variable_names` 为 task 全量复制(不跨文件拆分)
  - 设计取舍 (plan 拍板):
    - **D1 async 节点**:node 为 `async def` 而非同步包 `asyncio.run` (MCP session 跨 event loop 不安全);连带 F24 CLI 入口需用 `graph.ainvoke` / `graph.astream` + `asyncio.run(...)`,LangGraph 原生支持 async + sync 节点混合,不影响既有 hitl/requirement_analysis/model_construction 等 sync 节点
    - **D2 两步 probe + materialize**:不复用 F15 的 validation_id (TTL + 跨阶段耦合风险);csmar-mcp 服务端有 `has_cached_download` 缓存,重复 probe 开销可忽略
    - **D3 下载目录**:新增 config 字段而非节点内硬编码,遵循 F05 既定的 config 集中暴露原则;`<downloads_root>/<utc_ts>/<db>_<table>/` 分层降低重入冲突
    - **D5 失败即 raise**:任一 task probe 不可物化或 materialize 抛错直接 raise,不做 partial success;F23 主图后续负责把 raise 映射为 `workflow_status="failed_hard_contract"`
  - pyright strict 处理:对 `BaseTool.ainvoke` 沿用 probe_subgraph 的 `# pyright: ignore[reportUnknownMemberType]` 集中压制(2 处);`invalid_columns or []` 用 `cast("list[Any]", ...)` 避免 partial-unknown
- 质量门禁 9/9 通过 (全仓 48/48 pytest, 新增 6 用例)

## 下一步

1. F20: `nodes/data_cleaning.py` 借 F19 `build_react_subgraph` + 文件 IO/Python 执行工具产出 `MergedDataset` (F21/F22 的前置依赖)
2. F21 / F22: 描述性统计与基准回归节点 (依赖 F20 的 MergedDataset)
3. F23: 主图装配 (等 F20 / F21 / F22 就绪, F25 已完成; 需绑定 checkpointer 以支持 F17 interrupt)
4. F24: CLI 入口 (等 F23 就绪, 需用 asyncio.run + graph.ainvoke 适配 F18 引入的 async 模式, 并处理 HITL interrupt resume)

## 未解决/卡点

- pyright strict 下 ChatTongyi 缺少部分类型桩, clients/llm.py 中有 type: ignore 注释
- WorkflowState total=False 导致所有 state key 访问需要 type: ignore[reportTypedDictNotRequiredAccess]
- langgraph 1.1.6 缺少公开类型桩, `StateGraph.add_node` / `.compile()`, `BaseChatModel.bind_tools()` / `.with_structured_output()`, `BaseTool.invoke()` / `.ainvoke()`, `Runnable.invoke()` 被 pyright strict 判 reportUnknownMemberType, 统一通过 `# pyright: ignore[reportUnknownMemberType]` 压制
- ruff RUF001/RUF002 对中文全角标点与同形希腊字母的检查: docstring 与 Field description 中避免使用全角标点 (逗号/句号/括号等) 与 α/β/γ
- 主 `.venv` 缺 `prettytable` (csmarapi 的运行时依赖): `scripts/check.py` 已 9/9 通过, 但若要手动跑 csmar-mcp 子包单元测试会 ImportError; 修复方案待定
- `packages/stata-executor/` 的 ruff/pyright 收口尚未做 (类比 csmar-mcp 已完成的技术债), 留给独立会话
- `subgraphs/probe_subgraph.py` 当前 487 行触发 check_file_size warn (>300, <500 fail). 下一次本文件实质性扩展前应拆出 `_probe_helpers.py`
- F18 引入的 async 节点模式需在 F24 CLI 统一入口用 `asyncio.run(graph.ainvoke(...))`,并在 F25 / F20 / F21 / F22 四个节点保持一致的 async def 签名
