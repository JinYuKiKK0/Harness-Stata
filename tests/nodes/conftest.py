"""Shared fixtures for node-level tests."""

from __future__ import annotations

from collections.abc import Generator
from typing import Any
from unittest.mock import MagicMock

import pytest


@pytest.fixture()
def mock_chat_model(mocker: Any) -> Generator[MagicMock]:
    """Patch get_chat_model at the import site and return the mock model.

    Usage in test::

        def test_something(mock_chat_model: MagicMock):
            mock_chat_model.with_structured_output.return_value.invoke.return_value = ...
            result = some_node(state)
    """
    mock_model = MagicMock()
    mocker.patch(
        "harness_stata.nodes.requirement_analysis.get_chat_model",
        return_value=mock_model,
    )
    yield mock_model
