"""Prompt loading utilities."""

from __future__ import annotations

from pathlib import Path

_PROMPTS_DIR = Path(__file__).resolve().parent


def load_prompt(name: str) -> str:
    """Read ``prompts/{name}.md`` and return the raw text."""
    return (_PROMPTS_DIR / f"{name}.md").read_text(encoding="utf-8")
