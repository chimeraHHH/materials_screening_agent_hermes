from __future__ import annotations

import hashlib
import importlib.util
import os
import sys
import unittest
from pathlib import Path
from typing import Any

from material_agent.gateway.mcp_server import (
    MATERIALS_TOOL_NAMES,
    GatewayDispatchError,
    GatewayServerSettings,
    GatewayToolDispatcher,
    gateway_tool_manifest,
)
from material_agent.gateway.memory import (
    InMemoryArtifactStore,
    InMemoryGatewayRepository,
)
from material_agent.gateway.models import (
    ApprovalInteractionV1,
    ApproveActionV1,
    ArtifactClosureV1,
    ArtifactReferenceV1,
    CompanionTransitionV1,
    GatewayResultRecordV1,
    InspirationBundleSummaryV1,
    InspirationRunRequestV1,
    InteractionRequiredStateV1,
    RunActionV1,
    RunStateV1,
    SucceededStateV1,
    artifact_closure_sha256,
    gateway_result_sha256,
    inspiration_report_uri,
    inspiration_request_sha256,
)
from material_agent.gateway.service import MaterialsGatewayService


class _McpFixtureCompanion:
    def __init__(self, artifacts: InMemoryArtifactStore) -> None:
        self.artifacts = artifacts

    def start(
        self,
        *,
        run_id: str,
        request: InspirationRunRequestV1,
    ) -> CompanionTransitionV1:
        request_sha256 = inspiration_request_sha256(request)
        return CompanionTransitionV1(
            state=InteractionRequiredStateV1(
                interaction=ApprovalInteractionV1(
                    interaction_id=f"interaction-{request_sha256[:24]}",
                    approval_kind="requirement_freeze",
                    prompt="Freeze the bounded MCP fixture request?",
                    input_sha256=request_sha256,
                )
            )
        )

    def act(
        self,
        *,
        run_id: str,
        request: InspirationRunRequestV1,
        state: RunStateV1,
        action: RunActionV1,
    ) -> CompanionTransitionV1:
        del request, state
        if not isinstance(action, ApproveActionV1):
            raise AssertionError("the MCP fixture accepts only exact approval")
        report_payload = b"# MCP fixture inspiration report\n"
        report_uri = inspiration_report_uri(run_id)
        report_sha256 = hashlib.sha256(report_payload).hexdigest()
        self.artifacts.put_bytes(report_uri, report_payload)
        stage_result_payload = b'{"schema_version":"mcp-fixture-stage-result-v1"}'
        stage_result_uri = (
            f"artifact://stages/inspiration/{run_id}/stage_result.json"
        )
        self.artifacts.put_bytes(stage_result_uri, stage_result_payload)
        stage_result_reference = ArtifactReferenceV1(
            uri=stage_result_uri,
            sha256=hashlib.sha256(stage_result_payload).hexdigest(),
            size_bytes=len(stage_result_payload),
            media_type="application/json",
        )
        report_reference = ArtifactReferenceV1(
            uri=report_uri,
            sha256=report_sha256,
            size_bytes=len(report_payload),
            media_type="text/markdown",
        )
        closure_artifacts = (report_reference,)
        closure = ArtifactClosureV1(
            stage_result=stage_result_reference,
            artifacts=closure_artifacts,
            closure_sha256=artifact_closure_sha256(
                stage_result=stage_result_reference,
                artifacts=closure_artifacts,
            ),
        )
        result = GatewayResultRecordV1(
            run_id=run_id,
            report_uri=report_uri,
            authoritative_sha256=report_sha256,
            bundle=InspirationBundleSummaryV1(
                outcome="SCIENTIFIC_NO_MATCH",
                limitations=("The MCP fixture has no candidate corpus.",),
                next_validation_steps=("Provide a curated offline corpus.",),
            ),
            validation_boundaries=(
                "The fixture proves protocol behavior, not a scientific result.",
            ),
            artifact_closure=closure,
        )
        return CompanionTransitionV1(
            state=SucceededStateV1(
                report_uri=report_uri,
                authoritative_sha256=report_sha256,
                result_sha256=gateway_result_sha256(result),
            ),
            result=result,
        )


