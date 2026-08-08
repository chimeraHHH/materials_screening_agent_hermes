# Standalone completed-run verifier auxiliary experiment — 2026-08-08

## Verdict

Classification: `SUPPORTED` for the bounded engineering hypothesis on a static
Crossref transport through the production component path. A standalone,
read-only verifier accepted the normal completed workspace and a canonical
bounded network-retry workspace without changing either, verified the bounded
terminal-warning projection, and rejected all 43 negative/adversarial variants
in a 46-case representative Gate.

This is an `auxiliary/dev` experiment, not the final release experiment. It is
not a live Crossref run. At the time it was frozen, it was not verification of
the then-pending v2 terminal workspace; the later exact-terminal application is
recorded separately in the final v2 run record.

## Experiment contract

### Hypothesis

- Null hypothesis: an independent closeout verifier cannot distinguish a valid
  completed inspiration workspace from representative affected-pointer-chain
  rewrites or cross-wired tampering without trusting the already-persisted
  Gateway projection or writing to the historical workspace.
- Alternative hypothesis: a standalone verifier can reconstruct the approved
  binding and deterministic production pipeline from frozen inputs, accept the
  legitimate result without any workspace write, and fail closed on
  representative database, Artifact-closure, content-boundary, cost, vector,
  and lineage attacks.
- Research question: can final release verification become a repeatable,
  independent, no-write Gate rather than a manual inspection of
  `materials_result_get`?
- Objective: produce the smallest verifier and adversarial matrix sufficient to
  test that engineering claim without modifying the five frozen execution
  components, the Hermes profile, or the pending v2 run.

Stop condition: one legitimate static-production-path workspace passes with a
stable JSON summary and an identical before/after filesystem snapshot, while
every declared adversarial case exits nonzero. Abandonment condition: any
workspace mutation, dependence on writable Gateway state, manifest drift, or
need to weaken the frozen execution contract.

## Accepted baseline

The accepted baseline was the current public inspiration runner, Gateway
lifecycle, approval/grant store, deterministic component snapshots, and
Artifact schemas. Existing tests validated most invariants while results were
created, and `materials_result_get` revalidated selected terminal pointers, but
there was no independent post-completion replay of the entire raw-search to
candidate/report/Gateway chain.

The verifier is therefore an additional release observer. It does not replace
or modify the baseline runner, search adapter, vectorizer, transformation
engine, feedback compiler, Gateway service, Hermes Skill, or persisted result.

## Scope and implementation

The implementation under test is
`integrations/hermes/scripts/verify_completed_inspiration_run.py`, exercised by
`tests/integration/test_completed_inspiration_run_verifier.py`. The fixture uses
a static Crossref response while reconstructing the current production public
Crossref configuration, query planner, metadata/abstract parser, signed-hashing
vectorizer, parent catalog, transformation engine, feedback compiler, report,
and Gateway projection.

The verifier performs the following bounded checks:

- snapshots the project root, directories, and files before and after the run,
  including type, mode, size, timestamps, inode/link state, and file hashes;
- rejects symlinks, hard links, time-of-check/time-of-use changes, orphan files
  or directories, and non-checkpointed SQLite WAL state;
- opens Gateway and grant databases in immutable/query-only mode and validates
  integrity, foreign keys, schema, canonical JSON, terminal revision, exactly
  one run/result, and exactly one consumed approval grant with no recovery;
- reconstructs the request, interaction, action, grant, and execution-manifest
  bindings from the current frozen inputs and execution components;
- enforces an exact Artifact allowlist and recursively verifies every pointer,
  hash, size, canonical role URI/media type, lineage edge, canonical runner
  lineage order, deterministic bundle/stage identity, and stage/bundle closure;
- enforces the current public Crossref response envelope and requested-field
  allowlist, `rows=1`/exactly-one-item contract, 20,000-character raw-abstract
  ceiling, per-field/response byte ceilings, retry count,
  transient-only retry sequence, retry-delay ceiling, and total-wait ceiling;
