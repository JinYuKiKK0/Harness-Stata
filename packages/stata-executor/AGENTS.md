# Repository Guidelines

## Project Structure & Module Organization

`stata_executor/` contains the package code. Keep transport adapters in `stata_executor/adapters/` (`cli.py`, `mcp.py`), execution-facing contracts in `stata_executor/contract/`, process/runtime concerns in `stata_executor/runtime/`, and execution orchestration in `stata_executor/engine/`. `stata_executor/__main__.py` is the CLI module entrypoint.  
`tests/` holds repository tests; `tests/test_stata_executor.py` exercises CLI, MCP, timeout, artifact collection, and fake-Stata flows end to end. Treat `.tmp_test_runs/` as disposable test output.

## Build, Test, and Development Commands

- `uv sync`: create/update the local Python 3.12 environment.
- `uv run python -m unittest discover -s tests -v`: run the full test suite.
- `uv run python -m stata_executor doctor --stata-executable "D:/Program Files/Stata17/StataMP-64.exe"`: validate the configured Stata binary.
- `uv run python -m stata_executor run-inline "display 1" --stata-executable "..." --working-dir D:/work/project`: smoke-test CLI execution.
- `uv run python -m stata_executor.adapters.mcp`: start the MCP server over stdio for agent integration.

## Coding Style & Naming Conventions

Use Python 3.12+ and keep runtime code standard-library only. Follow PEP 8 with 4-space indentation, type hints, and small focused modules. Prefer `snake_case` for functions, variables, and module names; use `PascalCase` for request/result models and test classes. Keep public behavior stable across CLI, MCP, and Python API boundaries, and return structured machine-readable results instead of ad hoc strings.

## Testing Guidelines

Use `unittest`; add coverage in `tests/test_stata_executor.py` or a new `tests/test_*.py` file when behavior becomes large enough to isolate. Name tests `test_<behavior>` and assert both status fields and result payload details. Cover failure paths, timeout handling, and artifact discovery whenever execution logic changes. Use the fake Stata launcher pattern already in tests instead of requiring a real Stata install.

## Commit & Pull Request Guidelines

Recent history uses prefix-style subjects such as `feat:`, `refactor:`, and `update:`. Keep the first line short, imperative, and scoped to one change; avoid mixing refactors with behavior changes unless the commit message makes that explicit. PRs should describe the contract change, list affected entrypoints (`CLI`, `MCP`, `Python API`), include the test command you ran, and note any required environment variables such as `STATA_EXECUTOR_STATA_EXECUTABLE`.

## Configuration & Security Tips

Do not hardcode local Stata paths in source. MCP should receive configuration through environment variables, and CLI callers should pass `--stata-executable` explicitly. Keep generated logs and artifacts inside the working directory boundary unless the user requested otherwise.
