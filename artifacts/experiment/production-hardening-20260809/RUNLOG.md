# Production Hardening Run Log

## 2026-08-09 — Contract freeze

- Baseline: `537ddd446df71c2ffaab25c2a1801e2be2df3c00`.
- Current tree was clean before planning edits.
- Prior production audit: real Hermes/Crossref path completed, full closure verifier passed, full offline suite passed; production release remained NO-GO.
- Active frontier: Gate A security and integrity.
- No novelty work is in scope.

## 2026-08-09 — Gate A implementation checkpoint

- Redirect handling disables implicit urllib redirects and validates HTTPS,
  `api.crossref.org`, and port 443 before every physical hop; adversarial redirect,
  media-type, length, loop, and streaming-budget cases are covered.
- Execution manifest v2 content-addresses 17 source/build/runtime identities and
  recomputes them at approval and action boundaries.
- Operator decisions now implement approve, reject, and cancel; grant schema v3
  persists the exact decision and execution-manifest binding.
- Evidence relation promotion is sentence-local and conservative for negation,
  uncertainty, bare-keyword, and counter-query cases.
- Gateway result verification now hashes stage result plus every declared Artifact;
  tampering with an intermediate while leaving the report intact is rejected.
- The completed-run verifier was updated for manifest v2, grant v3, and the full
  Artifact closure. The original five integration failures were reduced to
  `5 passed`; the broader joint Gate reported `177 passed, 1 skipped` before
  the five fixes and those fixed cases then passed separately.

## 2026-08-09 — Gate C implementation checkpoint

- Approval responses expose an explicit `execution_manifest_sha256` equal to the
  compatibility `input_sha256` field.
- `materials_result_get` returns a verified Markdown prefix capped at 24,000
  characters (32,000 schema hard limit), including full-content hash, original
  byte/character counts, and a truncation flag.
- Selected evidence is projected to at most 32 run-bound excerpts of at most
  1,000 characters, with document/passage IDs, source metadata, relation, claim,
  source-passage hash, and truncation state.
- Gateway/unit/contract/MCP targeted checks reported `26 passed, 1 skipped`; the
  static production Crossref lifecycle with readable report/evidence passed.
- A full completed-run verifier rerun is intentionally deferred until the shared
  deadline/ledger implementation leaves its temporary interface-migration state.

## 2026-08-09 — Gate B deadline and runtime-ledger checkpoint

- Runner execution now uses an injectable monotonic deadline across search,
  extraction, vectorization, transformation, selection, reporting, and terminal
  Artifact verification. A mid-search expiry writes the partial attempt ledger and
  fails with `WALLTIME_BUDGET_EXCEEDED`.
- Crossref request timeouts, pacing, and retry delays are bounded by remaining run
  walltime. Redirect hops are explicit physical attempts and share a frozen
  `max_physical_requests` policy ceiling with provider retries.
- `CostLedgerV1.walltime_ms` is a measured monotonic snapshot instead of a constant
  placeholder. Tests inject a fixed/stepped clock to retain byte-stable replay and
  deterministic failure timing.
- Targeted evidence: search plus runner deadline tests `71 passed`; runner
  public/multiformat regression `7 passed`; public Gateway `3 passed`; canonical
  completed-run retry/tamper checks `3 passed`; schema contract passed in its
  targeted run. The broader verifier still has Gate-A manifest/approval assertion
  updates in progress and is not claimed as green here.

## 2026-08-09 — Stable-tree production audit

- Durable Gateway primitives now include atomic grant-consume plus queue outbox,
  claim/lease/heartbeat/fencing, transition checkpoints, a run-scoped POSIX lock,
  completed-stage recovery, reject/cancel replay, an opt-in queued factory, and a
  worker CLI. The combined targeted queue Gate reported `75 passed`.
- The production Hermes bundle still pins the synchronous service factory and the
  lifecycle does not manage or monitor the worker. Gate B is therefore an
  implemented-but-inactive production subsystem, not a completed release Gate.
- Full Artifact closure verification and bounded readable report/evidence are
  active. Historical terminal results without a closure fail explicitly and have
  no migration/backfill path.
- The first complete repository run exposed five regressions. All were diagnosed:
  stale verifier/test expectations for the new manifest/report contracts, a stale
  transport fake signature, and a real missing propagation of the injected
  monotonic clock into the public Runner. The clock propagation was fixed in
  production code so repeated Top-5 closures replay exactly.
- Stable complete offline Gate: `950 passed, 14 skipped, 362 warnings in 101.25s`.
- Stable opt-in live Crossref Gate (metadata release, full runner, approval-bound
  Gateway lifecycle): `3 passed in 9.93s`.
- Isolated Gateway MCP stdio Gate: `2 passed in 3.99s`.
- Production operations/deployment tests: `7 passed in 9.77s`.
- Main, Gateway, and Hermes dependency checks passed; Hermes bundle verification,
  `compileall`, and `git diff --check` passed.
- Real Node 22 component exercise passed bootstrap, Dashboard build, Dashboard and
  sidecar health/metrics/status/stop. Full provider-backed one-command start was
  not executed because no user credential was used; the isolated temporary runtime
  and listeners were removed.
- Current host observation: Node `20.20.2`, no installed default
  `materials-inspiration` profile, deployment `running=false`, no listeners on
  9119/9120, and the pinned Hermes checkout has a modified `package-lock.json`.
- Final decision: NO-GO for public production and scientific-performance claims;
  GO only for an explicitly bounded, single-host engineering preview.
- Detailed audit: `PRODUCTION_READINESS_AUDIT.md`.

## 2026-08-09 — Superseding production-activation checkpoint

This checkpoint supersedes the activation facts in the preceding stable-tree
snapshot; the earlier text is retained as chronological evidence.

- Every installed/profile/probe surface now pins
  `material_agent.integration.queued_gateway:create_queued_hermes_inspiration_service`.
- The lifecycle owns dashboard, monitor, and worker identities. Readiness and
  metrics include queue integrity, READY/RUNNING age, lease expiry, failed and
  acknowledged/unacknowledged blocked jobs, and exact runtime binding.
- The action supervisor enforces request-specific child deadlines, TERM-to-KILL
  escalation, group cleanup while the parent is alive, late-output rejection,
  immutable checkpoints, and a separate queue-attempt operations ledger.
- The production workflow, profile-closure probe, Node 22 component exercise,
  bounded log/event rotation, environment allowlists, lifecycle locks, operator
  failure acknowledgement, and installed queued static E2E are implemented.
- Current release regression: `985 passed, 14 skipped, 362 warnings`; queue and
  supervisor targeted `36 passed`; lifecycle/operations targeted `26 passed`;
  three environment dependency checks, bundle, compileall, and diff checks pass.
- Remaining P0: if the parent worker is itself hard-killed while its action child
  runs in a separate session, lifecycle cleanup cannot yet guarantee immediate
  child termination/reaping. The child has its own deadline, but that is not a
  sufficient parent-death contract for public production.
- A real credentialed provider deployment and real scientific benchmark remain
  unexecuted. Engineering fixture success does not establish scientific utility.
