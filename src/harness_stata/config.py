"""Centralized configuration loaded from the project-root .env file.

All runtime settings are read exclusively from ``<repo>/.env`` via
``dotenv_values``; the process environment is intentionally NOT consulted,
to keep ``.env`` as the single source of truth and avoid configuration
drift between machines.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import dotenv_values

PROJECT_ROOT = Path(__file__).resolve().parents[2]
ENV_PATH = PROJECT_ROOT / ".env"


@dataclass(frozen=True)
class Settings:
    dashscope_api_key: str
    llm_model_name: str
    llm_base_url: str
    llm_temperature: float
    csmar_account: str
    csmar_password: str
    stata_executable: str
    stata_edition: str
    downloads_root: Path
    planning_agent_max_calls: int
    fallback_react_max_calls: int
    substitute_max_rounds: int
    cleaning_coverage_threshold: float
    langsmith_tracing: bool
    langsmith_api_key: str | None
    langsmith_project: str
    langsmith_endpoint: str | None


def _load_env() -> dict[str, str]:
    raw = dotenv_values(ENV_PATH)
    return {k: v for k, v in raw.items() if v is not None}


def _parse_positive_int(env: dict[str, str], key: str, *, default: str) -> int:
    raw = env.get(key, default)
    try:
        value = int(raw)
    except ValueError as exc:
        msg = f"{key}={raw!r} 必须是整数。请在项目根 .env 中修正取值。"
        raise RuntimeError(msg) from exc
    if value < 1:
        msg = f"{key}={value} 必须 >= 1。"
        raise RuntimeError(msg)
    return value


def _parse_non_negative_int(env: dict[str, str], key: str, *, default: str) -> int:
    raw = env.get(key, default)
    try:
        value = int(raw)
    except ValueError as exc:
        msg = f"{key}={raw!r} 必须是整数。请在项目根 .env 中修正取值。"
        raise RuntimeError(msg) from exc
    if value < 0:
        msg = f"{key}={value} 必须 >= 0。"
        raise RuntimeError(msg)
    return value


def get_settings() -> Settings:
    env = _load_env()

    api_key = env.get("DASHSCOPE_API_KEY", "")
    if not api_key:
        msg = "DASHSCOPE_API_KEY 未配置。请在项目根 .env 中设置 DashScope API Key。"
        raise RuntimeError(msg)

    llm_base_url = env.get("LLM_BASE_URL", "")
    if not llm_base_url:
        msg = "LLM_BASE_URL 未配置。请在项目根 .env 中设置 LLM 服务的 base_url"
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

    planning_agent_max_calls = _parse_positive_int(
        env, "HARNESS_PLANNING_AGENT_MAX_CALLS", default="8"
    )
    fallback_react_max_calls = _parse_positive_int(
        env, "HARNESS_FALLBACK_REACT_MAX_CALLS", default="4"
    )
    substitute_max_rounds = _parse_non_negative_int(
        env, "HARNESS_SUBSTITUTE_MAX_ROUNDS", default="1"
    )

    coverage_raw = env.get("HARNESS_CLEANING_COVERAGE_THRESHOLD", "0.8")
    try:
        cleaning_coverage_threshold = float(coverage_raw)
    except ValueError as exc:
        msg = (
            f"HARNESS_CLEANING_COVERAGE_THRESHOLD={coverage_raw!r} 必须是浮点数。"
            " 请在项目根 .env 中修正取值。"
        )
        raise RuntimeError(msg) from exc
    if not 0 < cleaning_coverage_threshold <= 1:
        msg = f"HARNESS_CLEANING_COVERAGE_THRESHOLD={cleaning_coverage_threshold} 必须落在 (0, 1] 区间内。"
        raise RuntimeError(msg)

    langsmith_tracing = env.get("LANGSMITH_TRACING", "").strip().lower() in {
        "true",
        "1",
        "yes",
        "on",
    }
    langsmith_api_key = env.get("LANGSMITH_API_KEY") or None
    if langsmith_tracing and not langsmith_api_key:
        msg = (
            "LANGSMITH_TRACING=true 但 LANGSMITH_API_KEY 未配置。"
            " 请在项目根 .env 中设置 LangSmith API Key,或将 LANGSMITH_TRACING 改为 false。"
        )
        raise RuntimeError(msg)
    langsmith_project = env.get("LANGSMITH_PROJECT", "harness-stata")
    langsmith_endpoint = env.get("LANGSMITH_ENDPOINT") or None

    return Settings(
        dashscope_api_key=api_key,
        llm_model_name=env.get("LLM_MODEL", "qwen-plus"),
        llm_base_url=llm_base_url,
        llm_temperature=float(env.get("LLM_TEMPERATURE", "0.3")),
        csmar_account=csmar_account,
        csmar_password=csmar_password,
        stata_executable=stata_executable,
        stata_edition=env.get("STATA_EXECUTOR_EDITION", "mp"),
        downloads_root=downloads_root,
        planning_agent_max_calls=planning_agent_max_calls,
        fallback_react_max_calls=fallback_react_max_calls,
        substitute_max_rounds=substitute_max_rounds,
        cleaning_coverage_threshold=cleaning_coverage_threshold,
        langsmith_tracing=langsmith_tracing,
        langsmith_api_key=langsmith_api_key,
        langsmith_project=langsmith_project,
        langsmith_endpoint=langsmith_endpoint,
    )


def apply_langsmith_env() -> bool:
    """Export LangSmith config from .env to ``os.environ`` so the SDK auto-wires tracing.

    LangSmith SDK reads its API key/project/endpoint from ``os.environ``; the project
    rule is "configuration comes from .env only". This function bridges the two by
    pushing the .env-resolved values into ``os.environ`` at startup, keeping .env as
    the single source of truth (no fallback to system env). Opt-in via
    ``LANGSMITH_TRACING=true`` in .env. Returns True if tracing was enabled.
    """
    s = get_settings()
    api_key = s.langsmith_api_key
    if not s.langsmith_tracing or api_key is None:
        return False
    os.environ["LANGSMITH_TRACING"] = "true"
    os.environ["LANGSMITH_API_KEY"] = api_key
    os.environ["LANGSMITH_PROJECT"] = s.langsmith_project
    endpoint = s.langsmith_endpoint
    if endpoint:
        os.environ["LANGSMITH_ENDPOINT"] = endpoint
    return True
