# Material Screening Agent

This repository implements the durable Orchestrator P0.2 control plane,
the deterministic Materials Project retrieval stage, and explicitly
test-only mock control adapters for the downstream stages described in
`plans/subagents/material-screening-orchestrator-plan.md` and
`plans/subagents/material-screening-agent01-plan.md`.

The execution plan always contains the ordered
`retrieval → ml → dft → many_body` routes. Agent 01 is the only production
scientific runner today. Agent02 has both the P0.2 Fake path and an opt-in,
independent CHGNet CPU worker whose release Gate is not yet complete. Agent03
has a v1 mock controller, and Agent04 has an MVP mock controller. Agent02–04
remain absent from the default production registry; only the explicitly
configured real Agent02 worker may produce L2 ML evidence in its opt-in Gate.

## Development environment

The project targets Python 3.11 and uses a repository-local virtual
environment:

```bash
python3.11 -m venv .venv
.venv/bin/pip install -r requirements.lock
.venv/bin/pip install --no-deps -e .
```

The Materials Project API key is read from `MP_API_KEY`. It must not be placed
in project configuration or artifacts.

## Run the Orchestrator P0.2 offline demo

Create a project:

```bash
material-agent project create \
  --workspace workspace \
  --project-id demo
```

Start the fixed acceptance request with the offline Materials Project fixture:

```bash
material-agent run \
  --workspace workspace \
  --project demo \
  --run-id run-demo \
  --request "从 Materials Project 中寻找同时包含 Si 和 O、带隙为 0.5–1.0 eV、energy above hull 不超过 0.05 eV/atom 的非金属材料。" \
  --fixture tests/fixtures/mp-summary.si-o.json
```

The command stops at the durable Requirement confirmation Gate and prints an
`approval_id`. Resume the same checkpoint, including from a new process:

```bash
material-agent approve \
  --workspace workspace \
  --project demo \
  --run run-demo \
  --approval <approval_id> \
  --decision approve

material-agent status \
  --workspace workspace \
  --project demo \
  --run run-demo

material-agent report \
  --workspace workspace \
  --project demo \
  --run run-demo
```

Unknown requests stop at a clarification interaction instead of inventing
scientific thresholds. Use `material-agent respond --json ...` with the
interaction ID and a complete Requirement or a recursive `changes` object.

`status` is strictly local and read-only. For a stage that is waiting on an
external backend, only an explicit `resume` performs reconciliation:

```bash
material-agent resume \
  --workspace workspace \
  --project demo \
  --run <run_id>
```

An explicitly selected external job is cancelled through its runner contract,
not by overwriting local state:

```bash
material-agent cancel \
  --workspace workspace \
  --project demo \
  --run <run_id>
```

Each project owns a SQLite business-state/checkpoint database at
`state/orchestrator.sqlite3`. Orchestrator-owned tables use an explicit
versioned migration; LangGraph-owned checkpoint tables are not modified by
that migration. LangGraph state contains only small JSON control values and
URI/hash references; scientific data and reports remain in the project
Artifact Store.

## Start one stage from explicit inputs

`run-stage` never discovers the “latest” artifact. It creates a new Run from
an explicit source Run, Requirement revision, and immutable artifact hashes:

```json
{
  "schema_version": "orchestrator-p0.2-v3",
  "source_run_id": "run-demo",
  "requirement_revision": 1,
  "requirement_artifact_uri": "artifact://requirements/run-demo/requirement.v1.json",
  "requirement_artifact_sha256": "<sha256>",
  "artifacts": {
    "candidate_manifest": {
      "uri": "artifact://stages/agent01/run-demo/candidate_manifest.jsonl",
      "sha256": "<sha256>"
    }
  }
}
```

```bash
material-agent run-stage ml \
  --workspace workspace \
  --project demo \
  --input ml-stage-input.json \
  --run-id run-ml
```

### Agent02 Fake Adapter and opt-in real CPU worker

