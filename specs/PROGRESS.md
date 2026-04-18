# 项目进度

## 当前焦点

F23 完成: `src/harness_stata/graph.py` 装配主图 (8 节点 + 2 条条件边),编译时绑定 `InMemorySaver` 以支持 F17 hitl `interrupt()` 的暂停/续图。`build_graph()` 作为工厂入口返回 `CompiledStateGraph[WorkflowState, None, WorkflowState, WorkflowState]`,两条路由 `route_after_probe` / `route_after_hitl` 对外导出,便于单元测试与 F24 CLI 组合。

## 当前上下文

<!-- 每个会话覆盖此部分。保持简洁。 -->

- F23 完成:
  - `src/harness_stata/graph.py` (~100 行) 从零落地:
    - 仅从 `harness_stata.nodes.<name>` 直接 import 8 个节点函数 (nodes/__init__.py 保持空);**不** import subgraphs (符合 `[graph→subgraphs]` 契约)
    - 线性边: START → requirement_analysis → model_construction → data_probe;data_download → data_cleaning → descriptive_stats → regression → END
    - 条件边 1 `route_after_probe`: `workflow_status == "failed_hard_contract"` 或 `probe_report.overall_status == "hard_failure"` (防御兜底) → END;否则 → hitl
    - 条件边 2 `route_after_hitl`: `workflow_status == "rejected"` 或 `hitl_decision.approved is False` (防御兜底) → END;否则 → data_download
    - `compile(checkpointer=InMemorySaver())` 绑定内存 checkpointer (langgraph 首次在仓库中引入该保存器),必需以支持 hitl interrupt
    - `build_graph()` 工厂返回新实例;**不**导出模块级 `graph` 常量,避免 import 副作用 (F24 CLI 显式调用即可)
    - pyright strict 压制模式沿用 subgraphs/ 的 `# pyright: ignore[reportUnknownMemberType]` (StateGraph.add_node/.add_edge/.add_conditional_edges/.compile 全部需要)
    - 返回类型泛型参数 `CompiledStateGraph[WorkflowState, None, WorkflowState, WorkflowState]` 中 ContextT=None,匹配 langgraph 1.1.6 的 .compile() 实际推断
  - `tests/test_graph.py` (11 用例全过):
    - 拓扑 (3): build_graph 可编译且 8 个业务节点全在 / checkpointer 已绑定 / 每次调用返回独立实例
    - route_after_probe (4): workflow_status 硬失败 / probe_report 硬失败兜底 / success 两种 / 空 state 默认走 success
    - route_after_hitl (3): workflow_status rejected / decision.approved=False 兜底 / approved 正常
    - 条件边连线 (1): 从 `get_graph().edges` 导出邻接表,验证 data_probe 同时指向 hitl 和 END,hitl 同时指向 data_download 和 END,regression 只指向 END
  - pyproject.toml `[[tool.importlinter.contracts]]` `[graph→subgraphs]` 增加 `allow_indirect_imports = true`: 契约原意为禁止 graph.py **直接** import subgraphs,但 import-linter forbidden 默认也会抓传递链,而本架构正依赖 "graph → nodes → subgraphs" 的传递链。添加该开关让契约恢复原意,其它 4 条契约 (subgraphs→nodes / nodes&subgraphs→packages / llm-single-entry / layers) 保持原状且全绿
- 质量门禁 9/9 通过 (95/95 pytest: 原 74 + F21 5 + F22 5 + F23 新增 11)
- 先前 F21 / F22 / F20 / F25 / F18 完成内容见 git log c899a3b / 089a78a / 857d865 / d11faa1 / b7c300b

## 下一步

1. F24: CLI 入口 (typer 命令,接收 UserRequest 表单,用 `asyncio.run(graph.ainvoke(..., config={"configurable": {"thread_id": ...}}))` 跑通端到端,捕获 `hitl_plan_review` interrupt 事件 → 渲染 payload.plan → 采集 approved/rejected → `Command(resume=...)` 续图;端到端 mocked 冒烟覆盖 approved + rejected 两分支);F23 全部上游依赖已就绪

## 未解决/卡点

- pyright strict 下 ChatTongyi 缺少部分类型桩, clients/llm.py 中有 type: ignore 注释
- WorkflowState total=False 导致所有 state key 访问需要 type: ignore[reportTypedDictNotRequiredAccess]
- langgraph 1.1.6 缺少公开类型桩, `StateGraph.add_node` / `.add_edge` / `.add_conditional_edges` / `.compile()`, `BaseChatModel.bind_tools()` / `.with_structured_output()`, `BaseTool.invoke()` / `.ainvoke()`, `Runnable.invoke()` 被 pyright strict 判 reportUnknownMemberType, 统一通过 `# pyright: ignore[reportUnknownMemberType]` 压制
- `@tool` 装饰器在 pyright strict 下也需 `# pyright: ignore[reportUntypedFunctionDecorator, reportUnknownVariableType, reportUnknownArgumentType]` 压制 (F20 引入的新模式,后续节点若再写 inline @tool 沿用)
- pandas 在 pyright strict 下大量 reportUnknownMemberType (`.read_csv` / `.duplicated().sum()` / `.notna().sum()` / `.columns` 遍历),F20 采用 `cast("Any", ...)` + `# pyright: ignore` 的组合,后续若直接用 pandas 可沿用
- ruff RUF001/RUF002 对中文全角标点与同形希腊字母的检查: docstring 与 Field description 中避免使用全角标点 (逗号/句号/括号等) 与 α/β/γ
- 主 `.venv` 缺 `prettytable` (csmarapi 的运行时依赖): `scripts/check.py` 已 9/9 通过, 但若要手动跑 csmar-mcp 子包单元测试会 ImportError; 修复方案待定
- `packages/stata-executor/` 的 ruff/pyright 收口尚未做 (类比 csmar-mcp 已完成的技术债), 留给独立会话
- `subgraphs/probe_subgraph.py` 当前 487 行触发 check_file_size warn (>300, <500 fail). 下一次本文件实质性扩展前应拆出 `_probe_helpers.py`
- F18 + F20 + F21 + F22 引入的 async 节点模式需在 F24 CLI 统一入口用 `asyncio.run(graph.ainvoke(...))`,并为 interrupt/resume 管理 `thread_id`
- F20 `run_python` 工具当前直接用 `exec` + 闭包 namespace,无沙箱/子进程隔离 (MVP 本地单机可接受);若将来走服务端需替换为子进程或 Docker,但不在当前范围
- F20 `_COVERAGE_THRESHOLD = 0.8` 当前硬编码,若需按场景调整后续再提到 config
- F22 的 `actual_sign` 由 LLM 自己从 log 中抽取核心系数正负填回,节点不独立 parse log;MVP 信任 LLM,未来若发现 LLM 误读可改为节点端正则抽取 Stata 回归表格
- F22 / F21 要求 do 文件内部用 `log using` 显式指定绝对路径;如果 LLM 忘写 `log using`,节点在 `_assert_file_exists(log_file_path)` 阶段 raise——prompt 已强制约束此点
- `scripts/check.py` 的 ruff format 仅检查 `src/harness_stata`,不覆盖 `tests/` 与 `scripts/`,导致测试代码可能存在格式漂移;未来某次清理可考虑把 tests/ 也纳入 format 门禁
- F23 用 `InMemorySaver` 作为 MVP 本地单进程 checkpointer;若 F24 或后续需要跨进程 resume,可替换为 SqliteSaver,当前无需
