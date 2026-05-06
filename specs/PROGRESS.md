# 项目进度

## 当前焦点

`descriptive_stats` (F21) / `regression` (F22) 端到端 smoke 在 3 个 fixture 上全部通过,0 次 ReAct 自愈 1 次 run_inline 即终止。

## 当前上下文

<!-- 每次任务完成覆写此部分，删除之前会话的内容。保持简洁。 -->

- 本次会话 — 给 Stata 节点(`descriptive_stats` / `regression`)补 RTF 三线表导出能力 + 跨列行对齐机制约束:
  - **prompt 增量**(机制式正向表述,非禁令清单):两 prompt 各加 `## 表格导出` 段。`descriptive_stats.md` 推荐 `estpost summarize` → `esttab using "<rtf_table_path>", cells(...) booktabs replace`。`regression.md` 写明跨列对齐机制——`esttab` 以变量名为行键合并 `eststo` 结果,缺失单元格自动留空,因此跨 `eststo` 必须用严格相同变量名(case-sensitive),让模型自行推出"别为每列单写表"。`<reminder>` 末尾追加"`rtf_table_path` 已通过 `esttab using` 成功导出"终止条件。
  - **协议层接管 RTF 路径**:文件名规范 `01_descriptive_stats.rtf` / `02_regression.rtf` 由节点常量 `_RTF_FILENAME` 固化,**不进 prompt**(避免双源契约)。`_stata_agent.py` 的 `_resolve_workspace` 改为 public `resolve_stata_workspace`;节点先取 workspace,拼出 `<workspace>/<filename>` 绝对路径,渲染进 HumanMessage `<inputs>` 的 `## rtf_table_path` 字段,再把同一 workspace 传给 `run_stata_agent`。后续 robustness/heterogeneity 节点按 `03_*.rtf` / `04_*.rtf` 顺延即可。
  - **state schema 同步**:`DescStatsReport` / `RegressionResult` 各加 `rtf_table_path: str` 字段;两节点返回值新增 `str(rtf_path)`;`docs/state.md` 字段表更新。
  - **测试更新**:两个节点的 `_build_human_prompt` 测试加入 rtf_path 参数与"prompt 含 rtf_table_path / 路径字面量 / esttab using"断言。
  - **质量门禁**:pytest / ruff lint / ruff format / pyright / import-linter 全 PASS;custom lint 仅存量 ERROR(`probe/pure.py 627 行`)+ 36 WARN,本次未引入新失败。

