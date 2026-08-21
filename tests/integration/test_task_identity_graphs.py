"""Mutable graph topology regressions for durable task identity."""

import os
import subprocess
import sys
from collections.abc import Callable

import pytest

from envresearch.kernel.engine import TaskDefinition
from envresearch.kernel.task_identity import TaskIdentityError, definition_hash


class _EqualPreviewBox:
    def __init__(self) -> None:
        self.called = False

    def __hash__(self) -> int:
        return 0


class _MethodAliasWorker:
    def helper(self) -> str:
        return "same"


def _same_code_function() -> Callable[[], str]:
    def helper() -> str:
        return "same"

    return helper


def _same_code_class() -> type[object]:
    class Helper:
        def value(self) -> str:
            return "same"

    return Helper


def _function_alias_pair(*, shared: bool) -> tuple[object, object]:
    first = _same_code_function()
    return first, first if shared else _same_code_function()


def _method_alias_pair(*, shared: bool) -> tuple[object, object]:
    worker = _MethodAliasWorker()
    first = worker.helper
    return first, first if shared else worker.helper


def _class_alias_pair(*, shared: bool) -> tuple[object, object]:
    first = _same_code_class()
    return first, first if shared else _same_code_class()


def _code_alias_callback(
    pair_factory: Callable[..., tuple[object, object]],
    *,
    shared: bool,
    nested: bool,
) -> Callable[[], bool]:
    first, second = pair_factory(shared=shared)
    if nested:
        left = {"helper": first}
        right = {"helper": second}

        def nested_action() -> bool:
            return left["helper"] is right["helper"]

        return nested_action

    def sibling_action() -> bool:
        return first is second

    return sibling_action


def _sibling_alias_callback(*, shared: bool) -> Callable[[], object]:
    first: list[str] = []
    second = first if shared else []

    def action() -> object:
        first.append("called")
        return second

    return action


def _nested_alias_callback(*, shared: bool) -> Callable[[], object]:
    child: list[str] = []
    left = {"child": child}
    right = {"child": child if shared else []}

    def action() -> object:
        left["child"].append("called")
        return right["child"]

    return action


def _ambiguous_unordered_callback(*, reverse: bool) -> Callable[[], object]:
    selected = _EqualPreviewBox()
    other = _EqualPreviewBox()
    members = (other, selected) if reverse else (selected, other)
    boxes = frozenset(members)

    def action() -> object:
        selected.called = True
        return boxes

    return action


def test_shared_and_distinct_equal_sibling_containers_have_different_hashes() -> None:
    """Aliasing changes append-through-first/read-second callback behavior."""
    shared = TaskDefinition("alias", _sibling_alias_callback(shared=True))
    distinct = TaskDefinition("alias", _sibling_alias_callback(shared=False))

    assert definition_hash(shared) != definition_hash(distinct)


def test_nested_alias_topology_changes_definition_hash() -> None:
    """Aliases nested under sibling mappings must remain observable."""
    shared = TaskDefinition("nested", _nested_alias_callback(shared=True))
    distinct = TaskDefinition("nested", _nested_alias_callback(shared=False))

    assert definition_hash(shared) != definition_hash(distinct)


@pytest.mark.parametrize(
    "pair_factory",
    [_function_alias_pair, _method_alias_pair, _class_alias_pair],
    ids=["function", "bound-method", "class"],
)
def test_code_bearing_sibling_aliases_change_definition_hash(
    pair_factory: Callable[..., tuple[object, object]],
) -> None:
    """Shared helpers and distinct same-code helpers have different behavior."""
    shared = TaskDefinition(
        "code-alias",
        _code_alias_callback(pair_factory, shared=True, nested=False),
    )
    distinct = TaskDefinition(
        "code-alias",
        _code_alias_callback(pair_factory, shared=False, nested=False),
    )

    assert shared.action is not None
    assert distinct.action is not None
    assert shared.action() is True
    assert distinct.action() is False
    assert definition_hash(shared) != definition_hash(distinct)


@pytest.mark.parametrize(
    "pair_factory",
    [_function_alias_pair, _method_alias_pair, _class_alias_pair],
    ids=["function", "bound-method", "class"],
)
def test_code_bearing_nested_aliases_change_definition_hash(
    pair_factory: Callable[..., tuple[object, object]],
) -> None:
    """Code aliases nested under sibling mappings remain observable."""
    shared = TaskDefinition(
        "nested-code-alias",
        _code_alias_callback(pair_factory, shared=True, nested=True),
    )
    distinct = TaskDefinition(
        "nested-code-alias",
        _code_alias_callback(pair_factory, shared=False, nested=True),
    )

    assert shared.action is not None
    assert distinct.action is not None
    assert shared.action() is True
    assert distinct.action() is False
    assert definition_hash(shared) != definition_hash(distinct)


