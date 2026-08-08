# Final Hermes Crossref v2 release run — 2026-08-08

## Verdict

Engineering classification: `SUPPORTED`, with one explicitly accepted Hermes
host-session conformance deviation described below. The exact user-approved
Gateway run reached a result-bearing terminal state, and a standalone verifier
reconstructed the complete authorization, Artifact, cost, deduplication,
selection, report, and Gateway projection closure without writing to the
workspace.

The Gateway status is `PARTIAL`; the contained stage and bundle are
`SUCCEEDED`. This is not a scientific contradiction. `PARTIAL` preserves four
bounded evidence warnings, while the single selected structure proposal passed
structural checks. Its target property remains `UNKNOWN`, every output has
`scientific_conclusion=false`, and downstream band-dispersion calculation is
still required.

## Runtime and release identity

- Branch at execution: `agent/inspiration-generalization`.
- Execution-component checkpoint:
  `42abc5986b7a60184b11864ba89329c09e24d22d`.
- Public draft PR:
  [#3 Generalize and harden the Hermes inspiration workflow](https://github.com/chimeraHHH/materials_screening_agent_hermes/pull/3).
- Hermes: `0.20.0` / `v2026.8.3` / commit
  `3c27eb6234bf91b8ceee9e9071591b31e9b148cb`.
- Provider/model: `openai-codex` / `gpt-5.6-sol`.
- Materials runtime: isolated, lock-synchronized Python 3.11 Gateway environment.
- Workspace: `workspace/hermes-final-crossref-20260808-v2`.
- Project: `materials-inspiration`.
- Crossref contact email: explicitly unset; public pool used.

The completed-run verifier must run with `.venv-gateway/bin/python`. The general
development environment is not an interchangeable execution environment.

## Exact approval binding

The user supplied this exact decision after seeing the frozen interaction and
manifest:

```text
Approve manifest e6b0d901fa0a8ef60a2797e35ff4b6cc21c78f175c04258940a3e9f40324beda for run inspiration-2c470e4810392aca2c9a7c4d and interaction interaction-f2d2ab3eaa402a5ed4481e69, action approve.
```

| Binding | Value |
|---|---|
| Submission | `hermes-final-crossref-20260808-v2` |
| Run | `inspiration-2c470e4810392aca2c9a7c4d` |
| Request SHA-256 | `0598117ef45e17ec44f328f3effff5722166e2df589b8695ff0ff1c5a25220c6` |
| Interaction | `interaction-f2d2ab3eaa402a5ed4481e69` |
| Interaction SHA-256 | `e4124c90db33375f83ea87af04f937fb791130d3933c083224c2d9c52fc972e0` |
| Execution-manifest SHA-256 | `e6b0d901fa0a8ef60a2797e35ff4b6cc21c78f175c04258940a3e9f40324beda` |
| Canonical approve action | `{"confirmed_by_user":true,"interaction_id":"interaction-f2d2ab3eaa402a5ed4481e69","kind":"approve"}` |
| Action SHA-256 | `d38358ce205391fe24321add1f711954b0c74e4dbdb635a3b1cb3edc42dcf73f` |

The trusted operator CLI issued the only grant:

- grant: `grant-56f454ee3aa4f5f2cb96156c`;
- confirmation reference:
  `codex-user-exact-approval:2026-08-08-final-crossref-v2`;
- database state: one grant, `consumed=1`, zero recovery records.

The grant store uses an atomic consume-if-unconsumed update. The evidence
therefore supports the precise claim that the unique grant is consumed, has no
recovery, and cannot be consumed again under that transaction semantics. It
does not invent a separate consumption-event counter that the schema does not
contain.

## Hermes execution chronology and host-session deviation

The natural-language submit phase ran in Hermes session
`20260808_204029_977de9`, called `materials_inspiration_run` once, and stopped at
`INTERACTION_REQUIRED` without an act call.

After approval, the first attempted continuation omitted an explicit model.
Hermes rejected it before any tool call with `model must be a non-empty string`;
the grant remained unconsumed and the Gateway stayed at revision 1.

The successful action command supplied the pinned provider/model and asked for
`get → act → result`. It passed `--resume`, but also used Hermes's top-level
one-shot `-z` mode. Source and session-database inspection showed that Hermes
v0.20.0 dispatches this one-shot path before resume handling. The command
therefore created host session `20260808_225053_976ca7` instead of appending to
the submit session. Its durable Materials sequence was exactly:

```text
materials_run_get → materials_run_act → materials_result_get
```

This is the accepted conformance deviation: the act did not occur in the same
Hermes host conversation, even though it remained bound to the same persistent
Gateway run, exact interaction, execution manifest, and one-time grant. Gateway
identity is the authorization and idempotency boundary, so no new run or
approval surface was created.

The original session was then genuinely resumed with `hermes chat -q --resume`
for read-only closeout. It called `materials_run_get` once and
`materials_result_get` once, confirmed the terminal result, and made no run,
act, or network request. This restores durable final-result visibility in the
original conversation but does not retroactively conceal the one-shot CLI
behavior. Future continuations must use `hermes chat -q --resume`, not top-level
`-z`.

## Terminal result and bounded work

| Metric | Observed |
|---|---:|
| Gateway run/result/revision | `1 / 1 / 2` |
| Planned and executed queries | `4 / 4` |
| Physical Crossref attempts | `4` of maximum `8` |
| Successful HTTP responses | `4` |
| Raw hits/documents/unique documents | `4 / 4 / 4` |
| Raw search bytes | `12,999` |
| Selected metadata passages | `2` |
| Signed-hashing vectors | `2 × 32 dimensions` |
| Embedding input tokens | `167` |
| EvidenceCards / BridgePackets | `2 / 1` |
| Generated/rejected transformation plans | `2 / 0` |
| Candidates after internal dedup | `2` |
| Selected candidates | `1` |
| Article-body fetch attempts/requests/bytes | `0 / 0 / 0` |
| Full-PDF reads or PDF Artifacts | `0` |
| Materials-service LLM calls/input/output | `0 / 0 / 0` |

The four successful response byte counts were `2,900`, `2,360`, `5,658`, and
`2,081`. Every query succeeded on attempt 1; there was no retry or provider
error. The final request recorded `0.08611079199909` seconds of pacing, and the
first three recorded zero. `walltime_ms=0` is a deterministic serialized ledger
value, not a measured claim that execution took no time.

The fetch manifest has four rows: two `SKIPPED_METADATA_SUFFICIENT` and two
`DISABLED_BY_POLICY`. The stage contains no PDF path or PDF signature, and the
raw Crossref payloads contain no forbidden body/full-text/PDF fields.

## Deduplication and diversity interpretation

Two structure-valid plans produced two internal candidates and two duplicate
groups. Exact merge reduction, pool exact duplicates, pool strict duplicates,
and multi-route groups were all zero. The pool retained two parent families and
two hash-distinct physical routes.

With `top_k=1`, selection returned one candidate, one parent family, and one
physical route; selected exact and strict duplicates were zero. Route and
mechanism quotas were both `MET`. The only available, feasible, and achieved
mechanism was `local-resonance`, and there was no underfill.

This run therefore verifies the deduplication and quota audit chain. It does not
by itself demonstrate multi-candidate, cross-mechanism diversity because the
request selected one item and only one mechanism had sufficient evidence. That
broader behavior is established by the separate P3.2 Top-5 fixture/replay Gate.

## Candidate and evidence boundary

- Candidate: `candidate-70f86f8d9aaf19a5d12d65be`.
- Structure: `str_e3536ab24fcc8aa2e7d7a0ac`.
- Structure Artifact SHA-256:
  `374f90e84b6f1bf97217dd96db7daaccf58d622fe581ef31b826bad374e2d1c6`.
- Supported bridge: acoustic-metamaterial `local-resonance` to electronic flat
  band.
- Structural status: `STRUCTURE_VALID`.
- Target-property status: `UNKNOWN`.
- Cheapest falsification step: compute the target-band dispersion for the
  hash-verified output CIF.
- Tag feedback: `REVIEW_ONLY`, `expert_status=UNKNOWN`,
  `applies_to_tag_graph=false`, `scientific_conclusion=false`.

The two other cross-domain bridge rules were not promoted: the magnon route
lacked `line-graph-localization` SUPPORT and the photonic route lacked
`destructive-interference` SUPPORT.

## Warnings and status semantics

The exact Gateway/stage warnings are:

```text
BODY_NOT_FETCHED:hit-54d3784eb7430520141abcce
BODY_NOT_FETCHED:hit-d254d265fec1705a857951c2
BRIDGE_SKIPPED:magnon-line-graph-to-electronic-flat-band:required SUPPORT tags missing: ['line-graph-localization']
BRIDGE_SKIPPED:photonic-interference-to-electronic-flat-band:required SUPPORT tags missing: ['destructive-interference']
```

They explain `PARTIAL` without invalidating the structurally valid proposal.
They also prevent metadata snippets or analogies from being promoted to a
validated electronic-property claim.

## Hash and Artifact closure

| Artifact/projection | SHA-256 |
|---|---|
| Gateway result | `99b51a0b13198ab72932d0b2ac1c2060dfb2302f1cc6ad614fbadd7ff7814fa5` |
| Authoritative report | `35757fa3a8cef55d98051b1e8a1351bd9adc8d788fb88a82c79aa33a52ff6d72` |
| Inspiration bundle | `266a9962d855e8c25d5f8c09eb6f006554b504916bb9734c2d72fb3f90af621a` |
| Stage result | `9426dd1b8d0d03829029fca05e33099d06813564b25e039c31d5aabb7a8f2fe5` |
| Cost ledger | `0c90ffe24619877ab7598e831e3b5ac5a89dc3e1189b084229501f3984b2e4c1` |

The release verifier re-opened both SQLite stores in immutable/query-only mode,
recomputed the request/interaction/action/manifest binding, replayed the current
frozen components, verified all pointer/hash/size/media/role/URI and lineage
edges, verified zero declared fetch/PDF/model use plus the bounded Crossref
schema/marker rules, recomputed the ledger, report, bundle, stage result and
Gateway projection, and compared a full before/after filesystem snapshot. Its
stable result included:

```json
{"artifact_closure_verified":true,"bridge_packets":1,"bundle_sha256":"266a9962d855e8c25d5f8c09eb6f006554b504916bb9734c2d72fb3f90af621a","candidates":1,"cost_ledger_sha256":"0c90ffe24619877ab7598e831e3b5ac5a89dc3e1189b084229501f3984b2e4c1","evidence_cards":2,"execution_manifest_sha256":"e6b0d901fa0a8ef60a2797e35ff4b6cc21c78f175c04258940a3e9f40324beda","fetch_requests":0,"gateway_result_sha256":"99b51a0b13198ab72932d0b2ac1c2060dfb2302f1cc6ad614fbadd7ff7814fa5","grant":{"action_sha256":"d38358ce205391fe24321add1f711954b0c74e4dbdb635a3b1cb3edc42dcf73f","confirmation_reference":"codex-user-exact-approval:2026-08-08-final-crossref-v2","grant_id":"grant-56f454ee3aa4f5f2cb96156c","interaction_sha256":"e4124c90db33375f83ea87af04f937fb791130d3933c083224c2d9c52fc972e0"},"interaction_sha256":"e4124c90db33375f83ea87af04f937fb791130d3933c083224c2d9c52fc972e0","model_calls":0,"no_workspace_writes":true,"passages":2,"physical_search_attempts":4,"raw_search_bytes":12999,"report_sha256":"35757fa3a8cef55d98051b1e8a1351bd9adc8d788fb88a82c79aa33a52ff6d72","request_sha256":"0598117ef45e17ec44f328f3effff5722166e2df589b8695ff0ff1c5a25220c6","revision":2,"run_id":"inspiration-2c470e4810392aca2c9a7c4d","schema_version":"materials-inspiration-completed-run-verification-v1","stage_result_sha256":"9426dd1b8d0d03829029fca05e33099d06813564b25e039c31d5aabb7a8f2fe5","status":"PARTIAL","vectors":2}
```

There were 28 declared stage files and no orphan, undeclared, symlinked,
hard-linked, or uncheckpointed-WAL entry.

An independent adversarial review initially reproduced two verifier bypasses:
Python dict equality accepted `0/false` type confusion in non-model audit JSON,
and a field-name denylist allowed an unknown Crossref `payload` to carry HTML.
Before release, the verifier was changed to compare replayed canonical bytes,
enforce the exact Crossref `select` field/nested-shape allowlist, bind the exact
operator confirmation reference, and apply the production terminal-warning
projection. Selection/fetch type attacks that recompute the affected
intermediate pointer, bundle ID/pointer, and stage result ID now fail, as do
unknown HTML/PDF fields and forged confirmation references. A second review
then found that allowed `abstract` fields could still carry an oversized body
or PDF/HTML payload and that an unconsumed second Crossref item was not bound to
the production `rows=1` request. The final verifier requires
`items-per-page=1`, exactly one item, a raw abstract of at most 20,000
characters, and rejects PDF data/signature plus document-level HTML markers in
allowed strings. The exact v2 workspace passed again after every fix. These
content checks are structural/marker rules, not semantic detection of arbitrary
plain prose mislabeled by a provider as an abstract.

## Runtime-drift fail-closed observation

An accidental invocation through `.venv/bin/python` failed at transformation
replay. That environment contained the independent Agent02 dependency
`pymatgen-core==2026.7.31` and `pymatgen-io-validation==0.1.3`; the execution
environment contained `pymatgen==2025.10.7`, no split `pymatgen-core`, and
`pymatgen-io-validation==0.1.2`. Running through `.venv-gateway` passed.

The failure is recorded as environment-drift protection working as intended,
not as an Artifact inconsistency. The Gateway bootstrap was consequently
hardened from additive install to exact lock synchronization.

## Two separate cost ledgers

The materials-service ledger is authoritative only for bounded scientific work:
four search requests, 12,999 response bytes, zero body/PDF/internal-model use,
167 embedding input tokens, and deterministic `walltime_ms=0` serialization.

Hermes host usage is separate:

- submit plus final read-only resume session `20260808_204029_977de9`: seven
  provider calls, 25,755 non-cached input, 48,640 cache-read, zero cache-write,
  1,361 output, and 269 reasoning tokens;
- action/result one-shot session `20260808_225053_976ca7`: five provider calls,
  26,898 non-cached input, 27,136 cache-read, zero cache-write, 1,206 output, and
  231 reasoning tokens;
- rejected missing-model attempt `20260808_225004_e6af28`: zero provider and
  zero tool calls.

Both successful host sessions report `cost_status=included` and an estimated
cost of `0.0`. This means no separate usage charge was reported by the provider;
it is not interpreted as zero economic cost.

## Verification summary

```text
Standalone verifier matrix
46 passed in 59.15s

Related Gateway/runner/approval regression
61 passed in 64.13s

Full repository suite
825 passed, 12 skipped, 710 warnings in 78.86s

Exact v2 verifier under .venv-gateway
exit 0; artifact_closure_verified=true; no_workspace_writes=true
```

The final Git, secret, bundle, dependency, MCP, GitHub check, merge, and
anonymous-read evidence is recorded in the final work report because those
checks occur after this run record is written.

## Claim update and limitations

- `outcome_summary`: a bounded, exact-user-approved Crossref inspiration run
  produced one hash-verified structural hypothesis and a complete audit trail.
- `evaluation_summary`: authorization, search, passage/vector, bridge,
  transformation, deduplication, selection, cost, Artifact, and Gateway closure
  all passed independent replay in the pinned execution environment.
- `claim_update`: P3.x advances from static/live component Gates to an
  end-to-end production-profile release result.
- `baseline_relation`: additive generalization of the historical fixed offline
  pilot; the old pilot is not rewritten.
- `failure_mode`: two bridge paths lacked required SUPPORT evidence, two bodies
  were intentionally not fetched, and top-1 cannot demonstrate cross-mechanism
  selected diversity. The Hermes host act also landed in a new one-shot session
  because `-z` bypassed resume handling.
- `next_action`: downstream band-dispersion validation is required before any
  property claim; future Hermes continuations must use `chat -q --resume`.

Novelty is explicitly outside the requested scope. This run performs no
novelty, prior-art, patentability, or absence-from-literature evaluation and
makes no such claim.
