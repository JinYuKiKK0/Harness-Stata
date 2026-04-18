# 项目进度

## 当前焦点

F17 完成：`nodes/hitl.py` 纯代码节点以 langgraph `interrupt()` 原语暂停图执行, 一次性呈递完整研究方案 (选题/样本/方程/变量表/Soft 替代溯源/预期符号/样本规模预估), 采集 approved/rejected 决策写入 `hitl_decision`, rejected 时联动写 `workflow_status="rejected"` 驱动主图条件边. 下一步推进 F18 (data_download 纯代码节点) 或 F20 (data_cleaning 用 generic_react).

## 当前上下文

<!-- 每个会话覆盖此部分。保持简洁。 -->

- F17 完成:
  - `src/harness_stata/nodes/hitl.py` (247 行, < 300 warn 阈值) 新增:
    - `_INTERRUPT_TYPE = "hitl_plan_review"` 模块常量作为 F24 CLI/Web resume 的稳定契约
    - `_SECTION_HEADERS` / `_ROLE_LABEL` 常量字典便于测试断言
    - 7 个 `_format_*` 纯函数 (topic/sample/equation/variables_table/substitution_trace/core_hypothesis/sample_size), **全部无 I/O**, 保证 langgraph interrupt 重入语义下反复调用无副作用
    - 样本规模预估取 min~max 区间 (基于所有非 None record_count), 避免单值误导
    - `_validate` 三段校验: dict / approved:bool / approved=False 时 user_notes 必须非空
    - `_request_decision` 循环: 最多 3 次 interrupt, 每次失败把 error msg 附回 payload 让调用方重填, 彻底失败抛 ValueError
    - 主函数 `hitl(state)` 返回 approved 时 `{"hitl_decision":...}`, rejected 时附 `workflow_status:"rejected"`
  - `tests/nodes/test_hitl.py` (246 行, 11 用例 全过):
    - 5 条格式化纯函数用例 (full / no_substitution / all_counts / partial_counts / all_none)
    - 6 条 hitl 节点用例 (approved_with_notes / approved_no_notes / rejected_valid / rejected_empty_notes_retries / rejected_persistent_invalid_raises / malformed_resume_raises)
    - Mock 方案: `mocker.patch("harness_stata.nodes.hitl.interrupt", side_effect=...)` 在 import 站点打桩, 不引入 InMemorySaver + StateGraph (真实 interrupt/resume 留给 F23 集成测试)
  - `tests/nodes/conftest.py` (195 行) 追加 3 个 factory fixture: `make_empirical_spec` / `make_model_plan` / `make_probe_report(substituted, missing_counts)`, 与 `mock_chat_model_for` 同风格, 可被 F18+ 下游节点测试复用
  - `docs/state.md` 的 `hitl_decision` 小节追加 workflow_status 联动说明与 interrupt/Command(resume) 契约
  - 设计取舍 (用户拍板):
    - 交互机制 = langgraph `interrupt()` 原语而非同步阻塞 CLI: 为 F24 Web 端留路径, 节点纯函数重入安全
    - user_notes = approved 可选 / rejected 必填非空: 保留拒因便于后续会话追溯
    - 空 user_notes 兜底 = 二次 interrupt 最多 3 次, 而非首次 raise: 避免用户已填合法字段在图终止时丢失
    - 交付边界 = 只做节点 + 单测, 不打通 CLI (F24) 和主图装配 (F23)
  - pyright strict 处理: `_validate` 中 `isinstance(raw, dict)` 后用 `cast("dict[str, Any]", raw)` 消除 reportUnknownVariableType, 整个文件零 pyright ignore
- 质量门禁 9/9 通过 (全仓 42/42 pytest, 新增 11 用例)

## 下一步

1. F18: `nodes/data_download.py` 纯代码解析 `DownloadManifest` 调用 csmar-mcp 完成批量下载, 写 `DownloadedFiles`
2. F20: `nodes/data_cleaning.py` 借 F19 `build_react_subgraph` + 文件 IO/Python 执行工具产出 `MergedDataset`
3. `nodes/data_probe.py` 节点包装 (消费 `build_probe_subgraph`, 装入主图需要 F23)

## 未解决/卡点

- pyright strict 下 ChatTongyi 缺少部分类型桩, clients/llm.py 中有 type: ignore 注释
- WorkflowState total=False 导致所有 state key 访问需要 type: ignore[reportTypedDictNotRequiredAccess]
- langgraph 1.1.6 缺少公开类型桩, `StateGraph.add_node` / `.compile()`, `BaseChatModel.bind_tools()` / `.with_structured_output()`, `BaseTool.invoke()`, `Runnable.invoke()` 被 pyright strict 判 reportUnknownMemberType, 统一通过 `# pyright: ignore[reportUnknownMemberType]` 压制
- ruff RUF001/RUF002 对中文全角标点与同形希腊字母的检查: docstring 与 Field description 中避免使用全角标点 (逗号/句号/括号等) 与 α/β/γ
- 主 `.venv` 缺 `prettytable` (csmarapi 的运行时依赖): `scripts/check.py` 已 9/9 通过, 但若要手动跑 csmar-mcp 子包单元测试会 ImportError; 修复方案待定
- `packages/stata-executor/` 的 ruff/pyright 收口尚未做 (类比 csmar-mcp 已完成的技术债), 留给独立会话
- `subgraphs/probe_subgraph.py` 当前 492 行触发 check_file_size warn (>300, <500 fail). 下一次本文件实质性扩展前应拆出 `_probe_helpers.py`