- 上次会话 — 修复 langchain-mcp-adapters 0.2.x 三处适配缺口 + Stata `case(preserve)` 契约硬化,3 fixture × 2 节点共 6 个端到端跑全部 0 次 ReAct 自愈通过:
  - **MCP 适配修复**(`src/harness_stata/nodes/_stata_agent.py`):
    1. 新增 `_unwrap_mcp_payload` helper 把 adapter 0.2.x 的 `list[ContentBlock]` 形态(`[{"type":"text","text":"<json>","id":...}, ...]`)归一为原生 dict;`_doctor_precondition` 与 `_make_run_inline_wrapped` 共用。
    2. `run_inline` 闭包 `try ... except ToolException as exc: raw = str(exc)` —— adapter 在 `CallToolResult.isError=True` 时直接 raise,error_msg 即完整 ExecutionResult JSON;捕获后回流给 LLM 走 ReAct 自愈路径。
    3. `_extract_artifacts` 改为以 run.log 为锚点从父目录推 input.do —— stata-executor `collect_artifacts` 在 `stage_inline_input` 之后取 snapshot,差分把 input.do 误判为"未变更"漏报,绕开。
  - **Stata 列名契约硬化**(prompt 改 4 行,fixture/代码/equation 全不动):
    1. `prompts/data_cleaning.md:16` "snake_case 等价"→ **字节级一致**;主键照搬 `key_fields` 源字段名。
    2. `prompts/descriptive_stats.md:9` 与 `regression.md:9` 显式 `import delimited "...", case(preserve) clear`(治根:Stata 17 默认 `case(lower)` 把大写表头小写化)。
    3. 两 prompt 第 10 行删除"csv 首行若有大小写差异先 rename 对齐"防御层(`feedback_no_defense_layering`:契约硬化后防御层即诱因)。
  - **决策依据**:`requirement_analysis` 节点 EmpiricalSpec 命名是 PascalCase + 大写缩写(论文学术风格),`model_construction.md` 的 LaTeX equation 范本同样 PascalCase。.harness 历史 trace 显示真实 data_cleaning 输出列名一直是字节级吻合(`Bankcd / NPL / LLR / Size / ROA / CAR / GDPg / ProvinceName`),把"已成立的事实"硬化为契约,比让 LLM 改用 snake_case + 改 LaTeX 学术风格代价小得多。
  - **新增纯代码测试**(`tests/nodes/test_stata_agent.py`,13 测全 PASS):`_unwrap_mcp_payload` 五态(dict/str-JSON/str-非JSON/list-text块/list-空/list-无text)+ `_extract_artifacts` 五态(同 job 共存/缺 run.log/缺 input.do/无 succeeded/多 succeeded 取末)。
  - **6 个 fixture × 节点端到端跑 trace 矩阵**(全 success / 0 errors / 1 次 run_inline / 4 行 timeline):
    - 01 capital_structure_roa: desc → `desc_stats_report.summary` 含 8 变量 xtsum 方差分解;regr → Leverage 系数 -0.0211,sign_check.consistent=True
    - 02 digital_finance_liquidity: desc → 2,220 obs / 231 银行;regr → DIFI 系数为正,sign_check.consistent=True
    - 03 fintech_bank_npl: desc → 4,052 obs / 484 银行 / 30 省份;regr → DIF 系数为负,sign_check.consistent=True
  - **`docs/pitfalls.md` 新增 4 条 `[依赖坑]`**:adapter 0.2.x 返回 list / `isError=True` raise ToolException / stata-executor 漏报 input.do / Stata `import delimited` 默认 case(lower)。
  - **质量门禁**:pytest(13 新增 + 全量回归)/ ruff lint / ruff format / pyright / import-linter 全 PASS;custom lint 仅存量 ERROR(`probe/pure.py 627 行`)+ 36 WARN,本次未引入新失败(`_stata_agent.py` 新增 ~40 行后越过 300 行阈值多出 1 条文件大小 WARN,可接受)。

