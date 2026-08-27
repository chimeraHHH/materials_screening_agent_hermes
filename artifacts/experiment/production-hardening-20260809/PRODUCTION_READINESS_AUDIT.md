# Materials Inspiration Production Readiness Audit

> **Superseded implementation snapshot.** The detailed findings below describe
> the stable tree at the time of the first audit. The queued factory, managed
> worker, worker-aware readiness/metrics, hard child deadline, installed-profile
> closure checks, log rotation, and CI workflow were implemented afterward.
> Current status is summarized in the addendum immediately below; historical
> paragraphs are intentionally retained and must not be read as current facts.

## Superseding addendum — 2026-08-09

The public-production decision remains **NO-GO**, but its primary reasons have
changed:

1. The queued factory and managed worker are now the installed production path.
   Queue schema v2, worker identity, lease/backlog/blocked metrics, lifecycle
   rollback, bounded logs/events, strict environment allowlists, and operator
   acknowledgement are active and tested.
2. The supervisor now enforces request-specific child deadlines and TERM/KILL/
   reap while the parent is alive. A P0 remains: if the parent worker is itself
   hard-killed while the action child is in a separate session, immediate child
   cleanup/reaping is not yet guaranteed. The child's own deadline does not close
   that parent-death contract.
3. A pinned GitHub Actions workflow now exists. Platform branch protection,
   required review/check evidence, and a coverage threshold still do not.
4. The latest complete offline Gate is `985 passed, 14 skipped, 362 warnings`;
   queue/supervisor targeted tests are `36 passed`, and production lifecycle/
   operations tests are `26 passed`. These remain engineering evidence only.
5. No credentialed provider-backed end-to-end release or real expert-adjudicated
   flat/narrow-band benchmark has been completed. The scientific-performance
   decision therefore remains NO-GO.

The current research track is
[`flatband-benchmark-20260809`](../flatband-benchmark-20260809/PLAN.md).

Date: 2026-08-09 (Asia/Shanghai)

Branch: `agent/inspiration-generalization`

Baseline: `537ddd446df71c2ffaab25c2a1801e2be2df3c00`

Scope: Materials Inspiration only; novelty, patentability, and validated material-property claims are out of scope.

## Executive decision

**NO-GO for public production and NO-GO for a scientific-performance claim.**

**GO only for a controlled, single-host engineering preview** using the current
bounded Crossref metadata/abstract path, explicit operator approval, no body/PDF
fetch, no internal model call, and the user-facing statement that every proposed
property remains `UNKNOWN` until downstream validation.

The decision is not caused by a failing core test suite. The stable tree passes
the complete offline Gate and three live Crossref paths. The release blockers are
activation and evidence gaps: the durable queue is not connected to the default
Hermes production profile, the real provider-backed one-command lifecycle has not
completed, hard kill/recovery remains incomplete, and the repository has no real
expert gold set establishing scientific recall or cross-domain usefulness.

## Verification summary

| Gate | Result | Evidence |
| --- | --- | --- |
| Complete offline repository suite | PASS | `950 passed, 14 skipped, 362 warnings in 101.25s` |
| Live Crossref metadata, full runner, approval-bound Gateway | PASS | `3 passed in 9.93s` |
| Real MCP stdio in Gateway environment | PASS | `2 passed in 3.99s`; exact four-tool allowlist and process restart |
| Queue/outbox/lease/recovery/factory targeted Gate | PASS as infrastructure | `75 passed`; default production profile still does not use it |
| Production operations/deployment fixtures | PASS | `7 passed in 9.77s` |
| Node 22 real component exercise | PARTIAL | bootstrap, Dashboard build, Dashboard/monitor health and stop passed; full provider-backed `_start()` did not |
| Main/Gateway/Hermes dependency consistency | PASS | all three environments report compatible dependencies |
| Hermes bundle verifier | PASS | `Hermes bundle valid` |
| Compile and whitespace checks | PASS | `compileall` and `git diff --check` |
| Public-production activation | FAIL | sync factory remains pinned; no managed worker in lifecycle/readiness |
| Scientific release Gate | FAIL | no real recall gold set, authenticated expert release, semantic provider, or statistical acceptance Gate |
| CI/release governance | FAIL for public release | no `.github` workflow, coverage threshold, required check, or protected-branch evidence |

### What the live Gate proves

