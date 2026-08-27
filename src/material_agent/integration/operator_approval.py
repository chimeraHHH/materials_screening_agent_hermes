"""Out-of-band operator CLI for one requirement-freeze decision grant.

This module is never registered with MCP.  It must be invoked by a trusted
operator who controls the fixed workspace/project arguments and supplies an
external confirmation reference.
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from pathlib import Path

from material_agent.gateway.authorization import (
    ActionGrantStoreError,
    OperatorApprovalError,
    RequirementFreezeDecision,
    RequirementFreezeGrantIssuer,
    SqliteOneTimeActionGrantStore,
)
from material_agent.gateway.companion import (
    OfflineInspirationCompanionAdapter,
)
from material_agent.gateway.mcp_server import GatewayServerSettings
from material_agent.gateway.persistence import (
    GatewayPersistenceError,
    SqliteGatewayRepository,
)
from material_agent.integration.hermes_service import (
    GATEWAY_STATE_DATABASE_NAME,
    HermesFixtureConfigurationError,
    create_hermes_fixture_service,
    create_hermes_inspiration_service,
    resolve_hermes_project_root,
    trusted_state_database_path,
)


def _argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="material-agent-approve-requirement-freeze",
        description=(
            "Issue one exact approve, reject, or cancel operator grant for "
            "the current requirement_freeze approval in a fixed Hermes project."
        ),
    )
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--project", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--confirmation-reference", required=True)
    parser.add_argument(
        "--decision",
        choices=("approve", "reject", "cancel"),
        default="approve",
        help="exact advertised action to authorize; defaults to approve",
    )
    parser.add_argument(
        "--reason",
        help="optional bounded reason recorded only in the exact reject action",
    )
    parser.add_argument(
        "--service-mode",
        choices=("public", "fixture"),
        default="public",
        help=(
            "service contract that created the run; fixture is reserved for "
            "source-controlled replay tests"
        ),
    )
    parser.add_argument(
        "--recover-consumed-grant",
        action="store_true",
        help="re-arm a stranded consumed grant after a verified process crash",
    )
    parser.add_argument(
        "--confirm-original-process-stopped",
        action="store_true",
        help="required safety assertion for --recover-consumed-grant",
    )
    return parser


def issue_requirement_freeze_grant(
    *,
    settings: GatewayServerSettings,
    run_id: str,
    confirmation_reference: str,
    decision: RequirementFreezeDecision = "approve",
    reason: str | None = None,
    service_mode: str = "public",
    recover_consumed_grant: bool = False,
    confirm_original_process_stopped: bool = False,
) -> dict[str, str | None]:
    """Issue one grant after re-reading the current persisted interaction."""

    if recover_consumed_grant != confirm_original_process_stopped:
        raise OperatorApprovalError(
            "grant recovery requires both recovery flags and a stopped original process"
        )
    project_root = resolve_hermes_project_root(settings, create=False)
    trusted_state_database_path(
        project_root,
        GATEWAY_STATE_DATABASE_NAME,
        must_exist=True,
    )
    if service_mode == "public":
        service = create_hermes_inspiration_service(settings)
    elif service_mode == "fixture":
        service = create_hermes_fixture_service(settings)
    else:
        raise OperatorApprovalError("unsupported Hermes service mode")
    repository = service.repository
    if not isinstance(repository, SqliteGatewayRepository):
        raise OperatorApprovalError("trusted project repository is unavailable")
    try:
        record = repository.get_run(run_id)
        if record is None:
            raise OperatorApprovalError("run was not found in the fixed project")
        companion = service.companion
        if not isinstance(companion, OfflineInspirationCompanionAdapter):
            raise OperatorApprovalError("trusted inspiration companion is unavailable")
        prepared = companion.preparer.prepare(
            run_id=record.run_id,
            request=record.request,
        )
        execution_manifest_sha256 = companion.execution_manifest_sha256(
            request=record.request,
            prepared=prepared,
        )
        issuer = RequirementFreezeGrantIssuer(
            repository=repository,
            grant_store=service.action_authorizer,
        )
        if not isinstance(
            issuer.grant_store,
            SqliteOneTimeActionGrantStore,
        ):
            raise OperatorApprovalError("trusted approval store is unavailable")
        receipt = issuer.grant_current(
            run_id=run_id,
            confirmation_reference=confirmation_reference,
            expected_execution_manifest_sha256=execution_manifest_sha256,
            decision=decision,
            reason=reason,
            recover_consumed=recover_consumed_grant,
        )
        return receipt.as_json_value()
    finally:
        repository.close()


def main(argv: Sequence[str] | None = None) -> int:
    parser = _argument_parser()
    args = parser.parse_args(argv)
    settings = GatewayServerSettings(
        workspace=args.workspace,
        project_id=args.project,
    )
    try:
        receipt = issue_requirement_freeze_grant(
            settings=settings,
            run_id=args.run_id,
            confirmation_reference=args.confirmation_reference,
            decision=args.decision,
            reason=args.reason,
            service_mode=args.service_mode,
            recover_consumed_grant=args.recover_consumed_grant,
            confirm_original_process_stopped=(
                args.confirm_original_process_stopped
            ),
        )
    except (
        ActionGrantStoreError,
        GatewayPersistenceError,
        HermesFixtureConfigurationError,
        OperatorApprovalError,
        OSError,
    ) as exc:
        parser.exit(2, f"decision failed: {exc}\n")
    print(
        json.dumps(
            receipt,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
