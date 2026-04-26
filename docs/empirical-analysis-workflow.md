## 实证工作流链路设计

本期只做实证分析最小闭环，基准回归分析。

失败回滚范围：本期仅实现"数据探针阶段 Soft 变量替代并回写上游研究方案"这一条回滚路径；描述性统计异常与基准回归结果不符预期的回滚，留到二期随 Agent 架构改造一起处理。

### 节点总览

工作流拆分为 8 个节点。节点之间仅通过共享状态的切片耦合，下游节点只读上游节点已固化的状态切片，不直接依赖对象引用。

| #   | 节点                   | 执行形态  | 写入的状态切片                                                     |
| --- | ---------------------- | --------- | ------------------------------------------------------------------ |
| 1   | 需求解析               | 单轮 LLM  | EmpiricalSpec                                                      |
| 2   | 模型与基准线构建       | 单轮 LLM  | ModelPlan                                                          |
| 3   | 数据探针               | ReAct LLM | ProbeReport + DownloadManifest；可内部回写 EmpiricalSpec/ModelPlan |
| 4   | Human In the Loop      | 纯代码    | hitl_decision                                                      |
| 5   | 数据批量获取           | 纯代码    | DownloadedFiles                                                    |
| 6   | 数据清洗               | ReAct LLM | MergedDataset                                                      |
| 7   | 描述性统计分析执行验证 | ReAct LLM | DescStatsReport                                                    |
| 8   | 基准回归执行           | ReAct LLM | RegressionResult                                                   |

### 主图拓扑

```
START
  → 需求解析
  → 模型与基准线构建
  → 数据探针 ──[conditional]──┐
  │                           │
  │  ┌─ hard_failure ─────────┼──→ END
  │  │                        │
  │  └─ success ──→ HITL ──[conditional]──┐
  │                                       │
  │              ┌─ rejected ─────────────┼──→ END
  │              │                        │
  │              └─ approved ──→ 数据批量获取
  │                                → 数据清洗
  │                                → 描述性统计
  │                                → 基准回归
  │                                → END
```

**条件边：**

| 位置       | 条件                                       | 出口                      |
| ---------- | ------------------------------------------ | ------------------------- |
| 数据探针后 | Hard Contract 变量无法获取或数据结构不可得 | `hard_failure` → END      |
| 数据探针后 | 探针通过（含 Soft 替代成功）               | `success` → HITL          |
| HITL 后    | 用户审核通过                               | `approved` → 数据批量获取 |
| HITL 后    | 用户审核拒绝                               | `rejected` → END          |

其余全部为普通边（无条件直连）。两个 END 出口通过 state 中的状态标识区分成功终止与失败终止。

### ReAct 子图设计

#### 内联 create_agent（节点 6/7/8）

节点 6（数据清洗）、7（描述性统计）、8（基准回归）各自在节点内调用 `langchain.agents.create_agent`。tool 调用限制仅作为防止 LLM 空转烧 token 的安全兜底，全局 `max_iterations` 截断即可。

```
subgraph START
  → agent (LLM 推理与工具选择)
  → [should_continue]
      ├─ has_tool_calls AND iterations < max_iterations
      │     → tool_executor (执行工具调用，iteration_count += 1)
      │     → agent (循环)
      ├─ has_tool_calls AND iterations >= max_iterations
      │     → END (强制截断)
      └─ no_tool_calls
            → END (正常完成)
```

LangChain agent 内部负责模型循环与工具执行：

| 节点          | 类型 | 职责                        |
| ------------- | ---- | --------------------------- |
| agent         | LLM  | 推理、选择工具、生成输出    |
| tool_executor | 代码 | 执行 agent 发出的 tool 调用 |

各节点差异仅在绑定的 tools 集合与 system prompt：

| 节点       | tools                         |
| ---------- | ----------------------------- |
| 数据清洗   | 文件读写 + Python 代码执行    |
| 描述性统计 | 文件读写 + stata-executor-mcp |
| 基准回归   | 文件读写 + stata-executor-mcp |

#### 数据探针子图（节点 3）

数据探针与通用骨架不同：csmar_mcp 存在每日账号限流，需要**按变量**做调用预算控制，而非全局迭代上限。子图采用「变量级外循环 + 有预算上限的内层 ReAct」结构，并在外循环之外再叠一层「批量覆盖率验证」阶段——把字段定位（让不让 LLM 看 schema）和覆盖率验证（dry-run 拼 columns 看 row_count）显式拆成两个相互独立的事。

