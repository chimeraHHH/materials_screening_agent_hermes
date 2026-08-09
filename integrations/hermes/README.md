# Hermes integration

This directory contains the reproducible, narrow Hermes control-plane bundle for
the materials inspiration workflow. Hermes runs in a separate environment and
talks to the material engine only through the allowlisted Materials MCP server.

## Install the pinned runtime

From the repository root:

```bash
.venv/bin/python integrations/hermes/scripts/bootstrap_runtime.py
```

The script fetches only the commit recorded in `hermes.lock.json`, verifies the
resolved SHA, and runs Hermes's committed `uv.lock` into `.venv-hermes`. It does
not run a remote shell installer and does not install Hermes into the material
engine's `.venv`.

## Profile bundle

`profiles/materials-inspiration/` is the source-controlled profile distribution.
Its platform configuration names the raw `materials` server; Hermes v0.20.0 then
registers the dynamic `mcp-materials` toolset and `mcp__materials__*` tools. The
MCP server exposes four coarse tools and disables server resources and prompts.
The profile pins the repository-owned
`material_agent.integration.queued_gateway:create_queued_hermes_inspiration_service`
factory; neither the model nor a tool caller can replace it or supply paths.

The pinned factory is asynchronous. Grant consumption and job enqueue share one
SQLite transaction; successful `materials_run_act` returns `RUNNING` after durable
enqueue.
It does not execute Crossref or the inspiration runner on the MCP request thread.
The production lifecycle below starts and supervises the required worker for the
same workspace/project before accepting traffic. For standalone diagnostics,
the equivalent worker entry point is:

```bash
.venv-gateway/bin/python -m material_agent.integration.queued_gateway \
  --workspace /absolute/path/to/a/bounded/workspace \
  --project materials-inspiration
```

The worker claims jobs with a fenced lease, heartbeats during bounded execution,
and commits checkpoint/terminal state through the same persistent Gateway. If no
worker is healthy, actions remain durably `RUNNING`; Hermes must poll
`materials_run_get` and must not repeat the approval action or call
`materials_result_get` before a terminal state.

The versioned `SKILL.md` is mirrored into `SOUL.md` so its policy is loaded on
every API-server run without enabling Hermes's inseparable `skill_manage` tool.
Run `verify_bundle.py` after changing either file; drift fails the release gate.

At runtime, set these non-secret variables in the profile environment:

```text
MATERIAL_AGENT_PYTHON=/absolute/path/to/materials_screening_agent/.venv-gateway/bin/python
MATERIAL_AGENT_WORKSPACE=/absolute/path/to/a/bounded/workspace
MATERIAL_AGENT_PROJECT_ID=materials-inspiration
# Optional Crossref polite-pool identity; never persisted in artifacts:
MATERIALS_CROSSREF_CONTACT_EMAIL=operator@example.org
```

`MATERIAL_AGENT_PYTHON` must name a separate Gateway environment containing the
material project dependencies plus the pinned MCP SDK. The production service
compiles a narrow, source-controlled flat/narrow electronic-band request family
from strict structured constraints and performs bounded Crossref metadata search.
The free-form goal is approval-bound and hashed but never parsed to infer scope.
Unsupported requests fail before approval or network access. The current
operator-owned, SHA-pinned engineering catalog exposes only reviewed TiS2/TiSSe
to TiSe2 substitution routes and accepts no caller-supplied CIF or path; see the
versioned Skill for the exact vocabularies and minimum budgets. The service
stores artifacts beneath
`<workspace>/<project>/` and persists Gateway state in
`<workspace>/<project>/.gateway/materials-gateway.sqlite3`. A separate mode-0600
`operator-approval-grants.sqlite3` stores one-time action grants and is never
exposed through MCP. Restarting the MCP process therefore preserves submission
idempotency, pending approval, terminal state, and the bounded result. Scientific
completion still depends on the canonical structured-result hash plus immutable
`stage_result.json` and report artifacts passing URI/SHA/size verification.

Create or refresh that environment only through
`bootstrap_gateway_runtime.py`. It performs an exact `uv pip sync` against
`gateway-requirements.lock` before reinstalling this repository in editable,
no-dependency mode. The sync is intentional: a shared development or Agent02
environment can contain newer split Pymatgen distributions that change
structure-parser diagnostics and must not be used for execution or release
replay.

