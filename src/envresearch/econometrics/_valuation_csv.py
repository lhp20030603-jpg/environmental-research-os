"""Row semantics for bounded local valuation CSV inputs."""

from __future__ import annotations

import math

from envresearch.econometrics.data_snapshot import LocalDataInvalid
from envresearch.econometrics.valuation_contracts import (
    ContingentValuationSpec,
    DiscreteChoiceSpec,
    HedonicSpec,
    TravelCostSpec,
)

Rows = tuple[tuple[str, ...], ...]


def _fail(message: str, code: str) -> None:
    raise LocalDataInvalid(message, code=code)


def _number(value: str, label: str, code: str) -> float:
    try:
        result = float(value)
    except ValueError:
        _fail(f"{label} must be finite", code)
    if not math.isfinite(result):
        _fail(f"{label} must be finite", code)
    return result


def validate_hedonic(rows: Rows, positions: dict[str, int], spec: HedonicSpec) -> None:
    code = "HEDONIC_DATA_INVALID"
    log_code = "HEDONIC_LOG_DOMAIN_INVALID"
    forms = {spec.functional_form, spec.sensitivity_form}
    transactions: set[str] = set()
    environmental: set[float] = set()
    for row in rows:
        transaction = row[positions[spec.columns.transaction]]
        if not transaction or transaction in transactions:
            _fail("hedonic transaction keys must be nonmissing and unique", code)
        transactions.add(transaction)
        price = _number(row[positions[spec.columns.price]], "hedonic price", code)
        attribute = _number(
            row[positions[spec.columns.environmental_attribute]],
            "environmental attribute",
            code,
        )
        if price <= 0:
            _fail(
                "hedonic models require positive price support",
                log_code if any(form.startswith("log-") for form in forms) else code,
            )
        if forms & {"level-log", "log-log"} and attribute <= 0:
            _fail("logged environmental attributes must be positive", log_code)
        environmental.add(attribute)
        for column in spec.columns.controls:
            _number(row[positions[column]], "hedonic controls", code)
        for column in spec.columns.fixed_effects:
            if not row[positions[column]]:
                _fail("hedonic fixed effects must be nonmissing", code)
    if len(environmental) < 2:
        _fail("environmental attribute requires variation", code)


def validate_travel_cost(
    rows: Rows, positions: dict[str, int], spec: TravelCostSpec
) -> None:
    code = "TRAVEL_COST_DATA_INVALID"
    keys: set[tuple[str, str]] = set()
    for row in rows:
        unit = row[positions[spec.columns.unit]]
        site = row[positions[spec.columns.site]]
        if not unit or not site:
            _fail("travel-cost unit and site must be nonmissing", code)
        key = (unit, site)
        if key in keys:
            _fail("travel-cost unit-site keys must be unique", code)
        keys.add(key)
        visits = _number(row[positions[spec.columns.visits]], "visits", code)
        if visits < 0 or not visits.is_integer():
            _fail("visits must be nonnegative integers", code)
        cost = _number(row[positions[spec.columns.travel_cost]], "travel cost", code)
        if cost < 0:
            _fail("travel cost must be nonnegative", code)
        exposure = _number(row[positions[spec.columns.exposure]], "exposure", code)
        if exposure <= 0:
            _fail(
                "travel-cost models require positive exposure",
                "TRAVEL_COST_OFFSET_INVALID",
            )
        for column in spec.columns.substitute_controls:
            _number(row[positions[column]], "substitute controls", code)


def validate_cv(
    rows: Rows, positions: dict[str, int], spec: ContingentValuationSpec
) -> None:
    code = "CV_DATA_INVALID"
    respondents: set[str] = set()
    responses: set[int] = set()
    bids: set[float] = set()
    for row in rows:
        respondent = row[positions[spec.columns.respondent]]
        if not respondent or respondent in respondents:
            _fail("CV respondent keys must be nonmissing and unique", code)
        respondents.add(respondent)
        raw = row[positions[spec.columns.response]]
        if raw not in {"0", "1"}:
            _fail("CV response must be exactly binary", code)
        responses.add(int(raw))
        bid = _number(row[positions[spec.columns.bid]], "CV bid", code)
        if bid <= 0:
            _fail("CV models require positive bids", code)
        bids.add(bid)
        for column in spec.columns.covariates:
            _number(row[positions[column]], "CV covariates", code)
    if responses != {0, 1}:
        _fail("CV data must contain both yes and no responses", code)
    if len(bids) < 2:
        _fail("CV data requires at least two bid levels", code)


def validate_dce(
    rows: Rows, positions: dict[str, int], spec: DiscreteChoiceSpec
) -> None:
    code = "DCE_CHOICE_SET_INVALID"
    keys: set[tuple[str, str, str]] = set()
    sets: dict[tuple[str, str], list[tuple[str, ...]]] = {}
    for row in rows:
        respondent = row[positions[spec.columns.respondent]]
        choice_set = row[positions[spec.columns.choice_set]]
        alternative = row[positions[spec.columns.alternative]]
        if not respondent or not choice_set or not alternative:
            _fail("DCE identifiers must be nonmissing", code)
        key = (respondent, choice_set, alternative)
        if key in keys:
            _fail("DCE respondent-set-alternative keys must be unique", code)
        keys.add(key)
        chosen = row[positions[spec.columns.chosen]]
        if chosen not in {"0", "1"}:
            _fail("DCE chosen indicator must be exactly binary", code)
        _number(row[positions[spec.columns.cost]], "DCE cost", code)
        for column in spec.columns.attributes:
            _number(row[positions[column]], "DCE attributes", code)
        sets.setdefault((respondent, choice_set), []).append(row)
    for alternatives in sets.values():
        if len(alternatives) < 2:
            _fail("DCE choice sets require at least two alternatives", code)
        chosen_count = sum(
            row[positions[spec.columns.chosen]] == "1" for row in alternatives
        )
        if chosen_count != 1:
            _fail("DCE choice sets require exactly one chosen alternative", code)
    for column in (spec.columns.cost, *spec.columns.attributes):
        if not any(
            len({row[positions[column]] for row in alternatives}) > 1
            for alternatives in sets.values()
        ):
            _fail(f"DCE term {column} requires within-set variation", code)
