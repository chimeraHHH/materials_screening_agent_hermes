# Inspiration progress report — 2026-08-08 18:37 CST

## Verdict

P3.1 `Generalization & Reliability` is `PASSED` at the code, static lifecycle,
live Crossref, profile-bundle, and blind Skill-forward-test levels. The fixed
fixture pilot remains a replay baseline; the production profile now accepts a
strict reviewed request family and performs Crossref metadata search inside the
same approval-bound Gateway run.

The overall P3.x goal remains `IN_PROGRESS`: P3.2 multi-parent/multi-route Top-5,
P3.3 bounded multi-format fetch/tag feedback, and the final real Hermes provider
session have not yet passed.

## Time, Git, and publication state

- Work interval: 2026-08-08 17:57–18:37 CST.
- Base: `hermes-origin/main@dcd076d01061f36a2f9826deecefc274c9c3211d`.
- Branch: `agent/inspiration-generalization`.
- Covered-through pushed code/profile SHA: `8f1f14e`.
- Draft PR: [#3 Generalize and harden the Hermes inspiration workflow](https://github.com/chimeraHHH/materials_screening_agent_hermes/pull/3).
- Push target: only `hermes-origin` / public
  `chimeraHHH/materials_screening_agent_hermes`; the original upstream was not
  pushed.

Published checkpoint commits:

- `9648532` — plan, ADR, checklist, and reporting cadence;
- `be8c3ae` — bounded Crossref retry, pacing, and attempt DTO;
- `58b5dd1` — deterministic reviewed request compiler;
- `3a2a9d2` — document/passage/vector deduplication and authoritative report;
- `630b701` — production Crossref service, failure semantics, and approval CLI;
- `553bfbe` — real Crossref-in-Gateway live Gate;
- `8f1f14e` — production Hermes profile and Skill bundle.

## Implemented contract

### Deterministic request compilation

- Free-form `goal` is preserved and hashed into the approval manifest but is not
  parsed for scientific scope.
- Reviewed synonyms map only flat/narrow electronic-band constraints to the
  curated target tag.
- Material class, dimensionality, required/excluded elements, model-call budget,
  full-PDF permission, expensive-computation permission, and every bounded cost
  field have explicit execution meaning or fail closed.
- Three logical queries with one allowed retry each require room for six physical
  attempts; public-mode minimums are three documents, three passages, zero model
  calls, and 180 seconds.
- The output route remains the pinned operator-owned TiS2-to-TiSe2 route; this is
  controlled-domain generalization, not arbitrary chemistry.

### Search reliability and cost

- Retries are limited to network failures and HTTP
  `408/429/500/502/503/504`; permanent 4xx, schema drift, invalid payloads, and
  byte-budget violations do not retry.
- RFC `Retry-After` delta-seconds and HTTP-date are parsed and capped. Public
  Crossref pacing defaults to 1 request/s; an optional operator email selects
  the polite-pool interval. The raw email never enters an Artifact or component
  digest.
- Retry and pacing share a finite wait budget. Every attempt records query,
  ordinal, outcome, public error/status, response bytes, pacing delay, and retry
  delay without wall-clock timestamps.
- Transient exhaustion becomes terminal `EXTERNAL_SEARCH_UNAVAILABLE` with
  `retryable=true`; it is not converted to `SCIENTIFIC_NO_MATCH`. A new attempt
  requires a new user decision, submission ID, run, and approval.
- Static failure injection verified two 503 attempts and a durable
  `search_attempts.jsonl`. Schema drift remained nonretryable.

The retry/pacing policy follows Crossref's published pool and 429 guidance and
HTTP `Retry-After` semantics:
[Crossref REST access guidance](https://www.crossref.org/documentation/retrieve-metadata/rest-api/access-and-authentication/),
[Crossref rate-limit update](https://community.crossref.org/t/refining-rest-api-limits-for-improved-stability-and-reliability/16137),
and [RFC 9110](https://www.rfc-editor.org/rfc/rfc9110.html).

### Deduplication, lineage, and reporting

- All raw query hits remain in `search_hits.jsonl`, while the same DOI/arXiv/URL
  is processed once at document, passage, and vector layers.
- Representative processing prefers a SUPPORT-capable bridge hit and merges all
  member query/tag terms; `fetch_manifest.jsonl` preserves member hit IDs, query
  IDs, representative hit, and document ID.
- The live duplicate fixture locked 3 raw hits → 1 unique document → 1 extractor
  call → 1 passage → 1 vector without losing bridge/evidence closure.
- `search_requests` now counts physical attempts, not logical query objects.
- The report exposes the complete funnel, attempt ledger, query text/tag/rule,
  DOI/URL/raw lineage, passage locator/hash/tags, EvidenceCard hash, bridge
  invariant/control/conditions, transformation route/output hash, candidate
  merged routes/scores/redundancy, complete cost ledger, warnings, and next
  falsification step. It omits passage bodies and EvidenceCard claim bodies from
  the Markdown copy and safely encodes untrusted text.

### Hermes bundle

- Production profile factory changed from fixture to
  `create_hermes_inspiration_service`; fixture remains explicit replay only.
- Operator approval CLI defaults to `public`; old fixture tests and replay use
  `--service-mode fixture`.
- Skill, Gateway reference, SOUL mirror, README, architecture, and verifier now
  express the compiler vocabulary, minimum budget, new-run retry semantics,
  Crossref identity boundary, PDF prohibition, and scientific limitations.
- Skill Creator quick validation and deterministic SOUL verification passed.
- First blind forward test passed all supported/unsupported/transient scenarios
  and identified three nonblocking ambiguities. The Skill was tightened to use
  minimum budgets plus `top_k=1` for explicit low-cost requests, omit unstated
  material classes, and avoid creating runs known to be unsupported. A fresh
  blind retest returned `PASS` with no blocking ambiguity.

## Verification evidence

Focused implementation tests:

```text
.venv/bin/python -m pytest -q \
  tests/unit/test_inspiration_search.py \
  tests/unit/test_inspiration_request_compiler.py \
  tests/unit/test_inspiration_reporting.py \
  tests/integration/test_inspiration_runner.py \
  tests/integration/test_inspiration_runner_public.py

62 passed in 2.24s
```

Gateway lifecycle and approval tests:

```text
.venv/bin/python -m pytest -q \
  tests/integration/test_gateway_public_crossref.py \
  tests/integration/test_gateway_operator_approval.py \
  tests/integration/test_gateway_runner_e2e.py

9 passed, 1 skipped in 0.31s
```

The skip is the existing combined Gateway-MCP stdio environment Gate; static
transport uses the production factory and complete `run → grant → act → result`
lifecycle.

Hermes bundle checks:

```text
.venv/bin/python integrations/hermes/scripts/verify_bundle.py
Hermes bundle valid

.venv/bin/python /Users/yiminghua/.codex/skills/.system/skill-creator/scripts/quick_validate.py \
  integrations/hermes/profiles/materials-inspiration/skills/materials-inspiration
Skill is valid!

.venv/bin/python -m pytest -q tests/unit/test_hermes_bundle.py
2 passed in 0.06s
```

Full non-opt-in suite:

```text
.venv/bin/python -m pytest -q
715 passed, 14 skipped, 362 warnings in 15.31s
```

Observed wall time was 16.39s. Skips are explicitly gated MCP/live-provider/
real-ML environments. Warnings are known third-party pymatgen/spglib notices;
there was no test failure.

Real Crossref Gateway lifecycle:

```text
.venv/bin/python -m pytest -q --run-live-crossref \
  --basetemp workspace/p31-live-crossref-20260808 \
  tests/live/test_live_crossref_inspiration.py::test_live_crossref_runs_inside_the_approval_bound_gateway_lifecycle

1 passed in 3.18s
```

The lower-level and Gateway live tests together passed `2 passed in 4.29s`.
The detailed run record is
[`docs/runs/2026-08-08-p31-live-crossref-gateway.md`](../runs/2026-08-08-p31-live-crossref-gateway.md).

## Live metrics and hashes

- Run ID: `inspiration-f29a802d816c008e6e5a27fd`.
- Gateway terminal / bundle: `PARTIAL` / `SUCCEEDED`.
- Logical queries / HTTP attempts / retries: 3 / 3 / 0.
- Raw hits / unique documents / passages / vectors: 3 / 3 / 2 / 2.
- Search bytes / embedding tokens: 7,944 / 142.
- Plans / deduplicated candidates / selected: 1 / 1 / 1.
- Fetch / PDF / internal LLM: 0 / 0 / 0.
- Report SHA-256:
  `e38336b5646bce295aadaa6ebbd979bbbbe3237b6cda8ae6429c690ec43b505e`.
- Bundle SHA-256:
  `f5612b2ecfae8df24465309209a0141054d100bb91c482a2bfcbdab97a9255e6`.
- Stage-result SHA-256:
  `3901a0429805b17265455c3def871ca20978da5821f95a834aa979dcac1bb9d9`.
- Canonical Gateway result SHA-256:
  `bc3dd21c3ca3ee5770da01c08d384d17458a9d1e80ce84a94b51edfb4dc37b65`.

## Boundary and honest status

- Target property remains `UNKNOWN`; `STRUCTURE_VALID` is structural QC only.
- `scientific_conclusion=false` throughout the bundle and Gateway projection.
- Full-PDF reads and internal LLM calls are zero.
- No novelty, patent, prior-art, or validated-property conclusion is produced.
- The live result has only one candidate and one supported mechanism. It does
  not satisfy or claim the P3.2 Top-5 diversity Gate.
- Runner body-fetch integration and TagGraph feedback are still absent and are
  P3.3 work.
- The production profile is statically validated, but the final real Hermes
  provider session using Crossref remains a release Gate after P3.2/P3.3.

## Decision and next Gate

Decision: `GO` to P3.2. P3.1 refutes the hypothesis that request paraphrases,
same-run Crossref, bounded retry, document-level processing deduplication, and
strict approval/provenance cannot coexist. It does not address the strongest
remaining alternative explanation—success may still come from one pinned parent
and one substitution route.

Next Gate: freeze an operator-owned parent catalog and a multi-parent,
multi-route engineering calibration set that yields at least five structurally
valid proposals, merges identical structures with all route lineage, and proves
Top-5 exact/strict duplicates are zero with at least two mechanisms when the pool
supports them.

