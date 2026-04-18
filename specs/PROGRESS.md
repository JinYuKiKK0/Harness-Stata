# 项目进度

## 当前焦点

F13 CSMAR MCP 客户端落地。下一步按 feature_list.json 推进 F14 stata-executor 客户端，随后解锁 F15（probe 子图）与 F18（数据下载节点）。

## 当前上下文

<!-- 每个会话覆盖此部分。保持简洁。 -->

- 本会话交付 F13：`src/harness_stata/clients/csmar.py` 新增异步 contextmanager `get_csmar_tools()`，通过 `langchain-mcp-adapters` 的 `MultiServerMCPClient` 以 stdio 子进程方式拉起 `packages/csmar-mcp`，用 `sys.executable -m csmar_mcp` 避免跨平台解释器漂移
- `config.py` 的 `Settings` 扩展 `csmar_account` / `csmar_password` 字段并从 `CSMAR_ACCOUNT` / `CSMAR_PASSWORD` 环境变量读取，缺失时抛 `RuntimeError`
- `tests/conftest.py` 的 dummy env fixture 追加两个 CSMAR 变量，F09–F12 测试链路无回归
- 质量门禁 6/6 通过（14 tests）

## 下一步

1. F14：`clients/stata.py` 通过 langchain-mcp-adapters 暴露 stata-executor-mcp 工具集（镜像 F13 结构）
2. F13/F14 双客户端就绪后可推进 F15（probe_subgraph 骨架）与 F19（generic_react 工厂）

## 未解决/卡点

- pyright strict 下 ChatTongyi 缺少部分类型桩，clients/llm.py 中有 type: ignore 注释
- WorkflowState total=False 导致所有 state key 访问需要 type: ignore[reportTypedDictNotRequiredAccess]
- ruff RUF001/RUF002 对中文全角标点与同形希腊字母的检查：docstring 与 Field description 中避免使用全角标点（，。（）等）与 α/β/γ；F13 clients/csmar.py docstring 已改写为英文
