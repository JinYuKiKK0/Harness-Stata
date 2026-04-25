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

数据探针与通用骨架不同：csmar_mcp 存在每日账号限流，需要**按变量**做调用预算控制，而非全局迭代上限。子图采用"变量级外循环 + 有预算上限的内层 ReAct"结构。

```
probe_subgraph START
  → variable_dispatcher
  → variable_react (内层 ReAct，受 per_variable_max_calls 约束)
  → result_handler ──[conditional]──┐
      │                             │
      ├─ found                      │  记录到 DownloadManifest，
      │   回到 variable_dispatcher  │  继续下一个变量
      │                             │
      ├─ not_found AND Hard ────────┼──→ END (hard_failure)
      │                             │
      ├─ not_found AND Soft ────────┤  将替代搜寻任务塞回队列，
      │   回到 variable_dispatcher  │  复用同一条内层路径
      │                             │
      └─ 队列为空 ─────────────────┘──→ END (success)
```

子图内三个节点：

| 节点                | 类型       | 职责                                                                                       |
| ------------------- | ---------- | ------------------------------------------------------------------------------------------ |
| variable_dispatcher | 纯代码     | 从待处理变量队列取下一个变量，设置当前变量上下文                                           |
| variable_react      | 内层 ReAct | 针对当前单个变量执行 csmar_mcp 下钻探测（db→table→schema），受 per_variable_max_calls 约束 |
| result_handler      | 纯代码     | 判定 found/not_found，路由 Hard/Soft 分支，维护变量队列与 DownloadManifest                 |

**设计要点：**

- 每个变量的 csmar_mcp 调用次数被隔离计数，不会出现某个难找的变量耗光全局预算
- Soft 替代变量被当作新任务塞回变量队列，复用同一条内层 ReAct 路径，不需要额外的"替代搜寻"节点
- 替代成功后由 result_handler 将新变量回写到 `EmpiricalSpec`/`ModelPlan`（节点内部状态 mutation）

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
- action：
  - LLM 根据变量定义表和数据需求清单调用 csmar_mcp 提供的 tools 进行数据获取
  - 调用数据接口但不全量下载，仅查询表结构元数据、记录计数或拉取极少样本记录，以验证拟定的变量在给定时间、频率和分析粒度下是否存在、是否可访问，并确认数据结构满足模型形式要求
  - 记录计数是可得性判定的一部分：若关键字段记录数严重过低或大面积缺失，视同"无法获取"，以在本节点前置拦截绝大多数缺失问题
  - 若 Hard Contract 变量无法获取或目标数据结构不可得，立即失败
  - 若 Soft Contract 变量无法获取，Agent 在同一经济含义范围内尝试寻找替代变量，记录替代溯源；替代成功后须将新变量回写到 `EmpiricalSpec`/`ModelPlan`，若替代导致方程形式或预期符号基准线需要调整一并更新
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
