# 项目进度

## 当前焦点

F16 完成：`subgraphs/probe_subgraph.py` 的 `_result_handler` 已实质化, 探针子图能产出完整 `ProbeReport` + `DownloadManifest`, 并按 hard/soft 分支路由 (hard 失败立即写 `workflow_status="failed_hard_contract"` + END; soft 失败将候选替代塞回队列, 下一轮独立预算探测; 替代成功回写 `EmpiricalSpec.variables`). 下一步推进 F17 (HITL 节点) 或 F20 (data_cleaning 用 generic_react).

## 当前上下文

<!-- 每个会话覆盖此部分。保持简洁。 -->

- F16 完成:
  - `src/harness_stata/subgraphs/probe_subgraph.py` (492 行, warn but < 500 fail) 新增:
    - `_EXTRACTOR_PROMPT` 常量 (英文, 模块级) 用于 result_handler 二次 LLM 提取
    - `_VariableProbeFindingModel` Pydantic schema (status / source / key_fields / filters / candidate_substitute_*)
    - `_SubstituteMeta` TypedDict, 通过 `substitute_meta` 私有字段记账
    - `ProbeState` 扩展 `workflow_status`, `substitute_meta` 两字段
    - `_result_handler` 五分支: found / substituted / hard not_found (路由 END) / soft not_found 有替代 (入队) / soft not_found 无替代或替代失败 (record + 继续)
    - `_route_after_handler` 新增 hard_failure 检测
    - 模块级 helpers: `_extract_finding`, `_format_trace`, `_ensure_report/manifest`, `_build_*`, `_merge_into_manifest` (按 (database, table) 合并 DownloadTask), `_replace_variable_in_spec`, `_maybe_build_substitute`
  - `src/harness_stata/prompts/data_probe.md` (60 行) system prompt 撰写完成: 角色 / 工具 / 探测策略 / 终止契约 / hard-soft 差异 / 跨频率替代禁令 / 预算意识
  - `tests/subgraphs/test_probe_subgraph.py` (11 用例, 全过):
    - 修改: `TestEmptyQueue` 加 probe_report/download_manifest 初始化断言
    - 修改: 既有 3 个 react 用例追加 extractor_findings mock
    - 新增: `TestFoundSingleVariable` / `TestHardNotFound` / `TestSoftSubstituteSuccess` / `TestSoftSubstituteFailure` / `TestMultiVariableSameTable` 5 个 F16 分支用例
    - `_wire_models` helper 同时打桩 `bind_tools().invoke` 与 `with_structured_output().invoke` 两条调用流
  - `docs/state.md` §2 表格同步: 数据探针 "写回主图" 列删除 `ModelPlan*(回写)`, 追加 `workflow_status*(hard_failure 时)`; "子图内部" 列追加 `substitute_meta`
  - 设计取舍 (用户拍板):
    - 替代搜索路径 = B (塞回队列+独立预算): 与 docs/empirical-analysis-workflow.md:104 图示一致
    - ModelPlan 不回写: hard 不可替代; soft 替代仅发生在控制变量, 不影响 equation/core_hypothesis; prompt 禁跨频率替代避免 data_structure_requirements 漂移
- 质量门禁 9/9 通过 (新增 5 用例 + 修改 4 用例, 全仓 31/31 pytest 通过); pyright strict 在 `_format_trace` 的 `str(m.content)` 处用 `# pyright: ignore[reportUnknownMemberType, reportUnknownArgumentType]` 压制 BaseMessage.content 的 partial-unknown 类型

## 下一步

1. F17: `nodes/hitl.py` 一次性向用户呈递完整研究方案 (变量定义 + 模型方程 + 替代溯源 + 样本规模预估), CLI 交互采集 approved/rejected 决策, 写 `hitl_decision`
2. F20: `nodes/data_cleaning.py` 借 F19 `build_react_subgraph` + 文件 IO/Python 执行工具
3. `nodes/data_probe.py` 节点包装 (消费 `build_probe_subgraph`, 装入主图需要 F23)

## 未解决/卡点

- pyright strict 下 ChatTongyi 缺少部分类型桩, clients/llm.py 中有 type: ignore 注释
- WorkflowState total=False 导致所有 state key 访问需要 type: ignore[reportTypedDictNotRequiredAccess]
- langgraph 1.1.6 缺少公开类型桩, `StateGraph.add_node` / `.compile()`, `BaseChatModel.bind_tools()` / `.with_structured_output()`, `BaseTool.invoke()`, `Runnable.invoke()` 被 pyright strict 判 reportUnknownMemberType, 统一通过 `# pyright: ignore[reportUnknownMemberType]` 压制
- ruff RUF001/RUF002 对中文全角标点与同形希腊字母的检查: docstring 与 Field description 中避免使用全角标点 (逗号/句号/括号等) 与 α/β/γ
- 主 `.venv` 缺 `prettytable` (csmarapi 的运行时依赖): `scripts/check.py` 已 9/9 通过, 但若要手动跑 csmar-mcp 子包单元测试会 ImportError; 修复方案待定
- `packages/stata-executor/` 的 ruff/pyright 收口尚未做 (类比 csmar-mcp 已完成的技术债), 留给独立会话
- `subgraphs/probe_subgraph.py` 当前 492 行触发 check_file_size warn (>300, <500 fail). 下一次本文件实质性扩展前应拆出 `_probe_helpers.py`
