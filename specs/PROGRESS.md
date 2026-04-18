# 项目进度

## 当前焦点

F14 完成：`clients/stata.py` 通过 langchain-mcp-adapters 暴露 stata-executor 工具，配置读取同步收紧为"仅从 `.env`"。下一步推进 F15（probe 子图骨架）与 F19（generic_react 工厂）。

## 当前上下文

<!-- 每个会话覆盖此部分。保持简洁。 -->

- F14 完成：
  - `src/harness_stata/clients/stata.py` 镜像 csmar 实现，`MultiServerMCPClient` 以 `python -m stata_executor.adapters.mcp` 启动子进程，经子进程 `env` 注入 `STATA_EXECUTOR_STATA_EXECUTABLE` / `STATA_EXECUTOR_EDITION`
  - 新增用户硬约束：所有配置仅从项目根 `.env` 读取、禁止系统环境变量回退。`config.py` 切换到 `dotenv_values(ENV_PATH)`，主进程不再调用 `os.environ.get`
  - `config.Settings` 新增 `stata_executable`（必填）与 `stata_edition`（默认 `mp`）
  - `tests/conftest.py::_safe_env` 由 `monkeypatch.setenv` 改为 monkeypatch `config._load_env`，避免污染进程环境
  - `packages/stata-executor/` 首次纳入版本管控
  - `pyproject.toml` 新增 `python-dotenv>=1.0.0` 依赖
- import-linter 的 `[nodes/subgraphs→packages]` 契约已涵盖 `stata_executor`，`nodes/` 与 `subgraphs/` 访问 stata 必须经 `clients/stata.py`
- 质量门禁 9/9 通过

## 下一步

1. F15：`subgraphs/probe_subgraph.py` 三节点骨架（dispatcher → react → handler）+ `per_variable_max_calls` 预算
2. F19：`subgraphs/generic_react.py` 可复用 ReAct 工厂（agent + tool_executor + should_continue）
3. F15/F19 任一就绪后可推进 F16（Hard/Soft 分支 + 替代变量回写）与 F20（数据清洗节点）

## 未解决/卡点

- pyright strict 下 ChatTongyi 缺少部分类型桩，clients/llm.py 中有 type: ignore 注释
- WorkflowState total=False 导致所有 state key 访问需要 type: ignore[reportTypedDictNotRequiredAccess]
- ruff RUF001/RUF002 对中文全角标点与同形希腊字母的检查：docstring 与 Field description 中避免使用全角标点（逗号、句号、括号等）与 α/β/γ
- 主 `.venv` 缺 `prettytable`（csmarapi 的运行时依赖）：`scripts/check.py` 已 9/9 通过，但若要手动跑 csmar-mcp 子包单元测试会 ImportError；修复方案待定
- `packages/stata-executor/` 的 ruff/pyright 收口尚未做（类比 csmar-mcp 已完成的技术债），留给独立会话
