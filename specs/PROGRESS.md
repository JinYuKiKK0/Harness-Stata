# 项目进度

## 当前焦点

F24 完成: MVP 本地 CLI 端到端链路打通。`src/harness_stata/cli.py` 以 typer 暴露 `harness-stata run --x-variable ... --data-frequency ...` 命令,驱动 F23 主图,同进程阻塞式处理 hitl interrupt,并把 RegressionResult 摘要打印到 stdout + 把终态快照落盘为 `final_state.json`。全部 25 个 feature (F01-F25) 现已 `passes=true`,MVP 可交付。

## 当前上下文

<!-- 每个会话覆盖此部分。保持简洁。 -->

- F24 完成:
  - `src/harness_stata/cli.py` (~215 行):
    - 单命令 `run`,6 个必填 `--option` 直接映射 `UserRequest`;`data_frequency` 用 `StrEnum` 让 typer 自动做 choice 校验 (yearly/quarterly/monthly/daily)
    - `--thread-id` 可选,缺省 `uuid4()`,便于调试重跑
    - `@app.callback()` 占位,把单命令 app 升级为多命令 app 以便保留 `run` 子命令名 (测试/调用方都写 `harness-stata run ...`)
    - `_drive_graph` async 驱动循环:`await graph.ainvoke(initial, config)` → 若返回 `{"__interrupt__": [Interrupt(value=...)]}` → `_prompt_hitl_decision` 阻塞 typer.confirm/prompt → `await graph.ainvoke(Command(resume=decision), config)` 续图 → 循环直至 payload 为空 → `aget_state(config).values` 取终态
    - `_interrupt_payload(result)` 从 `Interrupt.value` 解出 dict payload (经 `getattr + isinstance` 收敛 pyright unknown)
    - `_prompt_hitl_decision`:approved 时 optional notes (Enter 跳过 → None);rejected 时 while 循环强制非空 rejection reason
    - `_render_summary` 按 workflow_status 三分支打印 (success/failed_hard_contract/rejected),success 同时列 merged/desc/regression 的 do+log 路径
    - `_dump_final_state`:从 `merged_dataset.file_path` 推 session_dir → 写 `final_state.json` (json.dumps ensure_ascii=False, default=str);hard_failure/rejected 无 session_dir 时返回 None (不落盘,仅 stdout)
    - exit code:success → 0;failed_hard_contract/rejected → 1;参数缺失/枚举错误 → 2 (typer 原生)
  - `src/harness_stata/__main__.py` (6 行):支持 `python -m harness_stata run ...`;纯委托 `cli.app`
  - `pyproject.toml`:
    - 新增 `[tool.ruff.lint.flake8-bugbear] extend-immutable-calls = ["typer.Option", "typer.Argument"]` 以豁免 B008 (typer 惯用在默认值位置调 Option)
    - `[project.scripts] harness-stata = "harness_stata.cli:app"` 之前已就位
  - `CLAUDE.md` 架构树追加 `__main__.py` 节点,通过 `check_architecture` 一致性检查
  - `tests/test_cli.py` (7 用例全过):
    - happy path approved:stub 全部 7 个非 hitl 节点 + 真跑 hitl (真实 interrupt 触发),CliRunner input="y\n\n",断言 exit 0 + stdout 含 regression summary + final_state.json 落盘
    - hard failure short-circuits:data_probe stub 返回 `workflow_status="failed_hard_contract"`,断言 data_cleaning 未被调用 (条件边生效)
    - hitl rejected:CliRunner input="n\n模型假设不合理\n",断言 exit 1 + stdout 含 rejection notes + data_cleaning 未被调用
    - missing required arg:缺 --x-variable → exit 2,typer 报 Missing option
    - invalid data_frequency:--data-frequency weekly → exit 2
    - _interrupt_payload 纯函数用例 (空 dict / 无 key / Interrupt 对象 3 种输入)
    - _dump_final_state 无 merged_dataset 时返回 None
  - 设计取舍:
    - **D1 输入**:全 typer 命令行参数 (拒绝 prompt/config 文件,MVP 最简)
    - **D2 HITL**:同进程阻塞 typer.confirm/prompt;InMemorySaver 够用,无需跨进程
    - **D3 测试 mock 粒度**:节点级 stub 7 个非 hitl 节点,hitl 真跑以验证 CLI 的 interrupt/resume 循环契约
    - **D4 产物**:stdout 摘要 + session_dir/final_state.json 快照 (hard_failure/rejected 无 merged_dataset 时仅 stdout)
  - pyright strict 处理:
    - graph.ainvoke / aget_state `# pyright: ignore[reportUnknownMemberType]`
    - `state.get(...)` 返回的 dict 值显式标注 `dict[str, Any]` 收敛 "partially unknown"
    - `data_frequency.value` 赋给 Literal 字段加 `# pyright: ignore[reportAssignmentType]`
    - `_main` callback 加 `# pyright: ignore[reportUnusedFunction]`
- 质量门禁 9/9 通过 (pytest 102 passed: 原 95 + CLI 7)
- 先前 F23 / F22 / F21 / F20 完成内容见 git log 8c291c5 / c899a3b / 089a78a / 857d865

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
- ruff RUF001/RUF002 对中文全角标点与同形希腊字母的检查: docstring 与 Field description 避免使用全角标点 (逗号/句号/括号等) 与 α/β/γ
- 主 `.venv` 缺 `prettytable` (csmarapi 的运行时依赖),scripts/check.py 已 9/9 通过但手动跑 csmar-mcp 子包单测会 ImportError
- `packages/stata-executor/` 的 ruff/pyright 收口尚未做 (类比 csmar-mcp 已完成的技术债)
- `subgraphs/probe_subgraph.py` 当前 487 行触发 check_file_size warn,下一次实质性扩展前应拆出 `_probe_helpers.py`
- F20 `run_python` 工具直接用 `exec` + 闭包 namespace,无沙箱/子进程隔离 (MVP 本地单机可接受);服务端化需替换子进程或 Docker
- F20 `_COVERAGE_THRESHOLD = 0.8` 硬编码,按场景调整可提到 config
- F22 `actual_sign` 由 LLM 从 log 抽取,节点不 parse log;若发现 LLM 误读可改为节点端正则抽取 Stata 回归表格
- F22/F21 要求 do 文件内部用 `log using` 显式指定绝对路径,否则节点在 `_assert_file_exists` 阶段 raise (prompt 已强制约束)
- `scripts/check.py` 的 ruff format 仅检查 `src/harness_stata`,不覆盖 `tests/` 与 `scripts/`,导致测试代码可能存在格式漂移
- F23 用 `InMemorySaver` 作为 MVP 本地单进程 checkpointer;若后续需要跨进程 resume 可替换为 SqliteSaver
- F24 CLI 为单进程阻塞模式,不支持长会话/Web 场景 (与 D2 决策一致);Web 迭代需要重新设计 HITL 适配层