The live Gate proves that the current source can query public Crossref, parse
bounded metadata/abstract responses, persist the search and transformation
lineage, execute the explicit approval lifecycle, return a bounded readable
result, and verify the full Artifact closure. It does not prove literature
recall, cross-domain scientific correctness, or candidate material properties.

The frozen production policy remains metadata-first: article-body requests = 0,
full-PDF reads = 0, and internal model calls = 0. Therefore the HTML/JATS/JSON-LD
extractors are tested offline but are not exercised by the current production
policy.

## Capabilities that are now materially stronger

1. **Network boundary:** urllib implicit redirects are disabled. Every Crossref
   redirect hop is validated before connecting and consumes the physical-request
   budget. Redirect loops, foreign hosts, ports, media types, declared sizes, and
   streamed `max+1` bytes fail closed.
2. **Approval/build identity:** manifest v2 binds the complete execution identity
   set and is recomputed at start and act. Approve, reject, and cancel are exact,
   audited decisions bound to the execution manifest.
3. **Evidence and result integrity:** negation/uncertainty classification is
   sentence-local and conservative. Online result reads verify the complete
   Artifact closure and return only run-bound, bounded report/evidence projections.
4. **Durable execution primitives:** SQLite queue/outbox, lease, heartbeat,
   fencing, checkpoints, a run-scoped process lock, and completed-stage recovery
   exist and pass fault-injection tests. Grant consumption and job creation share
   one transaction.
5. **Budget accounting:** physical redirect/retry attempts are counted; a
   monotonic deadline is propagated through the runner; `walltime_ms` is measured
   instead of always being zero.
6. **Operations foundation:** Node/Python/provider/model preflight, profile and
   MCP probes, PID identity, loopback health/readiness/metrics, mode-0600 JSONL
   events, start/stop/status, and a static production E2E exist.
7. **Scientific workflow foundations:** deterministic query allocation and
   metadata-quality audits, an immutable expert-review contract, a pinned local
   semantic-provider contract, hybrid-dedup evaluation code, and multi-candidate
   engineering metrics exist. They are not yet activated with real scientific
   assets.

## P0 — release blockers

### P0.1 Durable execution is not active in the production Hermes path

`integrations/hermes/scripts/verify_bundle.py` still pins:

`material_agent.integration.hermes_service:create_hermes_inspiration_service`

That is the synchronous service. The queued factory exists at
`material_agent.integration.queued_gateway:create_queued_hermes_inspiration_service`,
but it is opt-in only. `production_runtime.py` starts and supervises Dashboard and
monitor processes, not a Gateway action worker. `production_monitor.py` considers
Dashboard, its owned PID, and the Gateway database sufficient for readiness; it
does not check worker PID, lease age, queue age, failed jobs, or
`BLOCKED_MANUAL_RECOVERY`.

Consequence: a real Hermes `materials_run_act` still performs the Crossref run in
the request path. The durable queue is tested code but dormant production code.

Required release fix:

- pin the queued factory in the installed Hermes profile and bundle verifier;
- start the worker CLI with the exact same workspace/project, approval SQLite,
  Gateway SQLite, and Artifact root;
- supervise and stop the worker with Dashboard/monitor;
- expose worker PID, heartbeat/lease age, oldest READY age, failed/blocked counts,
  and queue integrity in readiness and metrics;
- add one automated production E2E proving `act -> RUNNING`, worker execution,
  terminal commit, and restart recovery through the installed profile.

### P0.2 The requested hard execution/recovery contract is incomplete

The deadline is cooperative. It is checked across runner stages and used to
shrink network timeouts, retries, and pacing, but there is no supervisor-enforced
child-process deadline that terminates an unresponsive local stage. A kill after a
complete canonical stage can recover without another Crossref call; a kill during
the runner or a partial stage intentionally becomes `BLOCKED_MANUAL_RECOVERY`.

This is the correct fail-closed behavior, but it does not satisfy automatic
production recovery. The body-fetch subsystem also has not propagated the same
per-hop deadline through all retry/redirect sleeps; production currently avoids
that path by setting body fetch to zero.

Required release fix: execute each job in an owned child process with a monotonic
supervisor deadline and termination escalation, add finer immutable checkpoints,
record a terminal operations duration separately from the deterministic
scientific snapshot, and add kill-at-every-boundary tests.

### P0.3 A real one-command provider-backed deployment has not passed

