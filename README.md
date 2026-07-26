# Material Screening Agent

This repository implements the material-retrieval stage described in
`material-screening-agent01-plan.md`.

## Development environment

The project targets Python 3.11 and uses a repository-local virtual
environment:

```bash
/opt/anaconda3/envs/py311/bin/python -m venv .venv
.venv/bin/pip install -r requirements.lock
.venv/bin/pip install --no-deps -e .
```

The Materials Project API key is read from `MP_API_KEY`. It must not be placed
in project configuration or artifacts.

## Run the offline demo

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

The real Materials Project release test is opt-in and requires both network
access and `MP_API_KEY`:

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

## Artifact integrity and resume behavior

- The Requirement URI, SHA-256, revision, and normalized content are checked
  before any Materials Project call.
- Input snapshots, query plans, raw batches, manifests, audits, reports, and
  completed operation records are written atomically.
- A completed operation is reused only when every registered artifact still
  matches its recorded SHA-256.
- Missing or modified completed artifacts return `BACKEND_INCONSISTENT`; the
  runner does not silently query again or overwrite historical evidence.
- If report generation is interrupted after raw retrieval, a retry reuses the
  validated raw-response checkpoint rather than repeating the database search.

## Known limits

- P0 uses at most ten 500-record chunks and reports `PARTIAL` when the
  5,000-record scan ceiling is reached.
- True cursor-level checkpointing, parallel structure analysis, large-scale
  StructureMatcher optimization, and additional database adapters remain P1.
- CrystalNN/Larsen warnings are preserved as data-quality warnings. They do not
  automatically reject a candidate.
- Agent 02–04 and the complete LangGraph Orchestrator are outside the current
  Agent 01 repository scope; the next planned milestone is the Orchestrator P0
  `Requirement → Agent 01 → Report` path.
