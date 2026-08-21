"""Focused code-bearing graph topology regressions."""

from collections.abc import Callable
from types import MethodType

import pytest

from envresearch.kernel.engine import TaskDefinition
from envresearch.kernel.task_identity import definition_hash


class _DynamicMethodReceiver:
    pass


def _same_code_method_function() -> Callable[[object], str]:
    def helper(receiver: object) -> str:
        return "same"

    return helper


def _method_function_alias_callback(
    *, shared: bool, nested: bool
) -> Callable[[], bool]:
    first_function = _same_code_method_function()
    second_function = (
        first_function if shared else _same_code_method_function()
    )
    receiver = _DynamicMethodReceiver()
    first = MethodType(first_function, receiver)
    second = MethodType(second_function, receiver)
    if nested:
        left = {"method": first}
        right = {"method": second}

        def nested_action() -> bool:
            return left["method"].__func__ is right["method"].__func__

        return nested_action

    def sibling_action() -> bool:
        return first.__func__ is second.__func__

    return sibling_action


def _function_attribute_alias_callback(*, shared: bool) -> Callable[[], bool]:
    def helper() -> str:
        return "same"

    attributes = helper.__dict__ if shared else {}

    def action() -> bool:
        return attributes is helper.__dict__

    return action


@pytest.mark.parametrize("nested", [False, True], ids=["sibling", "nested"])
def test_bound_methods_track_underlying_function_aliases(nested: bool) -> None:
    """Distinct wrappers can still share their underlying function object."""
    shared = TaskDefinition(
        "method-function",
        _method_function_alias_callback(shared=True, nested=nested),
    )
    distinct = TaskDefinition(
        "method-function",
        _method_function_alias_callback(shared=False, nested=nested),
    )

    assert shared.action is not None
    assert distinct.action is not None
    assert shared.action() is True
    assert distinct.action() is False
    assert definition_hash(shared) != definition_hash(distinct)


def test_empty_function_attributes_retain_alias_topology() -> None:
    """An empty function state mapping can be shared with another binding."""
    shared = TaskDefinition(
        "function-attributes",
        _function_attribute_alias_callback(shared=True),
    )
    distinct = TaskDefinition(
        "function-attributes",
        _function_attribute_alias_callback(shared=False),
    )

    assert shared.action is not None
    assert distinct.action is not None
    assert shared.action() is True
    assert distinct.action() is False
    assert definition_hash(shared) != definition_hash(distinct)
