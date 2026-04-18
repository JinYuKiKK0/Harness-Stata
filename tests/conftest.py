"""Root test fixtures shared across all tests."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest

from harness_stata.state import UserRequest


_DUMMY_ENV: dict[str, str] = {
    "DASHSCOPE_API_KEY": "test-dummy-key-not-real",
    "CSMAR_ACCOUNT": "test-dummy-account",
    "CSMAR_PASSWORD": "test-dummy-password",
    "STATA_EXECUTOR_STATA_EXECUTABLE": "/nonexistent/stata-dummy",
    "STATA_EXECUTOR_EDITION": "mp",
}


@pytest.fixture(autouse=True)
def _safe_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Patch config._load_env so tests never touch the real .env or system env."""
    monkeypatch.setattr(
        "harness_stata.config._load_env",
        lambda: dict(_DUMMY_ENV),
    )


def _make_user_request(**overrides: Any) -> UserRequest:
    defaults: UserRequest = {
        "x_variable": "公司治理质量",
        "y_variable": "ROA",
        "sample_scope": "A股上市公司",
        "time_range_start": "2018",
        "time_range_end": "2022",
        "data_frequency": "yearly",
    }
    return {**defaults, **overrides}  # type: ignore[return-value]


@pytest.fixture()
def make_user_request() -> Callable[..., UserRequest]:
    """Fixture exposing UserRequest factory. Call it like make_user_request(x_variable="ESG评分")."""
    return _make_user_request
