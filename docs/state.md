## 共享 State Schema 设计

### 1. 主图 state 结构

采用扁平结构，所有状态切片为 TypedDict 的顶层字段。

### 2. 子图 state 与主图 state 的边界

子图有独立的 state schema，与主图通过显式输入/输出映射交换数据，内部状态不泄漏到主图。

| 子图       | 从主图读入                              | 写回主图                                                                                   | 子图内部（不泄漏）                                                                   |
| ---------- | --------------------------------------- | ------------------------------------------------------------------------------------------ | ------------------------------------------------------------------------------------ |
| 数据探针   | EmpiricalSpec, ModelPlan                | ProbeReport, DownloadManifest, EmpiricalSpec\*(回写), workflow_status\*(hard_failure 时)   | variable_queue, current_variable, per_variable_call_count, messages, substitute_meta |
| 数据清洗   | EmpiricalSpec, DownloadedFiles          | MergedDataset                                                           | messages, iteration_count                                           |
| 描述性统计 | EmpiricalSpec, ModelPlan, MergedDataset | DescStatsReport                                                         | messages, iteration_count                                           |
| 基准回归   | ModelPlan, MergedDataset                | RegressionResult                                                        | messages, iteration_count                                           |

关键隔离项：

- `messages`（LLM 对话历史 / tool call 记录）是子图内部 ReAct 循环的驱动状态，体量最大，必须隔离，避免下游节点的 LLM 上下文被上游对话污染
- 探针子图存在额外一层嵌套（probe_subgraph → variable_react 内层 ReAct），内层的 messages 和 per_variable_call_count 对外层 probe_subgraph 也应隔离

### 3. State reducer 策略

所有主图切片均采用 overwrite 语义（后写覆盖），无需自定义 reducer。

理由：每个切片只有唯一写入者，无并行分支，不存在多节点同时写同一切片的冲突。探针对 EmpiricalSpec/ModelPlan 的回写本质是"用新版本替换旧版本"；ProbeReport 虽逐变量累积，但累积发生在子图内部，写回主图时是一次性写入完整对象。

### 4. 工作流级元数据

主图 state 中增加一个显式终态字段：

- `workflow_status: "running" | "success" | "failed_hard_contract" | "rejected"`

各节点写入时机：

- 初始值为 `"running"`
- 数据探针 hard_failure 时写入 `"failed_hard_contract"`
- HITL rejected 时写入 `"rejected"`
- 基准回归正常完成时写入 `"success"`

不额外设置 error_message 字段——失败详情已由 ProbeReport 承载。不额外设置 run_id / 时间戳——由 LangGraph 的 thread_id 与 checkpointing 机制覆盖。

### 5. 状态切片内部字段定义

#### 入口输入

**UserRequest**

由 CLI 入口或 graph.invoke() 调用方写入,需求解析节点读取。

| 字段           | 类型                                            | 说明                         |
| -------------- | ----------------------------------------------- | ---------------------------- |
| x_variable       | str                                             | 核心解释变量描述             |
| y_variable       | str                                             | 被解释变量描述               |
| sample_scope     | str                                             | 样本范围 e.g. "A股上市公司"  |
| time_range_start | str                                             | 起始时间                     |
| time_range_end   | str                                             | 结束时间                     |
| data_frequency   | "yearly" \| "quarterly" \| "monthly" \| "daily" | 数据频率                     |

#### 共享类型

**VariableDefinition**

| 字段          | 类型                                      | 说明                         |
| ------------- | ----------------------------------------- | ---------------------------- |
| name          | str                                       | 变量名 e.g. "ROA"            |
| description   | str                                       | 变量含义 e.g. "总资产收益率" |
| contract_type | "hard" \| "soft"                          | Hard/Soft 标签               |
| role          | "dependent" \| "independent" \| "control" | Y / X / 控制变量             |

**VariableSource**

| 字段     | 类型 | 说明       |
| -------- | ---- | ---------- |
| database | str  | 数据库名   |
| table    | str  | 表名       |
| field    | str  | 字段名     |

**SubstitutionTrace**

| 字段                    | 类型 | 说明             |
| ----------------------- | ---- | ---------------- |
| original                | str  | 原始变量名       |
| reason                  | str  | 替代原因         |
| substitute              | str  | 替代变量名       |
| substitute_description  | str  | 替代变量描述     |

#### EmpiricalSpec

由需求解析节点写入。

| 字段                 | 类型                                            | 说明                         |
| -------------------- | ----------------------------------------------- | ---------------------------- |
| topic                | str                                             | 研究选题                     |
| variables            | list[VariableDefinition]                        | 变量清单（Y + X + 控制变量） |
| sample_scope         | str                                             | 样本范围 e.g. "A股上市公司"  |
| time_range_start     | str                                             | 起始时间                     |
| time_range_end       | str                                             | 结束时间                     |
| data_frequency       | "yearly" \| "quarterly" \| "monthly" \| "daily" | 数据频率                     |
| analysis_granularity | str                                             | 分析粒度 e.g. "公司-年度"    |

说明：不设 primary_keys / time_key 字段。概念粒度由 analysis_granularity 承载；实际数据库字段名在探针阶段下钻 schema 后才能确定，记录在 DownloadManifest 的 key_fields 中向下游传递。

#### ModelPlan

由模型与基准线构建节点写入。

