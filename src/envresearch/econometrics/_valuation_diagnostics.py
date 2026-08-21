"""Independent reconstruction of valuation diagnostics from sealed evidence."""

from __future__ import annotations

import csv
import io
import math
from pathlib import Path

from envresearch.econometrics._causal_outputs import read_rows
from envresearch.econometrics._valuation_evidence import (
    BidYesShare,
    bid_yes_shares_match,
)
from envresearch.econometrics.valuation_contracts import (
    ContingentValuationSpec,
    HedonicSpec,
    TravelCostSpec,
)
from envresearch.econometrics.valuation_results import (
    ContingentValuationResult,
    HedonicResult,
    TravelCostResult,
)


def hedonic_diagnostics_match(
    data: bytes, spec: HedonicSpec, result: HedonicResult
) -> bool:
    """Rebuild the registered raw design matrix, condition number, and VIF."""
    try:
        header, rows = _snapshot_rows(data)
        positions = {name: index for index, name in enumerate(header)}
        matrix = tuple(
            (
                _environment_value(row, positions, spec),
                *(float(row[positions[name]]) for name in spec.columns.controls),
            )
            for row in rows
        )
        condition = _condition_number(matrix)
        max_vif = _max_vif(matrix)
        sensitivity = result.sensitivities[0]
        multiplier = _hedonic_multiplier(
            result.sensitivity_form,
            result.reference_price,
            result.reference_environment,
        )
        return (
            result.sensitivity_form == spec.sensitivity_form
            and math.isclose(
                sensitivity.estimate,
                result.sensitivity_coefficient * multiplier,
                rel_tol=1e-8,
                abs_tol=1e-8,
            )
            and math.isclose(
                result.condition_number, condition, rel_tol=1e-7, abs_tol=1e-7
            )
            and math.isclose(result.max_vif, max_vif, rel_tol=1e-7, abs_tol=1e-7)
        )
    except (IndexError, KeyError, OSError, TypeError, ValueError, ZeroDivisionError):
        return False


def travel_diagnostics_match(
    data: bytes,
    output_root: Path,
    spec: TravelCostSpec,
    result: TravelCostResult,
) -> bool:
    """Rebuild count-model fit statistics from row-level fitted evidence."""
    try:
        header, source = _snapshot_rows(data)
        visits = header.index(spec.columns.visits)
        rows = read_rows(
            output_root / "fit_evidence.csv", ("row_index", "observed", "fitted")
        )
        if len(rows) != len(source):
            return False
        observed: list[float] = []
        fitted: list[float] = []
        for index, (raw, evidence) in enumerate(zip(source, rows, strict=True), 1):
            y = float(raw[visits])
            if int(evidence["row_index"]) != index or float(evidence["observed"]) != y:
                return False
            mu = float(evidence["fitted"])
            if not math.isfinite(mu) or mu <= 0:
                return False
            observed.append(y)
            fitted.append(mu)
        dispersion, log_likelihood, deviance = _count_diagnostics(
            tuple(observed), tuple(fitted), result.residual_df, result.theta
        )
        sensitivity = result.sensitivities[0]
        return (
            result.sensitivity_family == spec.family
            and math.isclose(
                sensitivity.estimate,
                -1.0 / result.sensitivity_cost_coefficient,
                rel_tol=1e-8,
                abs_tol=1e-8,
            )
            and math.isclose(result.dispersion, dispersion, rel_tol=1e-7, abs_tol=1e-7)
            and math.isclose(
                result.log_likelihood, log_likelihood, rel_tol=1e-7, abs_tol=1e-7
            )
            and math.isclose(result.deviance, deviance, rel_tol=1e-7, abs_tol=1e-7)
        )
    except (IndexError, KeyError, OSError, TypeError, ValueError, ZeroDivisionError):
        return False


