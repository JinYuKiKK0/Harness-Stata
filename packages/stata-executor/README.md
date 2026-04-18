# Stata 执行器 (Stata Executor)

`stata_executor` 是一个小型、独立的 Stata 执行能力模块，面向 Agent、IDE 和本地自动化脚本设计。

它通过三种形式暴露统一的稳定边界：

- **MCP (stdio)**: 面向智能体集成
- **CLI**: 面向命令行调试和运维
- **Python API**: 面向编程复用

该工具返回两类信息：一类是稳定的执行事实，另一类是可直接给模型消费的干净实证结果正文。它不负责解释经济学或实证结果。

## 安装

```bash
uv sync
```

## 配置

`stata_executor` 不再解析用户级配置文件。

- MCP: 通过 MCP 启动 JSON 的 `env` 注入环境变量。
- CLI: 通过命令参数显式传入 Stata 路径。

MCP 环境变量：

- `STATA_EXECUTOR_STATA_EXECUTABLE`: Stata 可执行文件路径（必填）
- `STATA_EXECUTOR_EDITION`: 版本（可选，`mp` / `se` / `be`，默认 `mp`）

示例（MCP 启动前设置环境变量）：

```json
{
  "mcpServers": {
    "stata-executor": {
      "command": "D:/Developments/PythonProject/Stata-Executor-MCP/.venv/Scripts/python.exe",
      "args": ["-m", "stata_executor.adapters.mcp"],
      "cwd": "D:/Developments/PythonProject/Stata-Executor-MCP",
      "env": {
        "STATA_EXECUTOR_STATA_EXECUTABLE": "D:/Program Files/Stata17/StataMP-64.exe",
        "STATA_EXECUTOR_EDITION": "mp"
      }
    }
  }
}
```

## 命令行接口 (CLI)

```bash
python -m stata_executor doctor --stata-executable "D:/Program Files/Stata17/StataMP-64.exe"
python -m stata_executor run-do D:/work/project/analysis.do --stata-executable "D:/Program Files/Stata17/StataMP-64.exe" --working-dir D:/work/project
python -m stata_executor run-inline "sysuse auto, clear\nregress price weight mpg" --stata-executable "D:/Program Files/Stata17/StataMP-64.exe" --working-dir D:/work/project
```

常用参数：

- `--stata-executable`: 显式指定 Stata 可执行文件路径（必填）
- `--edition`: Stata 版本 (`mp`, `se`, `be`)
- `--working-dir`: 工作目录
- `--timeout-sec`: 超时时间（秒）
- `--artifact-glob`: 产物匹配规则
- `--env KEY=VALUE`: 环境变量覆盖
- `--pretty`: JSON 格式化输出

## Agent 集成 (MCP)

```bash
python -m stata_executor.adapters.mcp
```

暴露的工具 (Tools)：

- `doctor`: 检查环境与配置
- `run_do`: 执行现有的 .do 文件
- `run_inline`: 执行单条或多条 inline 命令

## Python API

```python
from stata_executor import RunDoRequest, StataExecutor

executor = StataExecutor()
result = executor.run_do(
    RunDoRequest(
        script_path="analysis.do",
        working_dir="D:/work/project",
    )
)
```

## 结果结构 (Result Shape)

`ExecutionResult` 包含以下字段：

- `status`: 执行状态 (`succeeded`, `failed`)
- `phase`: 发生的阶段
- `exit_code`: 退出码
- `error_kind`: 错误分类
- `summary`: 执行摘要
- `result_text`: 过滤命令回显后的完整结果正文，面向模型直接消费
- `diagnostic_excerpt`: 关键诊断摘要
- `error_signature`: 错误特征码 (如 r(198))
- `failed_command`: 导致失败的命令
- `artifacts`: 生成的产物列表
- `elapsed_ms`: 耗时（毫秒）

## 测试

```bash
python -m unittest discover -s tests -v
```