**工具暴露策略**：节点入口先调一次 `csmar_list_databases` 把已购数据库清单作为共享上下文注入子图（每个变量的 Agent 共用），然后只把字段发现工具集（`csmar_search_field` / `csmar_list_tables` / `csmar_bulk_schema` / `csmar_get_table_schema`）以白名单形式绑定给 Agent。`csmar_probe_query` 单独提取为 `probe_tool` 透传给子图工厂，由覆盖率验证阶段以代码方式批量调用，**不绑定**给 Agent。`csmar_materialize_query` / `csmar_refresh_cache` 完全不在本节点暴露。

```
probe_subgraph START
  → variable_dispatcher
  → variable_react (内层 ReAct，受 per_variable_max_calls 约束)
  → field_existence_handler ──[conditional]──┐
      │                                       │
      ├─ found                                 │  压入 validation_queue，
      │   回到 variable_dispatcher             │  manifest 留到覆盖率验证后再写
      │                                       │
      ├─ not_found AND Hard ───────────────────┼──→ END (hard_failure)
      │                                       │
      ├─ not_found AND Soft + 给出 substitute ─┤  替代变量塞回 discovery_queue，
      │   回到 variable_dispatcher             │  下一轮重新走字段发现
      │                                       │
      ├─ not_found AND Soft 无 substitute ─────┤  写 not_found result，继续下一个
      │                                       │
      └─ discovery_queue 空 ───────────────────┘──→ coverage_validator
                                              │
                                              ▼
  coverage_validator (纯代码批量调 csmar_probe_query)
      → coverage_validation_handler ──[conditional]──┐
          │                                          │
          ├─ can_materialize=true                    │  写 found/substituted result + 合并 manifest
          │                                          │
          ├─ can_materialize=false AND Hard ─────────┼──→ END (hard_failure)
          │                                          │
          ├─ can_materialize=false AND Soft + 候选 ──┤  替代变量塞回 discovery_queue，
          │   回到 variable_dispatcher               │  走字段发现 + 覆盖率新一轮
          │                                          │
          ├─ can_materialize=false AND substitute ───┤  链终止，记原变量名 not_found
          │                                          │
          └─ discovery_queue 与 validation_queue 空 ─┘──→ END (success)
```

子图五个节点：

| 节点                          | 类型       | 职责                                                                                                              |
| ----------------------------- | ---------- | ----------------------------------------------------------------------------------------------------------------- |
| variable_dispatcher           | 纯代码     | 从 discovery_queue 取下一个变量，设置当前变量上下文                                                                |
| variable_react                | 内层 ReAct | 针对当前单个变量执行字段发现（search_field / list_tables / bulk_schema / get_table_schema），受 per_variable_max_calls 约束 |
| field_existence_handler       | 纯代码     | 判定字段是否存在；found 推入 validation_queue，not_found 走 Hard/Soft 路由                                          |
| coverage_validator            | 纯代码     | 对 validation_queue 中每条候选批量调用 `csmar_probe_query`，把响应解码为 CoverageOutcome                            |
| coverage_validation_handler   | 纯代码     | 通过 → 写 ProbeReport / DownloadManifest；失败 → 等同 not_found 路由（hard 终止 / soft 触发 substitute / 链终止）  |

**设计要点：**

- 每个变量的 csmar_mcp 调用次数被隔离计数（`per_variable_max_calls` 通过 `ToolCallLimitMiddleware` 在 Agent 一轮里强制），不会出现某个难找的变量耗光全局预算
- Soft 替代变量被当作新任务塞回 discovery_queue，复用同一条内层 ReAct 路径以及随后的覆盖率验证；不需要额外的「替代搜寻」节点
- Agent 只判定字段在表中是否存在，**不再估算 record_count / 行数**——覆盖率与可物化由 `coverage_validator` 以代码批量验证（阈值 = `csmar_probe_query` 自带的 `can_materialize` 与 `invalid_columns`，与 MCP 服务端保持一致）
- 替代成功后由 coverage_validation_handler 把新变量回写到 `EmpiricalSpec`/`ModelPlan`（节点内部状态 mutation），并保留对原变量名的 SubstitutionTrace
- `data_download` 节点会再调一次 `csmar_probe_query` 取最新 validation_id；当前不复用本阶段的 validation_id 以避免 TTL 过期回滚成本（MCP 侧缓存命中开销极低）

---

### 需求解析