A disposable Node 22.22.0 runtime, verified against the official checksum, passed
bootstrap. Dashboard build produced 59 files, and Dashboard plus sidecar health,
metrics, status, and stop worked in an isolated temporary workspace. The complete
`deploy/start/health` path stopped at provider credential resolution, as intended;
no user secret or model call was used. Therefore production `_start()` has not
been verified behind a real configured provider and live Crossref preflight.

The current host is not deployable as-is:

- system Node is `20.20.2`, below the required `22.22.0`;
- the `materials-inspiration` Hermes profile is absent in the current default
  Hermes home;
- deployment status is `running=false`, with no listeners on 9119 or 9120;
- the pinned Hermes checkout has a modified `package-lock.json`, which the clean
  identity preflight is expected to reject;
- no real provider/model credential was supplied for this audit.

Required release evidence: clean pinned checkout, Node >=22.22, isolated profile,
real provider credential and model, one successful `deploy -> start -> health ->
bounded approved run -> result -> stop`, secret scan, and no residual process.

### P0.4 Scientific usefulness has not met a production evidence Gate

The production retriever is Crossref-only. It selects at most one direct and three
bridge queries, no counter query, and requests `max_results=1` with
`has-abstract:true`. This normally exposes at most about four metadata records and
systematically excludes relevant records without an abstract. The eight-request
budget includes redirect/retry attempts; it is not eight documents.

The TagGraph is still a small hard-coded graph consumed directly by production.
The expert-review schema supports two-person approval and immutable releases, but
reviewer identities are caller-provided strings and production does not consume a
reviewed release hash. Passage relevance and relation classification remain
lexical heuristics. The production vector is deterministic signed feature
hashing; the real semantic provider correctly reports
`SEMANTIC_PRODUCTION_UNAVAILABLE`. Candidate `quality_score` remains a fixed
engineering value and is not property confidence.

There is no real adjudicated expert gold set, reviewer agreement measurement,
confidence interval, domain-wise worst-case result, or release threshold. The
evaluation module explicitly and correctly labels its current corpus
`DESCRIPTIVE_BOUNDED_CORPUS_NO_CI` and `scientific_conclusion=false`.

Required release fix:

- create a multi-source pooled gold set with at least two independent expert
  reviewers and a distinct adjudicator;
- authenticate and persist reviewer decisions and make production consume the
  exact reviewed TagGraph release hash;
- measure recall@budget and bridge precision per domain, with pre-registered
  thresholds and case-clustered confidence intervals;
- connect a SHA-pinned, licensed local semantic bundle and prove improvement over
  lexical-only dedup/ranking on labeled pairs;
- align allowed `top_k` with real catalog capacity and add downstream band/property
  validation before making a material-property claim.

### P0.5 No platform-enforced public release Gate

The repository has no `.github` workflow and no configured coverage threshold,
required check, protected-branch evidence, or platform review Gate. A local full
suite is strong engineering evidence but cannot prevent an untested or different
commit from being published.

Required release fix: pinned CI environments for offline tests, isolated MCP,
bundle verification, dependency checks, production fixture E2E, secret scanning,
coverage/report artifact, and opt-in protected live smoke; require those checks on
the protected release branch.

## P1 — important production gaps

1. **Operations diagnostics:** `_output()` converts subprocess failure, timeout,
   and OS error into the generic `runtime verification command failed`, hiding the
   safe stderr reason an operator needs. Failed bootstrap/deploy/preflight/start
   attempts are not consistently appended to the operations event log.
2. **Readiness coupling:** `deploy`, `start`, and `health` all make live Crossref a
   hard preflight dependency. A Crossref outage can prevent an otherwise healthy
   local service from starting. Liveness, local readiness, and external dependency
   readiness should be separate states.
3. **MCP shutdown latency:** the production MCP probe reported roughly 3 seconds
   internally but about 23 seconds wall time, leaving about 20 seconds in stdio
   shutdown.
4. **Single-host boundary:** queue recovery uses SQLite and a POSIX local process
   lock. That is acceptable for an explicitly single-host deployment, not for HA
   or multi-node workers.
5. **Historical-result migration:** old terminal runs that predate the full
   Artifact closure now fail with an explicit “predates full artifact-closure
   verification” error. This is safer than accepting them, but there is no signed
   backfill/migration or archive-view path.
