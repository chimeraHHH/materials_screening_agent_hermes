"""Version-pinned SMACT prior gate for soft-chemistry proposals.

The gate is intentionally composition-level and conservative.  It can reject
an output composition for which the pinned SMACT policy finds no
charge-neutral, Pauling-consistent oxidation-state assignment.  Missing data,
all-metal systems, dependency drift, and evaluation limits require review
instead of being silently accepted or rejected.
"""

from __future__ import annotations

import math
import warnings
from enum import StrEnum
from functools import lru_cache
from importlib import metadata
from math import gcd
from typing import Annotated, Literal, Protocol

from pydantic import Field, model_validator
from pymatgen.core import Composition, Element, Structure

from material_agent.inspiration.models import (
    Identifier,
    StrictModel,
    canonical_sha256,
)
from material_agent.inspiration.transformations import (
    SubstitutionExecutionRequestV1,
)

SMACT_PRIOR_GATE_SCHEMA_VERSION = "smact-inorganic-prior-gate-v1"
SMACT_PRIOR_POLICY_SCHEMA_VERSION = "smact-inorganic-prior-policy-v1"


class SmactPriorDecision(StrEnum):
    PASS = "PASS"
    REJECT = "REJECT"
    REQUIRES_REVIEW = "REQUIRES_REVIEW"


class SmactPriorPolicyV1(StrictModel):
    """Frozen scientific and resource policy for the v1 prior gate."""

    schema_version: Literal["smact-inorganic-prior-policy-v1"] = (
        SMACT_PRIOR_POLICY_SCHEMA_VERSION
    )
    policy_id: Literal["smact-icsd24-pauling-prior-v1"] = (
        "smact-icsd24-pauling-prior-v1"
    )
    policy_version: Literal["1"] = "1"
    smact_version: Literal["4.0.0"] = "4.0.0"
    pymatgen_version: Literal["2025.10.7"] = "2025.10.7"
    oxidation_state_source: Literal["icsd24"] = "icsd24"
    icsd_consensus: Literal[3] = 3
    icsd_commonality: Literal["medium"] = "medium"
    include_zero_oxidation_state: Literal[False] = False
    use_pauling_test: Literal[True] = True
    include_alloys: Literal[False] = False
    mixed_valence: Literal[True] = True
    require_known_pauling_electronegativity: Literal[True] = True
    max_elements: Literal[16] = 16
    max_reduced_stoichiometry_sum: Literal[64] = 64
    scientific_conclusion: Literal[False] = False


DEFAULT_SMACT_PRIOR_POLICY_V1 = SmactPriorPolicyV1()


class ElementStoichiometryV1(StrictModel):
    element: Annotated[
        str, Field(pattern=r"^[A-Z][a-z]?$", min_length=1, max_length=2)
    ]
    amount: Annotated[int, Field(ge=1)]


class SmactPriorGateResultV1(StrictModel):
    """Auditable prior decision; never a computed material-property claim."""

    schema_version: Literal["smact-inorganic-prior-gate-v1"] = (
        SMACT_PRIOR_GATE_SCHEMA_VERSION
    )
    decision: SmactPriorDecision
    policy_id: Literal["smact-icsd24-pauling-prior-v1"]
    policy_sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    backend: Literal["SMACT"] = "SMACT"
    backend_version: str | None
    worker_lock_sha256: Annotated[
        str | None, Field(default=None, pattern=r"^[0-9a-f]{64}$")
    ] = None
    input_formula: Annotated[str, Field(min_length=1, max_length=256)]
    proposed_formula: Annotated[str, Field(min_length=1, max_length=256)]
    reduced_stoichiometry: Annotated[
        tuple[ElementStoichiometryV1, ...], Field(min_length=1, max_length=16)
    ]
    smact_valid: bool | None
    reason_codes: Annotated[tuple[Identifier, ...], Field(min_length=1, max_length=16)]
    evidence_level: Literal["NONE"] = "NONE"
    scientific_conclusion: Literal[False] = False

    @model_validator(mode="after")
    def validate_decision(self) -> SmactPriorGateResultV1:
        if len(set(self.reason_codes)) != len(self.reason_codes):
            raise ValueError("SMACT prior reason codes must be unique")
        if tuple(sorted(item.element for item in self.reduced_stoichiometry)) != tuple(
            item.element for item in self.reduced_stoichiometry
        ):
            raise ValueError("SMACT prior stoichiometry must be element-sorted")
        if self.decision is SmactPriorDecision.PASS and self.smact_valid is not True:
            raise ValueError("a passing SMACT prior requires smact_valid=true")
        if self.decision is SmactPriorDecision.PASS and self.backend_version != "4.0.0":
            raise ValueError("a passing SMACT prior requires the pinned backend version")
        if self.decision is SmactPriorDecision.REJECT and self.smact_valid is True:
            raise ValueError("a rejected SMACT prior cannot carry smact_valid=true")
        if self.decision is SmactPriorDecision.REQUIRES_REVIEW and self.smact_valid is not None:
            raise ValueError("review-required SMACT prior must remain unresolved")
        return self


