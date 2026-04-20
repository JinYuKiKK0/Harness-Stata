"""Centralized configuration loaded from the project-root .env file.

All runtime settings are read exclusively from ``<repo>/.env`` via
``dotenv_values``; the process environment is intentionally NOT consulted,
to keep ``.env`` as the single source of truth and avoid configuration
drift between machines.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from dotenv import dotenv_values

PROJECT_ROOT = Path(__file__).resolve().parents[2]
ENV_PATH = PROJECT_ROOT / ".env"


@dataclass(frozen=True)
class Settings:
    dashscope_api_key: str
    llm_model_name: str
    llm_temperature: float
    csmar_account: str
    csmar_password: str
    stata_executable: str
    stata_edition: str
    downloads_root: Path
    per_variable_max_calls: int


def _load_env() -> dict[str, str]:
    raw = dotenv_values(ENV_PATH)
    return {k: v for k, v in raw.items() if v is not None}


def get_settings() -> Settings:
    env = _load_env()

    api_key = env.get("DASHSCOPE_API_KEY", "")
    if not api_key:
        msg = "DASHSCOPE_API_KEY 未配置。请在项目根 .env 中设置 DashScope API Key。"
        raise RuntimeError(msg)

    csmar_account = env.get("CSMAR_ACCOUNT", "")
    csmar_password = env.get("CSMAR_PASSWORD", "")
    if not csmar_account or not csmar_password:
        msg = "CSMAR_ACCOUNT / CSMAR_PASSWORD 未配置。请在项目根 .env 中设置 CSMAR 账户凭据。"
        raise RuntimeError(msg)

    stata_executable = env.get("STATA_EXECUTOR_STATA_EXECUTABLE", "")
    if not stata_executable:
        msg = (
            "STATA_EXECUTOR_STATA_EXECUTABLE 未配置。"
            " 请在项目根 .env 中设置 Stata 可执行文件的绝对路径。"
        )
        raise RuntimeError(msg)

    downloads_root_raw = env.get("HARNESS_DOWNLOADS_ROOT")
    if downloads_root_raw:
        candidate = Path(downloads_root_raw)
        downloads_root = candidate if candidate.is_absolute() else PROJECT_ROOT / candidate
    else:
        downloads_root = PROJECT_ROOT / "downloads"

    per_var_raw = env.get("HARNESS_PER_VARIABLE_MAX_CALLS", "4")
    try:
        per_variable_max_calls = int(per_var_raw)
    except ValueError as exc:
        msg = (
            f"HARNESS_PER_VARIABLE_MAX_CALLS={per_var_raw!r} 必须是整数。"
            " 请在项目根 .env 中修正取值。"
        )
        raise RuntimeError(msg) from exc
    if per_variable_max_calls < 1:
        msg = (
            f"HARNESS_PER_VARIABLE_MAX_CALLS={per_variable_max_calls} 必须 >= 1。"
            " 每个变量至少需要 1 轮探针调用。"
        )
        raise RuntimeError(msg)

    return Settings(
        dashscope_api_key=api_key,
        llm_model_name=env.get("LLM_MODEL", "qwen-plus"),
        llm_temperature=float(env.get("LLM_TEMPERATURE", "0.3")),
        csmar_account=csmar_account,
        csmar_password=csmar_password,
        stata_executable=stata_executable,
        stata_edition=env.get("STATA_EXECUTOR_EDITION", "mp"),
        downloads_root=downloads_root,
        per_variable_max_calls=per_variable_max_calls,
    )