- rejects declared fetched-body/PDF Artifacts, forbidden or out-of-allowlist
  Crossref fields, raw PDF signatures, PDF data/signature markers and
  document-level HTML markers in bounded allowed strings, internal model use,
  and budget or cost-ledger drift;
- compares non-model audit Artifacts against replayed canonical bytes so JSON
  `false/0`, `true/1`, and integer/float type confusion cannot pass;
- binds the exact bounded operator confirmation reference in addition to the
  action, interaction, request, and manifest hashes;
- deterministically replays query planning, Crossref parsing, document grouping,
  metadata passage extraction, fetch manifest, signed vectors, EvidenceCards,
  BridgePackets, transformations/CIFs, candidate identity/deduplication/MMR,
  selection audit, ledger, tag feedback, report, bundle, stage result, and
  Gateway result projection.

The verifier is deliberately outside the frozen execution-component manifest.
It observes a completed run; it cannot authorize, resume, repair, or rewrite
one.

## Representative Gate matrix

The 37 test functions expand to 46 pytest cases because canonical Gateway JSON,
embedded/unknown body fields, strict search-attempt field types, fetch-manifest
type confusion, allowed-abstract payloads, and confirmation references are
parameterized. Three cases are positive controls and 43 are negative or
adversarial.

| Case | Workspace condition | Expected result | Result |
|---:|---|---|---|
| 1 | Valid completed static-production-path run | Pass; stable JSON summary; byte/metadata snapshot unchanged | Passed |
| 2 | Pending run with no terminal result | Reject as not result-bearing terminal state | Rejected |
| 3 | Report bytes changed after publication | Reject Artifact size/hash mismatch | Rejected |
| 4 | Undeclared file added under the stage | Reject orphan file | Rejected |
| 5 | Declared stage ancestor replaced by a symlink | Reject symlinked closure | Rejected |
| 6 | Non-empty, uncheckpointed Gateway WAL | Reject mutable/uncheckpointed database state | Rejected |
| 7 | CLI expected manifest replaced with another digest | Reject approved-manifest mismatch | Rejected |
| 8 | Manifest, interaction, action, and grant consistently forged together | Reject against recomputed current frozen components | Rejected |
| 9 | Gateway run `record_json` semantically valid but non-canonical | Reject non-canonical persisted run JSON | Rejected |
| 10 | Gateway result `result_json` semantically valid but non-canonical | Reject non-canonical persisted result JSON | Rejected |
| 11 | Empty undeclared directory added under the stage | Reject orphan directory | Rejected |
| 12 | Raw Crossref item embeds a `full_text` body | Reject forbidden body field | Rejected |
| 13 | Raw Crossref item embeds a `pdf_base64` payload | Reject forbidden PDF/body field | Rejected |
| 14 | Embedding-token cost and its affected cost/bundle pointers are rewritten | Reject deterministic cost-ledger replay mismatch | Rejected |
| 15 | Fetched HTML body is declared and re-hashed into stage/bundle lineage | Reject fetched-body Artifact in metadata-only mode | Rejected |
| 16 | Raw Artifact is replaced with a padded/BOM-prefixed PDF signature and re-hashed | Reject PDF signature bytes | Rejected |
| 17 | Vector bytes plus affected vector/manifest pointers and the enclosing bundle pointer are rewritten | Reject signed-vector deterministic replay mismatch | Rejected |
| 18 | Passage is cross-wired to a different hit for the same grouped document | Reject raw-response-to-passage lineage replay mismatch | Rejected |
| 19 | Candidate and merged route are cross-wired to a different valid plan inside the bundle, whose pointer is updated | Reject candidate identity/deduplication/MMR replay mismatch | Rejected |
| 20 | Raw abstract exceeds 20,000 characters while the whole response stays within its byte cap | Reject before accepting the pipeline's parsed truncation | Rejected |
| 21 | Stage `result_id` is replaced while all bytes remain otherwise valid | Reject deterministic stage-result identity mismatch | Rejected |
| 22 | Bundle `bundle_id` is replaced and its enclosing pointer is re-hashed | Reject deterministic bundle identity mismatch | Rejected |
| 23 | Canonical bundle bytes are moved to a different declared stage URI | Reject noncanonical role URI | Rejected |
| 24 | Raw Crossref JSON is padded beyond the frozen one-response byte ceiling and re-hashed | Reject raw response byte-bound violation | Rejected |
| 25 | A query ledger is synchronously expanded to three contiguous attempts | Reject the frozen one-retry/two-attempt ceiling | Rejected |
| 26 | Stage and bundle lineage are both reversed, then bundle/stage IDs are consistently recomputed | Reject noncanonical runner lineage order | Rejected |
| 27 | A query has one canonical `NETWORK_ERROR` attempt followed by a successful retry | Pass the legal bounded-retry control without writes | Passed |
| 28 | A failed attempt uses a forged transient error code before success | Reject noncanonical transient classification | Rejected |
| 29 | A `NETWORK_ERROR` retry records zero retry delay | Reject the frozen network-retry delay violation | Rejected |
| 30 | Valid attempts are reordered across query boundaries | Reject noncanonical global query-plan order | Rejected |
| 31 | The first physical request records nonzero pacing delay | Reject pacing that cannot arise from the frozen adapter | Rejected |
| 32 | `attempt_number` is JSON boolean `true` | Reject bool-as-integer type confusion | Rejected |
| 33 | `http_status` is JSON float `200.0` | Reject noncanonical numeric field type | Rejected |
| 34 | `response_bytes` is represented as a JSON float | Reject noncanonical numeric field type | Rejected |
| 35 | Unknown Crossref `payload` field carries an HTML body | Reject outside the exact requested metadata allowlist | Rejected |
| 36 | Unknown Crossref `blob` field carries a base64 PDF | Reject outside the exact requested metadata allowlist | Rejected |
| 37 | Selection audit changes numeric zero to JSON `false` and recomputes the affected intermediate pointer, bundle ID/pointer, and stage result ID | Reject canonical-byte replay mismatch | Rejected |
| 38 | Fetch manifest changes physical-request zero to JSON `false` and recomputes the same affected Artifact-to-stage chain | Reject canonical-byte replay mismatch | Rejected |
| 39 | Fetch manifest changes selected-passage one to JSON `true` and recomputes the same affected Artifact-to-stage chain | Reject canonical-byte replay mismatch | Rejected |
| 40 | Grant confirmation is whitespace and its grant ID is recomputed | Reject the public confirmation-reference contract | Rejected |
| 41 | Grant uses another valid confirmation and recomputes its ID | Reject mismatch with the expected operator reference | Rejected |
| 42 | Forty stage warnings are projected through the production 32-warning terminal bound | Pass the exact bounded-warning projection contract | Passed |
| 43 | An allowed `abstract` contains a PDF data URI/base64 signature marker | Reject the embedded PDF marker | Rejected |
| 44 | An allowed `abstract` contains document-level HTML | Reject the document-level markup marker | Rejected |
| 45 | Crossref claims `items-per-page=2` although the frozen request uses `rows=1` | Reject the response/request contract mismatch | Rejected |
| 46 | Crossref returns an unconsumed second item | Reject anything beyond the exactly one requested item | Rejected |