Agent02's P0.2 Adapter is implemented and covered offline with an explicitly
registered Fake Worker. It validates immutable Agent01 inputs, freezes native
and control plans, applies the 1–5/6–20/>20 batch policy, writes candidate- and
stage-level completion records, reuses completed operations, and fails closed
on Artifact/hash conflicts. Fake results are always `is_mock=true` and remain
at L1; they cannot satisfy an L2 target.

The same Adapter contract now also supports a no-shell, one-candidate JSON
subprocess worker. The real worker pins `chgnet==0.4.2`, loads the packaged
`CHGNet.load(model_name="0.3.0")` checkpoint, verifies its SHA-256 and the
environment lock, runs CPU static prediction and FIRE/FrechetCellFilter
relaxation, and emits only sandboxed CIF/NPZ artifacts. It can produce
`L2_ML_SCREENED` only after applicability, convergence, structure, numerical,
lineage, and Artifact validation all pass.

Create the separate Python 3.11 environment without adding Torch, CHGNet, or
ASE to the main `.venv`:

```bash
.venv/bin/python -m venv --copies .venv-agent02
.venv-agent02/bin/python -m pip install -r requirements-agent02.lock
.venv-agent02/bin/python -m pip check
```

Run the opt-in CPU and target-Mac MPS probes:

```bash
PYTHONDONTWRITEBYTECODE=1 MPLCONFIGDIR=/tmp/material-agent-mpl \
.venv/bin/python -m pytest -q -p no:cacheprovider \
  tests/real_ml --run-real-ml
```

The main process supplies the repository `src/` directory through a sanitized
`PYTHONPATH`; the heavy environment does not install the main project or its
dependency metadata. `MATERIAL_AGENT_ML_WORKER_PYTHON` may point the Gate at a
different dedicated Python executable.

The default production registry intentionally still leaves Agent02
unavailable. The current Mac reports PyTorch MPS support as built but not
runtime-available, so CPU/MPS parity, MPS fallback and production registration
remain Step 4 work. The CPU release Gate covers serial Top-5 execution,
candidate-level interruption recovery, wall time and peak-RSS recording. The
real worker never falls back to the Fake Worker.

### Agent03 v1 mock controller

Agent03 v1 verifies only the control chain: immutable input and plan hashes,
approval, mock submit/status/cancel/fetch, operation idempotency, external-job
waiting, restart/reconcile, and non-scientific reports. It does not run VASP or
VASPilot and does not create band gaps, total energies, magnetic moments, or
`L3_DFT_VALIDATED` evidence. The frozen reference fixture is
`tests/fixtures/contracts/agent03-v1/`.

The CLI accepts the stage route and the existing lifecycle commands:

```bash
material-agent run-stage dft --workspace workspace --project demo \
  --input dft-stage-input.json --run-id run-dft
material-agent approve --workspace workspace --project demo --run run-dft \
  --approval <approval_id> --decision approve
material-agent status --workspace workspace --project demo --run run-dft
material-agent resume --workspace workspace --project demo --run run-dft
material-agent cancel --workspace workspace --project demo --run run-dft
material-agent report --workspace workspace --project demo --run run-dft
```

The default production registry intentionally leaves Agent03 unavailable, so a
direct CLI `run-stage dft` currently reports `CAPABILITY_UNAVAILABLE`. The
successful mock lifecycle is reproduced offline through the explicit test
registry in `tests/integration/test_dft_runner_orchestrator.py` and the
cross-process E2E test; the command sequence above documents the shared CLI
surface and recovery semantics, not a real DFT execution.

Run the Agent03 contract, failure-injection, integration, and E2E coverage with:

```bash
PYTHONDONTWRITEBYTECODE=1 MPLCONFIGDIR=/tmp/material-agent-mpl \
.venv/bin/python -m pytest -q -p no:cacheprovider \
  tests/contract/test_frozen_agent03_fixture.py \
  tests/contract/test_dft_mock_backend.py \
  tests/integration/test_dft_failure_injection.py \
  tests/integration/test_dft_runner_orchestrator.py \
  tests/e2e/test_dft_cross_process.py
```

