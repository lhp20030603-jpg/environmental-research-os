"""Bounded exact-schema output readers shared by causal-policy recipes."""

from __future__ import annotations

import csv
import hashlib
import os
import stat
from pathlib import Path

from pydantic import ValidationError

from envresearch.econometrics.causal_models import (
    CausalPackageConfiguration,
    RegressionCoefficient,
    RegressionSupport,
)


class CausalOutputInvalid(ValueError):
    """A method output is missing, malformed, oversized, or not immutable."""

    def __init__(self, message: str, *, code: str = "OUTPUT_INVALID") -> None:
        super().__init__(message)
        self.code = code


def read_rows(
    path: Path,
    expected_header: tuple[str, ...],
    *,
    allow_empty: bool = False,
    max_bytes: int = 4 * 1024 * 1024,
) -> list[dict[str, str]]:
    """Read one regular bounded UTF-8 CSV with an exact header."""
    try:
        text = read_regular(path, max_bytes).decode("utf-8")
    except UnicodeDecodeError as error:
        raise CausalOutputInvalid("output CSV must be UTF-8") from error
    try:
        reader = csv.DictReader(text.splitlines())
        if tuple(reader.fieldnames or ()) != expected_header:
            raise CausalOutputInvalid("output CSV has an invalid schema")
        rows = list(reader)
    except csv.Error as error:
        raise CausalOutputInvalid("output CSV is malformed") from error
    if not allow_empty and not rows:
        raise CausalOutputInvalid("output CSV must not be empty")
    return rows


def read_regular(path: Path, max_bytes: int) -> bytes:
    """Read one exact regular non-symlink leaf without following replacement."""
    try:
        lexical = path.lstat()
        descriptor = os.open(
            path,
            os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0),
        )
    except OSError as error:
        raise CausalOutputInvalid("output must be a regular file") from error
    try:
        opened = os.fstat(descriptor)
        if (
            not stat.S_ISREG(lexical.st_mode)
            or not stat.S_ISREG(opened.st_mode)
            or (lexical.st_dev, lexical.st_ino) != (opened.st_dev, opened.st_ino)
            or opened.st_size > max_bytes
        ):
            raise CausalOutputInvalid("output must be a bounded regular file")
        data = bytearray()
        while chunk := os.read(descriptor, min(1024 * 1024, max_bytes - len(data) + 1)):
            data.extend(chunk)
            if len(data) > max_bytes:
                raise CausalOutputInvalid("output exceeds its byte limit")
        return bytes(data)
    finally:
        os.close(descriptor)


def parse_coefficients(
    path: Path, *, allow_empty: bool = False
) -> tuple[RegressionCoefficient, ...]:
    """Parse one exact finite coefficient table with unique terms."""
    rows = read_rows(
        path,
        ("term", "estimate", "std_error", "conf_low", "conf_high"),
        allow_empty=allow_empty,
    )
    try:
        coefficients = tuple(
            RegressionCoefficient(
                term=row["term"],
                estimate=float(row["estimate"]),
                std_error=float(row["std_error"]),
                conf_low=float(row["conf_low"]),
                conf_high=float(row["conf_high"]),
            )
            for row in rows
        )
    except (KeyError, ValueError, ValidationError) as error:
        raise CausalOutputInvalid("coefficient table is invalid") from error
    if len({item.term for item in coefficients}) != len(coefficients):
        raise CausalOutputInvalid("coefficient terms must be unique")
    return coefficients


def parse_support(path: Path, *, panel: bool = False) -> RegressionSupport:
    """Parse one exact regression support row."""
    header = (
        ("observations", "clusters", "units", "time_periods")
        if panel
        else ("observations", "clusters")
    )
    rows = read_rows(path, header)
    if len(rows) != 1:
        raise CausalOutputInvalid("support output must contain one row")
    try:
        cluster = rows[0]["clusters"]
        return RegressionSupport(
            observations=int(rows[0]["observations"]),
            clusters=None if cluster == "" else int(cluster),
            units=int(rows[0]["units"]) if panel else None,
            time_periods=int(rows[0]["time_periods"]) if panel else None,
        )
    except (KeyError, ValueError, ValidationError) as error:
        raise CausalOutputInvalid("support output is invalid") from error


def parse_configuration(path: Path) -> CausalPackageConfiguration:
    """Parse one exact package/inference configuration row."""
    rows = read_rows(
        path,
        (
            "method_id",
            "r_version",
            "fixest_version",
            "confidence_level",
            "cluster_column",
            "fixed_effects",
            "estimator_label",
            "cutoff",
            "bandwidth",
            "kernel",
            "donut_radius",
        ),
    )
    if len(rows) != 1:
        raise CausalOutputInvalid("package configuration must contain one row")
    row = rows[0]
    try:
        return CausalPackageConfiguration(
            method_id=row["method_id"],  # type: ignore[arg-type]
            r_version=row["r_version"],
            fixest_version=row["fixest_version"],
            confidence_level=float(row["confidence_level"]),
            cluster_column=None
            if row["cluster_column"] == ""
            else row["cluster_column"],
            fixed_effects=tuple(
                item for item in row["fixed_effects"].split(";") if item
            ),
            estimator_label=row["estimator_label"],
            cutoff=_optional_float(row["cutoff"]),
            bandwidth=_optional_float(row["bandwidth"]),
            kernel=None if row["kernel"] == "" else row["kernel"],  # type: ignore[arg-type]
            donut_radius=_optional_float(row["donut_radius"]),
        )
    except (KeyError, ValueError, ValidationError) as error:
        raise CausalOutputInvalid("package configuration is invalid") from error


def _optional_float(value: str) -> float | None:
    return None if value == "" else float(value)


def figure_digest(path: Path) -> str:
    """Bind one bounded headless SVG and reject a non-SVG payload."""
    data = read_regular(path, 4 * 1024 * 1024)
    if b'<svg xmlns="http://www.w3.org/2000/svg"' not in data[:512]:
        raise CausalOutputInvalid("figure output is not a canonical SVG")
    return hashlib.sha256(data).hexdigest()
