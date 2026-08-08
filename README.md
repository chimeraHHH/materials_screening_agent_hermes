# Material Screening Agent

This repository implements the durable Orchestrator P0.2 control plane,
the deterministic single-source public-database retrieval stage, and explicitly
test-only mock control adapters for the downstream stages described in
`plans/subagents/material-screening-orchestrator-plan.md` and
`plans/subagents/material-screening-agent01-plan.md`. It now also contains a
single-user local Hermes inspiration companion: metadata-first evidence,
cross-domain mechanism bridges, one deterministic structure operator, run-local
deduplication/diversity, and a four-tool persistent MCP Gateway.

The execution plan always contains the ordered
`retrieval → ml → dft → many_body` routes. Agent 01 is the only default
production scientific runner. Agent02 has both the P0.2 Fake path and an
opt-in independent CHGNet worker. It is registered as a production capability
only when a validated dedicated Worker executable is explicitly configured;
otherwise it remains fail-closed and unavailable. Agent03 has a v1 mock
controller, and Agent04 has an MVP mock controller. Only the real, configured
Agent02 worker may produce L2 ML evidence; every test fixture remains mock.

## Hermes inspiration beta

The production profile is deliberately narrow. It accepts reviewed structured
flat/narrow electronic-band constraints, preserves natural-language goals only
as approval-bound rationale, and executes bounded Crossref metadata search in
the same persistent Gateway run. Unsupported targets, conflicting Ti/Se route
constraints, and insufficient budgets fail before approval or network access.
The original exact-request fixture factory remains available only for explicit
offline replay. Neither path is a general scientific planner. Every output
remains a hypothesis:
`STRUCTURE_VALID` means structural QC only, the target property is `UNKNOWN`,
and this repository performs no novelty, patent, or prior-art assessment.

Three isolated Python 3.11 environments keep the scientific engine, MCP SDK,
and Hermes runtime from contaminating one another:

```bash
.venv/bin/python integrations/hermes/scripts/bootstrap_gateway_runtime.py
.venv/bin/python integrations/hermes/scripts/bootstrap_runtime.py
.venv/bin/python integrations/hermes/scripts/verify_bundle.py
```

The pinned Hermes distribution is `v2026.8.3` / package `0.20.0` / commit
`3c27eb6234bf91b8ceee9e9071591b31e9b148cb`. Its profile exposes exactly:

```text
materials_inspiration_run
materials_run_get
materials_run_act
materials_result_get
```

The first fixed-pilot natural-language Gate ran through the authorized Hermes
provider path.
Hermes created and resumed one persistent run, stopped for the real user
decision, consumed an out-of-band one-time operator grant, and returned one
hash-verified TiSe2 proposal. The Gateway state was honestly `PARTIAL` while the
contained bundle was `SUCCEEDED`; the target property remains `UNKNOWN`. The
fixed Hermes run is offline/fixture-backed, while a separate Crossref live Gate
proves the public metadata boundary. Exact sessions, hashes, warnings, cost
ledgers, and debug history are in the
[pilot run record](docs/runs/2026-08-08-hermes-inspiration-pilot.md).
For that run, the Gateway materials service used zero internal LLM calls; the
separate Hermes host audit recorded 11 provider calls, 36,240 non-cached input,
80,384 cache-read, 2,584 output, and 213 reasoning tokens. A recorded provider
cost of `0.0` means billing was unavailable, not that execution was free.

An MCP caller cannot approve its own action. `materials_run_act` remains blocked
until a trusted local operator records a one-time grant bound to the request,
complete interaction, frozen execution manifest, and exact action. See
[`integrations/hermes/README.md`](integrations/hermes/README.md) for installation,
approval, crash recovery, and profile commands.

The reproducible fixed-pilot smoke uses the actual MCP stdio subprocess. A full
replay must use a fresh, empty ignored workspace and a fresh submission ID; an
interrupted phase must reuse the exact same pair. The historical
`hermes-release-20260808-v2` workspace is already terminal and is evidence, not
an input for another `submit` call:

```bash
.venv-gateway/bin/python integrations/hermes/scripts/run_gateway_pilot.py submit \
  --workspace workspace/<fresh-pilot-workspace> \
  --project materials-inspiration \
  --submission-id <fresh-pilot-submission-id>

.venv-gateway/bin/python -m material_agent.integration.operator_approval \
  --workspace workspace/<fresh-pilot-workspace> \
  --project materials-inspiration \
  --run-id <run-id-from-submit> \
  --confirmation-reference <trusted-user-decision-reference> \
  --service-mode fixture

.venv-gateway/bin/python integrations/hermes/scripts/run_gateway_pilot.py finish \
  --workspace workspace/<fresh-pilot-workspace> \
  --project materials-inspiration \
  --submission-id <fresh-pilot-submission-id>
```