Real DFT remains unsupported. Before P2, the project needs a licensed and
validated VASP/POTCAR setup, an authenticated Slurm/backend bridge, a frozen
group method policy, expert approval, and the required security and scientific
validation Gates. Mock artifacts remain lifecycle evidence only and must not be
promoted to scientific evidence.

### Agent04 v1 mock controller demo

Agent04 has an offline MVP control-chain demo for the frozen 1D and 2D Hubbard
fixtures. It uses the explicit test registry and `MockManyBodyBackend`; the
default production registry intentionally does not register a many-body
capability, so this is not a production `run-stage many_body` CLI workflow.
The demo covers frozen planning, approval, queued/running status, process
restart, read-only `status`, `resume`, final Artifact/report, approval rejection,
missing input, and the mock evidence ceiling:

```bash
PYTHONDONTWRITEBYTECODE=1 MPLCONFIGDIR=/tmp/material-agent-mpl \
.venv/bin/python -m pytest -q -p no:cacheprovider \
  tests/contract/test_agent04_domain_schema.py \
  tests/contract/test_many_body_mock_backend.py \
  tests/integration/test_many_body_runner_orchestrator.py \
  tests/e2e/test_many_body_cross_process.py
```

The fixtures are abstract, explicitly marked `fixture=true` and `is_mock=true`,
produce no scientific observables, and cannot be promoted to L4. The planned
exact-diagonalization backend remains a separate P1 task.

Missing prerequisite artifacts produce an auditable
`BLOCKED_MISSING_INPUT`/`PAUSED` Run. A complete input for an unregistered
production capability produces `CAPABILITY_UNAVAILABLE`; neither case
fabricates a result.

Before execution, every registered runner freezes a native plan and an
Orchestrator-owned `PreparedStagePlan(orchestrator-stage-plan-v2)`. The
approval decision is the logical OR of the capability's mandatory approval
floor and the runner plan's dynamic requirement. Any approval directly binds
the stage plan, native plan, operation input, resource estimate, policy, and
input snapshot hashes.

The P0.2 fixture contract verifies automatic batches of 1–5 candidates,
approval for 6–20, and pre-plan blocking above the 20-candidate hard limit.
Agent02's production runner remains unavailable until the independent real
worker and its CPU/Mac, recovery, security, and release Gates are complete.
A `WaitingExternal` result is stored as Stage `RUNNING` plus Run `PAUSED`, so a
new process can reconcile the same external job and frozen plan without
preparing or submitting it again.

## Run Agent 01 standalone

```bash
material-agent retrieval \
  --requirement tests/fixtures/requirement.si-o.json \
  --output workspace/demo \
  --fixture tests/fixtures/mp-summary.si-o.json
```

The output is written below the selected Artifact Store root. The authoritative
candidate manifest is run-scoped:

```text
stages/agent01/<run_id>/candidate_manifest.jsonl
```

Shared structures are content-addressed:

```text
candidates/structures/<structure_id>.source.json
candidates/structures/<structure_id>.cif
```

## Tests

Run all offline tests without creating repository-local caches:

```bash
PYTHONDONTWRITEBYTECODE=1 \
MPLCONFIGDIR=/tmp/material-agent-mpl \
.venv/bin/python -m pytest -q -p no:cacheprovider
```

The Orchestrator P0.1 baseline is commit `d681de8`, the P0.2 stage-planning
bridge baseline is commit `701857c`, and P0 closeout is commit `505ac55`.
The P0 closeout run reported `325 passed, 2 skipped`; the skipped tests were
the two explicit `live_mp` Gates. P1 adds separate `real_ml`, `slow_real_ml`,
and `mps_ml` markers so the default offline Gate remains independent of the
heavy worker environment. The current P1 branch reports
`329 passed, 5 skipped` for the default Gate and `2 passed, 1 skipped` for the
explicit real-ML Gate; the latter skip is the unavailable MPS runtime.

