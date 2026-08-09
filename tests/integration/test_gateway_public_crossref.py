from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path

import pytest

from material_agent.gateway.authorization import RequirementFreezeGrantIssuer
from material_agent.gateway.companion import (
    CompanionAdapterError,
    OfflineInspirationCompanionAdapter,
)
from material_agent.gateway.mcp_server import GatewayServerSettings, GatewayToolDispatcher
from material_agent.gateway.models import (
    ApproveActionV1,
    InspirationBudgetV1,
    InspirationConstraintsV1,
)
from material_agent.gateway.persistence import SqliteGatewayRepository
from material_agent.integration.hermes_service import create_hermes_inspiration_service
from material_agent.inspiration.search import SearchAdapterError


class StaticCrossrefTransport:
    """Exercise the production adapter boundary without network access."""

    def __init__(self, payload: bytes) -> None:
        self.payload = payload
        self.calls: list[str] = []

    def get(
        self,
        url: str,
        *,
        headers: Mapping[str, str],
        timeout_seconds: float,
        max_response_bytes: int,
        deadline_monotonic: float | None = None,
        max_physical_requests: int | None = None,
    ) -> bytes:
        del headers, timeout_seconds, deadline_monotonic, max_physical_requests
        assert len(self.payload) <= max_response_bytes
        self.calls.append(url)
        return self.payload


class FailingCrossrefTransport:
    """Return one stable provider failure for every bounded HTTP attempt."""

    def __init__(self, *, code: str, http_status: int | None) -> None:
        self.code = code
        self.http_status = http_status
        self.calls: list[str] = []

    def get(
        self,
        url: str,
        *,
        headers: Mapping[str, str],
        timeout_seconds: float,
        max_response_bytes: int,
        deadline_monotonic: float | None = None,
        max_physical_requests: int | None = None,
    ) -> bytes:
        del (
            headers,
            timeout_seconds,
            max_response_bytes,
            deadline_monotonic,
            max_physical_requests,
        )
        self.calls.append(url)
        raise SearchAdapterError(
            self.code,
            "provider details must stay behind the public failure boundary",
            http_status=self.http_status,
        )


