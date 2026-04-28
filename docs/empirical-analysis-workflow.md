## 实证工作流链路设计

本期只做实证分析最小闭环，基准回归分析。

失败回滚范围：本期不实现任何回滚路径；描述性统计异常与基准回归结果不符预期的回滚，留到二期随 Agent 架构改造一起处理。

### 节点总览

工作流拆分为 8 个节点。节点之间仅通过共享状态的切片耦合，下游节点只读上游节点已固化的状态切片，不直接依赖对象引用。

| #   | 节点                   | 执行形态  | 写入的状态切片                                                     |
| --- | ---------------------- | --------- | ------------------------------------------------------------------ |
| 1   | 需求解析               | 单轮 LLM  | EmpiricalSpec                                                      |
| 2   | 模型与基准线构建       | 单轮 LLM  | ModelPlan                                                          |
| 3   | 数据探针               | ReAct LLM | ProbeReport + DownloadManifest                                     |
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
| 数据探针后 | 探针通过                                   | `success` → HITL          |
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

| 节点       | tools                                       |
| ---------- | ------------------------------------------- |
| 数据清洗   | 节点内联 `run_sql`（DuckDB 内存连接共享会话） |
| 描述性统计 | stata-executor-mcp 工具集（`run_do` 等）     |
| 基准回归   | stata-executor-mcp 工具集（`run_do` 等）     |

#### 数据探针子图（节点 3）

数据探针采用「批量字段发现 + 兜底单变量 ReAct + 批量覆盖率验证」的五节点单向流水线。核心设计：把字段发现从「按变量串行 ReAct」改为「全局规划 → bulk schema → 桶级验证」的代码主导广度优先流水线，跨变量复用 schema 拉取与上下文，让 `csmar_bulk_schema` 真正发挥作用；只对 hard 变量在桶级验证仍 not_found 时启用单变量 ReAct 兜底（修复 Planning Agent 漏选候选表的场景）。Soft 变量找不到直接记 `not_found`,不再尝试替代变量。

**工具暴露策略**：节点入口先调一次 `csmar_list_databases` 把已购数据库清单作为共享上下文注入子图。Planning Agent 只绑 `csmar_list_tables`（候选 table_code 必须出自 `list_tables` 返回，禁止盲猜）。Verification 阶段不绑任何工具（直接 `with_structured_output()`）。Fallback 单变量 ReAct 绑 `csmar_list_tables` + `csmar_get_table_schema` 两件套。`csmar_bulk_schema` 仅由 bulk_schema_phase 代码层调用，不绑给任何 Agent；`csmar_probe_query` 仅由 coverage_phase 代码层调用；`csmar_materialize_query` / `csmar_refresh_cache` / `csmar_search_field` 完全不在本节点暴露。

```
probe_subgraph START
  → planning_agent (LLM ReAct: spec.variables 全部待处理变量, 输出 (var → target_db + candidate_tables[]))
  → bulk_schema_phase (代码: 候选 table_code 跨变量去重, 一次 csmar_bulk_schema)
  → verification_phase (LLM 分桶 structured-output: 每 (db, table) 一次, 桶内多变量批判)
      ──[conditional]──┐
      │                │
      ├─ hard 仍 not_found → fallback_react_phase (单变量 ReAct, 仅 hard 触发)
      │                                  ──[conditional]──┐
      │                                  │  hard 仍 not_found → END (hard_failure)
      │                                  │  found → coverage_phase
      │                                  │
      └─ 否则 → coverage_phase (代码批量 csmar_probe_query + 解码后写报告/manifest)
                  │  can_materialize=true → 写 found + manifest
                  │  hard 失败            → END (hard_failure)
                  │  soft 失败            → 写 not_found
                  └─ END (success)
```

子图五个节点：

| 节点                          | 类型           | 职责                                                                                                          |
| ----------------------------- | -------------- | ------------------------------------------------------------------------------------------------------------- |
| planning_agent                | LLM ReAct      | 取 spec.variables 全部变量；用 `list_tables` 推断每个变量的 (target_db, candidate_table_codes[])，受全局预算约束 |
| bulk_schema_phase             | 纯代码 + 工具  | 收集所有候选 table_code 跨变量去重，一次 `csmar_bulk_schema` 拉回 schema 字典(含 `field_label` / `role_tags`)；同时灌进 csmar-mcp 本地缓存 |
| verification_phase            | LLM 分桶批量   | 按 (db, table) 分桶，每桶一次 `with_structured_output()` 调用判定字段；输出按变量合并(任一桶 found 即 found);soft not_found 直接记入报告 |
| fallback_react_phase          | LLM 单变量 ReAct | 仅 hard 变量在 verification 仍 not_found 时启用；带 list_tables + get_table_schema 两件套兜底,负责修复 Planning 漏选候选表的场景 |
| coverage_phase                | 纯代码         | 对 validation_queue 每条候选批量调用 `csmar_probe_query` 解码为 CoverageOutcome,并据此写 ProbeReport / DownloadManifest;hard 失败 → END (hard_failure);soft 失败 → 写 not_found |

**设计要点：**

- 字段发现由广度优先流水线主导，跨变量复用 list_tables 与 bulk_schema 的结果，最优路径 = 1 次 Planning + 1 次 bulk_schema + #候选(db,table) 桶次 Verification
- 预算控制分层：Planning Agent 全局 `planning_agent_max_calls`；Fallback 单变量 `fallback_react_max_calls`
- 覆盖率与 record_count 由 `coverage_phase` 代码批量验证(`can_materialize` / `invalid_columns` 与 MCP 服务端保持一致)
- Verification 阶段对 LLM 输出的 field 做后处理校验(必须出现在 schema 中)，凭空字段降级为 not_found
- `data_download` 节点会再调一次 `csmar_probe_query` 取最新 validation_id；当前不复用本阶段的 validation_id 以避免 TTL 过期回滚成本(MCP 侧缓存命中开销极低)

---

### 需求解析

- input：用户填写的实证要求表单，必填：研究选题 topic、核心解释变量 X、被解释变量 Y、样本范围、时间范围（起止）、数据频率
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
  - **阶段一 字段发现（Agent）**：Planning Agent 用 `csmar_list_tables` 给出每个变量的 (target_db, candidate_tables[])，代码层批量调 `csmar_bulk_schema` 拉 schema，Verification 分桶判定字段是否存在；hard 变量仍 not_found 时启用 Fallback ReAct（`csmar_list_tables` + `csmar_get_table_schema` 两件套）。整阶段只输出 `(database, table, field, key_fields, filters.condition?)` 与 status；**不再估算 record_count / 行数**
  - **阶段二 覆盖率验证（代码）**：对阶段一所有 found 的字段批量调用 `csmar_probe_query`（dry-run），用 MCP 自带的 `can_materialize` / `invalid_columns` 作为门禁；通过即写入 `DownloadManifest`，失败则等同 `not_found` 走与字段未找到一致的 Hard/Soft 路由
  - 若 Hard Contract 变量在任一阶段失败，立即整体硬失败
  - 若 Soft Contract 变量在任一阶段失败,直接记 `not_found`,不再尝试替代变量
- output：`ProbeReport`（逐变量可得性结论）+ `DownloadManifest`（具体到 database/table/field/过滤条件的下载参数清单）

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