The first audited run and exact hashes are recorded in
[`docs/runs/2026-08-08-hermes-inspiration-pilot.md`](docs/runs/2026-08-08-hermes-inspiration-pilot.md).

## Development environment

The project targets Python 3.11 and uses a repository-local virtual
environment:

```bash
python3.11 -m venv .venv
.venv/bin/pip install -r requirements.lock
.venv/bin/pip install --no-deps -e .
```

For a new Windows 10 computer, see the [Windows 10 deployment guide](docs/windows-setup.md).

The Materials Project API key is resolved lazily from an explicit Adapter value
(test/integration injection only), then `MP_API_KEY`, then the macOS Keychain
entry for account `$USER` and service `material-screening-agent-mp-api`. It
must not be placed in project configuration or artifacts. On non-macOS systems,
inject `MP_API_KEY` from the platform secret store.

## Parse a Requirement draft without starting retrieval

Stage 0 can write a strict, unconfirmed Requirement JSON file independently:

```bash
material-agent requirement parse \
  --request "从 Materials Project 中寻找同时包含 Si 和 O、带隙为 0.5–1.0 eV、energy above hull 不超过 0.05 eV/atom 的非金属材料。" \
  --output /absolute/path/to/requirement.draft.json
```

The command prints the parser/version, SHA-256, clarification questions and
either `REVIEW_REQUIRED` or `CLARIFICATION_REQUIRED`. The output file is a pure
Requirement object with `confirmed_by_user=false`; the command is idempotent
for identical bytes and refuses to overwrite different content. To freeze a
confirmed revision, pass the draft to the normal Orchestrator flow with
`material-agent run --requirement-file ...` and approve the existing
`REQUIREMENT_CONFIRMATION` Gate.

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
  --source materials_project \
  --request "从 Materials Project 中寻找同时包含 Si 和 O、带隙为 0.5–1.0 eV、energy above hull 不超过 0.05 eV/atom 的非金属材料。" \
  --fixture tests/fixtures/mp-summary.si-o.json
```

### Select an Agent01 database

Each Agent01 run queries exactly one database. Pass `--source` to either
`material-agent run` or the standalone `material-agent retrieval` command:

```bash
material-agent retrieval \
  --source c2db \
  --requirement /absolute/path/to/confirmed-requirement.json \
  --output /absolute/path/to/artifact-root
```

Available choices are `materials_project`, `nomad`, `mc3d`, `c2db`,
`topological_quantum_chemistry`, and `nims_supercon`. `--source` is required:
the user must explicitly confirm one database before retrieval can proceed.
The run records the selected source, endpoint and version; it never combines
databases or fills a missing property from another source.

After the source is selected, Agent01 compiles the confirmed Requirement into
an immutable source-native Requirement artifact. Conditions that the selected
database cannot express are retained as `UNMAPPED` constraints with a reason;
they are never silently dropped or treated as satisfied. The query plan uses
mapped conditions, while retained conditions keep affected candidates at
`UNCERTAIN` until suitable downstream evidence exists.

For every selected source, `raw_response_manifest.jsonl` points to immutable
compressed raw-response batches. These batches retain the source payload used
by the adapter in addition to the normalized Candidate manifest: MP requests
all fields advertised by its current metadata; MC3D retains complete
OPTIMADE entries; NOMAD retains archive entries; C2DB retains the search HTML,
table row and download JSON; TQC retains the search item, detail JSON and CIF
content. The normalized manifest intentionally remains smaller and only
contains fields safe for deterministic Agent01 screening.

“All returned data” therefore means all data actually returned by the selected
public endpoint and preserved in the run Artifact Store. It does not mean that
a database can provide properties it does not expose, that a bounded scan
covers its entire database, or that missing properties are inferred from
another source.

After a confirmed Requirement has been received, an explicitly configured
structured LLM can recommend one of the five primary databases (Materials
Project, C2DB, NOMAD, TQC, or MC3D):

```bash
export MATERIAL_AGENT_LLM_PROVIDER=deepseek
material-agent recommend-source \
  --requirement /absolute/path/to/confirmed-requirement.json