Cases 37–39 recompute the affected intermediate pointer, bundle ID/pointer, and
stage result ID. They do not claim to forge the persisted Gateway database
projection. Other rewrite/crosswire probes update only the closure layer needed
to reach their target guard.

This is a representative adversarial Gate, not a claim that every individual
guard branch has its own fault-injection case.

## Commands and results

Targeted verifier experiment:

```text
.venv-gateway/bin/python -m pytest -q \
  tests/integration/test_completed_inspiration_run_verifier.py

46 passed in 59.15s
```

Related Gateway/runner/approval regression group:

```text
.venv-gateway/bin/python -m pytest -q \
  tests/integration/test_completed_inspiration_run_verifier.py \
  tests/integration/test_gateway_mcp.py \
  tests/integration/test_gateway_operator_approval.py \
  tests/integration/test_gateway_public_crossref.py \
  tests/integration/test_gateway_runner_e2e.py \
  tests/integration/test_inspiration_runner_public.py

61 passed in 64.13s
```

The lock-synchronized Gateway environment includes the optional MCP SDK and
combined Gateway-MCP stdio dependencies, so those cases ran rather than
skipping.

Full non-opt-in repository regression:

```text
.venv-gateway/bin/python -m pytest -q

825 passed, 12 skipped, 710 warnings in 78.86s
```

