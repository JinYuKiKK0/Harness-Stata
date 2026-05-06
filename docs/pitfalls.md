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

### [x] `langchain-mcp-adapters` 0.2.x `ainvoke` 返回 `list[ContentBlock]` — [依赖坑]
**现象/根因** — `BaseTool.ainvoke({...})` 在裸 ToolNode 之外的调用路径下返回**内容块列表** `[{"type":"text","text":"<json>","id":"lc_..."}, ...]`,而不是字符串。`_convert_call_tool_result` (`langchain_mcp_adapters/tools.py`) 把 `CallToolResult.content` 转成 LC content blocks,`structuredContent` 单独走 artifact 通道,默认 `ainvoke` 不返回 artifact。早期代码 `payload = json.loads(raw) if isinstance(raw, str) else raw` 在 list 形态下既不进 JSON 分支也不是 dict,直接抛 `unexpected payload type list`,**descriptive_stats / regression 节点的 `_doctor_precondition` 与 `_make_run_inline_wrapped` 都中招**。
**方案** — `src/harness_stata/nodes/_stata_agent.py::_unwrap_mcp_payload` helper 归一处理 dict / str / list 三态:list 形态抽 `type=text` 的 text 块拼接后 JSON 解码。覆盖测试见 `tests/nodes/test_stata_agent.py`。

### [x] `langchain-mcp-adapters` 把 `CallToolResult.isError=True` raise 成 `ToolException` — [依赖坑]
**现象/根因** — 同上 0.2.x 适配器的 `_convert_call_tool_result` 在 `call_tool_result.isError=True` 时**直接 raise `ToolException(error_msg)`**(`tools.py:189`),而不是把失败结果作为正常返回值递给上层。stata-executor 的 `_build_result` 在 `execution.status == "failed"` 时设 `is_error=True`,导致 Stata 失败结果被包成异常一路上抛、ToolNode 没处理就崩。后果:ReAct 子图的"看错误修代码"自愈循环**永不发生**,LLM 看不到失败 ExecutionResult,节点直接 raise。
**方案** — `_make_run_inline_wrapped` 的 `run_inline` 闭包 `try ... except ToolException as exc: raw = str(exc)`。adapter 拼出的 `error_msg` 即完整 ExecutionResult JSON 字符串(stata-executor 把 dict.dump 后塞进 TextContent.text),还原为 raw → `_unwrap_mcp_payload` → history append → 返回给 LLM。

### [x] stata-executor `collect_artifacts` 漏报 `input.do` — [依赖坑]
**现象/根因** — `stata_executor/engine/executor.py::_execute_prepared_job` 的调用顺序是 `stage_inline_input(写 input.do)` → `write_wrapper_do` → `snapshot_artifacts` → 执行 → `collect_artifacts(差分)`。snapshot 在 input.do 已写入之后取,执行过程不再修改 input.do,差分逻辑 `before_snapshot.get(resolved) != marker` 把它判定为"未变更",**最终 ExecutionResult.artifacts 只列 run.log,不含 input.do**。`_extract_artifacts` 期待两文件并存,raise `succeeded ExecutionResult missing artifacts`。
**方案** — 不修 stata-executor 自身(它的 `result.json` 持久化里也只列 run.log,但 input.do 实际就在 `<jobs>/job_<ts>_<hash>/input.do`)。在 `_extract_artifacts` 以 run.log 为锚点,从其父目录直接拼 `input.do` 路径并 `is_file()` 校验存在,绕开差分逻辑。两文件由 stata-executor job 目录布局保证共存,推导稳健。

---

## Stata

### [x] `import delimited` 默认 `case(lower)` 强制把变量名小写化 — [依赖坑]
**现象/根因** — Stata 17 `import delimited "...", clear` 默认 `case(lower)`,把 csv 表头(无论原始大小写)一律转小写后落到 Stata 变量名。即便 csv 表头是 `ROA,Leverage,...`,加载后 `describe` 显示的变量名也是 `roa,leverage,...`。LLM 看到 EmpiricalSpec 是 PascalCase 自然写 `summarize ROA Leverage` → `variable ROA not found r(111)`,白白浪费一轮 ReAct 自愈。
**方案** — 跨节点契约 + 显式 case 选项双重保障:(1) `prompts/data_cleaning.md` 强制最终 csv 列名与 `EmpiricalSpec.variables[*].name` 字节级一致(含大小写),不做 snake_case / lower 等任何变换;(2) `prompts/descriptive_stats.md` 与 `prompts/regression.md` 显式要求 `import delimited "...", case(preserve) clear`,跳过 Stata 的 case(lower) 默认行为;(3) 删除"csv 首行若有大小写差异先 rename 对齐"防御层,`EmpiricalSpec.variables` 即真理之源,直接用。

### [x] `esttab` 不从文件名后缀推断格式;`booktabs` 是 LaTeX 模式专属选项 — [依赖坑]
**现象/根因** — `esttab using "out.rtf", ... booktabs replace` 产出的不是 RTF,而是带 `\toprule`/`\midrule`/`\bottomrule`/`\addlinespace` 的 LaTeX 源码,Word 打开就是一堆反斜杠看着像乱码。原因:`esttab` 的输出格式由显式选项(`rtf` / `tex` / `csv` / `html` 等)决定,**完全不看文件名后缀**;而 `booktabs` 是 LaTeX 的 `\toprule` 等命令开关,esttab 看到 `booktabs` 就切到 LaTeX 输出路径。
**方案** — `prompts/descriptive_stats.md` 与 `prompts/regression.md` 的"## 表格导出"段:推荐命令显式 `rtf` 选项,不叠加 `booktabs`;补一句机制说明"格式由显式选项控制,不从后缀推断;`rtf` 模式下 esttab 默认在表头/中/底各画一条横线,即三线表样式"。让 LLM 自然推出"用 rtf,不要 booktabs"。

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