- input：用户填写的实证要求表单，必填：核心解释变量 X、被解释变量 Y、样本范围、时间范围、数据频率
- action：
  - LLM 以表单为准，初步推断 X、Y，并自行拟定基础的控制变量
  - 同时整理目标分析粒度、关键主键、时间键
  - 用户提供的 X、Y 纳入 Hard Contract，LLM 自行拟定的控制变量纳入 Soft Contract
- output：`EmpiricalSpec`——结构化实证要求、变量定义表与数据需求清单，变量以 Hard/Soft 区分来源

### 模型与基准线构建

- input：`EmpiricalSpec`
- action：
  - 基于样本范围、时间跨度与分析粒度确立数学方程式（如双向固定效应面板模型等）；模型形式同时决定数据探针需要验证的数据结构要求
  - 基于经济学常识为核心解释变量输出"预期符号判定基准线"，作为后续回归结果的验收依据
- output：`ModelPlan`——模型方程与预期符号基准线

### 数据探针

- input：`EmpiricalSpec` + `ModelPlan`
- action（拆为两阶段）：
  - **阶段一 字段发现（Agent）**：LLM 在白名单工具集（`csmar_search_field` / `csmar_list_tables` / `csmar_bulk_schema` / `csmar_get_table_schema`）下完成 `database → table → schema → 字段是否存在` 的下钻，只输出 `(database, table, field, key_fields, filters.condition?)` 与 status；**不再估算 record_count / 行数**
  - **阶段二 覆盖率验证（代码）**：对阶段一所有 found 的字段批量调用 `csmar_probe_query`（dry-run），用 MCP 自带的 `can_materialize` / `invalid_columns` 作为门禁；通过即写入 `DownloadManifest`，失败则等同 `not_found` 走与字段未找到一致的 Hard/Soft 路由
  - 若 Hard Contract 变量在任一阶段失败，立即整体硬失败
  - 若 Soft Contract 变量在阶段一失败但 Agent 给出 substitute 候选，候选会重新走完整两阶段（字段发现 + 覆盖率验证）；阶段二失败的 soft 变量同样可以触发 substitute 重试，substitute 任务再失败则链终止视为 `not_found`
  - 替代成功后须将新变量回写到 `EmpiricalSpec`/`ModelPlan`，若替代导致方程形式或预期符号基准线需要调整一并更新
- output：`ProbeReport`（可得性结论与替代溯源记录）+ `DownloadManifest`（具体到 database/table/field/过滤条件的下载参数清单）；以及被内部回写后的最新 `EmpiricalSpec`/`ModelPlan`
- 边界说明：回写属于节点内部的状态 mutation，不构成图结构上的反向边

### Human In the Loop

- input：`EmpiricalSpec` + `ModelPlan` + `ProbeReport`
- action：
  - 向用户一次性呈递完整研究方案：实证选题、样本与时间范围、模型方程、变量定义表（标注 Hard/Soft 来源及 Soft 替换溯源）、预期符号基准线、样本规模预估
  - 用户审核
- output：`hitl_decision`——`"approved"` 或 `"rejected"`，供条件边路由
- 出口分支：
  - `approved` → 数据批量获取
  - `rejected` → 终止（本期 MVP 不处理"带反馈回退到需求解析"）

### 数据批量获取

- input：`DownloadManifest`
- action：纯代码解析 DownloadManifest 中的下载参数，调用 csmar_mcp 提供的 tools 进行数据下载
- output：`DownloadedFiles`——所有下载文件的路径清单

### 数据清洗

- input：`EmpiricalSpec` + `DownloadedFiles`
- action：
  - 需要整理DownloadedFiles中的变量名，规划最终单一分析长表的表名，表名整理成能够直接导入stata分析的格式
  - LLM 编写 Python 脚本，将 csv 文件完成跨表主键对齐、宽长表转换与合并，构建单一分析长表
- output：`MergedDataset`——整理清洗合并后的单一分析长表 csv 文件路径
- post-condition：主键唯一性、长表行数合理、关键字段 key 覆盖率校验通过；失败则在节点内部重试

### 描述性统计分析执行验证

- input：`EmpiricalSpec` + `ModelPlan` + `MergedDataset`
- action：编写 do 代码在单一分析长表上执行描述性统计、逻辑校验、缺失与极值扫描
- output：`DescStatsReport`

### 基准回归执行

- input：`ModelPlan` + `MergedDataset`（消费 ModelPlan 中的模型方程与预期符号基准线）
- action：
  - 按模型方程编写 do 代码执行基准回归
  - 产出系数后强制对照预期符号基准线，输出"符号一致性校验"
- output：`RegressionResult`——回归结果与符号一致性校验，作为 workflow 的 terminal 输出
