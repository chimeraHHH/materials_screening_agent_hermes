from __future__ import annotations

import hashlib
import json
import warnings
from collections.abc import Mapping
from dataclasses import dataclass
from itertools import combinations
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from pymatgen.analysis.structure_matcher import SpeciesComparator, StructureMatcher
from pymatgen.core import Structure

from material_agent.gateway.authorization import RequirementFreezeGrantIssuer
from material_agent.gateway.mcp_server import GatewayServerSettings, GatewayToolDispatcher
from material_agent.gateway.models import InspirationBudgetV1, InspirationConstraintsV1
from material_agent.inspiration.parent_catalog import FLAT_BAND_PARENT_CATALOG_ID
from material_agent.integration.hermes_service import create_hermes_inspiration_service


_PROJECT_ID = "hermes-parent-catalog-top5"
_SUBMISSION_ID = "parent-catalog-top5-submission"


def _crossref_item(kind: str) -> bytes:
    records = {
        "direct": (
            "10.5555/catalog-top5.direct",
            "Bounded electronic flat-band metadata context",
            "An electronic flat band is a bounded metadata target for a layered "
            "transition metal material. This source records search context only, "
            "and says that a computed band dispersion is required before any "
            "material-property conclusion can be made.",
            ("Electronic flat band",),
        ),
        "acoustic": (
            "10.5555/catalog-top5.acoustic",
            "Acoustic metamaterial local resonance flat band",
            "An acoustic metamaterial supports a local resonance flat band because "
            "a spectrally identifiable local mode couples weakly to an extended "
            "network. The local resonance mechanism suppresses dispersion while "
            "spectral separation remains controlled, and strong hybridization "
            "breaks the localized response.",
            ("Acoustic metamaterial", "Local resonance flat band"),
        ),
        "magnon": (
            "10.5555/catalog-top5.magnon",
            "Frustrated magnetism flat magnon band and line graph localization",
            "Frustrated magnetism on kagome connectivity realizes line graph flat "
            "band localization because connectivity-equivalent hopping paths "
            "support a localized eigenvector. The mechanism suppresses dispersion, "
            "while connectivity changes break the localized mode and provide a "
            "bounded falsification condition.",
            ("Frustrated magnetism flat magnon band", "Line graph localization"),
        ),
        "photonic": (
            "10.5555/catalog-top5.photonic",
            "Photonic lattice compact localized state and destructive interference",
            "A photonic lattice hosts a compact localized state because destructive "
            "interference flat band cancellation balances coherent propagation "
            "paths. The compact localized state mechanism confines amplitude, while "
            "path imbalance or disorder breaks destructive interference and supplies "
            "a bounded falsification condition.",
            ("Photonic lattice", "Destructive interference flat band"),
        ),
    }
    doi, title, abstract, subjects = records[kind]
    return json.dumps(
        {
            "message": {
                "items": [
                    {
                        "DOI": doi,
                        "URL": f"https://doi.org/{doi}",
                        "abstract": f"<jats:p>{abstract}</jats:p>",
                        "author": [{"family": "Reviewer", "given": "Bounded"}],
                        "published": {"date-parts": [[2026]]},
                        "subject": list(subjects),
                        "title": [title],
                    }
                ]
            },
            "status": "ok",
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


class QueryAwareStaticCrossrefTransport:
    """Return one distinct, bridge-complete metadata record per planned query."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def get(
        self,
        url: str,
        *,
        headers: Mapping[str, str],
        timeout_seconds: int,
        max_response_bytes: int,
    ) -> bytes:
        del headers, timeout_seconds
        parameters = parse_qs(urlsplit(url).query, strict_parsing=True)
        texts = parameters.get("query.bibliographic")
        assert texts is not None and len(texts) == 1
        query_text = texts[0].casefold()
        if "acoustic metamaterial" in query_text:
            kind = "acoustic"
        elif "frustrated magnetism" in query_text:
            kind = "magnon"
        elif "photonic lattice" in query_text:
            kind = "photonic"
        else:
            assert query_text == "electronic flat band"
            kind = "direct"
        payload = _crossref_item(kind)
        assert len(payload) <= max_response_bytes
        self.calls.append((kind, url))
        return payload


def _arguments() -> dict[str, object]:
    constraints = InspirationConstraintsV1(
        required_elements=("Se", "Ti"),
        excluded_elements=("Pb",),
        material_classes=("layered transition-metal dichalcogenide",),
        dimensionality="2D",
        target_features=("narrow electronic band",),
        top_k=5,
        require_diverse_routes=True,
        budget=InspirationBudgetV1(
            max_search_requests=8,
            max_unique_documents=4,
            max_passages=4,
            max_model_calls=0,
            max_walltime_seconds=300,
        ),
    )
    return {
        "submission_id": _SUBMISSION_ID,
        "goal": (
            "Generate five reviewable, mechanism-guided structure proposals from "
            "the bounded operator catalog."
        ),
        "constraints": constraints.model_dump(mode="json"),
    }


@dataclass(frozen=True, slots=True)
class _CompletedRun:
    project_root: Path
    run_id: str
    result: dict[str, object]
    transport_kinds: tuple[str, ...]

    @property
    def stage_root(self) -> Path:
        return self.project_root / "stages" / "inspiration" / self.run_id


def _run_approved_gateway(workspace: Path) -> _CompletedRun:
    transport = QueryAwareStaticCrossrefTransport()
    service = create_hermes_inspiration_service(
        GatewayServerSettings(workspace, _PROJECT_ID),
        transport=transport,
        sleeper=lambda _seconds: None,
        wall_clock=lambda: 0.0,
        monotonic_clock=lambda: 0.0,
    )
    dispatcher = GatewayToolDispatcher(service)
    try:
        started = dispatcher.dispatch("materials_inspiration_run", _arguments())
        assert started["state"]["status"] == "INTERACTION_REQUIRED"
        run_id = started["run_id"]
        record = service.repository.get_run(run_id)
        assert record is not None
        prepared = service.companion.preparer.prepare(
            run_id=record.run_id,
            request=record.request,
        )
        manifest_sha256 = service.companion.execution_manifest_sha256(
            request=record.request,
            prepared=prepared,
        )
        assert started["state"]["interaction"]["input_sha256"] == manifest_sha256
        RequirementFreezeGrantIssuer(
            repository=service.repository,
            grant_store=service.action_authorizer,
        ).grant_current(
            run_id=run_id,
            confirmation_reference="test:parent-catalog-top5-user-confirmation",
            expected_execution_manifest_sha256=manifest_sha256,
        )
        terminal = dispatcher.dispatch(
            "materials_run_act",
            {
                "run_id": run_id,
                "action": {
                    "kind": "approve",
                    "interaction_id": started["state"]["interaction"][
                        "interaction_id"
                    ],
                    "confirmed_by_user": True,
                },
            },
        )
        # The direct target-only passage deliberately has no mechanism tag, so
        # the conservative Gateway projection is PARTIAL even though the
        # scientific stage and all Top-5 diversity quotas succeed.
        assert terminal["state"]["status"] == "PARTIAL"
        result = dispatcher.dispatch("materials_result_get", {"run_id": run_id})
        assert result["verified"] is True
        assert len(result["bundle"]["selected_candidates"]) == 5
        assert result["cost_ledger"]["search_requests"] == 4
        assert result["cost_ledger"]["model_calls"] == 0
        return _CompletedRun(
            project_root=workspace / _PROJECT_ID,
            run_id=run_id,
            result=result,
            transport_kinds=tuple(kind for kind, _url in transport.calls),
        )
    finally:
        service.repository.close()


def _read_json(path: Path) -> dict[str, object]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(payload, dict)
    return payload


def _read_jsonl(path: Path) -> tuple[dict[str, object], ...]:
    records = tuple(
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    )
    assert all(isinstance(record, dict) for record in records)
    return records


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _assert_selected_structures_are_pairwise_strictly_distinct(
    run: _CompletedRun,
    candidates: tuple[dict[str, object], ...],
) -> None:
    structures: list[Structure] = []
    for candidate in candidates:
        artifact = candidate["structure_artifact"]
        assert isinstance(artifact, dict)
        uri = artifact["uri"]
        assert isinstance(uri, str) and uri.startswith("artifact://")
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            structures.append(
                Structure.from_str(
                    (run.project_root / uri.removeprefix("artifact://")).read_text(
                        encoding="utf-8"
                    ),
                    fmt="cif",
                )
            )
    matcher = StructureMatcher(
        ltol=1e-6,
        stol=1e-5,
        angle_tol=1e-5,
        primitive_cell=False,
        scale=False,
        attempt_supercell=False,
        allow_subset=False,
        comparator=SpeciesComparator(),
    )
    assert all(not matcher.fit(left, right) for left, right in combinations(structures, 2))


def test_parent_catalog_top5_gateway_replays_complete_diverse_lineage(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.delenv("MATERIALS_CROSSREF_CONTACT_EMAIL", raising=False)
    first = _run_approved_gateway(tmp_path / "workspace-a")
    second = _run_approved_gateway(tmp_path / "workspace-b")

    assert first.run_id == second.run_id
    assert first.transport_kinds == second.transport_kinds
    assert first.transport_kinds == ("direct", "acoustic", "magnon", "photonic")

    plans = _read_jsonl(first.stage_root / "transformation_proposals.jsonl")
    assert len(plans) == 6
    assert all(plan["status"] == "STRUCTURE_VALID" for plan in plans)
    assert len({plan["route_sha256"] for plan in plans}) == 6

    bridges = _read_jsonl(first.stage_root / "bridge_packets.jsonl")
    assert len(bridges) == 3
    assert {bridge["bridge_rule_id"] for bridge in bridges} == {
        "acoustic-resonance-to-electronic-flat-band",
        "magnon-line-graph-to-electronic-flat-band",
        "photonic-interference-to-electronic-flat-band",
    }

    duplicate_groups = _read_jsonl(
        first.stage_root / "internal_duplicate_groups.jsonl"
    )
    assert len(duplicate_groups) == 5
    merged_groups = tuple(
        group for group in duplicate_groups if len(group["merged_route_sha256s"]) == 2
    )
    assert len(merged_groups) == 1

    bundle = _read_json(first.stage_root / "inspiration_bundle.json")
    selected = tuple(bundle["selected_candidates"])
    assert len(selected) == 5
    converged = tuple(
        candidate for candidate in selected if len(candidate["merged_routes"]) == 2
    )
    assert len(converged) == 1
    converged_candidate = converged[0]
    routes = tuple(converged_candidate["merged_routes"])
    assert len({route["route_sha256"] for route in routes}) == 2
    assert len({route["parent_candidate_id"] for route in routes}) == 2
    assert len(converged_candidate["parent_candidate_ids"]) == 2
    assert len(
        {
            bridge_id
            for route in routes
            for bridge_id in route["bridge_packet_ids"]
        }
    ) == 2
    assert len(
        {
            evidence_id
            for route in routes
            for evidence_id in route["evidence_card_ids"]
        }
    ) == 2

    audit = _read_json(first.stage_root / "selection_audit.json")
    assert audit["structure_valid_proposal_count"] == 6
    assert audit["post_exact_merge_candidate_count"] == 5
    assert audit["exact_merge_reduction_count"] == 1
    assert audit["selected_candidate_count"] == 5
    assert audit["quota_status"] == "MET"
    assert audit["route_quota_status"] == "MET"
    assert audit["achieved_mechanism_count"] >= 2
    assert audit["selected_distinct_physical_route_count"] >= 2
    assert audit["selected_multi_route_group_count"] == 1
    assert audit["underfill_reasons"] == []
    assert audit["selected_exact_duplicate_count"] == 0
    assert audit["selected_strict_duplicate_count"] == 0

    stage = _read_json(first.stage_root / "stage_result.json")
    assert stage["outcome"] == "SUCCEEDED"
    assert len(stage["warnings"]) == 1
    assert stage["warnings"][0].startswith("NO_MECHANISM_TAG:")

    _assert_selected_structures_are_pairwise_strictly_distinct(first, selected)

    relative_artifacts = {
        "catalog": Path(
            "inputs",
            "catalogs",
            FLAT_BAND_PARENT_CATALOG_ID,
            "manifest.json",
        ),
        "plans": Path(
            "stages",
            "inspiration",
            first.run_id,
            "transformation_proposals.jsonl",
        ),
        "duplicates": Path(
            "stages",
            "inspiration",
            first.run_id,
            "internal_duplicate_groups.jsonl",
        ),
        "selection_audit": Path(
            "stages", "inspiration", first.run_id, "selection_audit.json"
        ),
        "report": Path("stages", "inspiration", first.run_id, "report.md"),
        "bundle": Path(
            "stages", "inspiration", first.run_id, "inspiration_bundle.json"
        ),
        "stage": Path("stages", "inspiration", first.run_id, "stage_result.json"),
    }
    first_hashes = {
        name: _sha256(first.project_root / relative)
        for name, relative in relative_artifacts.items()
    }
    second_hashes = {
        name: _sha256(second.project_root / relative)
        for name, relative in relative_artifacts.items()
    }
    assert first_hashes == second_hashes