All three results were captured after the review-driven warning, identity,
response/retry-bound, canonical-URI, and canonical-lineage fixes. They establish
the static auxiliary Gate; they do not substitute for verification of the exact
v2 terminal workspace.

## Six-field evaluation

| Field | Evaluation |
|---|---|
| `outcome_summary` | Two legitimate completed workspaces and the bounded terminal-warning projection passed without writes; all 43 representative negative/adversarial variants failed closed. |
| `evaluation_summary` | The static production-component path demonstrates independent replay of authorization, Artifact closure, costs, scientific boundaries, deterministic intermediates, and terminal projection. |
| `claim_update` | The claim advances from “a closeout verifier is missing” to “the standalone verifier mechanism is supported on a representative static-path Gate”; no live-release claim is added. |
| `baseline_relation` | Additive observer only: the accepted production runner/Gateway baseline and its five frozen execution components remain unchanged. |
| `failure_mode` | No tested adversarial variant escaped detection. Residual risk is untested guard combinations or divergence that appears only on the exact live terminal workspace. |
| `next_action` | Completed separately: exact v2 reached terminal state in the same Gateway run/manifest/grant, although `act` landed in a new one-shot Hermes host session; the pinned verifier then passed with no writes. |

## Classification and limitations

The alternative engineering hypothesis is `SUPPORTED` at the auxiliary/dev
evidence tier. This classification is intentionally narrower than release
acceptance.

- The Crossref transport in this experiment is static; it performs no public
  network request and cannot establish live provider behavior.
- At the time of this auxiliary experiment, the v2 workspace was still
  `INTERACTION_REQUIRED` and had no result. This experiment issued no grant or
  approval action. Its later exact-terminal application is recorded separately
  in the final v2 run record.
- The matrix is representative rather than exhaustive for every guard and every
  composition of attacks.
- Passing the verifier establishes engineering consistency and provenance only.
  It does not validate any proposed material property; property status remains
  `UNKNOWN` and `scientific_conclusion=false` remains mandatory.
- Provider credentials, billing truth, TLS/DNS behavior, and public-service
  availability are outside this static experiment.

Novelty is explicitly out of scope. This experiment performs no novelty,
prior-art, patentability, or absence-from-literature evaluation and makes no
such claim.

## Runtime boundary discovered during release application

The final representative pytest matrix and exact historical replay both run in
the lock-synchronized Gateway environment. An exact historical release must be
replayed with that pinned runtime used for execution:

```text
.venv-gateway/bin/python \
  integrations/hermes/scripts/verify_completed_inspiration_run.py ...
```

During exact v2 closeout, accidentally invoking the verifier with `.venv` failed
closed at transformation-plan replay. That development environment had been
augmented by the independent Agent02 stack with `pymatgen-core==2026.7.31` and
`pymatgen-io-validation==0.1.3`; the execution environment had
`pymatgen==2025.10.7`, no split `pymatgen-core` distribution, and
`pymatgen-io-validation==0.1.2`. Re-running in `.venv-gateway` passed the full
closure. The failure was therefore useful environment-drift detection, not an
Artifact inconsistency. The Gateway bootstrap now uses exact lock synchronization
so unrelated packages cannot remain installed.