```

The recommendation is advisory and schema-validated. It returns exactly one
database plus rationale, confidence, Requirement hash, and provider audit
metadata. It never merges databases, changes the Requirement, or silently
falls back when the LLM is unavailable; pass the returned `source_database`
to the normal retrieval command. The existing NIMS SuperCon adapter remains
available for backward-compatible explicit retrieval, but is not part of the
LLM recommendation catalog because it has no canonical structures.

All sources use the same audited retrieval flow, but their scientific coverage
differs. NOMAD, MC3D, C2DB and TQC can return canonical structures for
downstream screening when their records pass validation. NIMS SuperCon is a
versioned superconductivity datasheet without atomic coordinates, so it is
queryable and reported but its records are blocked from downstream structure
screening. Missing source properties remain `UNCERTAIN`; they are not inferred
from Materials Project.

Each Agent01 run writes `source_property_coverage.json`, which names the
properties that the selected source can support at Agent01 and those deferred
for lack of source evidence.

| Source | Agent01 may judge | Deferred when unavailable |
| --- | --- | --- |
| C2DB | GPAW/PBE band gap, energy above hull, derived metallicity, layer group, magnetic label, structure dimensionality | band dispersion/flat-band width, Fermi-window band identity, orbital projections, crossings, oxidation states |
| NOMAD | parsed archive band gap with entry-specific method provenance; structure dimensionality | any property absent from the selected archive, including dispersion/projections |
| MC3D | structure dimensionality | electronic, orbital and thermodynamic properties |
| TQC | topology label/SOC/index、费米交叉计数/标签（仅诊断）、structure dimensionality | flat-band dispersion, orbital projections, thermodynamics, oxidation states; 交叉不能归属到具体能带，不能单独判定严格无交叉条件 |
| NIMS SuperCon | none (no canonical structure) | all structure and electronic properties |

MP deep-endpoint values are source-specific and never fill a record from any
other database.

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
scientific thresholds. Use `material-agent respond --text "..."` for a natural
language supplement, or `material-agent respond --json ...` with the
interaction ID and a complete Requirement or recursive `changes` object.
Offline text clarification only merges constraints recognized by the
deterministic acceptance parser. After each text response, remaining questions
produce a new `CLARIFYING` response with an incremented `round` and a new
`interaction_id`; repeat `respond --text` with that ID until the status becomes
`REQUIREMENT_REVIEW`. Every completed draft is still displayed at the
Requirement confirmation Gate before it can be frozen.

### Opt-in DeepSeek Stage 0 parser

The default remains `OfflineRequirementParser`; no network or secret-store
access occurs unless `MATERIAL_AGENT_LLM_PROVIDER=deepseek` is explicitly set.
The opt-in parser uses DeepSeek's OpenAI-compatible non-streaming Chat
Completions endpoint with `deepseek-v4-pro`, thinking enabled at `high` effort,
and JSON Output. Provider output is still local-untrusted input: it is
validated as a `Requirement`, displayed at the existing Requirement
confirmation Gate, and cannot control the Requirement ID, revision,
confirmation state, policy version, stage routing, scientific thresholds, or
evidence promotion.

The API key is resolved lazily from `MATERIAL_AGENT_LLM_API_KEY` or, when that
variable is absent, from macOS Keychain. Configure only non-secret values in
the launching process:

```bash
export MATERIAL_AGENT_LLM_PROVIDER=deepseek
export MATERIAL_AGENT_LLM_BASE_URL=https://api.deepseek.com
export MATERIAL_AGENT_LLM_MODEL=deepseek-v4-pro
export MATERIAL_AGENT_LLM_KEYCHAIN_SERVICE=material-screening-agent-llm-api
export MATERIAL_AGENT_LLM_KEYCHAIN_ACCOUNT="$USER"

material-agent run \
  --workspace workspace \
  --project demo \
  --run-id run-deepseek \
  --source materials_project \
  --request "寻找带隙和稳定性约束明确的 Si/O 非金属材料。" \
  --fixture tests/fixtures/mp-summary.si-o.json
```

The key itself must not be passed as a CLI argument or written to project
configuration. Authentication errors, response bodies, and model reasoning
content are not persisted. The audit event records only provider/model/prompt
metadata, token counts, and request/response hashes.

### Read-only research-advice companion flow

After a run has produced its final report, `research-advice` builds a bounded
evidence snapshot from hash-verified Orchestrator and Agent01 report artifacts.
It identifies recorded candidate evidence gaps and unavailable-stage
prerequisites, then emits policy-defined next-step *proposals*:

```bash
material-agent research-advice \
  --workspace workspace \
  --project demo \
  --run run-demo
```

Without a configured LLM, the command provides a deterministic explanation.
With the explicit DeepSeek configuration above, the LLM may explain the frozen
evidence snapshot and its already-defined proposals. In either mode it is
strictly read-only: it does not modify a Requirement, checkpoint, stage route,
budget, approval, model selection, artifact, or evidence level; it cannot
start Agent02, DFT, or many-body work. LLM output cannot add actions or make a
scientific conclusion. Use the normal `run-stage`/approval workflow for any
actual downstream execution.

Offline tests use injected providers and never read Keychain. The real
one-request release probe is separately opt-in and may incur API cost:

```bash
PYTHONDONTWRITEBYTECODE=1 MPLCONFIGDIR=/tmp/material-agent-mpl \
.venv/bin/python -m pytest -q -p no:cacheprovider \
  tests/live/test_live_llm_requirement_parser.py --run-live-llm
