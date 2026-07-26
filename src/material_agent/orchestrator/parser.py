"""Deterministic requirement parser used by the offline P0 path."""

from __future__ import annotations

import re
from copy import deepcopy
from typing import Any, Protocol, runtime_checkable

from pydantic import ValidationError

from material_agent.orchestrator.models import ParseResult
from material_agent.retrieval.models import Requirement


@runtime_checkable
class RequirementParser(Protocol):
    """Replaceable parser boundary; routing never depends on its provider."""

    name: str
    version: str

    def parse(self, raw_request: str, requirement_id: str) -> ParseResult: ...

    def normalize_structured(
        self, payload: dict[str, Any], requirement_id: str
    ) -> ParseResult: ...

    def apply_response(
        self, current: dict[str, Any], response: dict[str, Any]
    ) -> dict[str, Any]: ...


class OfflineRequirementParser:
    """Parse the fixed acceptance request without an LLM.

    Unknown requests are not guessed. They produce a draft plus explicit
    clarification questions, which the graph exposes through an interrupt.
    """

    name = "offline-demo-parser"
    version = "offline-requirement-parser-v1"

    def parse(self, raw_request: str, requirement_id: str) -> ParseResult:
        normalized = (
            raw_request.replace("–", "-")
            .replace("—", "-")
            .replace("≤", "<=")
            .strip()
        )
        include_elements = _parse_include_elements(normalized)
        band_gap = _parse_range(
            normalized,
            (
                r"(?:带隙|band[\s_-]*gap)"
                r"[^0-9]{0,30}([0-9]+(?:\.[0-9]+)?)"
                r"\s*(?:-|~|至|到)\s*([0-9]+(?:\.[0-9]+)?)\s*eV"
            ),
            "eV",
        )
        hull = _parse_upper_bound(
            normalized,
            (
                r"(?:energy[\s_-]*above[\s_-]*hull|凸包能|离凸包能)"
                r"[^0-9]{0,40}([0-9]+(?:\.[0-9]+)?)\s*eV(?:/atom)?"
            ),
            "eV/atom",
        )
        is_nonmetal = bool(
            re.search(r"非金属|non[\s-]?metal", normalized, flags=re.IGNORECASE)
        )

        questions: list[str] = []
        if not include_elements:
            questions.append("请明确必须包含的元素符号。")
        if band_gap is None:
            questions.append("请明确带隙范围及单位 eV。")
        if hull is None:
            questions.append(
                "请明确 energy above hull 上限及单位 eV/atom。"
            )
        if not is_nonmetal:
            questions.append("请确认是否要求非金属材料。")

        requirement = {
            "requirement_id": requirement_id,
            "revision": 1,
            "target_class": (
                "simple_semiconductor" if band_gap and is_nonmetal else "custom"
            ),
            "hard_constraints": {
                "include_elements": include_elements,
                "exclude_elements": [],
                "band_gap_ev": band_gap,
                "energy_above_hull_ev_atom": hull,
                "is_metal": False if is_nonmetal else None,
                "dimensionality": None,
                "max_num_sites": None,
            },
            "scientific_targets": [],
            "ranking_preferences": [
                {"property": "energy_above_hull", "mode": "minimize"}
            ],
            "budget": {
                "max_candidates": 200,
                "allow_ml": True,
                "allow_dft": False,
                "allow_many_body": False,
            },
            "data_sources": {"materials_project": {"include_gnome": False}},
            "confirmed_by_user": False,
            "policy_version": "requirement-policy-v1",
        }
        Requirement.model_validate(requirement)
        return ParseResult(
            requirement=requirement,
            clarification_questions=questions,
            parser_name=self.name,
            parser_version=self.version,
        )

    def normalize_structured(
        self, payload: dict[str, Any], requirement_id: str
    ) -> ParseResult:
        candidate = deepcopy(payload)
        candidate["requirement_id"] = candidate.get(
            "requirement_id", requirement_id
        )
        candidate["revision"] = int(candidate.get("revision", 1))
        candidate["confirmed_by_user"] = False
        requirement = Requirement.model_validate(candidate)
        return ParseResult(
            requirement=requirement.model_dump(mode="json"),
            parser_name="structured-requirement-input",
            parser_version=self.version,
        )

    def apply_response(
        self, current: dict[str, Any], response: dict[str, Any]
    ) -> dict[str, Any]:
        if isinstance(response.get("requirement"), dict):
            candidate = deepcopy(response["requirement"])
        elif isinstance(response.get("changes"), dict):
            candidate = _deep_merge(deepcopy(current), response["changes"])
        else:
            raise ValueError(
                "clarification response must contain 'requirement' or 'changes'"
            )
        candidate["requirement_id"] = current["requirement_id"]
        candidate["revision"] = current["revision"]
        candidate["confirmed_by_user"] = False
        try:
            return Requirement.model_validate(candidate).model_dump(mode="json")
        except ValidationError as exc:
            raise ValueError(f"invalid Requirement response: {exc}") from exc


def _parse_include_elements(text: str) -> list[str]:
    patterns = (
        r"(?:包含|含有)\s*([A-Z][a-z]?)\s*(?:和|、|,)\s*([A-Z][a-z]?)",
        r"(?:contains?|including)\s+([A-Z][a-z]?)\s*(?:and|,)\s*([A-Z][a-z]?)",
    )
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            return sorted({match.group(1).title(), match.group(2).title()})
    if re.search(r"\bSi\b", text) and re.search(r"\bO\b", text):
        return ["O", "Si"]
    return []


def _parse_range(text: str, pattern: str, unit: str) -> dict[str, Any] | None:
    match = re.search(pattern, text, flags=re.IGNORECASE)
    if not match:
        return None
    return {"min": float(match.group(1)), "max": float(match.group(2)), "unit": unit}


def _parse_upper_bound(
    text: str, pattern: str, unit: str
) -> dict[str, Any] | None:
    match = re.search(pattern, text, flags=re.IGNORECASE)
    if not match:
        return None
    return {"min": None, "max": float(match.group(1)), "unit": unit}


def _deep_merge(base: dict[str, Any], changes: dict[str, Any]) -> dict[str, Any]:
    for key, value in changes.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            base[key] = _deep_merge(dict(base[key]), value)
        else:
            base[key] = deepcopy(value)
    return base