`MATERIALS_CROSSREF_CONTACT_EMAIL` is optional operator-owned environment state.
It selects Crossref's polite pool but must never enter an Artifact, manifest,
report, component digest, or model-visible result. The adapter reads bounded
metadata and abstracts only; the production policy has a zero body-fetch request
budget and does not follow article, full-text, or PDF links. The repository's
JSON-LD/Highwire, JATS/XML, and HTML passage Gate is offline and fixture-backed.
It proves bounded runner/extractor behavior, not public-network body-fetch
capability. Public body fetching remains disabled until DNS validation is bound
to the actual connection address and all redirect, host, content-type, request,
and byte-budget release checks pass. The current preflight DNS resolution and
later hostname connection are separate operations; they do not close DNS
rebinding/TOCTOU. Exhausted
transient retries terminate with `EXTERNAL_SEARCH_UNAVAILABLE` and
`retryable=true`; retry only after a new user decision, with a new submission ID,
new run, and fresh approval. Schema drift is nonretryable.

Runtime `tag_feedback.json` is immutable and review-only. It cannot mutate the
curated TagGraph or current-run query plan. Its v1 wire values are
`review_disposition=REVIEW_ONLY`, `applies_to_tag_graph=false`,
`scientific_conclusion=false`, and
`aggregation_semantics=INCLUSIVE_NON_ADDITIVE`. The top level and every bridge
row use `expert_status=UNKNOWN`; v1 accepts no expert-review input. Any change
requires a separately versioned, verified review workflow. Query/tag/bridge cost
allocations are inclusive and non-additive; the Gateway `CostLedger` is the sole
additive run total. The v1 `materials_result_get` projection exposes only the
feedback Artifact URI/hash, not its rows, so Hermes must not claim to have read
or quote those rows.

## Record a real user approval out of band

The MCP caller cannot authorize itself: sending `confirmed_by_user=true` without
a matching operator grant fails while the run remains `INTERACTION_REQUIRED`.
After showing the exact interaction and frozen execution-manifest hash to the
user, a trusted local operator records the decision with the Gateway runtime:

For the production profile, that interaction must explicitly say that approval
permits bounded public Crossref metadata/abstract network access, state the
physical search-attempt ceiling, and state zero article-body fetch, full-PDF, and
internal-model budgets. An interaction that incorrectly says the production run
is offline is not informed approval: do not issue a grant, and replace the run
only after correcting and validating the Gateway contract.

```bash
.venv-gateway/bin/python -m material_agent.integration.operator_approval \
  --workspace /absolute/path/to/a/bounded/workspace \
  --project materials-inspiration \
  --run-id inspiration-... \
  --confirmation-reference user-confirmation:ticket-001
```

`approve` remains the backward-compatible default. To decline without running
scientific code, authorize the exact advertised `reject` or `cancel` action:

```bash
.venv-gateway/bin/python -m material_agent.integration.operator_approval \
  --workspace /absolute/path/to/a/bounded/workspace \
  --project materials-inspiration \
  --run-id inspiration-... \
  --confirmation-reference user-rejection:ticket-002 \
  --decision reject \
  --reason "bounded scope was not accepted"
```

The JSON receipt and private grant database record the exact canonical action,
its decision kind, and the verified execution-manifest SHA. A grant for one
decision or reject reason cannot authorize another action.

The CLI defaults to the production public service. Source-controlled fixture
replay is test-only and must be selected explicitly with `--service-mode fixture`.

The grant binds the canonical request, complete interaction, execution manifest,
and exact action. In the default queued profile it is consumed atomically with
job creation, so an MCP restart cannot leave a consumed grant without a durable
job. The recovery command below exists only for a verified stranded grant from
the explicitly selected legacy synchronous compatibility factory. First verify
that the original process has stopped; the command can then revalidate all
frozen inputs and explicitly re-arm that legacy grant with a fresh reference:

```bash
.venv-gateway/bin/python -m material_agent.integration.operator_approval \
  --workspace /absolute/path/to/a/bounded/workspace \
  --project materials-inspiration \
  --run-id inspiration-... \
  --confirmation-reference user-confirmation:recovery-002 \
  --recover-consumed-grant \
  --confirm-original-process-stopped
```

This recovery command is deliberately absent from Hermes's tool list. Never use
it to compensate for an absent queued worker, replay a terminal provider failure,
or bypass a new user decision. The
recovery command must repeat the original `--decision` and, for rejection, the
exact original `--reason`; otherwise no consumed grant matches. The
fixture service remains available only through explicit `--service-mode fixture`
for deterministic source-controlled replay; any byte drift fails closed.

## Verify a completed release run

After the same run reaches a result-bearing terminal state and every writer has
stopped, run the standalone verifier with the values witnessed before approval:

```bash
.venv-gateway/bin/python \
  integrations/hermes/scripts/verify_completed_inspiration_run.py \
  --workspace /absolute/path/to/a/bounded/workspace \
  --project materials-inspiration \
  --run-id inspiration-... \
  --expected-request-sha256 <64-lowercase-hex> \
  --expected-execution-manifest-sha256 <64-lowercase-hex> \
  --expected-interaction-id interaction-... \
  --expected-interaction-sha256 <64-lowercase-hex> \
  --expected-action-sha256 <64-lowercase-hex> \
  --expected-confirmation-reference <exact-operator-reference>
```

This command is fail-closed and read-only. It rejects an uncheckpointed WAL,
symlink/hard-link traversal, a pending or unbound grant, noncanonical database
records, undeclared stage entries, declared fetched-body/PDF Artifacts,
forbidden or out-of-allowlist Crossref fields, raw PDF signatures, PDF
data/signature markers or document-level HTML markers in bounded allowed
strings, and any pointer or lineage drift. It also binds the production
`rows=1` response to exactly one item and caps the raw abstract at 20,000
characters. It then deterministically replays the current approval-bound
components from query planning and Crossref parsing through passages, vectors,
evidence, bridges, structure transformation, internal dedup/MMR, ledger,
feedback, report, and Gateway projection. These are structural and marker
checks, not semantic recognition of arbitrary plain prose mislabeled by a
provider as an abstract. The project tree is byte/metadata snapshotted before
and after verification; any write fails the Gate. A historical run whose frozen
components no longer match the current release must be verified with its
source-pinned release checkout, never by weakening the manifest check. The
executable is part of the contract: use the lock-synchronized
`.venv-gateway/bin/python`, not the general development or Agent02 environment.
Raw Crossref items are restricted to the exact metadata fields requested by the
adapter (`DOI,title,author,published,URL,abstract,subject`) with bounded nested
shapes and lengths. Replayed selection/fetch/duplicate audits are compared as
canonical bytes, not permissive Python values, and the grant must match the
exact operator confirmation reference supplied on the command line.

OAuth provider credentials belong only in Hermes's ignored runtime credential
store; environment-based provider secrets and `API_SERVER_KEY` belong only in
the ignored runtime profile `.env`. Never print or commit either form. Bind the
API server to loopback unless a separate authenticated deployment boundary has
been designed.

The profile is intentionally not a filesystem sandbox. Its practical boundary is
the absence of terminal/file/browser toolsets plus the Gateway's fixed schemas,
path guards, size limits, hash verification, and human-approval checks.

## Production lifecycle and release E2E

The checked-in lifecycle wrapper owns one isolated Hermes home, the source
profile install, dashboard build, process identities, health endpoints, and
secret-safe operational counters. It requires `uv`, Git, and Node.js
`>=22.22.0`; the Node check happens before any checkout, environment, profile,
or dashboard mutation. Provider credentials and `API_SERVER_KEY` remain in the
process environment or Hermes credential store and are never accepted as CLI
arguments or copied into the operations event log.

```bash
export MATERIAL_AGENT_WORKSPACE=/absolute/path/to/a/bounded/workspace
export MATERIALS_HERMES_PROVIDER=openrouter
export MATERIALS_HERMES_MODEL=openai/gpt-4.1
export API_SERVER_KEY='<at-least-32-character-loopback-api-key>'
export OPENROUTER_API_KEY='<provider-credential>'

./deploy/materials-inspiration deploy
# Release smoke only: fail deployment on current Crossref unavailability.
./deploy/materials-inspiration deploy --require-live-crossref
./deploy/materials-inspiration status
./deploy/materials-inspiration health
./deploy/materials-inspiration metrics
# Resolve only the exact retained manual-recovery incident after review.
./deploy/materials-inspiration ack-failure \
  --job-id '<gateway-job-id>' \
  --expected-code BLOCKED_MANUAL_RECOVERY \
  --actor '<operator-id>' \
  --reason '<reviewed incident disposition>'
./deploy/materials-inspiration stop
```