```

An explicitly configured Provider fails closed on missing credentials,
authentication failure, timeout exhaustion, unexpected model identity,
oversized/empty/invalid JSON, or Requirement Schema/policy failure. It never
silently falls back to the offline parser or another model.

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

The target arm64 Mac Release Gate passed with a non-sandboxed Terminal process:
MPS was both built and runtime-available, CPU/MPS Si relaxation outputs met the
release tolerances, and the full real Gate passed. An MPS runtime failure that
matches the explicit MPS/Metal failure policy clears the MPS cache and retries
the same candidate exactly once on CPU. It records the requested and actual
device plus a warning; any CPU failure or unrelated error still fails closed.
It never falls back to the Fake Worker.

To enable the real production capability for a direct ML stage, set the
absolute path to the dedicated executable in the process that launches the
CLI:

```bash
export MATERIAL_AGENT_ML_WORKER_PYTHON="$PWD/.venv-agent02/bin/python"
material-agent run-stage ml --workspace workspace --project PROJECT --input stage-input.json
```

The production factory verifies the executable, repository lock hash and model
card before registration. The frozen `policy`, `registry`, `health`, Candidate
Manifest and Requirement Artifact references are still mandatory per run; an
absent or invalid configuration leaves `ml` unavailable with remediation.
The release Gate covers serial Top-5 execution, candidate-level interruption
recovery, wall time and peak-RSS recording.

### Agent02 DeepH companion flow

Agent02 also provides an independent, opt-in DeepH-pack control bridge. It
does not modify the frozen CHGNet v1 request/plan/result/worker contracts and
is not registered as a default Orchestrator capability. The bridge is for an
explicit handoff after CHGNet, not an automatic claim that a relaxed CIF is
enough for DeepH.

DeepH inference requires all of the following immutable inputs:

- a trained DeepH model bundle;
- an overlap bundle produced by OpenMX or ABACUS;
- the exact DFT interface, software version and localized-basis identity shared
  by the model and overlap;
- the structure URI/hash for which the overlap was calculated;
- a dedicated worker Python and absolute `deeph-inference` executable.

The explicit helper
`material_agent.ml_screening.deeph_handoff.deeph_request_from_chgnet_result()`
accepts only a real, QC-passed `CHGNet 0.3.0` L2 result and verifies the
relaxed structure Artifact. It still requires the caller to supply the
independent model/overlap bundles; it never creates or guesses them. Keeping
this helper in its explicit submodule preserves the lightweight top-level
Agent02 import.

Run the explicit companion flow after creating a strict
`agent02-deeph-request-v1` JSON file whose artifacts all live below the
selected project root:

```bash
.venv/bin/python scripts/run_agent02_deeph_flow.py \
  --request /absolute/path/to/deeph-request.json \
  --artifact-root /absolute/path/to/project \
  --worker-python /absolute/path/to/deeph-env/bin/python \
  --deeph-executable /absolute/path/to/deeph-env/bin/deeph-inference