- 上次会话 — 为 `descriptive_stats` 与 `regression` 铺设隔离单跑前置资源:
  - **决策**(用户 3 项采访锁定):单一 `input_state.json` 累加 `merged_dataset`+`model_plan` 切片 / `model_plan` 手工编写但严格模拟 model_construction 节点真实输出风格 / 本次只铺 fixture + 注册 registry,真 Stata+LLM 端到端 smoke 由用户在另起环境手动跑。
  - **运行时视野修正**(用户提醒后第二轮重写,首版被推翻):
    - **拓扑事实**:`requirement_analysis → model_construction → data_probe → ... → data_cleaning → descriptive_stats → regression`,**model_construction 在 data_cleaning 之前**——它产出 `model_plan` 时只看 `EmpiricalSpec`,看不到 csv 列名/大小写。首版 fixture 把 `i.bankcd`/`i.Bankcd`(csv 真实列大小写)写进 equation 是双重失真:既违背"运行时视野"边界,又违背 model_construction prompt 的 LaTeX 强制契约。
    - **重写遵循 prompts/model_construction.md 契约**:`model_type` 取 5 个中文标签(此处全为 `双向固定效应面板模型`);`equation` 用 LaTeX 源码 + `$$...$$` 包裹 + `\alpha / \beta_1 / \gamma_k Controls_{k,i,t} / \mu_i / \delta_t / \varepsilon_{i,t}`,**禁止 Unicode 字形**且控制变量统一为向量形式不逐一展开;`rationale` 30-80 字中文文献风;`data_structure_requirements` 3-5 条自然语言中文(数据组织形态/时间跨度/样本规模/平衡性)。
    - **副作用**:LaTeX `Leverage_{i,t}` 让 `test_equation_references_core_variable` 的 `(?![A-Za-z0-9_])` 边界否决,改为 `(?![A-Za-z0-9])`(允许 `_` 跟在变量名后)——同时仍排除 `DIF` 误命中 `DIFI`(后跟字母 I 仍被否决)。
    - `expected_sign` 不兜底 `ambiguous`——三 fixture 各按文献先验填具体符号(01=`-` 资本结构、02=`+` 数字金融对流动性创造、03=`-` 金融科技对 NPL),让 regression 节点 LLM 填 `sign_check.consistent` 时可观测。
    - 03 双 dependent (NPL/LLR) 处理:user_request.y_variable=NPL → equation 选 NPL 作被解释变量;LLR 仅在 descriptive_stats 阶段被覆盖,COV/USE/DIG 不进 baseline 避免分指数共线。
  - **文件变动**:
    - 三 fixture 的 `input_state.json` 各追加 `merged_dataset`(file_path 绝对路径 + columns 与 `merged.csv` 首行字节级一致 + row_count 与 csv 数据行数一致 + warnings=[])和 `model_plan` 切片。
    - `observability/registry.py` 注册 `descriptive_stats` (`@awrites_to` 装饰加 `# type: ignore[dict-item]`) 与 `regression` (返回 `RegressionOutput` TypedDict 也加 ignore);REQUIRED_FIELDS 加 `("empirical_spec","merged_dataset")` 与 `("empirical_spec","merged_dataset","model_plan")` 两行。
    - 新增 `tests/observability/test_registry.py`(4 测):NODE_REGISTRY 含两节点 + 字段元组字面量 + 两表 key 完全一致;**不**做 `_validate` 反射对齐(粒度不同)。
    - 新增 `tests/observability/test_fixtures.py`(6 参数化 × 3 fixture = 18 测):input_state 含 4 关键 key、merged.csv 是 fixture 同目录绝对路径文件、columns 与 csv 首行一致、row_count 与 csv 数据行数一致、core_hypothesis.variable_name ∈ independent 名册、expected_sign ∈ {+,-,ambiguous}、equation 含 core 变量名(单词边界,与 `_check_core_var_present` 同款 lookaround)。
  - **质量门禁**:pytest / ruff lint / ruff format / pyright / import-linter 全 PASS;custom lint 仅存量 ERROR(`probe/pure.py 627 行`)+ 35 WARN(`CLAUDE.md` 架构树未维护具体文件、几个文件超 300 行),本次未引入新失败。
  - **未做端到端 smoke**:对应 PROGRESS"下一步" (c) 后半段,需真 Stata + LLM API key 环境跑 `harness-stata node-run descriptive_stats --from-fixture 01_capital_structure_roa` 与 `... regression --from-fixture 01_capital_structure_roa`,核对 `.harness/runs/<run_id>/nodes/<node>/{input,update,output,events}.{json,jsonl}` 字段完整性。