6. **Operational durability:** no lifecycle-wide lock, log rotation, event-log
   rotation, or retention policy. The event reader fails closed after 32 MB rather
   than rotating. Raw Dashboard logs are not passed through the JSON event
   sanitizer.
7. **Production extraction scope:** the multi-format HTML/JATS/JSON-LD pipeline is
   only an offline-tested seam because production body-fetch budget is zero. If it
   is later enabled, range/streaming/caching and the remaining fetch deadline path
   need a new network-security Gate.
8. **Cross-document dedup:** DOI/URL exact document grouping works, and local
   passage near-dedup works within a hit. Cross-provider/cross-document fuzzy
   dedup is not active.
9. **Capacity mismatch:** the frozen parent catalog has five unique outputs while
   the public schema allows `top_k` up to 32, so large requests predictably
   underfill.
10. **Manifest archaeology:** current executions bind complete source identities,
    but historical verification still depends on retaining the exact source/build
    snapshot. There is no immutable release image/SBOM archive tying a deploy to a
    distributable artifact.

## P2 — cleanup and maintainability

- The full suite emits 362 warnings, dominated by upstream spglib/pymatgen
  deprecations and scientific-data warnings. They are not current failures, but a
  warning budget is needed before dependency upgrades turn them into breakage.
- Dashboard build emits a Vite `__dirname` compatibility warning.
- Operations JSON replacement fsyncs the file but not its parent directory after
  rename; an explicit directory durability policy would make crash semantics
  clearer.
- No automated code-coverage report exists, so the pass count cannot be converted
  into a statement about uncovered lines or branches.

## User-facing scientific wording required today

- Replace “scientifically no match” with **“本次限定检索未产生候选”**.
- Explain that `SEARCH_SUPPORTED` means lexical support in selected metadata or
  abstract passages, not experimental or computational validation.
- Do not describe signed feature hashing as semantic understanding.
- Describe diversity as Tag/lineage route diversity, not validated mechanism
  diversity.
- Explain that `SUCCEEDED` means workflow success; proposed material properties
  remain `UNKNOWN`.

Recommended current label:

> Ti/Se 二维平带场景的封闭范围工程预览；仅检索 Crossref 元数据/摘要；使用词法特征；候选性质未知，必须经下游计算或实验验证。

## Recommended implementation order

1. Activate the queued factory and managed worker in the production profile,
   lifecycle, readiness, and E2E.
2. Add supervisor-enforced deadlines, finer recovery checkpoints, and full
   operations-ledger timing with kill-boundary tests.
3. Complete one real Node 22/provider deployment; separate local readiness from
   Crossref readiness; preserve bounded diagnostic stderr and audit every failure.
4. Add CI/branch protection and immutable release-image/SBOM evidence.
5. Build the authenticated expert Tag release and real recall gold set; only then
   evaluate a pinned semantic model and raise the scientific product label.

## Exact stable-tree commands

```bash
PYTHONDONTWRITEBYTECODE=1 MPLCONFIGDIR=/tmp/material-agent-mpl \
  .venv/bin/python -m pytest -q -p no:cacheprovider

PYTHONDONTWRITEBYTECODE=1 MPLCONFIGDIR=/tmp/material-agent-mpl \
  .venv/bin/python -m pytest -q -p no:cacheprovider \
  tests/live/test_live_crossref_inspiration.py \
  tests/live/test_live_crossref_inspiration_runner.py --run-live-crossref

PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src \
  .venv-gateway/bin/python -m pytest -q -p no:cacheprovider \
  tests/integration/test_gateway_mcp.py::GatewayMcpStdioTest::test_real_stdio_round_trip_exposes_only_the_allowlist \
  tests/integration/test_gateway_runner_e2e.py::test_real_runner_through_mcp_stdio_survives_process_restart

.venv/bin/pip check
uv pip check --python .venv-gateway/bin/python
uv pip check --python .venv-hermes/bin/python
.venv/bin/python integrations/hermes/scripts/verify_bundle.py
.venv/bin/python -m compileall -q src integrations/hermes/scripts
git diff --check
```

## Skips and exclusions

The complete offline suite skipped 14 explicitly opt-in cases. The two MCP cases
were run separately in the Gateway environment and passed. The three Crossref
cases were run separately with `--run-live-crossref` and passed. Live LLM,
Materials Project, NOMAD, and real CHGNet tests are outside this bounded
inspiration audit; they were not silently counted as inspiration coverage.
