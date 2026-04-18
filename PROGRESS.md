# 项目进度

## 当前焦点

需求解析节点（requirement_analysis）已实现,质量门禁 5/5 通过。待冒烟测试验证端到端 LLM 调用。

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
  - 自定义 lint `scripts/lint_custom.py`：prompt 存在性、nodes 导出约定、文件大小
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

## 下一步

1. 冒烟测试：硬编码 UserRequest 调用需求解析节点,验证 LLM 返回正确 EmpiricalSpec
2. 实现模型与基准线构建节点（model_construction）
3. Git commit 固化当前进展

## 未解决/卡点

- pyright strict 下 ChatTongyi 缺少部分类型桩,clients/llm.py 中有 type: ignore 注释
- WorkflowState total=False 导致所有 state key 访问需要 type: ignore[reportTypedDictNotRequiredAccess]
