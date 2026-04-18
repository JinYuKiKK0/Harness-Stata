# 项目进度

## 当前焦点

完成项目基础设施搭建：机械化质量门禁、session 初始化脚本、进度追踪文件。尚未开始任何节点的代码实现。

## 已完成

- 顶层设计
  - 工作流与节点编排：docs/empirical-analysis-workflow.md（8 节点 + 主图拓扑 + ReAct 子图设计）
  - State schema 设计：docs/state.md（9 个切片 + reducer 策略 + 子图隔离）
- 项目骨架：src/harness_stata/ 下全部空文件与 `__init__.py`
- 技术栈与依赖声明：pyproject.toml（langgraph / langchain / pandas / typer + dev tools）
- 机械化质量门禁
  - ruff（11 组规则含 T20 禁裸 print）、pyright strict
  - import-linter 5 条契约（graph/subgraphs/nodes 分层 + LLM 单一入口）
  - 错误信息已内嵌修复指引
  - pre-commit hook + 统一脚本 `scripts/check.py`
  - 自定义 lint `scripts/lint_custom.py`：prompt 存在性、nodes 导出约定、文件大小
- Session 基础设施：`scripts/init.py`（跑质量门禁 + git log + 本进度文件）

## 下一步

1. 安装 dev 依赖并验证所有质量门禁工具在本地实际跑通（当前 .venv 是 uv 风格空 venv，工具未实装）
2. 首次 git commit，把基础设施固化入库
3. 开始实现 state.py——所有节点的前置契约，根据 docs/state.md 第 5 节逐个切片落 TypedDict

## 未解决/卡点

- LLM 模型选型未决：pyproject.toml 只锁了 `langchain-openai`，具体模型与参数待运行时配置层决定
- 质量门禁的实际执行能力未经验证（取决于上面下一步 #1）
