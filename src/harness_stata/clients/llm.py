"""Unified LLM client factory.

This is the **only** module allowed to import ``langchain_openai`` or
``openai``.  Every other module must obtain a
chat model via :func:`get_chat_model`.

走 OpenAI 兼容端点，兼容纯文本与多模态模型；同一 base_url 由模型名自动分发。
"""

from __future__ import annotations

from langchain_core.language_models import BaseChatModel
from langchain_openai import ChatOpenAI
from pydantic import SecretStr

from harness_stata.config import get_settings


def get_chat_model() -> BaseChatModel:
    """Return a configured OpenAI-compatible :class:`BaseChatModel`."""
    s = get_settings()
    return ChatOpenAI(
        model=s.llm_model_name,
        api_key=SecretStr(s.api_key),
        base_url=s.llm_base_url,
        temperature=s.llm_temperature,
        # extra_body={"enable_thinking": False},
        extra_body={"chat_template_kwargs":{"thinking":False}}
    )
