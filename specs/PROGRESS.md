# 项目进度

## 当前焦点

feature_list.json 设计与首版填充落地，Session 流程引入"读 feature_list → 选目标 → 自动标 passes"环节。下一步按 feature_list.json 推进 F10（需求解析节点冒烟测试）。

## 已完成

- 顶层设计
  - 工作流与节点编排：docs/empirical-analysis-workflow.md（8 节点 + 主图拓扑 + ReAct 子图设计）
  - State schema 设计：docs/state.md（9 个切片 + reducer 策略 + 子图隔离）
- 项目骨架：src/harness_stata/ 下全部空文件与 `__init__.py`
- 技术栈与依赖声明：pyproject.toml（langgraph / langchain / langchain-community / pandas / typer + dev tools）
- 机械化质量门禁
  - ruff（11 组规则含 T20 禁裸 print）、pyright strict
  - import-linter 5 条契约（graph/subgraphs/nodes 分层 + LLM 单一入口）
  - 错误信息已内嵌修复指引
  - pre-commit hook + 统一脚本 `scripts/check.py`
  - 自定义 lint `scripts/lint_custom.py`：prompt 存在性、nodes 导出约定、文件大小、架构树一致性、状态文档一致性
- Session 基础设施：`scripts/init.py`（跑质量门禁 + git log + 本进度文件）
- Dev 依赖安装与质量门禁验证通过
- LLM 选型落地：DashScope ChatTongyi（qwen-plus）,通过 langchain-community 集成
- 基础设施层实现
  - config.py：集中配置,环境变量 DASHSCOPE_API_KEY / LLM_MODEL / LLM_TEMPERATURE
  - prompts/__init__.py：load_prompt() 加载 markdown prompt
  - clients/llm.py：get_chat_model() 返回 BaseChatModel（唯一 LLM SDK 入口）
- state.py 新增 UserRequest TypedDict + WorkflowState.user_request 字段
- 需求解析节点 nodes/requirement_analysis.py：单轮 LLM + with_structured_output
- 需求解析 prompt：prompts/requirement_analysis.md
- feature_list.json 宏观需求清单
  - 24 个能力级 user story，与 PROGRESS.md 正交（宏观稳态 vs 微观流水）
  - schema：id / description / steps / depends_on / passes
  - 挑选机制：depends_on 约束可选集 + LLM 结合 PROGRESS.md 与 MVP 价值推断
  - passes 判定：Claude 自检 steps 全走通且 check.py 5/5 通过后自动翻转
  - CLAUDE.md Session 流程集成：init.py → 读 feature_list.json → 选目标 → 实现 → 标 passes → 更 PROGRESS.md → commit
  - 结构性增改（新增/拆分/合并 feature）需用户确认；passes 翻转无需确认

## 下一步

1. F10：需求解析节点冒烟测试（硬编码 UserRequest 调用节点验证 EmpiricalSpec 结构）
2. F11：模型与基准线构建节点（model_construction）
3. 按 feature_list.json depends_on 逐步推进后续 feature

## 未解决/卡点

- pyright strict 下 ChatTongyi 缺少部分类型桩,clients/llm.py 中有 type: ignore 注释
- WorkflowState total=False 导致所有 state key 访问需要 type: ignore[reportTypedDictNotRequiredAccess]
