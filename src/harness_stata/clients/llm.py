"""Unified LLM client factory.

This is the **only** module allowed to import ``langchain_community``,
``langchain_openai`` or ``openai``.  Every other module must obtain a
chat model via :func:`get_chat_model`.

走 DashScope 的 OpenAI 兼容端点，兼容纯文本与多模态（qwen-vl-*）模型：
同一 base_url 由模型名自动分发，避免 ChatTongyi 基于 content 类型路由导致
多模态模型被错打到纯文本端点的 "url error" 问题。
"""

from __future__ import annotations

from langchain_core.language_models import BaseChatModel
from langchain_openai import ChatOpenAI

from harness_stata.config import get_settings


def get_chat_model() -> BaseChatModel:
    """Return a configured :class:`BaseChatModel` backed by DashScope."""
    s = get_settings()
    return ChatOpenAI(
        model=s.llm_model_name,
        api_key=s.dashscope_api_key,  # type: ignore[arg-type]
        base_url=s.llm_base_url,
        temperature=s.llm_temperature,
        extra_body={"enable_thinking": False},
    )
