# 项目进度

## 当前焦点

F21 完成: `nodes/descriptive_stats.py` 复用 F19 `build_react_subgraph` + F14 `clients/stata.get_stata_tools` 产出的 stata MCP 工具集 (`doctor` / `run_inline` / `run_do`),让 LLM 按 `MergedDataset` 列清单自由编写并执行 do 文件做描述性统计 + 缺失/极值扫描 + 逻辑校验,产出 `<session_dir>/descriptive_stats.do` + `descriptive_stats.log`;节点负责 JSON 解析 + do/log 存在性校验,组装 `DescStatsReport` 但**不**写 `workflow_status` (非终端节点,保持 running 让图继续流向 F22)。F23 主图装配的全部上游依赖 (F09/F11/F16/F17/F18/F20/F21/F22/F25) 均已就绪,可立即开工。

## 当前上下文

<!-- 每个会话覆盖此部分。保持简洁。 -->

- F21 完成:
  - `src/harness_stata/prompts/descriptive_stats.md` 撰写完整 system prompt(角色 + 任务上下文 + 可用工具 + 工作流程建议含 4 类逻辑校验示例 + 终止契约);终止契约要求 LLM 最后一条消息不再发起 tool_call,content 直接输出 JSON `{"do_file_path", "log_file_path", "summary"}`,summary 要求 4-8 句覆盖关键发现 + 缺失/异常 + 逻辑校验结论
  - `src/harness_stata/nodes/descriptive_stats.py` (177 行, 远低于 300 warn 阈值,作为 F22 226 行的简化版):
    - 模块常量:`_MAX_ITERATIONS = 15` (F22=20,描述性统计更简单减少预算) / `_DO_FILENAME = "descriptive_stats.do"` / `_LOG_FILENAME = "descriptive_stats.log"` / `_FENCE_RE` (复用 F22 markdown 围栏剥离正则)
    - `_validate` 校验两个必备 state 切片 (merged_dataset / empirical_spec);**不**依赖 model_plan
    - `_derive_session_dir(merged_path)`: `Path(merged_path).resolve().parent` 复用 F20/F22 session_dir 约定,零新增 config
    - `_build_human_prompt`: 拼接研究上下文 + 样本/时间/频率 + merged 元信息 (file_path/row_count/columns/warnings) + 输出 do/log 绝对路径; **不**含 model/core_hypothesis 段落
    - `_extract_final_json` / `_require_str` / `_validate_payload`: 解析并严格校验 3 个字段 (do_file_path / log_file_path / summary 均非空 str)
    - `_assert_file_exists`: do 或 log 文件不存在即 raise (LLM 声称但未落盘)
    - 主函数 `async def descriptive_stats(state) -> dict[str, Any]`: 验证 → `async with get_stata_tools() as tools` → 构造子图 → `await subgraph.ainvoke` → 终止校验 (messages 非空 + AIMessage + tool_calls 为空) → 解析 JSON → 校验字段 → 校验 do/log 存在 → 组装 `DescStatsReport`,**只**返回 `{"desc_stats_report": report}`,**不**含 `workflow_status` (非终端节点,保持 running)
  - `tests/nodes/test_descriptive_stats.py` (5 用例全过):
    - 2 条 success (returns_report 且断言 `workflow_status` 不存在 / preserves_merged_session_dir)
    - 3 条 failure (react_truncation / log_file_missing / missing_merged_dataset)
    - Mock 方案完全复刻 F22:patch `harness_stata.nodes.descriptive_stats.build_react_subgraph` 返回 MagicMock 的 `.ainvoke` AsyncMock + 同时 patch `get_stata_tools` 为返回 `@asynccontextmanager` 产出空 list
    - async 测试统一 `asyncio.run(descriptive_stats(state))` 包同步,延续 F18/F20/F22 约定
  - 设计取舍 (plan 拍板):
    - **D1 任务范围**:LLM 自由发挥 (描述性统计 + 缺失扫描 + 逻辑校验),节点**不**解析 log 内容,只校验 do/log 存在 + JSON 合法
    - **D2 产物命名**:`descriptive_stats.do` + `descriptive_stats.log` 与 `regression.do/log` 并列落在同一 session_dir
    - **D3 max_iterations = 15**:F22=20 用于建模更复杂,描述性统计只做 import + summarize/tabulate/misstable 几轮,15 够用
    - **D4 终止契约**:照搬 F22 模板去掉 `actual_sign`,3 字段 JSON
    - **非终端节点**:不写 `workflow_status`,只返回 desc_stats_report,让图继续流向 F22 regression
  - pyright strict 处理完全沿用 F22 模式:`subgraph.ainvoke` `# pyright: ignore[reportUnknownMemberType]`、`AIMessage.content` 用 `cast("Any", ...)` + isinstance 收敛、WorkflowState 切片访问 `# pyright: ignore[reportTypedDictNotRequiredAccess]`