def cv_diagnostics_match(
    data: bytes, spec: ContingentValuationSpec, result: ContingentValuationResult
) -> bool:
    """Rebuild CV probabilities and observed bid-level yes shares."""
    try:
        header, rows = _snapshot_rows(data)
        positions = {name: index for index, name in enumerate(header)}
        terms = ("(Intercept)", spec.columns.bid, *spec.columns.covariates)
        coefficients = {item.term: item.estimate for item in result.coefficients}
        if set(coefficients) != set(terms):
            return False
        means = {
            name: sum(float(row[positions[name]]) for row in rows) / len(rows)
            for name in spec.columns.covariates
        }
        probabilities: list[float] = []
        grouped: dict[float, list[int]] = {}
        for row in rows:
            bid = float(row[positions[spec.columns.bid]])
            response = float(row[positions[spec.columns.response]])
            if response not in {0.0, 1.0}:
                return False
            linear = coefficients["(Intercept)"] + coefficients[spec.columns.bid] * bid
            linear += sum(
                coefficients[name] * (float(row[positions[name]]) - means[name])
                for name in spec.columns.covariates
            )
            probabilities.append(_binary_probability(linear, spec.link))
            counts = grouped.setdefault(bid, [0, 0])
            counts[0] += int(response)
            counts[1] += 1
        shares = tuple(
            BidYesShare(
                bid=bid,
                yes_count=counts[0],
                observations=counts[1],
                yes_share=counts[0] / counts[1],
            )
            for bid, counts in sorted(grouped.items())
        )
        extreme_share = sum(
            value <= 0.01 or value >= 0.99 for value in probabilities
        ) / len(probabilities)
        return (
            bid_yes_shares_match(result.bid_yes_shares, shares)
            and result.max_extreme_probability_share
            == spec.max_extreme_probability_share
            and math.isclose(
                result.probability_min,
                min(probabilities),
                rel_tol=1e-8,
                abs_tol=1e-8,
            )
            and math.isclose(
                result.probability_max,
                max(probabilities),
                rel_tol=1e-8,
                abs_tol=1e-8,
            )
            and math.isclose(
                result.extreme_probability_share,
                extreme_share,
                abs_tol=1e-12,
            )
        )
    except (
        IndexError,
        KeyError,
        TypeError,
        UnicodeDecodeError,
        ValueError,
        ZeroDivisionError,
    ):
        return False


def _binary_probability(linear: float, link: str) -> float:
    if link == "probit":
        return 0.5 * (1.0 + math.erf(linear / math.sqrt(2.0)))
    if linear >= 0:
        return 1.0 / (1.0 + math.exp(-linear))
    exponent = math.exp(linear)
    return exponent / (1.0 + exponent)


def _snapshot_rows(data: bytes) -> tuple[tuple[str, ...], tuple[tuple[str, ...], ...]]:
    reader = csv.reader(io.StringIO(data.decode("utf-8"), newline=""))
    return tuple(next(reader)), tuple(tuple(row) for row in reader)


def _environment_value(
    row: tuple[str, ...], positions: dict[str, int], spec: HedonicSpec
) -> float:
    value = float(row[positions[spec.columns.environmental_attribute]])
    return math.log(value) if spec.functional_form.endswith("-log") else value


def _hedonic_multiplier(
    form: str, reference_price: float, reference_environment: float
) -> float:
    return {
        "level-level": 1.0,
        "log-level": reference_price,
        "level-log": 1.0 / reference_environment,
        "log-log": reference_price / reference_environment,
    }[form]


def _condition_number(matrix: tuple[tuple[float, ...], ...]) -> float:
    columns = len(matrix[0])
    gram = [
        [sum(row[left] * row[right] for row in matrix) for right in range(columns)]
        for left in range(columns)
    ]
    eigenvalues = _symmetric_eigenvalues(gram)
    smallest = min(eigenvalues)
    if smallest <= 0:
        raise ValueError("singular valuation design")
    return math.sqrt(max(eigenvalues) / smallest)