class _AllowAllTestAuthorizer:
    def authorize_and_consume(self, **_arguments: object) -> None:
        return None


def build_test_service(_settings: GatewayServerSettings) -> MaterialsGatewayService:
    """Subprocess-loadable factory used by the stdio round-trip test."""

    artifacts = InMemoryArtifactStore()
    return MaterialsGatewayService(
        repository=InMemoryGatewayRepository(),
        companion=_McpFixtureCompanion(artifacts),
        artifact_reader=artifacts,
        action_authorizer=_AllowAllTestAuthorizer(),
    )


class GatewayManifestAndDispatchTest(unittest.TestCase):
    def setUp(self) -> None:
        service = build_test_service(
            GatewayServerSettings(workspace=None, project_id="dispatch-test")
        )
        self.dispatcher = GatewayToolDispatcher(service)

    def test_manifest_is_an_exact_four_tool_allowlist(self) -> None:
        manifest = gateway_tool_manifest()

        self.assertEqual(tuple(item["name"] for item in manifest), MATERIALS_TOOL_NAMES)
        self.assertEqual(len(manifest), 4)
        self.assertEqual(
            [item["readOnly"] for item in manifest],
            [False, True, False, True],
        )
        for item in manifest:
            self.assertFalse(item["inputSchema"].get("additionalProperties", True))
            self.assertFalse(item["outputSchema"].get("additionalProperties", True))
        self.assertFalse(
            any(
                forbidden in item["name"]
                for item in manifest
                for forbidden in ("shell", "file", "browser")
            )
        )

    def test_dispatch_uses_strict_dtos_and_exact_confirmation(self) -> None:
        with self.assertRaisesRegex(GatewayDispatchError, "unknown"):
            self.dispatcher.dispatch("shell", {"command": "whoami"})
        with self.assertRaisesRegex(GatewayDispatchError, "invalid arguments"):
            self.dispatcher.dispatch(
                "materials_inspiration_run",
                {
                    "submission_id": "dispatch-submission",
                    "goal": "Exercise strict dispatch",
                    "constraints": {},
                    "shell": "whoami",
                },
            )

        started = self.dispatcher.dispatch(
            "materials_inspiration_run",
            {
                "submission_id": "dispatch-submission",
                "goal": "Exercise strict dispatch",
                "constraints": {},
            },
        )
        interaction_id = started["state"]["interaction"]["interaction_id"]
        with self.assertRaisesRegex(GatewayDispatchError, "invalid arguments"):
            self.dispatcher.dispatch(
                "materials_run_act",
                {
                    "run_id": started["run_id"],
                    "action": {
                        "kind": "approve",
                        "interaction_id": interaction_id,
                        "confirmed_by_user": False,
                    },
                },
            )

        terminal = self.dispatcher.dispatch(
            "materials_run_act",
            {
                "run_id": started["run_id"],
                "action": {
                    "kind": "approve",
                    "interaction_id": interaction_id,
                    "confirmed_by_user": True,
                },
            },
        )
        result = self.dispatcher.dispatch(
            "materials_result_get",
            {"run_id": terminal["run_id"]},
        )

        self.assertEqual(terminal["state"]["status"], "SUCCEEDED")
        self.assertTrue(result["verified"])
        self.assertEqual(
            terminal["state"]["result_sha256"],
            result["result_sha256"],
        )

    def test_dispatch_sanitizes_unexpected_service_failures(self) -> None:
        class _ExplodingService:
            def materials_run_get(self, *, run_id: str) -> None:
                del run_id
                raise RuntimeError("private backend path and token")

        dispatcher = GatewayToolDispatcher(_ExplodingService())  # type: ignore[arg-type]
        with self.assertRaisesRegex(
            GatewayDispatchError,
            "materials_run_get failed",
        ) as raised:
            dispatcher.dispatch(
                "materials_run_get",
                {"run_id": "inspiration-private-failure"},
            )
        self.assertNotIn("private backend", str(raised.exception))


