"""Deterministic graph snapshots for callback recovery identity."""

from __future__ import annotations

import inspect
import json
import math
from collections.abc import Callable
from functools import partial
from pathlib import Path
from types import CodeType, FunctionType, ModuleType

_UNREPRESENTABLE = object()


class TaskIdentityError(ValueError):
    """Raised when callback state lacks an explicit durable identity."""


def callback_identity(
    action: Callable[[], object], explicit_version: str | None
) -> dict[str, object]:
    """Snapshot one callback with deterministic graph references."""
    return _IdentityGraph(explicit_version).callback_identity(action)


class _IdentityGraph:
    """Serialize one callback graph while retaining mutable-node aliases."""

    def __init__(self, explicit_version: str | None) -> None:
        self._explicit_version = explicit_version
        self._graph_ids: dict[int, int] = {}

    def callback_identity(
        self, action: Callable[..., object]
    ) -> dict[str, object]:
        if not isinstance(action, FunctionType) and not inspect.ismethod(action):
            return self._callback_body(action)
        reference, graph_id = self._start_graph_node(action)
        if reference is not None:
            return reference
        assert graph_id is not None
        return {"graph_id": graph_id, **self._callback_body(action)}

    def _callback_body(
        self, action: Callable[..., object]
    ) -> dict[str, object]:
        bound: list[dict[str, object]] = []
        callback: object = action

        if isinstance(action, partial):
            callback = action.func
            for index, value in enumerate(action.args):
                self._append_binding(bound, f"partial.arg.{index}", value)
            for name, value in sorted((action.keywords or {}).items()):
                self._append_binding(bound, f"partial.keyword.{name}", value)

        if inspect.ismethod(callback) and callback.__self__ is not None:
            self._append_binding(bound, "bound_instance", callback.__self__)
            method_function = callback.__func__
            self._append_binding(bound, "method_function", method_function)
            callback = method_function
        elif not isinstance(callback, FunctionType):
            self._append_binding(bound, "callable_instance", callback)
            callback = type(callback).__call__

        code = getattr(callback, "__code__", None)
        if not isinstance(code, CodeType):
            if self._explicit_version is None:
                _require_explicit_identity("callback without stable Python code")
            code_identity: object = {"unavailable": _type_name(callback)}
        else:
            code_identity = self._code_identity(code)

        defaults = getattr(callback, "__defaults__", None) or ()
        for index, value in enumerate(defaults):
            self._append_binding(bound, f"default.{index}", value)
        keyword_defaults = getattr(callback, "__kwdefaults__", None) or {}
        for name, value in sorted(keyword_defaults.items()):
            self._append_binding(bound, f"keyword_default.{name}", value)

        closure = getattr(callback, "__closure__", None) or ()
        free_variables = code.co_freevars if isinstance(code, CodeType) else ()
        for index, cell in enumerate(closure):
            name = free_variables[index] if index < len(free_variables) else str(index)
            try:
                value = cell.cell_contents
            except ValueError:
                continue
            self._append_binding(bound, f"closure.{name}", value)

        if isinstance(callback, FunctionType):
            closure_variables = inspect.getclosurevars(callback)
            for name, value in sorted(closure_variables.globals.items()):
                self._append_binding(bound, f"global.{name}", value)
        attributes = getattr(callback, "__dict__", None)
        if isinstance(attributes, dict):
            self._append_binding(bound, "callable_attributes", attributes)

        return {
            "module": getattr(callback, "__module__", type(callback).__module__),
            "qualname": getattr(
                callback, "__qualname__", type(callback).__qualname__
            ),
            "code": code_identity,
            "bound": bound,
        }

    def _append_binding(
        self,
        bindings: list[dict[str, object]],
        name: str,
        value: object,
    ) -> None:
        stable = self._stable_value(value)
        if stable is _UNREPRESENTABLE:
            if self._explicit_version is None:
                _require_explicit_identity(f"unrepresentable binding {name}")
            stable = {"explicit_version_owns": _type_name(value)}
        bindings.append({"name": name, "value": stable})

    def _stable_value(self, value: object) -> object:
        if value is None or isinstance(value, (bool, int, str)):
            return value
        if isinstance(value, float):
            if math.isnan(value):
                return {"float": "nan"}
            return {"float_hex": value.hex()}
        if isinstance(value, complex):
            return {"complex": [value.real.hex(), value.imag.hex()]}
        if isinstance(value, bytes):
            return {"bytes_hex": value.hex()}
        if isinstance(value, Path):
            return {"path": value.as_posix()}
        if value is Ellipsis:
            return {"ellipsis": True}
        if isinstance(value, FunctionType) or inspect.ismethod(value):
            return {"callable": self.callback_identity(value)}
        if isinstance(value, type):
            return self._class_identity(value)
        if isinstance(value, ModuleType):
            return _UNREPRESENTABLE
        if isinstance(value, list):
            return self._list_identity(value)
        if isinstance(value, dict) and all(isinstance(key, str) for key in value):
            return self._dict_identity(value)
        if isinstance(value, tuple):
            return self._sequence_identity("tuple", value)
        if isinstance(value, frozenset):
            return self._frozenset_identity(value)
        attributes = getattr(value, "__dict__", None)
        if isinstance(attributes, dict) and all(
            isinstance(name, str) for name in attributes
        ):
            return self._object_identity(value, attributes)
        return _UNREPRESENTABLE

    def _list_identity(self, value: list[object]) -> object:
        reference, graph_id = self._start_graph_node(value)
        if reference is not None:
            return reference
        start_size = len(self._graph_ids)
        items = [self._stable_value(item) for item in value]
        if any(item is _UNREPRESENTABLE for item in items):
            self._rollback_graph(start_size - 1)
            return _UNREPRESENTABLE
        return {"graph_id": graph_id, "list": items}

    def _dict_identity(self, value: dict[object, object]) -> object:
        reference, graph_id = self._start_graph_node(value)
        if reference is not None:
            return reference
        start_size = len(self._graph_ids)
        items = {
            key: self._stable_value(item)
            for key, item in sorted(value.items())
            if isinstance(key, str)
        }
        if any(item is _UNREPRESENTABLE for item in items.values()):
            self._rollback_graph(start_size - 1)
            return _UNREPRESENTABLE
        return {"graph_id": graph_id, "dict": items}

    def _sequence_identity(self, kind: str, value: tuple[object, ...]) -> object:
        start_size = len(self._graph_ids)
        items = [self._stable_value(item) for item in value]
        if any(item is _UNREPRESENTABLE for item in items):
            self._rollback_graph(start_size)
            return _UNREPRESENTABLE
        return {kind: items}

    def _frozenset_identity(self, value: frozenset[object]) -> object:
        start_size = len(self._graph_ids)
        ordered: list[tuple[str, object]] = []
        for item in value:
            preview = self._preview_value(item)
            if preview is _UNREPRESENTABLE:
                return _UNREPRESENTABLE
            ordered.append((json.dumps(preview, sort_keys=True), item))
        ordered.sort(key=lambda pair: pair[0])
        preview_keys = {key for key, _ in ordered}
        if len(preview_keys) != len(ordered):
            return _UNREPRESENTABLE
        items = [self._stable_value(item) for _, item in ordered]
        if any(item is _UNREPRESENTABLE for item in items):
            self._rollback_graph(start_size)
            return _UNREPRESENTABLE
        return {"frozenset": items}

    def _preview_value(self, value: object) -> object:
        preview = _IdentityGraph(self._explicit_version)
        preview._graph_ids = self._graph_ids.copy()
        return preview._stable_value(value)

    def _object_identity(
        self, value: object, attributes: dict[object, object]
    ) -> object:
        reference, graph_id = self._start_graph_node(value)
        if reference is not None:
            return reference
        start_size = len(self._graph_ids)
        stable_attributes = self._dict_identity(attributes)
        if stable_attributes is _UNREPRESENTABLE:
            self._rollback_graph(start_size - 1)
            return _UNREPRESENTABLE
        stable_class = self._class_identity(type(value))
        if stable_class is _UNREPRESENTABLE:
            self._rollback_graph(start_size - 1)
            return _UNREPRESENTABLE
        return {
            "graph_id": graph_id,
            "object": stable_class,
            "attributes": stable_attributes,
        }

    def _start_graph_node(
        self, value: object
    ) -> tuple[dict[str, object] | None, int | None]:
        identity = id(value)
        if identity in self._graph_ids:
            return {"graph_reference": self._graph_ids[identity]}, None
        graph_id = len(self._graph_ids)
        self._graph_ids[identity] = graph_id
        return None, graph_id

    def _rollback_graph(self, size: int) -> None:
        while len(self._graph_ids) > size:
            self._graph_ids.popitem()

    def _class_identity(self, value: type[object]) -> object:
        start_size = len(self._graph_ids)
        reference, graph_id = self._start_graph_node(value)
        if reference is not None:
            return reference
        assert graph_id is not None
        if type(value) is not type and self._explicit_version is None:
            self._rollback_graph(start_size)
            return _UNREPRESENTABLE
        body = self._class_body(value)
        if body is _UNREPRESENTABLE:
            self._rollback_graph(start_size)
            return _UNREPRESENTABLE
        assert isinstance(body, dict)
        return {"graph_id": graph_id, **body}

    def _class_body(self, value: type[object]) -> object:
        attributes: list[dict[str, object]] = []
        ignored = {
            "__dict__",
            "__doc__",
            "__module__",
            "__qualname__",
            "__weakref__",
        }
        for name, member in sorted(vars(value).items()):
            if name in ignored:
                continue
            if isinstance(member, (classmethod, staticmethod)):
                member = member.__func__
            elif isinstance(member, property):
                member = (member.fget, member.fset, member.fdel)
            stable = self._stable_value(member)
            if stable is _UNREPRESENTABLE:
                if self._explicit_version is None:
                    return _UNREPRESENTABLE
                stable = {"explicit_version_owns": _type_name(member)}
            attributes.append({"name": name, "value": stable})
        bases: list[object] = []
        for base in value.__bases__:
            if base.__module__ == "builtins":
                bases.append({"builtin": _type_name(base)})
                continue
            stable_base = self._class_identity(base)
            if stable_base is _UNREPRESENTABLE:
                if self._explicit_version is None:
                    return _UNREPRESENTABLE
                stable_base = {"explicit_version_owns": _type_name(base)}
            bases.append(stable_base)
        return {
            "class": _type_name(value),
            "metaclass": _type_name(type(value)),
            "bases": bases,
            "attributes": attributes,
        }

    def _code_identity(self, code: CodeType) -> dict[str, object]:
        constants: list[object] = []
        for constant in code.co_consts:
            if isinstance(constant, CodeType):
                constants.append({"code": self._code_identity(constant)})
                continue
            stable = self._stable_value(constant)
            if stable is _UNREPRESENTABLE:
                if self._explicit_version is None:
                    _require_explicit_identity("unrepresentable code constant")
                stable = {"explicit_version_owns": _type_name(constant)}
            constants.append(stable)
        return {
            "bytecode": code.co_code.hex(),
            "constants": constants,
            "names": list(code.co_names),
            "varnames": list(code.co_varnames),
            "freevars": list(code.co_freevars),
            "cellvars": list(code.co_cellvars),
            "argcount": code.co_argcount,
            "posonlyargcount": code.co_posonlyargcount,
            "kwonlyargcount": code.co_kwonlyargcount,
            "flags": code.co_flags,
            "exception_table": code.co_exceptiontable.hex(),
        }


def _require_explicit_identity(detail: str) -> None:
    raise TaskIdentityError(f"{detail} requires an explicit durable task version")


def _type_name(value: object) -> str:
    value_type = value if isinstance(value, type) else type(value)
    return f"{value_type.__module__}.{value_type.__qualname__}"
