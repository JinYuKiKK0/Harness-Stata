"""Centralized configuration loaded from environment variables."""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    dashscope_api_key: str
    llm_model_name: str
    llm_temperature: float
    csmar_account: str
    csmar_password: str


def get_settings() -> Settings:
    api_key = os.environ.get("DASHSCOPE_API_KEY", "")
    if not api_key:
        msg = (
            "环境变量 DASHSCOPE_API_KEY 未设置。"
            " 请在 .env 文件或系统环境变量中配置 DashScope API Key。"
        )
        raise RuntimeError(msg)

    csmar_account = os.environ.get("CSMAR_ACCOUNT", "")
    csmar_password = os.environ.get("CSMAR_PASSWORD", "")
    if not csmar_account or not csmar_password:
        msg = (
            "环境变量 CSMAR_ACCOUNT / CSMAR_PASSWORD 未设置。"
            " 请在 .env 文件或系统环境变量中配置 CSMAR 账户凭据。"
        )
        raise RuntimeError(msg)

    return Settings(
        dashscope_api_key=api_key,
        llm_model_name=os.environ.get("LLM_MODEL", "qwen-plus"),
        llm_temperature=float(os.environ.get("LLM_TEMPERATURE", "0.0")),
        csmar_account=csmar_account,
        csmar_password=csmar_password,
    )