```

The worker uses no shell, freezes DeepH tasks `[1, 2, 3, 4]`, writes only in a
candidate operation sandbox and revalidates every input/output path, size and
SHA-256. Task 5 band/sparse calculation, Julia execution, model/data download,
training and benchmark are outside this flow.

Successful execution means only that the configured DeepH program completed
and its files passed control-plane integrity checks. Results remain
`evidence_level=NONE`, `benchmark_status=NOT_RUN` and
`scientific_conclusion=false`; they are not DFT validation, a validated
Hamiltonian, a band result or a topology claim. Test executables are explicitly
`is_mock=true`. Missing model, overlap or scientific linkage fails closed.

The upstream [DeepH-pack repository](https://github.com/mzjb/DeepH-pack) and
[inference documentation](https://deeph-pack.readthedocs.io/en/latest/inference/inference.html)
describe the model/overlap prerequisites. The upstream README/LICENSE and
`setup.py` currently expose inconsistent license labels; freeze and review a
specific upstream revision before any production deployment.

### Agent02 ALIGNN property-prediction companion flow

Agent02 additionally has an explicit, structure-based ALIGNN companion flow.
It is independent from the frozen CHGNet v1 contracts and is not a default
Orchestrator capability.  It accepts only a canonical CIF plus a caller-supplied
and hash-verified official ALIGNN model ZIP; the worker never silently downloads
weights.  v1 freezes the initial property choices to JARVIS-DFT OptB88-vdW or
mBJ band-gap predictions and records the label method, model version, source
revision and environment-lock hash.

The worker runs in a dedicated Python environment, without a shell, and writes
one JSON prediction artifact into an operation sandbox.  A successful process
is only an ML prediction: before a frozen benchmark and applicability review,
the result remains `evidence_level=NONE`, `benchmark_status=NOT_RUN`, and
`scientific_conclusion=false`.  In particular it must not be described as an
experimental band gap, a DFT result, or a thermodynamic-stability conclusion.
The required isolated environment is pinned in `requirements-alignn.lock`; it
must not be installed in the repository's default `.venv`.

### Agent02 multi-model property-prediction companion flow

The optional `property-predict` flow adds model-family selection after CHGNet
without changing its frozen v1 stage contract. It supports registry entries
for [Crystalformer](https://github.com/omron-sinicx/crystalformer),
[CrystalFramer](https://github.com/omron-sinicx/crystalframer),
[ct-UAE](https://github.com/fduabinitio/ct-UAE),
[CrabNet](https://github.com/anthony-wang/CrabNet), and
[MODNet](https://github.com/ppdebreuck/modnet). The request fixes a property,
unit, candidate composition, optional canonical CIF and user model preference.
The selector considers only matching `READY` entries; it rejects wrong units,
missing structure input, mock models, disabled real inference, or unregistered
assets deterministically.

Every `READY` entry must contain a fixed upstream revision, explicit trained
property label/dataset, dedicated environment-lock artifact and checkpoint
artifact. Both assets and the input CIF are re-hashed inside the artifact root
before a plan can be created. `CrabNet` is composition-only and requires a
property-specific trained head. Crystalformer, CrystalFramer and ct-UAE use
structure input. MODNet's native `.pkl` files are prohibited by the artifact
policy; a reviewed non-pickle safe export is required before MODNet can be
`READY`.

Run a frozen request and registry through a dedicated worker environment:

```bash
material-agent property-predict \
  --artifact-root /absolute/path/to/artifacts \
  --request /absolute/path/to/property-request.json \
  --registry /absolute/path/to/property-registry.json \
  --worker-python /absolute/path/to/isolated-python \
  --ct-uae-source-root /absolute/path/to/verified/ct-uae
```

The CLI never downloads models. It persists immutable plan/result/completion
records and runs the adapter with no shell. A result is always an unbenchmarked
`L1_RETRIEVED` model estimate with `scientific_conclusion=false`; it is not a
DFT, formation-energy, stability, magnetic-ground-state or experimental claim.
The public ct-UAE band-gap checkpoint has passed the current isolated CPU smoke
test. The other families remain unavailable until their complete verified
weights, worker locks and family-specific safe loaders have passed the same
deployment Gate.

### Automatic post-relaxation ct-UAE + ALIGNN chain

Agent02 also provides an explicit chain for a relaxed structure. It verifies
that the ct-UAE request and ALIGNN request reference the same CIF URI/hash,
then invokes both existing model ledgers automatically. A failure in one model
is recorded independently and does not fabricate or suppress the other result.

```bash
material-agent property-predict-chain \
  --artifact-root /absolute/path/to/artifacts \
  --request /absolute/path/to/post-relaxation-property-chain.json \
  --ct-uae-worker-python /absolute/path/to/ct-uae-python \
  --alignn-worker-python /absolute/path/to/alignn-python \
  --ct-uae-source-root /absolute/path/to/verified/ct-uae
```

The request's `relaxed_structure` is the output of the preceding relaxation
step (for example a MatterSim CIF); the command itself does not perform
relaxation. Both model assets, environment locks, source revisions and hashes
remain mandatory. The existing ALIGNN deployment Gate is still required: an
official, hash-verified model ZIP and isolated environment must be supplied;
the chain never downloads or invents ALIGNN weights. Both outputs remain
unbenchmarked ML estimates and cannot establish flat-band width, orbital
character, crossings, stability, DFT or experimental evidence.

Run the Agent02 benchmark-v1 metadata-only dry-run against the existing Si
fixture. This validates the manifest, structure hash/size and parsed metadata;
it does not load CHGNet, evaluate reference values, or produce scientific
evidence:

```bash
.venv/bin/python scripts/run_agent02_benchmark_dry_run.py \
  --manifest tests/fixtures/benchmarks/agent02-benchmark-v1-si.json \
  --case-id si-diamond-release-v1 \
  --structure tests/fixtures/real_ml/si-diamond.cif
