# 项目进度

## 当前焦点

csmar-mcp 技术债清理完成：移除 search 工具 + 纳入 lint 规范。下一步按 feature_list.json 推进 F14 stata-executor 客户端，随后解锁 F15（probe 子图）与 F18（数据下载节点）。

## 当前上下文

<!-- 每个会话覆盖此部分。保持简洁。 -->

- 本会话完成 csmar-mcp 子包重构（非 feature，技术债清理）：
  - 移除 2 个 search tool（`csmar_search_tables` / `csmar_search_fields`）及其 client / service / models / core-types / tests / 文档全链路。对外工具面 7→5，保留 list_databases / list_tables / get_table_schema / probe_query / materialize_query 5 个确定性工具，避免内部嵌套循环 + 多次 CSMAR API 调用触发上游限流
  - `packages/csmar-mcp/pyproject.toml` 新增 `[tool.ruff]` + `[tool.pyright]`（strict），`csmarapi/` 作为遗留官方 SDK 排除；`csmar_gateway.py` 作为 SDK 唯一边界使用 `# pyright: basic` 指令
  - `scripts/check.py` 扩展 3 项子包检查，质量门禁从 6 项升到 9 项
  - 相对父级 import 全部改为绝对 import；代码按 ruff + pyright strict 修缮
- 主应用调用侧零破坏（`clients/csmar.py` 通过 `load_mcp_tools` 动态加载，无 tool 名硬编码；当前已实装节点未使用 csmar tool）
- 质量门禁 9/9 通过

## 下一步

1. F14：`clients/stata.py` 通过 langchain-mcp-adapters 暴露 stata-executor-mcp 工具集（镜像 F13 结构）
2. F13/F14 双客户端就绪后可推进 F15（probe_subgraph 骨架）与 F19（generic_react 工厂）

## 未解决/卡点

- pyright strict 下 ChatTongyi 缺少部分类型桩，clients/llm.py 中有 type: ignore 注释
- WorkflowState total=False 导致所有 state key 访问需要 type: ignore[reportTypedDictNotRequiredAccess]
- ruff RUF001/RUF002 对中文全角标点与同形希腊字母的检查：docstring 与 Field description 中避免使用全角标点（，。（）等）与 α/β/γ；F13 clients/csmar.py docstring 已改写为英文
- 主 `.venv` 缺 `prettytable`（csmarapi 的运行时依赖）：`scripts/check.py` 已 9/9 通过，但若要手动跑子包 `uv run python -m unittest discover -s tests` 会 ImportError；修复方案待定（加入主 dev 依赖或 `uv sync` 子包工作区）