The standalone Agent01 and Orchestrator-restart Materials Project release
Gates are opt-in and require both network access and `MP_API_KEY`:

```bash
agent_mp_key="$(security find-generic-password \
  -a "$USER" -s "material-screening-agent-mp-api" -w)"
MP_API_KEY="$agent_mp_key" .venv/bin/python -m pytest \
  tests/live -m live_mp --run-live-mp
unset agent_mp_key
```

This macOS example keeps the key out of shell history and clears the temporary
shell variable after the test. On another operating system, inject the key from
its secret store or an already configured process environment.

The P0.2 release run completed both real Materials Project Gates with
`2 passed` in `78.10s`; no API key or live run artifact was retained in the
repository.

## Frozen Agent 01 contract

The public contract is versioned as `agent01-contract-v1`. Orchestrators should
use the explicit lifecycle:

```text
validate_input(context) -> StageInputValidation
prepare(context) -> RetrievalStagePlan
start(plan, idempotency_key) -> StageOutcome
reconcile(operation_ref) -> StageOutcome
```

`run(requirement, stage_input)` remains as the standalone CLI compatibility
wrapper. Frozen JSON Schemas and a deterministic one-candidate reference output
are committed under `tests/fixtures/contracts/agent01-v1/`. The fixture is
generated from offline test data and contains no live Materials Project data.

The Orchestrator does not reuse this native envelope as its own public
contract. `Agent01RunnerAdapter` validates `agent01-contract-v1`, stores its
URI/hash, wraps its frozen native plan in `PreparedStagePlan`, and maps only
control state into `ControlStageOutcome(orchestrator-p0.2-v3)`.

## Artifact integrity and resume behavior

- The Requirement URI, SHA-256, revision, and normalized content are checked
  before any Materials Project call.
- Input snapshots, query plans, raw batches, manifests, audits, reports, and
  completed operation records are written atomically.
- A completed operation is reused only when every registered artifact still
  matches its recorded SHA-256.
- Missing or modified completed artifacts return `BACKEND_INCONSISTENT`; the
  runner does not silently query again or overwrite historical evidence.
- External job reference changes, status-sequence regressions, and result-hash
  changes fail closed as `BACKEND_INCONSISTENT`.
- A non-blocking project-level lock permits only one CLI process to advance
  any Run in a Project at a time.
- Unfinished `orchestrator-p0-v1` and `orchestrator-p0.1-v2` checkpoints are
  readable but explicitly rejected for P0.2 resume; completed reports and
  artifacts remain readable.
- If report generation is interrupted after raw retrieval, a retry reuses the
  validated raw-response checkpoint rather than repeating the database search.

## Known limits

- P0.2 has a replaceable Parser protocol and a deterministic offline default;
  an LLM Provider is not connected yet.
- Agent 01 is the only registered production scientific stage in the graph.
  Agent 02–04 remain unregistered production capabilities. Agent02's opt-in
  independent CPU Gate can create validated L2 ML evidence, while every
  test-only fixture runner stays `is_mock=true` and cannot raise evidence.
- The four ordered routes and fail-closed boundaries are covered end to end,
  but there is no default four-stage scientific success path. In particular,
  Agent04 requires an explicit expert-supplied `EffectiveModelPackage`; mock DFT
  output is not converted into a many-body model.
- Execution is synchronous and single-project. Long-running background workers,
  multi-user access, and Postgres checkpointing are server-stage work.
- P0 uses at most ten 500-record chunks and reports `PARTIAL` when the
  5,000-record scan ceiling is reached.
- True cursor-level checkpointing, parallel structure analysis, large-scale
  StructureMatcher optimization, and additional database adapters remain P1.
- CrystalNN/Larsen warnings are preserved as data-quality warnings. They do not
  automatically reject a candidate.
- Agent02's real CPU worker, serial Top-5, recovery and resource recording
  Gates exist, but its MPS parity, fallback and production registration remain
  incomplete. All real Agent03/04 scientific backends remain later
  milestones. Fake/mock adapters remain unregistered and validate control
  behavior only.
