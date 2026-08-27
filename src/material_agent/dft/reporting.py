"""Deterministic, explicitly non-scientific Agent03 mock reports."""

from __future__ import annotations

from typing import Any

MOCK_LIMITATIONS = (
    "仅完成工程控制链验证；未运行真实 DFT/VASP。",
    "没有产生 band gap、总能、磁矩或其他科学数值。",
    "所有 claim 均为 NOT_EVALUATED_MOCK；不得提升任何候选到 L3_DFT_VALIDATED。",
)


def render_mock_report(payload: dict[str, Any]) -> str:
    """Render a report from already validated control/native data.

    This renderer deliberately receives summaries rather than interpreting any
    scientific output.  The wording is part of the mock evidence ceiling.
    """

    backend = payload.get("backend", {})
    job = payload.get("external_job", {})
    refs = payload.get("references", {})
    lines = [
        "# Agent03 DFT Mock Report",
        "",
        "> MOCK / TEST FIXTURE: this report verifies lifecycle control only.",
        "",
        f"- Project / run: `{payload.get('project_id')}` / `{payload.get('run_id')}`",
        f"- Stage status: `{payload.get('stage_status')}`",
        f"- Reason code: `{payload.get('reason_code', 'NONE')}`",
        f"- Backend: `{backend.get('id', 'mock-dft')}` version `{backend.get('version', 'unknown')}`",
        f"- External job: `{job.get('id', 'not-submitted')}`",
        f"- External lifecycle status: `{job.get('status', 'NOT_SUBMITTED')}`",
        "",
        "## Approved inputs and plan",
        "",
        f"- Input snapshot: `{refs.get('input_snapshot_uri')}` (sha256 `{refs.get('input_snapshot_sha256')}`)",
        f"- Native plan: `{refs.get('native_plan_uri')}` (sha256 `{refs.get('native_plan_sha256')}`)",
        f"- Workflow plan hash: `{refs.get('workflow_plan_hash', 'not available')}`",
        f"- Approved input artifacts: `{refs.get('approved_input_artifacts', [])}`",
        "",
        "## Artifact and provenance references",
        "",
        f"- Result artifact: `{refs.get('result_uri', 'none')}` (sha256 `{refs.get('result_sha256', 'none')}`)",
        f"- Report provenance: backend `{backend.get('id', 'mock-dft')}`, version `{backend.get('version', 'unknown')}`, `is_mock=true`",
        "",
        "## Claims",
        "",
    ]
    claims = payload.get("claims", [])
    if claims:
        lines.extend(
            f"- `{claim.get('claim_type')}`: `{claim.get('status', 'NOT_EVALUATED_MOCK')}`; "
            f"evidence `{claim.get('evidence_level', 'L1_RETRIEVED')}`"
            for claim in claims
        )
    else:
        lines.append("- No claim was evaluated.")

    lines.extend(["", "## Failure / approval outcome", ""])
    if payload.get("public_message"):
        lines.append(f"- Reason: {payload['public_message']}")
    else:
        lines.append("- No failure or approval rejection was recorded.")
    remediation = payload.get("remediation", [])
    lines.append("- Remediation:")
    lines.extend(f"  - {item}" for item in remediation or ["no further action"])

    lines.extend(["", "## Explicit limitations", "", *[f"- {item}" for item in MOCK_LIMITATIONS]])
    lines.extend(["", "## Next step", "", payload.get("next_step", "保持 mock 结果为非证据状态；真实 DFT 需要单独的生产 capability、方法 policy、审批和后端 Gate。"), ""])
    return "\n".join(lines)
