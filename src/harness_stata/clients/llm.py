"""Unified LLM client factory.

This is the **only** module allowed to import ``langchain_community`` or
``openai``.  Every other module must obtain a chat model via
:func:`get_chat_model`.
"""

from __future__ import annotations

from langchain_community.chat_models.tongyi import ChatTongyi  # type: ignore[import-untyped]
from langchain_core.language_models import BaseChatModel

from harness_stata.config import get_settings


def get_chat_model() -> BaseChatModel:
    """Return a configured :class:`BaseChatModel` backed by DashScope."""
    s = get_settings()
    return ChatTongyi(
        model=s.llm_model_name,  # type: ignore[call-arg]
        dashscope_api_key=s.dashscope_api_key,  # type: ignore[call-arg]
        temperature=s.llm_temperature,  # type: ignore[call-arg]
    )
