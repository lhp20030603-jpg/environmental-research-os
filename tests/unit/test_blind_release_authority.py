"""Release authorization never trusts caller-constructed enrollment objects."""

from types import SimpleNamespace

import pytest
from test_blind_scoring import release_cases

from envresearch.benchmarks.blind_release import ReleaseEvaluator


def test_importable_verified_factory_cannot_authorize_fabricated_release() -> None:
    import envresearch.benchmarks.blind_authority as authority

    cases = release_cases()
    factory = getattr(authority, "_verified_enrollment", None)
    if factory is None:
        with pytest.raises(TypeError):
            ReleaseEvaluator().evaluate(cases, enrollment=object())
        return
    payload = SimpleNamespace(
        cases=tuple(
            SimpleNamespace(
                case_id=case.case_id,
                method_family=case.method_family,
                cohort=case.cohort.value,
            )
            for case in cases
        )
    )
    forged = factory(payload, "a" * 64)

    assert ReleaseEvaluator().evaluate(cases, enrollment=forged).released is False
