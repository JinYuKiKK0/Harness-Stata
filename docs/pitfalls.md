# 踩坑与卡点笔记

本文档记录三方依赖反直觉行为、调试卡点根因、代码债。**不收功能性待办**。
由 Claude Code 主动维护,用户负责删减低密度内容,把元数据压进标题行,正文不超过两段。

**条目格式约定**:
- `### [x] 标题 — [类型]` 已解决;`### [ ] 标题 — [类型]` 未解决
- 类型:`[依赖坑]` / `[调试卡点]` / `[代码债]`
- 已解决/未解决条目:`**现象/根因** — ...` + `**方案** — ...`(未解决留空)
- 代码债条目:`**问题** — ...` + `**影响** — ...`

---

## DashScope / LLM

### [x] `with_structured_output()` 默认 mode 报非法请求 — [依赖坑]
**现象/根因** — DashScope OpenAI 兼容端点不支持 LangChain 默认的 `json_schema` mode,直接调用 `ChatOpenAI(...).with_structured_output(Schema)` 会返回 InvalidParameter。必须显式指定 `method="function_calling"`。
**方案** — 全部 `.with_structured_output(...)` 调用必须传 `method="function_calling"`。

---

## MCP(langchain-mcp-adapters / csmar-mcp / stata-executor-mcp)

### [x] MCP `CallToolResult.structuredContent` 对 LLM/裸调用都不可见 — [依赖坑]
**现象/根因** — MCP 协议返回 `content`(文本块)+ `structuredContent`(结构化 dict)两部分。`langchain-mcp-adapters` 把工具注册成 `response_format="content_and_artifact"`,把 `structuredContent` 塞进 `ToolMessage.artifact.structured_content`,LLM 在对话历史里只看得到 `content`。两条裸调用路径都拿不到结构化数据:
1. 节点直接 `await tool.ainvoke(args)` → 只返回 content list,artifact 被丢。
2. Agent 调用 MCP 工具 → ToolMessage 被回灌进 prompt,但 LLM 只读 content,artifact 不入上下文。

**方案** — 节点侧统一走 `src/harness_stata/clients/mcp.py` 的 `call_structured_mcp_tool()`:用 ToolCall 协议调用,优先取 `artifact.structured_content`,否则 JSON 解码 content 文本。Agent 侧若需要 structuredContent 可见,需引入 mcp-interceptor 把结构化内容拼接进 content;但 `MultiServerMCPClient(tool_interceptors=[...])` 只在 `client.get_tools()` 路径生效,当前 `client.session(...) + load_mcp_tools(session)` 裸调用路径**不会**传递 interceptor(`load_mcp_tools` 自身有 `tool_interceptors` 参数但未传)— Agent 侧拦截器尚未启用。

---

## LangGraph / 状态机

(暂无条目)

---

## State / Schema

(暂无条目)

---

## 通用 / 跨组件

### [x] `python -m pkg.sub.module` 不会触发包的 `__main__.py` — [调试卡点]
**现象/根因** — `clients/stata.py` 的 `args=["-m", "stata_executor.adapters.mcp"]` 让子进程立即 `exit 0`，MCP client 在 `session.initialize()` 阶段抛 `McpError: Connection closed`(被 anyio TaskGroup 包成 `ExceptionGroup`)。`-m` 指向**模块**(`adapters/mcp.py`)时,Python 直接执行该模块顶层而不会去找上层包的 `__main__.py`;而该 `mcp.py` 顶层只定义了 `main()` 函数、没有 `if __name__ == "__main__": main()` 守卫,顶层执行完就结束 → MCP server 从未真正运行。子进程秒退 ⇒ stdout 流秒关 ⇒ 父进程发出的 `initialize` 请求收到 `Connection closed`。
**方案** — stdio 子进程的 `args` 必须指向**有 `__main__.py` 的包**(对照 `clients/csmar.py:42` 的 `-m csmar_mcp`)。已改为 `args=["-m", "stata_executor"]`,触发 `stata_executor/__main__.py:1-4` 的 `raise SystemExit(main())`。判定信号:`McpError: Connection closed` 出现在 `initialize()` 而非工具调用中 → 先把命令在终端裸跑,若**立即 exit 0**就是入口路径错。

### [x] DuckDB `read_csv(na_values=...)` 覆盖默认空串语义 — [依赖坑]
**现象/根因** — 不传 `na_values` 时,DuckDB sniffer 把 unquoted 空 cell `,,` 视为 NULL,数值列正常推断为 `DOUBLE`。一旦传入 `na_values=[...]`,该列表会**覆盖**(而非追加)默认 NULL 集合,空字符串不再被当作 NULL,任何含空 cell 的列都会回落到 `VARCHAR`——比不传还糟。
**方案** — `na_values` 列表必须始终包含 `""`。Harness-Stata 在 `src/harness_stata/nodes/data_cleaning.py::_NULL_TOKENS` 把 `""` 与 7 个 Excel 错误码(`#DIV/0!` 等)一起注入 `read_csv`,既消除脏值导致的 VARCHAR 退化,又保留默认空串语义。`tests/nodes/test_data_cleaning.py::test_register_sources_recovers_double_dtype_under_excel_pollution` 守住这条不变量。