@unittest.skipUnless(
    importlib.util.find_spec("mcp") is not None,
    "the optional MCP SDK is tested in the isolated Hermes environment",
)
class GatewayMcpStdioTest(unittest.TestCase):
    def test_real_stdio_round_trip_exposes_only_the_allowlist(self) -> None:
        import anyio

        anyio.run(self._stdio_scenario)

    async def _stdio_scenario(self) -> None:
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client

        repository_root = Path(__file__).resolve().parents[2]
        test_directory = Path(__file__).resolve().parent
        python_path = [str(repository_root / "src"), str(test_directory)]
        existing_python_path = os.environ.get("PYTHONPATH")
        if existing_python_path:
            python_path.append(existing_python_path)
        environment = dict(os.environ)
        environment["PYTHONPATH"] = os.pathsep.join(python_path)

        parameters = StdioServerParameters(
            command=sys.executable,
            args=[
                "-m",
                "material_agent.integration.mcp_server",
                "--workspace",
                str(repository_root),
                "--project",
                "mcp-stdio-test",
                "--service-factory",
                "test_gateway_mcp:build_test_service",
            ],
            cwd=str(repository_root),
            env=environment,
        )

        with open(os.devnull, "w", encoding="utf-8") as error_log:
            async with stdio_client(parameters, errlog=error_log) as streams:
                async with ClientSession(*streams) as session:
                    await session.initialize()
                    listed = await session.list_tools()
                    self.assertEqual(
                        tuple(tool.name for tool in listed.tools),
                        MATERIALS_TOOL_NAMES,
                    )

                    rejected = await session.call_tool(
                        "materials_inspiration_run",
                        {
                            "submission_id": "mcp-stdio-submission",
                            "goal": "Exercise the stdio boundary",
                            "constraints": {},
                            "budget": {"max_search_requests": 1},
                        },
                    )
                    self.assertTrue(rejected.isError)

                    started = await session.call_tool(
                        "materials_inspiration_run",
                        {
                            "submission_id": "mcp-stdio-submission",
                            "goal": "Exercise the stdio boundary",
                            "constraints": {},
                        },
                    )
                    self.assertFalse(started.isError)
                    self.assertIsNotNone(started.structuredContent)
                    started_body: dict[str, Any] = started.structuredContent or {}
                    interaction_id = started_body["state"]["interaction"][
                        "interaction_id"
                    ]

                    read_back = await session.call_tool(
                        "materials_run_get",
                        {"run_id": started_body["run_id"]},
                    )
                    self.assertFalse(read_back.isError)
                    self.assertEqual(read_back.structuredContent, started_body)

                    unconfirmed = await session.call_tool(
                        "materials_run_act",
                        {
                            "run_id": started_body["run_id"],
                            "action": {
                                "kind": "approve",
                                "interaction_id": interaction_id,
                                "confirmed_by_user": False,
                            },
                        },
                    )
                    self.assertTrue(unconfirmed.isError)

                    terminal = await session.call_tool(
                        "materials_run_act",
                        {
                            "run_id": started_body["run_id"],
                            "action": {
                                "kind": "approve",
                                "interaction_id": interaction_id,
                                "confirmed_by_user": True,
                            },
                        },
                    )
                    self.assertFalse(terminal.isError)
                    self.assertIsNotNone(terminal.structuredContent)
                    terminal_body = terminal.structuredContent or {}
                    self.assertEqual(terminal_body["state"]["status"], "SUCCEEDED")

                    result = await session.call_tool(
                        "materials_result_get",
                        {"run_id": started_body["run_id"]},
                    )
                    self.assertFalse(result.isError)
                    result_body = result.structuredContent or {}
                    self.assertTrue(result_body["verified"])
                    self.assertEqual(
                        terminal_body["state"]["result_sha256"],
                        result_body["result_sha256"],
                    )

                    unknown = await session.call_tool(
                        "shell",
                        {"command": "whoami"},
                    )
                    self.assertTrue(unknown.isError)


if __name__ == "__main__":
    unittest.main()