- 上次会话 — 重写 `descriptive_stats` (F21) 与 `regression` (F22) 两个节点(此前为 `NotImplementedError` 空壳):
  - **架构决策**(经 4 轮采访 + Plan agent 跨视角 review 锁定):
    - LLM 工具集**仅 1 个**——`run_inline`(节点层用 `@tool` 闭包包装预填 `working_dir / artifact_globs / timeout_sec`);`doctor` 改为节点入口 precondition,不进 LLM 工具集;不暴露 `run_do`、不引入 `FileManagementToolkit`。理由:`run_inline` 内部就是 stata-executor 的 "写 input.do + 跑 Stata" 封装,LLM 直接交付字符串等价于 `write_file + run_do` 但少一类 IO 失败模式;FileManagementToolkit 的 `write_file` 也是全文 overwrite,patch 优势不存在。
    - **agent 严格定位**:do 代码作者 + Stata 报错修复者,不做实证决策(不修数据/不改方程/不扫稳健性)。
    - **成功判定分工**:agent 看 `exit_code/result_text` 软判;变量覆盖率 / 核心解释变量命中由节点层 deterministic 校验(post-check),失败 raise 不重启 agent。
    - **失败兜底**:超轮 / 缺结构化输出 / 缺成功执行 / `bootstrap_error` / post-check 失败 / 缺 artifacts 六类失败统一 dump 到 `<workspace>/_failure/dump.txt` 后 raise。
  - **关键实现细节**(Plan agent review 修正了原方案 5 处):
    - `do_file_path / log_file_path` 都从 `ExecutionResult.artifacts` 取(因 `artifact_globs=(".stata-executor/jobs/*/run.log", ".stata-executor/jobs/*/input.do")` 已含两者);**不在节点层另写一份 do**——消除游离副本与重复 IO,与 stata-executor 内部 `input.do` 字节级一致。
    - 工具用 `@tool` 闭包 + 节点局部 `history: list[dict]`,每次 `ainvoke` 后 append `ExecutionResult` dict,绕开"从 ToolMessage 文本反 parse JSON"的脆弱链路。
    - **超轮 dump 必须自装 `create_agent`**(不复用 `_agent_runner.run_structured_agent`),才能 catch `ModelCallLimitExceededError` 并把 messages 落到 dump;`_agent_runner` 保持原状继续供 `data_cleaning` 使用。
    - post-check 字符串匹配先 `_strip_stata_noncode`(块/行尾/整行星号注释 + 双引号字面量),再用 `(?<![A-Za-z0-9_])varname(?![A-Za-z0-9_])` 大小写敏感匹配,防注释/字符串误命中;子串(`ROAB` vs `ROA`)、大小写差(`roa` vs `ROA`)由 lookaround + case-sensitive 否决。
    - artifact glob 单星(`jobs/*/run.log`)对应 stata-executor 单层 jobs 目录,不要写 `**`。
    - run_id 公式 `f"{int(time.time())}_{uuid.uuid4().hex[:8]}"` 与 stata-executor `runtime/__init__.py:89` 同款。
  - **文件变动**:
    - 新增 `src/harness_stata/nodes/_stata_agent.py`(310 行):`run_stata_agent` helper,按 `node_name + system_prompt + human_message + output_schema + iter_cap + post_check_fn` 装配一轮 ReAct 完整生命周期。
    - 重写 `nodes/descriptive_stats.py`(`@awrites_to("desc_stats_report")`,iter_cap=6) 与 `nodes/regression.py`(显式 `RegressionOutput` 双 slice,iter_cap=10)。
    - 重写 `prompts/descriptive_stats.md` 与 `prompts/regression.md`(纯静态 system prompt,严格按 `agent-node-prompting` skill checklist 通过:无双源契约、无工作流时序、无代码符号、决策判据非禁令)。
    - `config.py` 新增 `Settings.workspaces_root`(读 `HARNESS_WORKSPACES_ROOT`,默认 `<root>/workspaces`);严格遵守 `dotenv_values`-only 注入(无系统 env fallback)。
    - 新增 `tests/nodes/test_descriptive_stats.py` 与 `test_regression.py` 纯代码测试:`_validate` 早返三态、`_strip_stata_noncode` 各注释/字符串形式、`_check_*` 全覆盖/缺失/子串/大小写四态、HumanMessage 含 `<inputs>`+`<reminder>` 且无工作流时序泄漏、节点入口 raise 路径。无 LLM/MCP mock。
    - SignCheck `(variable_name, expected_sign, actual_sign, consistent)` 由 LLM 自填(state.py 中是单对象不是 list,只对 `core_hypothesis.variable_name` 一个变量做比对)。
  - **后续合并** — 用户指出 `_agent_runner.run_structured_agent` 与 `_stata_agent._run_react_loop` 是重复造轮子(装配代码逐行一致,差异只在异常处理策略)。合并:把 `run_structured_agent` 改为非抛异常接口,返回 `(payload | None, messages, AgentRunFailure | None)`,失败语义归调用方;`data_cleaning` 加 4 行显式 raise,`_stata_agent` 删 32 行 `_run_react_loop` 直接复用。结果:`_stata_agent.py` 273 行(回到 300 阈值以下,文件大小 WARN 消失)。
  - **质量门禁**:pytest / ruff lint / ruff format / pyright / import-linter 全 PASS;custom lint FAIL 项均为 PROGRESS.md 此前已记录的存量(`subgraphs/probe/pure.py` 627 行 ERROR、CLAUDE.md 架构树未维护具体文件的 WARN);本次未引入新失败。
  - **未做端到端 smoke**:需真 Stata + .env 配 `STATA_EXECUTOR_STATA_EXECUTABLE`+`HARNESS_WORKSPACES_ROOT`+LLM API key 的环境;按用户记忆 `feedback_pure_code_tests_only`,集成测试不进本仓,留作下一步手动验证。