```

The expected result is `DRY_RUN_ONLY` with
`reference_readiness=BLOCKED`/`BLOCKED_REFERENCE_MISSING`,
`evidence_level=NONE`, and `scientific_conclusion=false` until an
expert-approved reference artifact is supplied.

The benchmark metric formulas can be exercised independently with an
explicitly synthetic two-site fixture. This command validates units, shapes,
finite values, structure/calculation-level identity and the six deterministic
metric definitions without using a real material reference:

```bash
.venv/bin/python scripts/run_agent02_benchmark_metric_synthetic.py \
  --input tests/fixtures/benchmarks/agent02-benchmark-v1-synthetic-metrics.json
```

Its result is always `is_synthetic=true`, `evaluation_status=TEST_ONLY`,
`evidence_level=NONE`, and `scientific_conclusion=false`; it must not be used
to claim model accuracy or expand the Agent02 applicability domain.

Aggregate a zero-error case and the known-offset synthetic case with the
frozen benchmark aggregation definitions:

```bash
.venv/bin/python scripts/run_agent02_benchmark_summary_synthetic.py \
  --manifest tests/fixtures/benchmarks/agent02-benchmark-v1-si.json \
  --input tests/fixtures/benchmarks/agent02-benchmark-v1-synthetic-metrics.json
```

Use `--format markdown` for the deterministic warning-first report. Complete,
blocked and failed case records remain visible; aggregates never hide a case
failure. This summary is also permanently `TEST_ONLY` with no evidence or
scientific conclusion.

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

An engineering-only VASPilot Bridge PoC now freezes strict JSON contracts,
server-side idempotency semantics, bounded authenticated HTTP transport, and a
`VASPilotBackend` adapter. Its deterministic Fake Bridge is test-only,
`is_mock=true`, and exercises response-loss recovery, status/cancel, Artifact
manifests, and fail-closed hash checks. It does not enable the REAL planner,
contact VASPilot, register Agent03 in production, or relax any VASP/POTCAR,
method-policy, approval, security, validation, or evidence Gate.

Run the Agent03 contract, failure-injection, integration, and E2E coverage with:

```bash
PYTHONDONTWRITEBYTECODE=1 MPLCONFIGDIR=/tmp/material-agent-mpl \
.venv/bin/python -m pytest -q -p no:cacheprovider \
  tests/contract/test_frozen_agent03_fixture.py \
  tests/contract/test_dft_mock_backend.py \
  tests/contract/test_vaspilot_bridge_contract.py \
  tests/unit/test_vaspilot_bridge.py \
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
The production Agent02 runner is conditional: it is registered only when the
independent worker executable, lock, model card, health, recovery, security,
and release checks are valid. Without that explicit configuration it remains
fail-closed and unavailable.
A `WaitingExternal` result is stored as Stage `RUNNING` plus Run `PAUSED`, so a
new process can reconcile the same external job and frozen plan without
preparing or submitting it again.

## Run Agent 01 standalone

Materials Project runs additionally produce a human-readable rich report for
every published candidate. The public `candidate_manifest.jsonl` remains the
frozen `agent01-contract-v1` interface; report-only data lives in
`report_enrichment.jsonl`, `report_assets/`, and compressed `report_data/`.
Structure views are rendered locally. Report enrichment (electronic, phonon,
spectra, heterostructure and charge-density derivatives) is limited to the top
20 published candidates by default and can be changed without changing
screening using `--mp-report-heavy-limit 0..200` on either `material-agent
retrieval` or `material-agent run`. Missing endpoint data is shown as
`NOT_AVAILABLE`; optional fetch/render failures make the Stage `PARTIAL` but
never change a candidate decision or rank. Materials Project values remain
database calculations, not experimental validation.


Each run selects exactly one retrieval source. The available explicit
`--source` values are:

| Source | Interface | Safely mapped properties | Important limit |
|---|---|---|---|
| `materials_project` | official `mp-api` | structure, band gap, hull energy, metal flag; density, volume, formation energy, stability, crystal system, space group, direct-gap flag, magnetic ordering | requires `MP_API_KEY` |
| `nomad` | public Archive API | structure, reported band gap | no MP-equivalent hull field |
| `mc3d` | Materials Cloud OPTIMADE 1.2, PBE-v1 | relaxed 3D structure | band gap/hull/metal remain missing |
| `c2db` | official search plus per-material JSON | 2D structure, PBE gap, C2DB hull energy, layer group, magnetic label | CC BY-NC 4.0; live web snapshot is not immutable |
| `topological_quantum_chemistry` | public v4 search and v1 detail API | ICSD structure and topology provenance, classification/subclassification, SOC, index presence, crossing diagnostics | topology is not promoted above L1; historical invalid CIFs fail per record |
| `nims_supercon` | MDR SuperCon Ver.240322 TSV | formula, elements, Tc metadata/provenance | no atomic coordinates; records fail the structure Gate and are not published |