def _max_vif(matrix: tuple[tuple[float, ...], ...]) -> float:
    varying = tuple(
        index
        for index in range(len(matrix[0]))
        if max(row[index] for row in matrix) != min(row[index] for row in matrix)
    )
    if len(varying) < 2:
        return 1.0
    count = len(matrix)
    means = tuple(sum(row[index] for row in matrix) / count for index in varying)
    scales = tuple(
        math.sqrt(sum((row[index] - mean) ** 2 for row in matrix) / (count - 1))
        for index, mean in zip(varying, means, strict=True)
    )
    correlation = [
        [
            sum((row[left] - left_mean) * (row[right] - right_mean) for row in matrix)
            / ((count - 1) * left_scale * right_scale)
            for right, right_mean, right_scale in zip(
                varying, means, scales, strict=True
            )
        ]
        for left, left_mean, left_scale in zip(varying, means, scales, strict=True)
    ]
    inverse = _inverse(correlation)
    return max(inverse[index][index] for index in range(len(inverse)))


def _inverse(matrix: list[list[float]]) -> list[list[float]]:
    size = len(matrix)
    augmented = [
        row[:] + [float(left == right) for right in range(size)]
        for left, row in enumerate(matrix)
    ]
    for column in range(size):
        pivot = max(range(column, size), key=lambda row: abs(augmented[row][column]))
        if abs(augmented[pivot][column]) < 1e-12:
            raise ValueError("singular valuation correlation matrix")
        augmented[column], augmented[pivot] = augmented[pivot], augmented[column]
        divisor = augmented[column][column]
        augmented[column] = [value / divisor for value in augmented[column]]
        for row in range(size):
            if row != column:
                factor = augmented[row][column]
                augmented[row] = [
                    value - factor * pivot_value
                    for value, pivot_value in zip(
                        augmented[row], augmented[column], strict=True
                    )
                ]
    return [row[size:] for row in augmented]


def _symmetric_eigenvalues(matrix: list[list[float]]) -> tuple[float, ...]:
    size = len(matrix)
    for _ in range(max(1, 100 * size * size)):
        left, right = max(
            ((i, j) for i in range(size) for j in range(i + 1, size)),
            key=lambda pair: abs(matrix[pair[0]][pair[1]]),
            default=(0, 0),
        )
        if left == right or abs(matrix[left][right]) < 1e-12:
            break
        angle = 0.5 * math.atan2(
            2.0 * matrix[left][right], matrix[right][right] - matrix[left][left]
        )
        cosine, sine = math.cos(angle), math.sin(angle)
        ll, rr, lr = matrix[left][left], matrix[right][right], matrix[left][right]
        matrix[left][left] = cosine**2 * ll - 2 * sine * cosine * lr + sine**2 * rr
        matrix[right][right] = sine**2 * ll + 2 * sine * cosine * lr + cosine**2 * rr
        matrix[left][right] = matrix[right][left] = 0.0
        for index in range(size):
            if index not in (left, right):
                il, ir = matrix[index][left], matrix[index][right]
                matrix[index][left] = matrix[left][index] = cosine * il - sine * ir
                matrix[index][right] = matrix[right][index] = sine * il + cosine * ir
    return tuple(matrix[index][index] for index in range(size))


def _count_diagnostics(
    observed: tuple[float, ...],
    fitted: tuple[float, ...],
    residual_df: int,
    theta: float | None,
) -> tuple[float, float, float]:
    variance = fitted if theta is None else tuple(mu + mu**2 / theta for mu in fitted)
    dispersion = (
        sum(
            (y - mu) ** 2 / value
            for y, mu, value in zip(observed, fitted, variance, strict=True)
        )
        / residual_df
    )
    if theta is None:
        log_likelihood = sum(
            y * math.log(mu) - mu - math.lgamma(y + 1)
            for y, mu in zip(observed, fitted, strict=True)
        )
        deviance = 2 * sum(
            (y * math.log(y / mu) if y else 0.0) - (y - mu)
            for y, mu in zip(observed, fitted, strict=True)
        )
    else:
        log_likelihood = sum(
            math.lgamma(y + theta)
            - math.lgamma(theta)
            - math.lgamma(y + 1)
            + theta * math.log(theta / (theta + mu))
            + (y * math.log(mu / (theta + mu)) if y else 0.0)
            for y, mu in zip(observed, fitted, strict=True)
        )
        deviance = 2 * sum(
            (y * math.log(y / mu) if y else 0.0)
            - (y + theta) * math.log((y + theta) / (mu + theta))
            for y, mu in zip(observed, fitted, strict=True)
        )
    return dispersion, log_likelihood, deviance