- 上上次会话 — 修复 `descriptive_stats` 节点首次启动 stata-executor MCP 子进程时 `McpError: Connection closed`:
  - 根因:`clients/stata.py:43` 用 `args=["-m", "stata_executor.adapters.mcp"]`,而 `adapters/mcp.py` 顶层只定义 `main()` 函数、无 `if __name__ == "__main__":` 守卫,`adapters/` 也无 `__main__.py` → `python -m pkg.sub.module` 直接执行该模块顶层即结束 → 子进程 `exit 0`,MCP server 从未运行,父进程在 `session.initialize()` 收到 `Connection closed`(被 anyio TaskGroup 包成 `ExceptionGroup`)。
  - 修复:`args` 改为 `["-m", "stata_executor"]`,触发 `stata_executor/__main__.py` 的 `raise SystemExit(main())`;与 `clients/csmar.py` 的 `["-m", "csmar_mcp"]` 形态对齐。
  - 验证:`get_stata_tools()` 现在加载 doctor/run_do/run_inline 3 个工具;keep-alive stdin 实测下 broken entry 立即 `exit 0`,fixed entry 持续监听。
  - `docs/pitfalls.md` 新增 `[调试卡点]` 条目,沉淀"`McpError: Connection closed` @ `initialize()` ⇒ 先裸跑 `args` 命令观察是否秒退"诊断套路。

- 上次会话 — 按 `agent-node-prompting` skill 重构 `data_cleaning` 节点的 prompt 链路:
  - **system prompt 去双源契约**:删除整段「可用工具」(`run_sql` 的签名/返回规则在 tool docstring 已是真理之源);删除「终止与输出」中复述 `_CleaningOutput` 字段语义的部分,只留业务判据。
  - **`variable mapping contract` 上移**:从 HumanMessage(本轮可变区)搬到 system prompt(跨轮稳定区),提升 prompt cache 命中率。
  - **正向化禁令**:"不要从样本数值反推变换公式" → "依据 description 的业务语义";"不要凭空命名" → "必须取自 key_fields";"不要凭表名猜列名" → "依据 schema 与预览决策";删除冗余"不要重复执行等价查询"。
  - **删除 `output_path` 诱因**:不再渲染给 LLM,导出由节点接管;同步删除 "不要 COPY/EXPORT" 防御指令(消除诱因优于禁令)。
  - **HumanMessage 加 `<inputs>` / `<reminder>` XML 结构**:reminder 块复述两次自检 SQL + 终止动作,抵消 ReAct 多轮后的 recency bias。
  - **字段顺序按决策依赖深度重排**:source views(含 schema + 前 3 行预览)→ variables + analysis_granularity → topic → sample/time(降级)。
  - **`_register_sources` 顺手探查列结构与样本**:返回 `dict[view_name, _ViewMeta]`,在 prompt 内嵌 schema + 预览行,省去 LLM 自行 `DESCRIBE` / `SELECT LIMIT` 的工具回合。
  - 路径/视图名/source_table/标识符在 HumanMessage 中统一用反引号包裹,避免被模型误读为指令片段。
  - 测试 `test_data_cleaning_prompt_includes_variable_mappings` 重写为端到端构造 `_register_sources` 的形式,新增对 schema/preview/`<reminder>`/output_path 不被渲染的断言;8/8 测试通过。
  - 质量门禁:本次改动文件 ruff format/lint/pytest 全绿;pyright `int(df.iat[0,0])` 与其他文件 ruff format 失败均为存量问题,本次未引入新失败。

