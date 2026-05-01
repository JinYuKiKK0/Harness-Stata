# 项目进度

## 当前焦点

data_download 节点过滤 CSMAR 返回的非数据附属文件(如 [DES] 字段字典 .txt),避免污染 downloaded_files.files 导致 data_cleaning 在 DuckDB 登记阶段抛 ValueError。

## 当前上下文

<!-- 每次任务完成覆写此部分，删除之前会话的内容。保持简洁。 -->

- 本次会话 — 修复 data_cleaning 因 .txt 字段字典文件而 raise `unsupported source format '.txt'` 的链路缺陷:
  - 根因:CSMAR `csmar_materialize_query` 把数据 .csv 与同包字段字典 `*[DES][csv].txt` 一起放进 `mat_result["files"]`;`data_download._extract_file_paths` 未做后缀白名单,把字典文件也透传成 `DownloadedFile`;`data_cleaning._register_sources` 仅识别 `.csv/.xlsx/.xls`,撞上 `.txt` 直接 ValueError。
  - 修复:在 `src/harness_stata/nodes/data_download.py` 增加 `_DATA_FILE_SUFFIXES = {.csv, .xlsx, .xls}` 白名单,`_extract_file_paths` 跳过非数据后缀;若过滤后没有任何数据文件则 RuntimeError(保留硬失败语义)。DES 文件仍留在 task_dir 内,需要时按需读取,但不再混入数据流。
  - 质量门禁:check.py 的 ruff format / pyright / 文件行数 / 架构漂移失败均为存量问题,与本次改动无关;data_download.py 自身通过。

- 上次会话 — 显式弃用 SOFT 替代变量机制,把 data_probe 子图回到一次性单向流水线:
  - 决策动因:替代变量链路对 LLM 输出与 spec/plan 改写的耦合过深(verification + coverage 两处入口、跨阶段 sub_meta 维护、二次 planning agent 重跑、回写 EmpiricalSpec / ModelPlan),实跑收益远低于复杂度成本。
  - 状态 schema:删除 `SubstitutionTrace`、`VariableProbeResult.substitution_trace`、`VariableProbeResult.status` 中的 `"substituted"` literal。
  - 子图:`ProbeState` 移除 `substitute_meta`/`substitute_queue`/`substitute_round`/`pipeline_initialized`;`build_probe_subgraph` 移除 `substitute_max_rounds` 参数;`coverage_validation_handler` 后无重试回边,直接 END。
  - Helper:删除 `SubstituteMeta`、`maybe_build_substitute`、`build_substituted_result`、`replace_variable_in_spec`、`replace_variable_in_model_plan`、`_replace_token`、`PendingValidation.is_substitute_task`、`VariableProbeFindingModel.candidate_substitute_*` 三字段、`BucketVariableFinding.candidate_substitute_*` 三字段、`_build_not_found_with_substitute`。
  - 节点:`verification_phase` / `fallback_react_phase` SOFT not_found → 直接 `build_not_found_result`;`coverage_validation_handler` SOFT 失败 → 直接 `build_not_found_result`;通过分支不再回写 spec/plan。
  - 配置:`Settings.substitute_max_rounds` + `_parse_non_negative_int` helper 删除;`.env` 删除 `HARNESS_SUBSTITUTE_MAX_ROUNDS=1`。
  - data_probe 节点:`DataProbeOutput` 移除 `empirical_spec`/`model_plan` 字段;不再回写。
  - Prompt:`data_probe_verification.md` 删除「Substitute 候选」「跨频率替代禁令」两段;`data_probe_fallback.md` 删除「Substitute 候选」段(按用户要求只删除不补反向防御文案,schema 删字段已构成完整防御)。
  - HITL:`hitl.py` 删除 `_format_substitution_trace` 函数与 `_SECTION_HEADERS["substitution"]` 条目。
  - 文档:`docs/state.md`、`docs/empirical-analysis-workflow.md` 同步;`docs/data_probe.md` JSON 快照清理。
  - 测试:`tests/subgraphs/test_probe_subgraph.py` 删除 `test_negative_substitute_rounds_rejected`;`tests/subgraphs/test_probe_helpers.py` 删除两个 substitute 用例;`tests/nodes/conftest.py` `make_probe_report` 移除 `substituted` 参数;`tests/nodes/test_hitl.py` 合并替代相关用例;`tests/test_cli.py` 移除 `substitution_trace` 键。
  - `uv run scripts/check.py` 6/6 通过。
  - 仍未验证:LangSmith / CLI 端到端确认子图行为(planning_agent 只跑一轮)。

## 下一步

- **MVP 本地 CLI 已完成**。所有 25+F26 个 feature passes=true，ReAct 子图已迁移到 `create_agent`。下一阶段方向由用户决定:
  - (a) 真实端到端联调:启动 CSMAR-Data-MCP / Stata-Executor-MCP 服务,配 DashScope API key,跑真实 UserRequest 验证 LLM + MCP 链路
  - (b) 技术债清理:stata-executor ruff+pyright 收口 / tests/ 纳入 ruff format 门禁
  - (c) Web 迭代启动:把 CLI 换成 HTTP/WS 适配层,checkpointer 升级为 SqliteSaver 以跨进程 resume
  - (d) 功能扩展:稳健性回归 / 异质性分析 / 可视化等非 MVP feature