def test_cyclic_mutable_graphs_terminate_and_preserve_topology() -> None:
    """One-node and two-node cycles have finite, distinct identities."""
    one_node: list[object] = []
    one_node.append(one_node)

    first: list[object] = []
    second: list[object] = []
    first.append(second)
    second.append(first)

    one = TaskDefinition("cycle", lambda: one_node)
    two = TaskDefinition("cycle", lambda: first)

    assert definition_hash(one) != definition_hash(two)


def test_shared_cyclic_graph_reconstructs_deterministically_across_processes() -> None:
    """Traversal references must depend on graph order, never memory addresses."""
    script = """
from envresearch.kernel.engine import TaskDefinition
from envresearch.kernel.task_identity import definition_hash
child = []
left = {"child": child}
right = {"child": child}
child.append(left)
def action():
    left["child"].append("called")
    return right["child"]
print(definition_hash(TaskDefinition("graph", action)))
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


def test_code_bearing_alias_graph_reconstructs_deterministically() -> None:
    """Code graph IDs must follow traversal order, never process object IDs."""
    script = """
from envresearch.kernel.engine import TaskDefinition
from envresearch.kernel.task_identity import definition_hash
def make_function():
    def helper():
        return "same"
    return helper
class MethodWorker:
    def helper(self):
        return "same"
def make_class():
    class Helper:
        def value(self):
            return "same"
    return Helper
def identity(shared):
    function = make_function()
    other_function = function if shared else make_function()
    worker = MethodWorker()
    method = worker.helper
    other_method = method if shared else worker.helper
    helper_class = make_class()
    other_class = helper_class if shared else make_class()
    left = {"function": function, "method": method, "class": helper_class}
    right = {
        "function": other_function,
        "method": other_method,
        "class": other_class,
    }
    def action():
        return all(left[name] is right[name] for name in left)
    return definition_hash(TaskDefinition("code-graph", action))
print(identity(True), identity(False))
"""
    environment = {"PATH": os.environ.get("PATH", "")}
    hash_pairs = [
        tuple(
            subprocess.run(
                [sys.executable, "-c", script],
                check=True,
                capture_output=True,
                text=True,
                env=environment,
            ).stdout.split()
        )
        for _ in range(2)
    ]

    assert hash_pairs[0] == hash_pairs[1]
    assert hash_pairs[0][0] != hash_pairs[0][1]


def test_unordered_object_graph_reconstructs_deterministically() -> None:
    """Hash-randomized set iteration must not choose traversal IDs."""
    script = """
from envresearch.kernel.engine import TaskDefinition
from envresearch.kernel.task_identity import definition_hash
class Box:
    def __init__(self, name):
        self.name = name
    def __hash__(self):
        return hash(self.name)
boxes = frozenset((Box("first"), Box("second")))
def action():
    return boxes
print(definition_hash(TaskDefinition("unordered", action)))
"""
    environment = {"PATH": os.environ.get("PATH", "")}
    hashes = {
        subprocess.run(
            [sys.executable, "-c", script],
            check=True,
            capture_output=True,
            text=True,
            env=environment,
        ).stdout.strip()
        for _ in range(12)
    }

    assert len(hashes) == 1


def test_ambiguous_unordered_graph_requires_explicit_version() -> None:
    """Equal previews cannot use process-local set order as a tie-breaker."""
    with pytest.raises(TaskIdentityError, match="explicit.*version"):
        TaskDefinition("ambiguous", _ambiguous_unordered_callback(reverse=False))

    forward = TaskDefinition(
        "ambiguous",
        _ambiguous_unordered_callback(reverse=False),
        version="ambiguous-v1",
    )
    reverse = TaskDefinition(
        "ambiguous",
        _ambiguous_unordered_callback(reverse=True),
        version="ambiguous-v1",
    )

    assert definition_hash(forward) == definition_hash(reverse)


def test_explicit_version_owns_rejected_opaque_state() -> None:
    """A version remains the supported fallback for opaque bound state."""
    opaque = object()

    def action() -> object:
        return opaque

    task = TaskDefinition("opaque", action, version="opaque-v1")

    assert len(definition_hash(task)) == 64
