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

(暂无条目)