For example, choose the public, read-only NOMAD API explicitly:

```bash
material-agent retrieval \
  --requirement tests/fixtures/requirement.si-o.json \
  --output workspace/demo \
  --source nomad
```

NOMAD public entries do not require `MP_API_KEY`. The adapter queries
`/entries/archive/query` with `owner=public`, records the API/entry/parser/method
provenance, converts archive lengths from metres to ångström and electronic
gaps from joules to eV, and keeps the evidence ceiling at `L1_RETRIEVED`.
NOMAD has no standardized field that this implementation can safely equate to
Materials Project `energy_above_hull`; when that property is required it stays
missing and the candidate is `UNCERTAIN`. A run never fills missing NOMAD
properties from Materials Project.

`candidate_manifest.jsonl` publishes both `PASS` and `UNCERTAIN` records, in
that order. An `UNCERTAIN` record retains its `missing_evidence` and L1 ceiling
for downstream review; only `REJECT` (explicitly conflicting database evidence)
and `FAILED` records are blocked from downstream publication.

MC3D, C2DB, TQC, and NIMS retrieval also require no secret. Unsupported
properties are never filled from another source. The NIMS source is useful for
auditing SuperCon metadata only: Agent01's structure-required downstream
contract intentionally marks every structureless record `FAILED`.

Database-specific hard constraints can be placed under
`hard_constraints.source_constraints`. Materials Project accepts simple scalar
or label checks such as `density_g_cm3`, `volume_a3`,
`formation_energy_ev_atom`, `is_stable`, `crystal_system`, `spacegroup_number`,
`is_gap_direct`, and `magnetic_ordering`; C2DB accepts `layer_group` and
`magnetic_label`; TQC accepts its classification, SOC/index, and crossing
labels/counts. These checks are local and deterministic after normalization.
NOMAD and MC3D currently expose no additional stable normalized fields beyond
the common constraints, so their source-specific maps remain unavailable.
If a source-specific constraint targets a different selected database, query
planning fails closed. If the selected record lacks the field, the candidate is
`UNCERTAIN` rather than inferred.

Atomly is not yet a selectable source. A credentialed 2026-07-30 read-only
probe confirmed that `search_by_formula` and `get_struct_detail` accept the
documented `Authorization: token …` header, and the latter returns structure
and property payloads. However, `search_by_elements` returned HTTP 500 for the
documented-style `{"include": ["Si", "O"]}` request, and the public page does
not define its valid request body, result-column names, pagination, or the CIF
location inside `output_structs`. Agent01 must not infer those scientific field
mappings. Keep Atomly unavailable until its provider supplies the schema or
fixes the elements endpoint; use a Keychain entry named
`material-screening-agent-atomly-api` rather than storing the token in files.

SpringerMaterials is also not a selectable source. It is a licensed database;
the public Springer Nature APIs cover publication metadata and open-access
content, not a documented SpringerMaterials structure/property query API.
Before any integration, obtain institutional access plus written TDM/API terms,
an authenticated endpoint specification, a credential delivery mechanism, and
permission to archive the returned data as Agent01 Artifacts. Do not automate
the interactive website or use a subscription cookie as an API substitute.

For the deterministic offline fixture, add
`--fixture tests/fixtures/mp-summary.si-o.json`; fixture results remain
`is_mock=true` when exercising any `--source` value.

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

The historical P0.1/P0.2 and v1-closeout commits are retained for traceability.
After the closeout, the current development branch added the DeepSeek Stage 0
provider, additional Agent01 retrieval sources, Agent02 benchmark/DeepH control
flows, the Agent03 structured VASPilot bridge PoC, and the Hermes inspiration
pilot. The current offline Gate reports `668 passed, 13 skipped, 362 warnings`.
The skips are two MCP/stdio checks assigned to the isolated Gateway environment,
opt-in Crossref/LLM/Materials Project/NOMAD live probes, and five real-ML tests.
The warnings are known pymatgen/spglib warnings and do not indicate test
failures. Real-ML tests are never part of the offline Gate. On a non-sandboxed
target Mac, the historical optional real-ML Gate reported `5 passed`; sandbox
MPS unavailability is an environmental limitation, not a hardware failure.

The standalone Agent01 and Orchestrator-restart Materials Project release
Gates are opt-in and require network access plus a credential available from
`MP_API_KEY` or the macOS Keychain entry described above:

```bash
agent_mp_key="$(security find-generic-password \
  -a "$USER" -s "material-screening-agent-mp-api" -w)"
MP_API_KEY="$agent_mp_key" .venv/bin/python -m pytest \
  tests/live -m live_mp --run-live-mp
unset agent_mp_key
```