class SmactPriorEvaluator(Protocol):
    def evaluate(
        self,
        composition: Composition | str,
        *,
        input_formula: str | None,
        policy: SmactPriorPolicyV1,
    ) -> SmactPriorGateResultV1: ...


class SmactPriorWorkerRequestV1(StrictModel):
    schema_version: Literal["smact-inorganic-prior-worker-request-v1"] = (
        "smact-inorganic-prior-worker-request-v1"
    )
    composition_formula: Annotated[str, Field(min_length=1, max_length=256)]
    input_formula: Annotated[str, Field(min_length=1, max_length=256)]
    policy: SmactPriorPolicyV1
    package_lock_sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


def smact_prior_policy_sha256(policy: SmactPriorPolicyV1) -> str:
    if not isinstance(policy, SmactPriorPolicyV1):
        raise TypeError("policy must be a SmactPriorPolicyV1")
    return canonical_sha256(policy)


def _integer_stoichiometry(
    composition: Composition,
) -> tuple[tuple[ElementStoichiometryV1, ...] | None, str | None]:
    integers, invalid_reason = _integer_counts(composition)
    if integers is None:
        return None, invalid_reason
    divisor = 0
    for amount in integers.values():
        divisor = gcd(divisor, amount)
    reduced = tuple(
        ElementStoichiometryV1(element=symbol, amount=integers[symbol] // divisor)
        for symbol in sorted(integers)
    )
    return reduced, None


def _integer_counts(
    composition: Composition,
) -> tuple[dict[str, int] | None, str | None]:
    amounts = composition.element_composition.get_el_amt_dict()
    integers: dict[str, int] = {}
    for symbol, raw_amount in amounts.items():
        amount = float(raw_amount)
        rounded = round(amount)
        if (
            not math.isfinite(amount)
            or amount <= 0.0
            or not math.isclose(amount, rounded, rel_tol=0.0, abs_tol=1e-8)
        ):
            return None, "NON_INTEGER_OR_INVALID_STOICHIOMETRY"
        integers[str(Element(symbol))] = int(rounded)
    if not integers:
        return None, "EMPTY_COMPOSITION"
    return integers, None


def _formula(stoichiometry: tuple[ElementStoichiometryV1, ...]) -> str:
    return Composition(
        {item.element: item.amount for item in stoichiometry}
    ).reduced_formula


@lru_cache(maxsize=8)
def _available_icsd24_symbols(
    *,
    consensus: int,
    include_zero: bool,
    commonality: str,
) -> frozenset[str]:
    from smact.utils.oxidation import ICSD24OxStatesFilter

    rows = ICSD24OxStatesFilter().filter(
        consensus=consensus,
        include_zero=include_zero,
        commonality=commonality,
    )
    return frozenset(str(value) for value in rows["element"])


def _result(
    *,
    decision: SmactPriorDecision,
    policy: SmactPriorPolicyV1,
    backend_version: str | None,
    input_formula: str,
    stoichiometry: tuple[ElementStoichiometryV1, ...],
    smact_valid: bool | None,
    reason_codes: tuple[str, ...],
    worker_lock_sha256: str | None = None,
) -> SmactPriorGateResultV1:
    return SmactPriorGateResultV1(
        decision=decision,
        policy_id=policy.policy_id,
        policy_sha256=smact_prior_policy_sha256(policy),
        backend_version=backend_version,
        worker_lock_sha256=worker_lock_sha256,
        input_formula=input_formula,
        proposed_formula=_formula(stoichiometry),
        reduced_stoichiometry=stoichiometry,
        smact_valid=smact_valid,
        reason_codes=reason_codes,
    )


def evaluate_smact_composition_prior(
    composition: Composition | str,
    *,
    input_formula: str | None = None,
    policy: SmactPriorPolicyV1 = DEFAULT_SMACT_PRIOR_POLICY_V1,
) -> SmactPriorGateResultV1:
    """Evaluate one composition under an exact, locally installed SMACT policy."""

    if not isinstance(policy, SmactPriorPolicyV1):
        raise TypeError("policy must be a SmactPriorPolicyV1")
    parsed = (
        composition
        if isinstance(composition, Composition)
        else Composition(composition)
    )
    stoichiometry, invalid_reason = _integer_stoichiometry(parsed)
    if stoichiometry is None:
        # Preserve a valid strict result even when the supplied composition is
        # fractional: its element-only support is enough to identify the review.
        support = tuple(
            ElementStoichiometryV1(element=str(element), amount=1)
            for element in sorted(parsed.element_composition.elements, key=str)
        )
        if not support:
            support = (ElementStoichiometryV1(element="H", amount=1),)
        return _result(
            decision=SmactPriorDecision.REQUIRES_REVIEW,
            policy=policy,
            backend_version=None,
            input_formula=input_formula or parsed.reduced_formula or "UNKNOWN",
            stoichiometry=support,
            smact_valid=None,
            reason_codes=(invalid_reason or "INVALID_COMPOSITION",),
        )

    source_formula = input_formula or _formula(stoichiometry)
    if len(stoichiometry) > policy.max_elements:
        return _result(
            decision=SmactPriorDecision.REQUIRES_REVIEW,
            policy=policy,
            backend_version=None,
            input_formula=source_formula,
            stoichiometry=stoichiometry,
            smact_valid=None,
            reason_codes=("SMACT_ELEMENT_BUDGET_EXCEEDED",),
        )
    if sum(item.amount for item in stoichiometry) > policy.max_reduced_stoichiometry_sum:
        return _result(
            decision=SmactPriorDecision.REQUIRES_REVIEW,
            policy=policy,
            backend_version=None,
            input_formula=source_formula,
            stoichiometry=stoichiometry,
            smact_valid=None,
            reason_codes=("SMACT_STOICHIOMETRY_BUDGET_EXCEEDED",),
        )

    try:
        backend_version = metadata.version("SMACT")
    except metadata.PackageNotFoundError:
        return _result(
            decision=SmactPriorDecision.REQUIRES_REVIEW,
            policy=policy,
            backend_version=None,
            input_formula=source_formula,
            stoichiometry=stoichiometry,
            smact_valid=None,
            reason_codes=("SMACT_PACKAGE_UNAVAILABLE",),
        )
    if backend_version != policy.smact_version:
        return _result(
            decision=SmactPriorDecision.REQUIRES_REVIEW,
            policy=policy,
            backend_version=backend_version,
            input_formula=source_formula,
            stoichiometry=stoichiometry,
            smact_valid=None,
            reason_codes=("SMACT_VERSION_MISMATCH",),
        )
    try:
        pymatgen_version = metadata.version("pymatgen")
    except metadata.PackageNotFoundError:
        pymatgen_version = None
    if pymatgen_version != policy.pymatgen_version:
        return _result(
            decision=SmactPriorDecision.REQUIRES_REVIEW,
            policy=policy,
            backend_version=backend_version,
            input_formula=source_formula,
            stoichiometry=stoichiometry,
            smact_valid=None,
            reason_codes=("SMACT_PYMATGEN_VERSION_MISMATCH",),
        )

    try:
        from smact import Element as SmactElement
        from smact import metals
        from smact.screening import ICSD24FilterConfig, smact_validity

        symbols = tuple(item.element for item in stoichiometry)
        if len(symbols) == 1:
            return _result(
                decision=SmactPriorDecision.REQUIRES_REVIEW,
                policy=policy,
                backend_version=backend_version,
                input_formula=source_formula,
                stoichiometry=stoichiometry,
                smact_valid=None,
                reason_codes=("MONOATOMIC_COMPOSITION_REQUIRES_REVIEW",),
            )
        if all(symbol in metals for symbol in symbols):
            return _result(
                decision=SmactPriorDecision.REQUIRES_REVIEW,
                policy=policy,
                backend_version=backend_version,
                input_formula=source_formula,
                stoichiometry=stoichiometry,
                smact_valid=None,
                reason_codes=("ALLOY_COMPOSITION_REQUIRES_REVIEW",),
            )

        elements = tuple(SmactElement(symbol) for symbol in symbols)
        if policy.require_known_pauling_electronegativity and any(
            element.pauling_eneg is None or element.pauling_eneg <= 0.0
            for element in elements
        ):
            return _result(
                decision=SmactPriorDecision.REQUIRES_REVIEW,
                policy=policy,
                backend_version=backend_version,
                input_formula=source_formula,
                stoichiometry=stoichiometry,
                smact_valid=None,
                reason_codes=("PAULING_ELECTRONEGATIVITY_UNAVAILABLE",),
            )

        available_symbols = _available_icsd24_symbols(
            consensus=policy.icsd_consensus,
            include_zero=policy.include_zero_oxidation_state,
            commonality=policy.icsd_commonality,
        )
        if any(symbol not in available_symbols for symbol in symbols):
            return _result(
                decision=SmactPriorDecision.REQUIRES_REVIEW,
                policy=policy,
                backend_version=backend_version,
                input_formula=source_formula,
                stoichiometry=stoichiometry,
                smact_valid=None,
                reason_codes=("ICSD24_OXIDATION_STATE_DATA_UNAVAILABLE",),
            )

        config = ICSD24FilterConfig(
            include_zero=policy.include_zero_oxidation_state,
            consensus=policy.icsd_consensus,
            commonality=policy.icsd_commonality,
        )
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            valid = bool(
                smact_validity(
                    _formula(stoichiometry),
                    use_pauling_test=policy.use_pauling_test,
                    include_alloys=policy.include_alloys,
                    check_metallicity=False,
                    icsd_filter=config,
                    mixed_valence=policy.mixed_valence,
                )
            )
        if any("too many combinations" in str(item.message) for item in caught):
            return _result(
                decision=SmactPriorDecision.REQUIRES_REVIEW,
                policy=policy,
                backend_version=backend_version,
                input_formula=source_formula,
                stoichiometry=stoichiometry,
                smact_valid=None,
                reason_codes=("SMACT_SEARCH_SPACE_LIMIT",),
            )
    except (
        ArithmeticError,
        ImportError,
        KeyError,
        OSError,
        RuntimeError,
        TypeError,
        ValueError,
    ):
        return _result(
            decision=SmactPriorDecision.REQUIRES_REVIEW,
            policy=policy,
            backend_version=backend_version,
            input_formula=source_formula,
            stoichiometry=stoichiometry,
            smact_valid=None,
            reason_codes=("SMACT_EVALUATION_ERROR",),
        )

    return _result(
        decision=(SmactPriorDecision.PASS if valid else SmactPriorDecision.REJECT),
        policy=policy,
        backend_version=backend_version,
        input_formula=source_formula,
        stoichiometry=stoichiometry,
        smact_valid=valid,
        reason_codes=(
            (
                "CHARGE_NEUTRALITY_AND_PAULING_PASS"
                if valid
                else "NO_ALLOWED_OXIDATION_STATE_ASSIGNMENT"
            ),
        ),
    )


def evaluate_smact_substitution_prior(
    request: SubstitutionExecutionRequestV1,
    *,
    parent_structure: Structure,
    policy: SmactPriorPolicyV1 = DEFAULT_SMACT_PRIOR_POLICY_V1,
    evaluator: SmactPriorEvaluator | None = None,
) -> SmactPriorGateResultV1:
    """Evaluate the proposed elemental composition before registry dispatch."""

    if not isinstance(request, SubstitutionExecutionRequestV1):
        raise TypeError("request must be a SubstitutionExecutionRequestV1")
    if not isinstance(parent_structure, Structure):
        raise TypeError("parent_structure must be a pymatgen Structure")

    parent = parent_structure.composition.element_composition
    parent_stoichiometry, _invalid_reason = _integer_stoichiometry(parent)
    parent_counts, _counts_reason = _integer_counts(parent)
    if parent_stoichiometry is None or parent_counts is None:
        return evaluate_smact_composition_prior(
            parent,
            input_formula=parent.reduced_formula or "UNKNOWN",
            policy=policy,
        )
    input_formula = _formula(parent_stoichiometry)
    parameters = request.plan.parameters
    try:
        source = str(Element(parameters.source_species))
        target = str(Element(parameters.target_species))
    except ValueError:
        return _result(
            decision=SmactPriorDecision.REJECT,
            policy=policy,
            backend_version=None,
            input_formula=input_formula,
            stoichiometry=parent_stoichiometry,
            smact_valid=None,
            reason_codes=("NON_CANONICAL_ROUTE_SPECIES",),
        )
    if source != parameters.source_species or target != parameters.target_species:
        return _result(
            decision=SmactPriorDecision.REJECT,
            policy=policy,
            backend_version=None,
            input_formula=input_formula,
            stoichiometry=parent_stoichiometry,
            smact_valid=None,
            reason_codes=("NON_CANONICAL_ROUTE_SPECIES",),
        )

    # Apply the route to absolute cell counts, then reduce only for SMACT.  Using
    # the reduced parent formula here would falsely reject supercells.
    counts = dict(parent_counts)
    selected_count = len(parameters.equivalent_site_indices)
    if counts.get(source, 0) < selected_count:
        return _result(
            decision=SmactPriorDecision.REJECT,
            policy=policy,
            backend_version=None,
            input_formula=input_formula,
            stoichiometry=parent_stoichiometry,
            smact_valid=None,
            reason_codes=("ROUTE_SOURCE_STOICHIOMETRY_MISMATCH",),
        )
    counts[source] -= selected_count
    if counts[source] == 0:
        counts.pop(source)
    counts[target] = counts.get(target, 0) + selected_count
    proposed = Composition(counts)
    stoichiometry, invalid_reason = _integer_stoichiometry(proposed)
    if stoichiometry is None:
        return evaluate_smact_composition_prior(
            proposed,
            input_formula=input_formula,
            policy=policy,
        )
    if evaluator is None:
        from material_agent.softchem.prior_client import (
            SmactPriorProcessError,
            smact_prior_evaluator_from_environment,
        )

        try:
            evaluator = smact_prior_evaluator_from_environment()
        except SmactPriorProcessError as exc:
            return _result(
                decision=SmactPriorDecision.REQUIRES_REVIEW,
                policy=policy,
                backend_version=None,
                input_formula=input_formula,
                stoichiometry=stoichiometry,
                smact_valid=None,
                reason_codes=(exc.category,),
            )
    if evaluator is not None:
        try:
            return evaluator.evaluate(
                proposed,
                input_formula=input_formula,
                policy=policy,
            )
        except Exception as exc:
            from material_agent.softchem.prior_client import SmactPriorProcessError

            if not isinstance(exc, SmactPriorProcessError):
                raise
            return _result(
                decision=SmactPriorDecision.REQUIRES_REVIEW,
                policy=policy,
                backend_version=None,
                input_formula=input_formula,
                stoichiometry=stoichiometry,
                smact_valid=None,
                reason_codes=(exc.category,),
            )

    return _result(
        decision=SmactPriorDecision.REQUIRES_REVIEW,
        policy=policy,
        backend_version=None,
        input_formula=input_formula,
        stoichiometry=stoichiometry,
        smact_valid=None,
        reason_codes=(invalid_reason or "SMACT_WORKER_UNCONFIGURED",),
    )
