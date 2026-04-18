"""Shared fixtures for node-level tests."""

from __future__ import annotations

from collections.abc import Callable, Generator
from typing import Any
from unittest.mock import MagicMock

import pytest


@pytest.fixture()
def mock_chat_model(mocker: Any) -> Generator[MagicMock]:
    """Patch get_chat_model at requirement_analysis's import site.

    Kept for F09 unit tests. New node tests should prefer :func:`mock_chat_model_for`.
    """
    mock_model = MagicMock()
    mocker.patch(
        "harness_stata.nodes.requirement_analysis.get_chat_model",
        return_value=mock_model,
    )
    yield mock_model


@pytest.fixture()
def mock_chat_model_for(mocker: Any) -> Callable[[str], MagicMock]:
    """Factory: pass a node module short name, get a patched mock chat model.

    Usage::

        def test_something(mock_chat_model_for: Callable[[str], MagicMock]):
            model = mock_chat_model_for("model_construction")
            model.with_structured_output.return_value.invoke.return_value = ...
    """

    def _make(node_module: str) -> MagicMock:
        m = MagicMock()
        mocker.patch(f"harness_stata.nodes.{node_module}.get_chat_model", return_value=m)
        return m

    return _make