This compatibility example keeps the key out of shell history and clears the
temporary shell variable after the test; it is no longer needed when the
Keychain entry exists. On another operating system, inject the key from its
secret store or an already configured process environment.

The P0.2 release run completed both real Materials Project Gates with
`2 passed` in `78.10s`; no API key or live run artifact was retained in the
repository.

The public NOMAD release probe is separately opt-in and requires network access
but no credential:

```bash
PYTHONDONTWRITEBYTECODE=1 MPLCONFIGDIR=/tmp/material-agent-mpl \
.venv/bin/python -m pytest -q -p no:cacheprovider \
  tests/live/test_live_nomad_release.py --run-live-nomad
```

## Agent 01 contracts

The frozen Materials Project contract remains `agent01-contract-v1`, including
its byte-reproducible fixture. Every non-MP source uses
`agent01-contract-v2` so the source identity is explicit without changing the
v1 schema. Orchestrators should use the explicit lifecycle:

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

The Orchestrator does not reuse either native envelope as its own public
contract. `Agent01RunnerAdapter` validates the selected source's native result,
stores its URI/hash, wraps its native plan in `PreparedStagePlan`, and maps only
control state into `ControlStageOutcome(orchestrator-p0.2-v3)`.

## v1 release closeout (P2)

The v1 boundary is frozen. This closeout adds no scientific model, backend,
dependency, public schema, migration, or scientific-threshold changes:

- Agent01 is the default production scientific runner.
- Agent02 is registered only after `MATERIAL_AGENT_ML_WORKER_PYTHON` points to
  a validated dedicated Python executable and the lock/model-card/health Gates
  pass. The current L2 audit scope is only periodic 3D elemental Si.
- Agent03 and Agent04 are mock control chains only; they are not real DFT or
  many-body scientific backends and remain unavailable in the default registry.
- Benchmarks, expanded applicability, OOD detection, and calibrated uncertainty
  are explicitly out of scope for this P2 closeout.

Reproducible offline release checklist (run from the repository root):

```bash
material-agent project create --workspace workspace --project-id demo
material-agent run --workspace workspace --project demo --run-id run-demo \
  --source materials_project \
  --request "从 Materials Project 中寻找同时包含 Si 和 O、带隙为 0.5–1.0 eV、energy above hull 不超过 0.05 eV/atom 的非金属材料。" \
  --fixture tests/fixtures/mp-summary.si-o.json
# approve the printed approval_id, then run status/report as shown above
PYTHONDONTWRITEBYTECODE=1 MPLCONFIGDIR=/tmp/material-agent-mpl \
  .venv/bin/python -m pytest -q -p no:cacheprovider
.venv/bin/python -m pip check
git diff --check
```

The historical closeout result was `337 passed, 7 skipped`. The P3.1
generalization checkpoint result is `715 passed, 14 skipped`; the skip reasons
are the isolated MCP/stdio checks, opt-in Crossref/LLM/Materials Project/NOMAD
live probes, and real-ML tests. Do not run the live MP Gate in this offline
audit: it requires network access and a secret `MP_API_KEY`, neither of which is
needed for the offline baseline.
The optional real-ML Gate may be run only in its separately provisioned worker
environment; lack of MPS visibility in a sandbox is recorded as an environment
boundary. Repository checks also require no tracked virtual environment, model
cache, live artifact, secret, temporary file, or traceback.

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

- P0.2 has a replaceable Parser protocol, deterministic offline default, and
  an explicitly configured DeepSeek `deepseek-v4-pro` Stage 0 implementation.
  Its offline Provider/Parser/integration Gates and the one-request real
  `live_llm` release Gate passed on 2026-07-29 (`1 passed in 26.56s`). Future
  live probes remain opt-in and are not part of the offline Gate.
- Agent 01 is the only production scientific stage registered by default.
  Agent02 is additionally registered only when
  `MATERIAL_AGENT_ML_WORKER_PYTHON` passes the production factory checks;
  Agent03/04 remain unregistered. Agent02 currently audits only elemental Si
  for L2 release use, while every test-only fixture runner stays `is_mock=true`
  and cannot raise evidence.
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
- Agent02's real CPU/MPS, serial Top-5, recovery, resource-recording and
  direct-stage CLI Gates are complete. Its L2 applicability audit is presently
  limited to elemental 3D Si; scientific benchmarks, broader domain coverage,
  calibrated uncertainty and any model migration remain later P1 work. All
  real Agent03/04 scientific backends remain later milestones. Fake/mock
  adapters remain unregistered and validate control behavior only.
