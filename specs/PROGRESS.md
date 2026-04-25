# 项目进度

## 当前焦点

Review findings 收口：修复下载 filters 契约、回归结果确定性解析、Soft 替代回写 ModelPlan，并同步清理 InMemorySaver 文档漂移与 LLM 依赖漂移。

## 当前上下文

<!-- 每次任务完成覆写此部分，删除之前会话的内容。保持简洁。 -->

- 本次会话 — Review findings 修复：
  - `DownloadTask.filters` 已收敛为 `start_date` / `end_date` 必填、`condition` 可选；探针子图按 `EmpiricalSpec` 时间范围确定性生成日期。
  - `regression` 不再信任 LLM 自报 `actual_sign`，改为校验 `run_do` 成功并从 Stata log / result_text 解析核心系数方向。
  - Soft 替代成功后同步回写 `EmpiricalSpec` 与 `ModelPlan`；探针 helper 已拆到 `_probe_helpers.py`，避免 file-size hard gate。
  - 主仓库依赖移除 `langchain-community` / `dashscope`，文档统一为 `ChatOpenAI` 调用 OpenAI 兼容接口；InMemorySaver 保持本地 CLI 当前策略。
  - `uv sync --extra dev`、漂移扫描与 `uv run scripts/check.py` 已通过。

## 下一步

- **MVP 本地 CLI 已完成**。所有 25+F26 个 feature passes=true，ReAct 子图已迁移到 `create_agent`。下一阶段方向由用户决定:
  - (a) 真实端到端联调:启动 CSMAR-Data-MCP / Stata-Executor-MCP 服务,配 DashScope API key,跑真实 UserRequest 验证 LLM + MCP 链路
  - (b) 技术债清理:stata-executor ruff+pyright 收口 / tests/ 纳入 ruff format 门禁
  - (c) Web 迭代启动:把 CLI 换成 HTTP/WS 适配层,checkpointer 升级为 SqliteSaver 以跨进程 resume
  - (d) 功能扩展:稳健性回归 / 异质性分析 / 可视化等非 MVP feature

## 未解决/卡点

- pyright strict 下 ChatOpenAI API key 参数类型仍需在 clients/llm.py 中保留 type: ignore 注释
- WorkflowState total=False 导致所有 state key 访问需要 type: ignore[reportTypedDictNotRequiredAccess]
- langgraph 1.1.6 缺少公开类型桩, `StateGraph.add_node` / `.add_edge` / `.compile()`, `BaseChatModel.bind_tools()` / `.with_structured_output()`, `BaseTool.invoke()` / `.ainvoke()`, `Runnable.invoke()` / `ainvoke` / `aget_state` 被 pyright strict 判 reportUnknownMemberType, 统一通过 `# pyright: ignore[reportUnknownMemberType]` 压制
- `@tool` 装饰器在 pyright strict 下需 `# pyright: ignore[reportUntypedFunctionDecorator, reportUnknownVariableType, reportUnknownArgumentType]` 压制
- pandas 在 pyright strict 下大量 reportUnknownMemberType,F20 采用 `cast("Any", ...)` + `# pyright: ignore` 组合
- ruff RUF001/RUF002/RUF003 已在 pyproject 中 ignore（中文 docstring/注释采用全角标点为项目约定）；但 Field description 仍要避免同形希腊字母 α/β/γ
- `stata-executor/` 的 ruff/pyright 收口尚未做；临时 ruff 检查暴露 import 排序、相对导入、SIM103 等既有问题
- `stata-executor/` 临时 pytest 在当前环境触发 pytest capture 临时文件 `FileNotFoundError`，需后续在子仓库单独定位
- F20 数据清洗节点已从 pandas REPL 重构为 DuckDB SQL；覆盖率阈值已提到 `Settings.cleaning_coverage_threshold`（不再硬编码）
- F22 `actual_sign` 已改为节点端从 Stata log / result_text 确定性解析,不再由 LLM 自报
- F22/F21 要求 do 文件内部用 `log using` 显式指定绝对路径,否则节点在 `_assert_file_exists` 阶段 raise (prompt 已强制约束)
- `scripts/check.py` 的 ruff format 仅检查 `src/harness_stata`,不覆盖 `tests/` 与 `scripts/`,导致测试代码可能存在格式漂移
- F23 用 `InMemorySaver` 作为 MVP 本地单进程 checkpointer;若后续需要跨进程 resume 可替换为 SqliteSaver
- F24 CLI 为单进程阻塞模式,不支持长会话/Web 场景 (与 D2 决策一致);Web 迭代需要重新设计 HITL 适配层