def _response_bytes() -> bytes:
    abstract = (
        "Local resonance in an acoustic metamaterial produces a weakly dispersive "
        "mode because a resonator couples weakly to an extended lattice. The local "
        "resonance mechanism preserves spectral separation and suppresses dispersion, "
        "which can guide electronic flat band hypotheses when connectivity and "
        "equivalent site chemistry remain controlled. Strong hybridization breaks "
        "localization and broadens the mode, providing a falsification condition."
    )
    return json.dumps(
        {
            "message": {
                "items": [
                    {
                        "DOI": "10.5555/hermes-public-static.1",
                        "URL": "https://doi.org/10.5555/hermes-public-static.1",
                        "abstract": f"<jats:p>{abstract}</jats:p>",
                        "author": [{"family": "Reviewer", "given": "Bounded"}],
                        "published": {"date-parts": [[2026]]},
                        "subject": ["Local resonance", "Electronic flat band"],
                        "title": ["Bounded cross-domain local resonance"],
                    }
                ]
            },
            "status": "ok",
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _arguments() -> dict[str, object]:
    constraints = InspirationConstraintsV1(
        required_elements=("Se", "Ti"),
        excluded_elements=("Pb",),
        material_classes=("layered transition-metal dichalcogenide",),
        dimensionality="2D",
        target_features=("narrow electronic band",),
        top_k=1,
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
        "submission_id": "public-crossref-static-submission",
        "goal": (
            "Find a reviewable narrow-band mechanism using bounded public metadata."
        ),
        "constraints": constraints.model_dump(mode="json"),
    }


def _approve_current_interaction(service, dispatcher):
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
    RequirementFreezeGrantIssuer(
        repository=service.repository,
        grant_store=service.action_authorizer,
    ).grant_current(
        run_id=run_id,
        confirmation_reference="test:public-failure-user-confirmation",
        expected_execution_manifest_sha256=manifest_sha256,
    )
    terminal = dispatcher.dispatch(
        "materials_run_act",
        {
            "run_id": run_id,
            "action": {
                "kind": "approve",
                "interaction_id": started["state"]["interaction"]["interaction_id"],
                "confirmed_by_user": True,
            },
        },
    )
    return run_id, terminal


def test_public_factory_runs_static_crossref_through_approval_lifecycle(
    tmp_path: Path,
    monkeypatch,
) -> None:
    contact_email = "private-crossref-contact@example.test"
    monkeypatch.setenv("MATERIALS_CROSSREF_CONTACT_EMAIL", contact_email)
    transport = StaticCrossrefTransport(_response_bytes())
    settings = GatewayServerSettings(tmp_path, "hermes-public-static")
    service = create_hermes_inspiration_service(
        settings,
        transport=transport,
        sleeper=lambda _seconds: None,
    )
    dispatcher = GatewayToolDispatcher(service)

    started = dispatcher.dispatch("materials_inspiration_run", _arguments())
    assert started["state"]["status"] == "INTERACTION_REQUIRED"
    run_id = started["run_id"]
    interaction_id = started["state"]["interaction"]["interaction_id"]
    assert started["state"]["interaction"]["prompt"] == (
        "Freeze this bounded inspiration request before execution? Approval "
        "permits bounded public Crossref metadata/abstract network access with "
        "at most 8 physical search attempts; article-body fetch requests=0, "
        "full-PDF reads=0, and internal model calls=0."
    )

    assert isinstance(service.companion, OfflineInspirationCompanionAdapter)
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
    assert (
        started["state"]["interaction"]["execution_manifest_sha256"]
        == manifest_sha256
    )
    assert prepared.policy.network_access is True
    assert prepared.inspiration_input.search_fixture_artifact is None
    assert len(prepared.inspiration_input.parent_candidates) == 6
    requirement = service.companion.preparer.store.read_json(
        prepared.inspiration_input.requirement_artifact.uri
    )
    compiled_scope = requirement["compiled_scope"]
    assert compiled_scope["parent_catalog_id"] == "flat-band-parent-catalog-v1"
    assert compiled_scope["parent_catalog_sha256"] == (
        "09d563732717e05ccf216d3b8572b1bcd1d855dd3f5d0106a4cdbc15b9197b99"
    )
    assert len(compiled_scope["catalog_entries"]) == 6
    assert prepared.inspiration_input.search_adapter == (
        service.companion.runner.search_adapter.component
    )
    component_ids = {
        item.component_id for item in service.companion.runner.execution_components
    }
    assert {
        "crossref-public-adapter",
        "disabled-document-fetcher",
        "inspiration-bridge-implementation",
        "inspiration-contracts-implementation",
        "inspiration-evidence-implementation",
        "inspiration-extraction-implementation",
        "inspiration-feedback-implementation",
        "inspiration-identity-implementation",
        "inspiration-passages-implementation",
        "inspiration-projector-implementation",
        "inspiration-report-implementation",
        "inspiration-request-compiler-implementation",
        "inspiration-runner-implementation",
        "inspiration-search-implementation",
        "inspiration-selection-implementation",
        "inspiration-transform-implementation",
        "inspiration-vector-implementation",
        "inspiration-tag-feedback-compiler",
        "material-agent-build-identity",
        "material-agent-execution-source-tree",
        "pymatgen-substitution-engine",
        "signed-hashing-v1",
    }.issubset(component_ids)
    engine_component = service.companion.runner.transformation_engine.component
    assert engine_component.version == "2-catalog-v1"

    issuer = RequirementFreezeGrantIssuer(
        repository=service.repository,
        grant_store=service.action_authorizer,
    )
    issuer.grant_current(
        run_id=run_id,
        confirmation_reference="test:public-static-user-confirmation",
        expected_execution_manifest_sha256=manifest_sha256,
    )
    terminal = dispatcher.dispatch(
        "materials_run_act",
        {
            "run_id": run_id,
            "action": {
                "kind": "approve",
                "interaction_id": interaction_id,
                "confirmed_by_user": True,
            },
        },
    )
    assert terminal["state"]["status"] in {"PARTIAL", "SUCCEEDED"}
    result = dispatcher.dispatch("materials_result_get", {"run_id": run_id})
    assert result["verified"] is True
    assert result["readable_report"]["content"].startswith(
        "# Inspiration run report"
    )
    assert result["readable_report"]["full_content_sha256"] == result[
        "authoritative_sha256"
    ]
    assert result["readable_evidence"]["total_available"] >= 1
    assert result["readable_evidence"]["items"][0]["excerpt"]
    assert result["readable_evidence"]["items"][0]["relations"] == ["SUPPORT"]
    assert len(result["bundle"]["selected_candidates"]) == 1
    assert result["cost_ledger"]["model_calls"] == 0
    assert len(transport.calls) == 4
    assert all("api.crossref.org/v1/works" in url for url in transport.calls)

    project_root = tmp_path / "hermes-public-static"
    text_artifacts = tuple(
        path
        for path in project_root.rglob("*")
        if path.is_file() and path.suffix in {".cif", ".json", ".jsonl", ".md"}
    )
    assert text_artifacts
    assert all(contact_email.encode("utf-8") not in path.read_bytes() for path in text_artifacts)

    assert isinstance(service.repository, SqliteGatewayRepository)
    service.repository.close()


def test_completed_public_run_recovers_after_precommit_crash_without_network(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Discard a terminal transition, restart, and recover its exact closure."""

    monkeypatch.delenv("MATERIALS_CROSSREF_CONTACT_EMAIL", raising=False)
    settings = GatewayServerSettings(tmp_path, "hermes-public-recovery")
    first_transport = StaticCrossrefTransport(_response_bytes())
    first_service = create_hermes_inspiration_service(
        settings,
        transport=first_transport,
        sleeper=lambda _seconds: None,
    )
    first_dispatcher = GatewayToolDispatcher(first_service)
    started = first_dispatcher.dispatch("materials_inspiration_run", _arguments())
    run_id = started["run_id"]
    first_record = first_service.repository.get_run(run_id)
    assert first_record is not None
    first_prepared = first_service.companion.preparer.prepare(
        run_id=run_id,
        request=first_record.request,
    )
    manifest_sha256 = first_service.companion.execution_manifest_sha256(
        request=first_record.request,
        prepared=first_prepared,
    )

    # The companion completed, but its transition is intentionally discarded
    # before MaterialsGatewayService can commit it.
    approval = ApproveActionV1(
        interaction_id=started["state"]["interaction"]["interaction_id"],
        confirmed_by_user=True,
    )
    discarded_terminal = first_service.companion.act(
        run_id=run_id,
        request=first_record.request,
        state=first_record.state,
        action=approval,
    )
    assert discarded_terminal.result is not None
    assert first_transport.calls
    first_call_count = len(first_transport.calls)
    first_service.repository.close()

    second_transport = StaticCrossrefTransport(_response_bytes())
    second_service = create_hermes_inspiration_service(
        settings,
        transport=second_transport,
        sleeper=lambda _seconds: None,
    )
    pending_record = second_service.repository.get_run(run_id)
    assert pending_record is not None
    assert pending_record.state.status == "INTERACTION_REQUIRED"
    second_prepared = second_service.companion.preparer.prepare(
        run_id=run_id,
        request=pending_record.request,
    )
    assert second_service.companion.execution_manifest_sha256(
        request=pending_record.request,
        prepared=second_prepared,
    ) == manifest_sha256

    recovered_terminal = second_service.companion.act(
        run_id=run_id,
        request=pending_record.request,
        state=pending_record.state,
        action=approval,
    )

    assert recovered_terminal == discarded_terminal
    assert len(first_transport.calls) == first_call_count
    assert second_transport.calls == []
    assert recovered_terminal.result is not None
    closure = recovered_terminal.result.artifact_closure
    assert closure is not None
    assert closure.stage_result.uri == (
        f"artifact://stages/inspiration/{run_id}/stage_result.json"
    )
    assert closure.artifacts
    second_service.repository.close()


@pytest.mark.parametrize(
    "artifact_name",
    ("inspiration_bundle.json", "stage_result.json"),
)
def test_completed_public_recovery_fails_closed_on_closure_drift(
    tmp_path: Path,
    monkeypatch,
    artifact_name: str,
) -> None:
    monkeypatch.delenv("MATERIALS_CROSSREF_CONTACT_EMAIL", raising=False)
    project_id = f"hermes-public-recovery-{artifact_name.removesuffix('.json')}"
    settings = GatewayServerSettings(tmp_path, project_id)
    first_transport = StaticCrossrefTransport(_response_bytes())
    first_service = create_hermes_inspiration_service(
        settings,
        transport=first_transport,
        sleeper=lambda _seconds: None,
    )
    started = GatewayToolDispatcher(first_service).dispatch(
        "materials_inspiration_run",
        _arguments(),
    )
    run_id = started["run_id"]
    record = first_service.repository.get_run(run_id)
    assert record is not None
    prepared = first_service.companion.preparer.prepare(
        run_id=run_id,
        request=record.request,
    )
    manifest_sha256 = first_service.companion.execution_manifest_sha256(
        request=record.request,
        prepared=prepared,
    )
    completed = first_service.companion._execute(
        run_id=run_id,
        request=record.request,
        expected_execution_manifest_sha256=manifest_sha256,
    )
    assert completed.result is not None
    drifted_path = (
        tmp_path
        / project_id
        / "stages"
        / "inspiration"
        / run_id
        / artifact_name
    )
    if artifact_name == "stage_result.json":
        drifted_path.write_bytes(drifted_path.read_bytes() + b"\n")
    else:
        drifted_path.write_bytes(b"{}")
    first_service.repository.close()

    retry_transport = StaticCrossrefTransport(_response_bytes())
    retry_service = create_hermes_inspiration_service(
        settings,
        transport=retry_transport,
        sleeper=lambda _seconds: None,
    )
    retry_record = retry_service.repository.get_run(run_id)
    assert retry_record is not None
    with pytest.raises(CompanionAdapterError):
        retry_service.companion._execute(
            run_id=run_id,
            request=retry_record.request,
            expected_execution_manifest_sha256=manifest_sha256,
        )
    assert retry_transport.calls == []
    retry_service.repository.close()


def test_partial_bound_public_run_is_not_implicitly_reexecuted(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.delenv("MATERIALS_CROSSREF_CONTACT_EMAIL", raising=False)
    settings = GatewayServerSettings(tmp_path, "hermes-public-partial-recovery")
    first_transport = StaticCrossrefTransport(_response_bytes())
    first_service = create_hermes_inspiration_service(
        settings,
        transport=first_transport,
        sleeper=lambda _seconds: None,
    )
    started = GatewayToolDispatcher(first_service).dispatch(
        "materials_inspiration_run",
        _arguments(),
    )
    run_id = started["run_id"]
    record = first_service.repository.get_run(run_id)
    assert record is not None
    prepared = first_service.companion.preparer.prepare(
        run_id=run_id,
        request=record.request,
    )
    manifest_sha256 = first_service.companion.execution_manifest_sha256(
        request=record.request,
        prepared=prepared,
    )
    assert first_service.companion.projector.recover_or_bind_completed(
        run_id=run_id,
        request=record.request,
        prepared=prepared,
        execution_manifest_sha256=manifest_sha256,
    ) is None
    assert first_transport.calls == []
    first_service.repository.close()

    retry_transport = StaticCrossrefTransport(_response_bytes())
    retry_service = create_hermes_inspiration_service(
        settings,
        transport=retry_transport,
        sleeper=lambda _seconds: None,
    )
    retry_record = retry_service.repository.get_run(run_id)
    assert retry_record is not None
    with pytest.raises(CompanionAdapterError, match="incomplete"):
        retry_service.companion._execute(
            run_id=run_id,
            request=retry_record.request,
            expected_execution_manifest_sha256=manifest_sha256,
        )
    assert retry_transport.calls == []
    retry_service.repository.close()


@pytest.mark.parametrize("prior_state", ("unbound-stage", "different-manifest"))
def test_untrusted_prior_public_state_is_never_upgraded_or_reexecuted(
    tmp_path: Path,
    monkeypatch,
    prior_state: str,
) -> None:
    monkeypatch.delenv("MATERIALS_CROSSREF_CONTACT_EMAIL", raising=False)
    project_id = f"hermes-public-prior-{prior_state}"
    settings = GatewayServerSettings(tmp_path, project_id)
    transport = StaticCrossrefTransport(_response_bytes())
    service = create_hermes_inspiration_service(
        settings,
        transport=transport,
        sleeper=lambda _seconds: None,
    )
    started = GatewayToolDispatcher(service).dispatch(
        "materials_inspiration_run",
        _arguments(),
    )
    run_id = started["run_id"]
    record = service.repository.get_run(run_id)
    assert record is not None
    prepared = service.companion.preparer.prepare(
        run_id=run_id,
        request=record.request,
    )
    manifest_sha256 = service.companion.execution_manifest_sha256(
        request=record.request,
        prepared=prepared,
    )
    if prior_state == "unbound-stage":
        prior_path = (
            tmp_path
            / project_id
            / "stages"
            / "inspiration"
            / run_id
            / "partial.json"
        )
        prior_path.parent.mkdir(parents=True)
        prior_path.write_bytes(b"{}")
    else:
        assert service.companion.projector.recover_or_bind_completed(
            run_id=run_id,
            request=record.request,
            prepared=prepared,
            execution_manifest_sha256="0" * 64,
        ) is None

    with pytest.raises(CompanionAdapterError):
        service.companion._execute(
            run_id=run_id,
            request=record.request,
            expected_execution_manifest_sha256=manifest_sha256,
        )
    assert transport.calls == []
    service.repository.close()


def test_public_factory_exposes_retryable_failure_after_transient_exhaustion(
    tmp_path: Path,
    monkeypatch,
) -> None:
    contact_email = "private-crossref-contact@example.test"
    monkeypatch.setenv("MATERIALS_CROSSREF_CONTACT_EMAIL", contact_email)
    transport = FailingCrossrefTransport(
        code="TRANSIENT_HTTP_ERROR",
        http_status=503,
    )
    settings = GatewayServerSettings(tmp_path, "hermes-public-transient")
    service = create_hermes_inspiration_service(
        settings,
        transport=transport,
        sleeper=lambda _seconds: None,
    )
    dispatcher = GatewayToolDispatcher(service)

    run_id, terminal = _approve_current_interaction(service, dispatcher)

    assert terminal["state"] == {
        "status": "FAILED",
        "public_error_code": "EXTERNAL_SEARCH_UNAVAILABLE",
        "public_message": (
            "public metadata search is temporarily unavailable; submit a new run later"
        ),
        "retryable": True,
    }
    assert len(transport.calls) == 2
    assert all("api.crossref.org/v1/works" in url for url in transport.calls)

    attempts_path = (
        tmp_path
        / "hermes-public-transient"
        / "stages"
        / "inspiration"
        / run_id
        / "search_attempts.jsonl"
    )
    assert attempts_path.is_file()
    attempts = tuple(
        json.loads(line)
        for line in attempts_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    )
    assert [attempt["attempt_number"] for attempt in attempts] == [1, 2]
    assert all(attempt["outcome"] == "error" for attempt in attempts)
    assert all(attempt["error_code"] == "TRANSIENT_HTTP_ERROR" for attempt in attempts)
    assert all(attempt["http_status"] == 503 for attempt in attempts)
    assert attempts[0]["retry_delay_seconds"] == 1.0
    assert attempts[1]["retry_delay_seconds"] == 0.0

    text_artifacts = tuple(
        path
        for path in (tmp_path / "hermes-public-transient").rglob("*")
        if path.is_file() and path.suffix in {".cif", ".json", ".jsonl", ".md"}
    )
    assert text_artifacts
    assert all(
        contact_email.encode("utf-8") not in path.read_bytes()
        for path in text_artifacts
    )
    assert service.repository.get_run(run_id).state.retryable is True
    service.repository.close()


def test_public_factory_keeps_permanent_schema_failure_nonretryable(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.delenv("MATERIALS_CROSSREF_CONTACT_EMAIL", raising=False)
    transport = StaticCrossrefTransport(b'{"message":{},"status":"ok"}')
    settings = GatewayServerSettings(tmp_path, "hermes-public-schema-failure")
    service = create_hermes_inspiration_service(
        settings,
        transport=transport,
        sleeper=lambda _seconds: None,
    )
    dispatcher = GatewayToolDispatcher(service)

    run_id, terminal = _approve_current_interaction(service, dispatcher)

    assert terminal["state"]["status"] == "FAILED"
    assert terminal["state"]["public_error_code"] == "ADAPTER_EXECUTION_ERROR"
    assert terminal["state"]["retryable"] is False
    assert len(transport.calls) == 1
    attempts_path = (
        tmp_path
        / "hermes-public-schema-failure"
        / "stages"
        / "inspiration"
        / run_id
        / "search_attempts.jsonl"
    )
    attempts = tuple(
        json.loads(line)
        for line in attempts_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    )
    assert len(attempts) == 1
    assert attempts[0]["outcome"] == "success"
    assert attempts[0]["response_bytes"] == len(transport.payload)
    service.repository.close()
