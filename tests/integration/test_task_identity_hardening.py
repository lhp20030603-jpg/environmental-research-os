"""Cross-process and semantic task identity regressions."""

import os
import subprocess
import sys
from collections.abc import Callable
from functools import partial
from types import FunctionType

import pytest

from envresearch.kernel.engine import TaskDefinition
from envresearch.kernel.task_identity import TaskIdentityError, definition_hash

_GLOBAL_IDENTITY_VALUE = "old"
_GLOBAL_MUTABLE_VALUE: list[str] = ["initial"]


def _read_global_identity() -> str:
    return _GLOBAL_IDENTITY_VALUE


def _mutate_global_identity() -> None:
    _GLOBAL_MUTABLE_VALUE.append("changed")


def _constant_template() -> complex:
    return 0j


def test_definition_hash_is_stable_across_processes() -> None:
    """Durable identities must not contain process-local addresses or line tables."""
    script = """
from envresearch.kernel.engine import TaskDefinition
from envresearch.kernel.task_identity import definition_hash
def action():
    return None
print(definition_hash(TaskDefinition('stable', action)))
"""
    environment = {"PATH": os.environ.get("PATH", "")}
    first = subprocess.run(
        [sys.executable, "-c", script],
        check=True,
        capture_output=True,
        text=True,
        env=environment,
    ).stdout.strip()
    second = subprocess.run(
        [sys.executable, "-c", script],
        check=True,
        capture_output=True,
        text=True,
        env=environment,
    ).stdout.strip()

    assert first == second
    assert len(first) == 64


def test_definition_hash_tracks_referenced_immutable_global() -> None:
    """Changing a referenced immutable global invalidates the definition."""
    global _GLOBAL_IDENTITY_VALUE
    original = definition_hash(TaskDefinition("global", _read_global_identity))
    _GLOBAL_IDENTITY_VALUE = "new"
    try:
        changed = definition_hash(TaskDefinition("global", _read_global_identity))
    finally:
        _GLOBAL_IDENTITY_VALUE = "old"

    assert changed != original


def test_mutable_referenced_global_is_snapshotted_at_construction() -> None:
    """Runtime mutation must not change a definition's captured identity."""
    task = TaskDefinition("global", _mutate_global_identity)
    original = definition_hash(task)

    _GLOBAL_MUTABLE_VALUE.append("runtime")
    try:
        assert definition_hash(task) == original
        assert definition_hash(TaskDefinition("global", _mutate_global_identity)) != original
    finally:
        _GLOBAL_MUTABLE_VALUE[:] = ["initial"]


def test_definition_hash_distinguishes_supported_code_constants() -> None:
    """Different same-type constants must not collapse to one type marker."""
    first_code = _constant_template.__code__.replace(co_consts=(None, 1j))
    second_code = _constant_template.__code__.replace(co_consts=(None, 2j))
    namespace = {"__name__": "stable_module"}
    first = FunctionType(first_code, namespace, "action")
    second = FunctionType(second_code, namespace, "action")
    first.__qualname__ = second.__qualname__ = "action"

    assert definition_hash(TaskDefinition("constant", first)) != definition_hash(
        TaskDefinition("constant", second)
    )


def _append_value(values: list[str], value: str) -> None:
    values.append(value)


def test_mutable_partial_initial_values_have_distinct_identities() -> None:
    """Different initial mutable partial state must invalidate checkpoint reuse."""
    old = TaskDefinition("partial", partial(_append_value, ["old"], "called"))
    new = TaskDefinition("partial", partial(_append_value, ["new"], "called"))

    assert definition_hash(old) != definition_hash(new)


def test_mutation_after_task_construction_keeps_captured_identity() -> None:
    """Executing a callback must not rewrite its already-captured plan identity."""
    values: list[str] = []
    task = TaskDefinition("partial", partial(_append_value, values, "called"))
    before = definition_hash(task)

    assert task.action is not None
    task.action()

    assert values == ["called"]
    assert definition_hash(task) == before


def test_mutable_identity_reconstructs_across_processes() -> None:
    """The same initial JSON-like state must hash identically in new processes."""
    script = """
from functools import partial
from envresearch.kernel.engine import TaskDefinition
from envresearch.kernel.task_identity import definition_hash
def append_value(values, value):
    values.append(value)
task = TaskDefinition('partial', partial(append_value, ['initial'], 'called'))
print(definition_hash(task))
"""
    environment = {"PATH": os.environ.get("PATH", "")}
    hashes = [
        subprocess.run(
            [sys.executable, "-c", script],
            check=True,
            capture_output=True,
            text=True,
            env=environment,
        ).stdout.strip()
        for _ in range(2)
    ]

    assert hashes[0] == hashes[1]


def test_unsupported_mutable_state_still_requires_explicit_version() -> None:
    """Non-deterministic bindings must never be silently omitted."""
    unsupported = object()

    def action() -> object:
        return unsupported

    with pytest.raises(TaskIdentityError, match="explicit.*version"):
        TaskDefinition("unsupported", action)


def _bind_helper(helper: Callable[[], object]) -> Callable[[], object]:
    def outer() -> object:
        return helper()

    return outer


def _helper_factory(value: str) -> Callable[[], object]:
    def helper() -> object:
        return value

    return helper


def _method_factory(value: str) -> Callable[[], object]:
    class Worker:
        def run(self) -> object:
            return value

    return Worker().run


def _class_factory(value: str) -> type[object]:
    class Worker:
        def result(self) -> str:
            return value

    return Worker


@pytest.mark.parametrize(
    "old_helper,new_helper",
    [
        (_helper_factory("old"), _helper_factory("new")),
        (_method_factory("old"), _method_factory("new")),
        (_class_factory("old"), _class_factory("new")),
    ],
    ids=["function", "method", "class"],
)
def test_outer_callback_tracks_code_bearing_helper_implementation(
    old_helper: Callable[[], object],
    new_helper: Callable[[], object],
) -> None:
    """Different helper implementations must never share a checkpoint identity."""
    old = TaskDefinition("helper", _bind_helper(old_helper))
    new = TaskDefinition("helper", _bind_helper(new_helper))

    assert definition_hash(old) != definition_hash(new)


def _cyclic_helper_factory(value: str) -> Callable[[], object]:
    def first() -> object:
        return second()

    def second() -> object:
        if value:
            return value
        return first()

    return first


def test_recursive_helper_graph_is_finite_and_tracks_nested_state() -> None:
    """Mutually recursive helper bindings need finite, distinguishing identity."""
    old = TaskDefinition("cycle", _bind_helper(_cyclic_helper_factory("old")))
    new = TaskDefinition("cycle", _bind_helper(_cyclic_helper_factory("new")))

    assert definition_hash(old) != definition_hash(new)