- 上上次会话 — 搭建 `src/harness_stata/observability/` 包(单向覆盖层,不侵入 nodes/graph/state/subgraphs):
  - `tracer.py` 双通道:**stream 通道** 用 `astream(stream_mode=["updates","values"], subgraphs=True)` 捕获节点 IO 与子图嵌套(namespace tuple → `nodes/<root>/sub_nodes/<child>/{input,update,output}.json`);**callback 通道** 继承 `BaseCallbackHandler` 在 `on_llm_*`/`on_tool_*` 写 `events.jsonl` 摘要 + `raw/<evt>.json` 完整体。
  - `store.py` `RunStore` 管 `.harness/runs/<run_id>/` 目录、JSONL 追加、`event_id` 单调、`.harness/latest` 文本指针(Windows symlink 不稳定故用文件)。
  - `loader.py` `FixtureLoader` 双源:`--from-run <id>`(unwrap NodeIOPayload)+ `--from-fixture <subdir>`(纯 WorkflowState dict);加 `validate_for_node` 校验关键字段。
  - `registry.py` `NODE_REGISTRY` = {data_probe, data_cleaning},`REQUIRED_FIELDS` 锁定每个节点的入口字段。新增节点单跑能力时只改这里。
  - `runner.py` `NodeRunner` 组装 minimal `StateGraph(START→node→END)` 注入 tracer 跑单节点。
  - `cli.py` 新增 `node-run <node> [--from-run|--from-fixture]` 子命令;`run` 命令的 `_drive_graph` 改写为 `tracer.run(graph, ..)`,interrupt-resume 共用同一 `RunStore`。
  - `studio.py` 显式不绑定 tracer(`langgraph dev` 跨 session 复用 graph 会污染单实例),docstring 写明边界。
  - 39 个 observability 测试通过;107 个全仓非集成测试通过(无回归)。
  - LangGraph 1.x astream 行为通过 spike 验证:namespace 是 `()` 或 `("parent:task_id",)` 形态、节点名来自 updates payload key、values 在 updates 之后。
  - 修订设计:plan 中"namespace tuple 第二段直接给子节点名"实际不成立(namespace 仅标识子图作用域,子节点名在 update payload key 中);通过 `namespace_path_segments` helper 推导 `nodes/<root>/sub_nodes/<child>/` 路径。
  - import-linter layers 加入 `harness_stata.observability`(cli > observability > graph > nodes > subgraphs > clients);CLAUDE.md 架构树同步;`.gitignore` 加 `.harness/` + 豁免 `input_state.json`。
  - 三个 fixture 子目录(`01_capital_structure_roa`/`02_digital_finance_liquidity`/`03_fintech_bank_npl`)生成 `input_state.json`(整合 request.json + data_cleaning_input.json 的 user_request + empirical_spec + downloaded_files)。
  - 质量门禁:check.py 失败项(ruff format / pyright / 文件行数 / 架构漂移)均为上次会话已记录的存量问题,本次新增代码无任何引入失败。

## 下一步

- **MVP 本地 CLI 已完成**,**面向 Claude 的可观测性基础设施已落地**。所有 25+F26 个 feature passes=true,ReAct 子图已迁移到 `create_agent`,`.harness/runs/` 持久化 trace 接管 LangSmith 在调试场景下的角色。下一阶段方向由用户决定:
  - (a) **基础设施端到端验证**:启动 CSMAR-Data-MCP / Stata-Executor-MCP 服务,配 DashScope API key,实际跑 `harness-stata node-run data_cleaning --from-fixture 01_capital_structure_roa` 与 `harness-stata node-run data_probe --from-fixture 01_capital_structure_roa`,验证 trace 字段、子图嵌套、LLM/tool 事件归属是否符合预期。
  - (b) 真实端到端全流程:跑 `harness-stata run` 真实 UserRequest 验证 LLM + MCP 链路 + 完整 trace 覆盖 8 节点。
  - (c) `descriptive_stats` 与 `regression` 节点已通过 3 fixture × 2 节点端到端 smoke,0 次 ReAct 自愈 1 次 run_inline 即终止,trace 字段完整,sign_check 与文献先验一致。
  - (d) 技术债清理:stata-executor ruff+pyright 收口 / tests/ 纳入 ruff format 门禁 / data_cleaning.py:202 Scalar/complex pyright 存量。
  - (e) Web 迭代启动:把 CLI 换成 HTTP/WS 适配层,checkpointer 升级为 SqliteSaver 以跨进程 resume。
  - (f) 功能扩展:稳健性回归 / 异质性分析 / 可视化等非 MVP feature。