| 字段                        | 类型           | 说明                                                          |
| --------------------------- | -------------- | ------------------------------------------------------------- |
| model_type                  | str            | 模型类型 e.g. "双向固定效应面板模型"                          |
| equation                    | str            | 数学方程 e.g. "Y_it = α + β₁X_it + γ'Z_it + μ_i + λ_t + ε_it" |
| core_hypothesis             | CoreHypothesis | 核心解释变量的预期符号                                        |
| data_structure_requirements | list[str]      | 模型对数据结构的要求 e.g. ["面板结构", "至少两期"]            |

**CoreHypothesis**

| 字段          | 类型                      | 说明           |
| ------------- | ------------------------- | -------------- |
| variable_name | str                       | 核心解释变量名 |
| expected_sign | "+" \| "-" \| "ambiguous" | 预期符号       |
| rationale     | str                       | 经济学依据     |

说明：本期只做核心 X→Y 系数符号预测，故 core_hypothesis 为单个对象而非列表。

#### ProbeReport

由数据探针节点写入。

| 字段             | 类型                        | 说明              |
| ---------------- | --------------------------- | ----------------- |
| variable_results | list[VariableProbeResult]   | 逐变量探测结果    |
| overall_status   | "success" \| "hard_failure" | 探针总体结论      |
| failure_reason   | str \| None                 | Hard 失败时的原因 |

**VariableProbeResult**

| 字段               | 类型                                                                               | 说明          |
| ------------------ | ---------------------------------------------------------------------------------- | ------------- |
| variable_name      | str                                                                                | 变量名        |
| status             | "found" \| "substituted" \| "not_found"                                            | 可得性状态    |
| source             | {database: str, table: str, field: str} \| None                                    | 数据来源定位  |
| record_count       | int \| None                                                                        | 记录计数      |
| substitution_trace | {original: str, reason: str, substitute: str, substitute_description: str} \| None | Soft 替代溯源 |

#### DownloadManifest

由数据探针节点写入。以表为单位组织下载任务，同表多字段合并为一个任务，减少下游清洗的 join 次数与故障率。

| 字段  | 类型               | 说明         |
| ----- | ------------------ | ------------ |
| items | list[DownloadTask] | 下载任务清单 |

**DownloadTask**

| 字段            | 类型      | 说明                                        |
| --------------- | --------- | ------------------------------------------- |
| database        | str       | 目标数据库                                  |
| table           | str       | 目标表                                      |
| key_fields      | list[str] | 主键/时间键字段（探针下钻 schema 后确定）   |
| variable_fields | list[str] | 变量字段                                    |
| variable_names  | list[str] | 对应的变量名（与 variable_fields 一一对应） |
| filters         | dict      | 过滤条件（时间范围、样本筛选等）            |

#### hitl_decision

由 HITL 节点写入。轻量审批标记，不承担数据传递职责。

| 字段       | 类型        | 说明                                                 |
| ---------- | ----------- | ---------------------------------------------------- |
| approved   | bool        | 是否审核通过                                         |
| user_notes | str \| None | 用户审核备注（approved 时可选；rejected 时必填非空） |

说明：HITL 节点依据 approved 联动写入 `workflow_status`——approved 时不写入（保持 `running`）、rejected 时写入 `"rejected"`，驱动主图 HITL 后的条件边。节点通过 langgraph `interrupt()` 原语暂停图执行，外部调用方通过 `Command(resume={approved, user_notes})` 续图；若 resume 值不合法（非 dict、缺 approved、或 approved=False 且 user_notes 为空），节点最多 3 次重新 `interrupt()` 让调用方重填，仍失败则抛 `ValueError`。

#### DownloadedFiles

由数据批量获取节点写入。

| 字段  | 类型                 | 说明         |
| ----- | -------------------- | ------------ |
| files | list[DownloadedFile] | 下载文件清单 |

**DownloadedFile**

| 字段           | 类型      | 说明                                  |
| -------------- | --------- | ------------------------------------- |
| path           | str       | 文件路径                              |
| source_table   | str       | 来源表                                |
| key_fields     | list[str] | 从 DownloadTask 继承的主键/时间键字段 |
| variable_names | list[str] | 该文件包含的变量                      |

#### MergedDataset

由数据清洗节点写入。

| 字段      | 类型      | 说明           |
| --------- | --------- | -------------- |
| file_path | str       | 合并后长表路径 |
| row_count | int       | 行数           |
| columns   | list[str] | 列名清单       |

#### DescStatsReport

由描述性统计节点写入。Terminal 产出物，面向用户阅读，不被下游节点消费。

| 字段          | 类型 | 说明                     |
| ------------- | ---- | ------------------------ |
| do_file_path  | str  | do 文件路径              |
| log_file_path | str  | Stata 日志路径           |
| summary       | str  | LLM 生成的描述性统计概述 |

#### RegressionResult

由基准回归节点写入。Terminal 产出物，包含结构化的符号校验。

| 字段          | 类型      | 说明                     |
| ------------- | --------- | ------------------------ |
| do_file_path  | str       | do 文件路径              |
| log_file_path | str       | Stata 日志路径           |
| sign_check    | SignCheck | 核心系数的符号一致性校验 |
| summary       | str       | LLM 生成的回归结果概述   |

**SignCheck**

| 字段          | 类型 | 说明           |
| ------------- | ---- | -------------- |
| variable_name | str  | 核心解释变量名 |
| expected_sign | str  | 预期符号       |
| actual_sign   | str  | 实际符号       |
| consistent    | bool | 是否一致       |
