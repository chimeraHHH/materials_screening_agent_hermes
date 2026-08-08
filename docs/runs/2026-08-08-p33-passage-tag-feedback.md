# P3.3 passage and tag-feedback Gate — 2026-08-08

## Scope

This is the frozen P3.3 engineering Gate for metadata-first, bounded passage
extraction and immutable query/tag/bridge feedback. It exercises the complete
offline runner seam from search hit through optional body fetch, persisted source
Artifact, multi-format extraction, selected-passage-only vectorization,
EvidenceCard, BridgePacket, feedback, report, bundle, and verified stage result.

The bodies are operator-owned fixtures. This Gate does not establish that
public-network article fetching is enabled or safe. The production Hermes
compiler remains Crossref metadata/abstract-only with a zero body-fetch budget.

## Git and commands

- Branch: `agent/inspiration-generalization`.
- Implementation/profile checkpoint:
  `697c297da83d4a87a4beb8b346fdd8c433077c4d`.
- Baseline: P3.2 checkpoint `fc1951b`.
- Public draft PR:
  [#3 Generalize and harden the Hermes inspiration workflow](https://github.com/chimeraHHH/materials_screening_agent_hermes/pull/3).

Durable offline main Gate:

```text
.venv/bin/python -m pytest -q \
  --basetemp /Users/yiminghua/2026Summer/FDU/materials_screening_agent/workspace/p33-main-gate-20260808.kOWdtJ \
  tests/integration/test_inspiration_runner_multiformat.py

4 passed in 0.23s
```

The durable primary replay pair is under
`workspace/p33-main-gate-20260808.kOWdtJ/test_offline_runner_fetches_on0/{first,second}`.
The same basetemp also retains the shared-document, request-budget, and transient
failure runs.

Broad P3.3/P3.2 regression:

```text
.venv/bin/pytest -q \
  tests/unit/test_inspiration_fetch.py \
  tests/unit/test_inspiration_extractors.py \
  tests/unit/test_inspiration_feedback.py \
  tests/unit/test_inspiration_reporting.py \
  tests/unit/test_inspiration_vectorizer.py \
  tests/unit/test_hermes_bundle.py \
  tests/integration/test_inspiration_runner.py \
  tests/integration/test_inspiration_runner_public.py \
  tests/integration/test_inspiration_runner_multiformat.py \
  tests/integration/test_gateway_runner_e2e.py \
  tests/integration/test_gateway_public_crossref.py \
  tests/integration/test_gateway_parent_catalog_top5.py

79 passed, 1 skipped in 4.28s
```

The single skip is the explicitly isolated combined-Gateway-MCP stdio test.

## Frozen inputs

| Fixture | SHA-256 | Bytes |
|---|---|---:|
| Body manifest | `8ecdc3513c9856bd7c9192b7e0bc461391503a1dc850a0853c2e7e66eb964298` | 1,163 |
| Structured HTML / JSON-LD / Highwire | `0b51c83a361549d4f16207b3c7be503458b77d856b00213f3cddb12430a2514f` | 1,893 |
| JATS/XML | `7a3aa4e33e54102506a99692be529eeb4ea2407d85cfac0b308e0279db99a3e5` | 1,738 |
| Ordinary HTML | `961d1cd1817af4c5f93d7b3dd2a12b44b640b337e34f388cd3e26ad21fedd487` | 1,383 |
| Standalone JSON-LD extractor fixture | `7b3f11a5bfa6e301576d13e2729887b27d4c3092be94ff7d259787e1dc591132` | 552 |

Every fixture is `ENGINEERING_ONLY`, has expert status `UNKNOWN`, and carries no
scientific or novelty conclusion.

## Primary run result

- Project/request/run:
  `project-p33-multiformat` / `request-p33-multiformat` /
  `run-p33-multiformat`.
- Result ID: `inspiration-result-dbd6bac985b35f5330c646ad`.
- Stage/bundle outcome: `SUCCEEDED` / `SUCCEEDED`.
- Scope: `STRUCTURE_PROPOSALS_REQUIRE_DOWNSTREAM_VALIDATION`.
- Warnings: one expected target-only `NO_MECHANISM_TAG` and
  `STRUCTURED_METADATA_SUFFICIENT`.

| Metric | Value |
|---|---:|
| Logical search queries / physical search attempts | 4 / 4 |
| Search hits / raw documents / unique documents | 4 / 4 / 4 |
| Search response bytes | 2,543 |
| Metadata-sufficient documents skipped before body fetch | 1 |
| Successful body requests / accepted body bytes | 3 / 5,014 |
| Extracted / vectorized passages | 6 / 6 |
| Embedding input tokens | 270 |
| EvidenceCards / BridgePackets | 5 / 3 |
| Structure-valid proposals / selected candidates | 1 / 1 |
| Full-PDF reads | 0 |
| Internal LLM calls / input / output tokens | 0 / 0 / 0 |

The locator set is `JSON_PATH`, `JSON_LD`, `HTML_META`, `JATS_XPATH`, and
`CSS_SELECTOR`. The vector test reconstructs every signed input from only the
title, optional heading, selected passage, and normalized tags. The embedded
prompt-injection sentence remains untrusted source data and does not enter the
report or alter policy, tools, fetch targets, or TagGraph.

## Feedback result

- Feedback ID: `tag-feedback-a3a7351fa98a79d832728c6f`.
- Rows: 4 query, 12 tag, 3 bridge.
- All three reviewed bridges are `SEARCH_SUPPORTED` in the primary calibration.
- Wire boundary: `review_disposition=REVIEW_ONLY`,
  `expert_status=UNKNOWN`, `applies_to_tag_graph=false`,
  `scientific_conclusion=false`, and
  `aggregation_semantics=INCLUSIVE_NON_ADDITIVE`.
- Shared requests/documents may be attributed to multiple rows; only the
  `CostLedger` is additive.
- V1 accepts no expert-review input and contains no replacement graph or mutation
  operation. The input, output, and replay TagGraph SHA-256 is unchanged:
  `8f2a4b40f6857b34e3827987155582f66a3cda68ec1de507b6cedee980320917`.

The v1 `materials_result_get` projection exposes the feedback Artifact pointer
and hash, not its rows. A Hermes conversation must not claim to have inspected
those rows.

## Fault, deduplication, and budget controls

- Same DOI in two bridge queries: four raw hits collapse to three unique
  documents; the shared document preserves both query lineages, is fetched once,
  and is inclusively attributed once to each query row. The run performs two
  physical fetches and accepts 3,276 bytes. The acoustic bridge is
  `SEARCH_SUPPORTED`; the magnon bridge is `EVIDENCE_INSUFFICIENT`, proving that
  document identity does not fabricate cross-rule evidence.
- Shared body request budget of one: exactly one physical request succeeds, two
  body-eligible documents become `REQUEST_BUDGET_EXHAUSTED`, and the
  metadata-sufficient document remains skipped.
- Three fixture HTTP 503 responses: all attempts are recorded as
  `TRANSIENT_HTTP_ERROR`; no `stage_result.json` or `tag_feedback.json` is
  published, and the runner raises `EXTERNAL_FETCH_UNAVAILABLE` rather than a
  scientific no-match.
- Unit fault injection separately covers exact-host HTTPS policy, userinfo and
  non-default-port rejection, redirect limits, private/reserved addresses,
  content type, PDF MIME/magic mismatch, `Content-Length`, streamed `max+1`,
  request/byte/wait budgets, and bounded retry.

## Replay and authoritative hashes

The complete primary stage directories in `first` and `second` are byte-identical.

| Artifact | SHA-256 |
|---|---|
| Search attempts | `356ef9570709ff5e0bebd5e42923dd82b84bfaa183ad7b08419dc1dcd37f523e` |
| Fetch attempts | `2e0dbf3e70463ec0fc9c226cbb8e00a9aabf2eb7f5b7e18c6bcf14e30ad01749` |
| Fetch manifest | `4a0bfed6742ca3d98ecbfb265cd7ee22c5e2a1c4e3f90bcbf05a8ec070964034` |
| Passages | `da1605504b80d5354ee271541cb4cccffe6325e2f2e772a960c419fe34fe642e` |
| Passage vectors | `ea8de320a80a67d1940d6576bd443e2e4e33133307d2bb10b0f2be8a06a2acbc` |
| EvidenceCards | `97024f0e45d23fcd2e5d6a500a20837825d3ea155d7558903b6e0ae417696176` |
| BridgePackets | `c86bcb6129dc93398c156af66653a64cbeaef932887e27ad579b639322c88d31` |
| Cost ledger | `f068d429b87a14b8671c844dbc28608dce00f5445913727cfca04ae7b9e38742` |
| Tag feedback | `d0b471cdf10ba9668f12b78b190717d16e3e0e304830fa8fafd1fa780d910293` |
| Report | `8eb12e771d28a4670cde445841de66081cff2df8146a2300a8616ff2000b2158` |
| Inspiration bundle | `0111f2538bfc59fe4efdb54aec4967e9d906160561dbcacb3d0edc533488a78f` |
| Stage result | `ff9f88e78680bfba72db85d699c8e88979efcc00516e1a2af71d5e6b45591866` |

## Release-width regression

```text
.venv/bin/python -m pytest -q -p no:cacheprovider
776 passed, 14 skipped, 362 warnings in 17.25s

.venv/bin/python -m compileall -q src tests integrations/hermes/scripts
.venv/bin/python -m pip check
No broken requirements found.

uv pip check --python .venv-gateway/bin/python
Checked 106 packages; all installed packages are compatible.

uv pip check --python .venv-hermes/bin/python
Checked 71 packages; all installed packages are compatible.

.venv/bin/python integrations/hermes/scripts/verify_bundle.py
Hermes bundle valid
```

The 14 skips are explicit isolated MCP, opt-in provider, and real-ML Gates. The
warnings are existing pymatgen/spglib notices.

Opt-in public Crossref regression after repairing two stale retry/query-count
assertions:

```text
.venv/bin/python -m pytest -q -s --run-live-crossref \
  --basetemp /Users/yiminghua/2026Summer/FDU/materials_screening_agent/workspace/p33-live-crossref-final-20260808.sw8O3m \
  tests/live/test_live_crossref_inspiration.py \
  tests/live/test_live_crossref_inspiration_runner.py

3 passed in 10.71s
```

The live runner summary contains one candidate, two passages, two EvidenceCards,
one bridge, one proposal, 7,944 accepted response bytes, three physical search
requests, zero retries in the final run, and bundle/stage hashes
`27e90dd96778c73ed0eef0dbf856b4fc20341d82487e14067dad242ceafbfdea` /
`fc5820c1de9575fc1d9edcbaa7dc5e07c57f2dd72613792acd8a8cce5abe029a`.
The production path performs zero body fetches, PDF reads, or internal model calls.

## Boundary and verdict

Verdict: `SUPPORTED` for the P3.3 engineering claim. The runner can skip fetch
when metadata is sufficient, process three bounded offline body formats, persist
and verify source lineage, vectorize selected passages only, keep shared work
deduplicated, publish immutable non-additive feedback, and fail operationally
without fabricating scientific absence.

Public-network body fetching remains disabled. The current DNS preflight and
later hostname connection are separate operations, so they do not close DNS
rebinding/TOCTOU or prove peer-address binding. The offline fixture transport
also does not test DNS, TLS, or peer identity.

All proposed material properties and expert judgments remain `UNKNOWN`;
`scientific_conclusion=false`. This Gate makes no novelty, prior-art, patent, or
validated-property claim. The next Gate is a real Hermes natural-language
Crossref session with an exact post-interaction user grant.
