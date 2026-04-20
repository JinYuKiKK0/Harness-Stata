"""Decorators for single-slice node functions.

``@writes_to("field")`` (sync) and ``@awrites_to("field")`` (async) let a node
return its bare slice type (e.g. ``ModelPlan``) while still satisfying
LangGraph's partial-update contract by wrapping the return into
``{field: slice}`` at runtime.

Two decorators instead of one overloaded decorator: pyright's overload
resolution cannot disambiguate ``Callable[_P, _R]`` from
``Callable[_P, Coroutine[..., _R]]`` cleanly, and mis-dispatches sync callers.
Splitting by sync/async keeps types precise at every call site.

Use only for nodes that write exactly one state slice; nodes that write
multiple slices (or branch conditionally) should return an explicit Output
TypedDict instead.
"""

from __future__ import annotations

from collections.abc import Callable, Coroutine
from functools import wraps
from typing import Any, ParamSpec, TypeVar

_P = ParamSpec("_P")
_R = TypeVar("_R")


def writes_to(
    field: str,
) -> Callable[[Callable[_P, _R]], Callable[_P, dict[str, _R]]]:
    """Wrap a *sync* node so its bare-slice return becomes ``{field: return_value}``."""

    def decorator(fn: Callable[_P, _R]) -> Callable[_P, dict[str, _R]]:
        @wraps(fn)
        def wrapper(*args: _P.args, **kwargs: _P.kwargs) -> dict[str, _R]:
            return {field: fn(*args, **kwargs)}

        return wrapper

    return decorator


def awrites_to(
    field: str,
) -> Callable[
    [Callable[_P, Coroutine[Any, Any, _R]]],
    Callable[_P, Coroutine[Any, Any, dict[str, _R]]],
]:
    """Wrap an *async* node so its bare-slice return becomes ``{field: return_value}``."""

    def decorator(
        fn: Callable[_P, Coroutine[Any, Any, _R]],
    ) -> Callable[_P, Coroutine[Any, Any, dict[str, _R]]]:
        @wraps(fn)
        async def wrapper(*args: _P.args, **kwargs: _P.kwargs) -> dict[str, _R]:
            return {field: await fn(*args, **kwargs)}

        return wrapper

    return decorator