- 质量门禁 9/9 通过 (全仓 78/78 pytest, 新增 5 用例)
- 先前 F22 / F20 / F25 / F18 完成内容见 git log 089a78a / 857d865 / d11faa1 / b7c300b

## 下一步

1. F23: 主图装配 (8 节点串联 + probe 后 hard_failure/success + HITL 后 approved/rejected 两条条件边 + 编译时绑定 checkpointer 以支持 F17 interrupt 暂停/续图);全部上游依赖均就绪
2. F24: CLI 入口 (等 F23 就绪, 需用 `asyncio.run + graph.ainvoke` 适配 F18/F20/F21/F22 的 async 模式, 并处理 HITL interrupt resume)

## 未解决/卡点

- pyright strict 下 ChatTongyi 缺少部分类型桩, clients/llm.py 中有 type: ignore 注释
- WorkflowState total=False 导致所有 state key 访问需要 type: ignore[reportTypedDictNotRequiredAccess]
- langgraph 1.1.6 缺少公开类型桩, `StateGraph.add_node` / `.compile()`, `BaseChatModel.bind_tools()` / `.with_structured_output()`, `BaseTool.invoke()` / `.ainvoke()`, `Runnable.invoke()` 被 pyright strict 判 reportUnknownMemberType, 统一通过 `# pyright: ignore[reportUnknownMemberType]` 压制
- `@tool` 装饰器在 pyright strict 下也需 `# pyright: ignore[reportUntypedFunctionDecorator, reportUnknownVariableType, reportUnknownArgumentType]` 压制 (F20 引入的新模式,后续节点若再写 inline @tool 沿用)
- pandas 在 pyright strict 下大量 reportUnknownMemberType (`.read_csv` / `.duplicated().sum()` / `.notna().sum()` / `.columns` 遍历),F20 采用 `cast("Any", ...)` + `# pyright: ignore` 的组合,后续若直接用 pandas 可沿用
- ruff RUF001/RUF002 对中文全角标点与同形希腊字母的检查: docstring 与 Field description 中避免使用全角标点 (逗号/句号/括号等) 与 α/β/γ
- 主 `.venv` 缺 `prettytable` (csmarapi 的运行时依赖): `scripts/check.py` 已 9/9 通过, 但若要手动跑 csmar-mcp 子包单元测试会 ImportError; 修复方案待定
- `packages/stata-executor/` 的 ruff/pyright 收口尚未做 (类比 csmar-mcp 已完成的技术债), 留给独立会话
- `subgraphs/probe_subgraph.py` 当前 487 行触发 check_file_size warn (>300, <500 fail). 下一次本文件实质性扩展前应拆出 `_probe_helpers.py`
- F18 + F20 + F21 + F22 引入的 async 节点模式需在 F24 CLI 统一入口用 `asyncio.run(graph.ainvoke(...))`
- F20 `run_python` 工具当前直接用 `exec` + 闭包 namespace,无沙箱/子进程隔离 (MVP 本地单机可接受);若将来走服务端需替换为子进程或 Docker,但不在当前范围
- F20 `_COVERAGE_THRESHOLD = 0.8` 当前硬编码,若需按场景调整后续再提到 config
- F22 的 `actual_sign` 由 LLM 自己从 log 中抽取核心系数正负填回,节点不独立 parse log;MVP 信任 LLM,未来若发现 LLM 误读可改为节点端正则抽取 Stata 回归表格
- F22 / F21 要求 do 文件内部用 `log using` 显式指定绝对路径;如果 LLM 忘写 `log using`,节点在 `_assert_file_exists(log_file_path)` 阶段 raise——prompt 已强制约束此点
- `scripts/check.py` 的 ruff format 仅检查 `src/harness_stata`,不覆盖 `tests/` 与 `scripts/`,导致测试代码可能存在格式漂移 (本会话发现 conftest.py / test_data_download.py / test_generic_react.py / test_probe_subgraph.py 存在已有 format 偏差);未来某次清理可考虑把 tests/ 也纳入 format 门禁