`deploy` synchronizes the two pinned Python environments, installs the
source-controlled profile, records the non-secret model/provider selection,
builds the dashboard, performs the full local preflight, and starts the owned
processes. This lifecycle starts the queued Gateway worker before accepting
dashboard traffic and binds it to the exact same resolved workspace, project,
Gateway database, approval/outbox database, and Artifact root as the MCP
server. The mode-0600 process-set record keeps the worker PID, POSIX start
marker, command SHA-256, and a SHA-256 of that shared runtime binding; status
recomputes both identities instead of trusting a PID alone. The binding also
pins the resolved Hermes home/profile root and operations/process-record paths,
so copying a process record to a different deployment root fails closed. The
process record and lifecycle lock live at a canonical project `.gateway` path,
so changing `--ops-dir` cannot create a second ownership domain for the same
worker queue. The
managed worker uses a 30-second fenced lease and durable heartbeats/checkpoints.
An idle worker has no synthetic heartbeat, so idle liveness is explicitly the
owned PID identity plus queue integrity; while a job is `RUNNING`, its parent
supervisor refreshes the durable lease heartbeat. Worker and monitor children
do not inherit provider/API-server credential variables; the worker retains
only the non-secret runtime binding and optional Crossref contact identity
needed for its bounded metadata requests. Profile installation and dashboard
builds also use a minimal environment without provider or API-server secrets;
only the final dashboard receives the selected provider credential. The release
profile admits one concurrent Hermes run because the lifecycle currently
manages one serial action worker; increasing that limit requires a
correspondingly supervised worker pool and a new queue-capacity test.

The local preflight fails closed on the Node/Python/Hermes pin, profile or
model/provider drift, missing provider credentials, either SQLite database,
and the exact four-tool MCP stdio handshake. The profile check compares the
installed configuration semantically against the
source profile after only the approved provider/model substitution, and verifies
the installed SOUL and complete Skill tree byte-for-byte; enabling another tool,
plugin, prompt, resource, private URL, or editing installed instructions therefore
blocks start and health. Crossref is an external dependency,
so a transient outage does not block the default local `deploy`/`start`; use
`--require-live-crossref` for a release smoke that also fails closed on bounded
Crossref connectivity. The runtime
binds the dashboard to `127.0.0.1:9119` and the operational sidecar to
`127.0.0.1:9120`; the latter exposes `/healthz`, `/readyz`, and Prometheus
`/metrics`. `/healthz` reports local process/identity/database liveness.
`/readyz` additionally fails closed when the oldest `READY` job exceeds 120
seconds, the oldest `RUNNING` lease heartbeat exceeds 15 seconds, a lease has
expired, or a job is in `BLOCKED_MANUAL_RECOVERY`. Status and metrics expose
the worker PID/identity match, queue `quick_check`/schema status, queue state
counts, oldest queue/lease ages, and terminal failed/blocked counts. Queue
integrity also verifies foreign keys and exact v2 tables, columns, indexes, and
core constraints. The
blocked readiness count includes only unacknowledged
`BLOCKED_MANUAL_RECOVERY` incidents; acknowledged incidents remain visible in
the failed and acknowledged-blocked totals without keeping readiness red. The
`ack-failure` command uses the queue's exact failure-code binding: an identical
replay is idempotent, while a different code, actor, or reason fails closed.
Its JSON output and mode-0600 operations event include the job, status,
acknowledgement time, and actor, but never echo the review reason. The
lifecycle `health` command reports `local_ready` independently from
`external_crossref_ready`, so an external outage does not hide local worker or
database state. All lifecycle mutations use fail-fast mode-0600 locks: a
repository lock serializes shared environment bootstrap, and a workspace lock
prevents concurrent `deploy`/`start`/`stop`/operator actions from overwriting
process ownership. Runtime logs rotate at 8 MB with three retained generations;
the operations audit log rotates at 32 MB with three retained generations, and
metrics read the retained generations in order. Atomic process-record
replacement fsyncs both the file and parent directory. `stop` first closes
dashboard traffic, gives the supervised worker five seconds to checkpoint and
exit, and bounds the two local-process waits at two seconds each (under ten
seconds total before command overhead). It signals only PIDs whose process start
marker and command hash still match the ownership record.

The default automated production E2E has no network dependency. It replaces
only the production factory's injectable HTTP transport, then verifies submit,
ungranted-action rejection, out-of-band grant, durable enqueue with an immediate
`RUNNING` response, execution by an independent worker instance, full online
artifact closure/readable result, and MCP process restarts:

```bash
.venv-gateway/bin/python \
  integrations/hermes/scripts/run_production_e2e.py \
  --workspace /tmp/materials-inspiration-e2e \
  --project materials-inspiration-e2e
```

Pass `--live-crossref-smoke` only for an explicit one-request public Crossref
connectivity check. It does not mint user approval or execute a live Gateway
run. Provider readiness resolves the configured credential without printing
it or making a paid model call; actual credential validity and model behavior
still require an explicit provider smoke outside the zero-model-call scientific
run contract.
