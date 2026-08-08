# Inspiration progress report — 2026-08-08 20:23 CST

## Verdict

P3.3 `Passage & Tag Feedback` is `PASSED`. The inspiration runner now has a
metadata-first, approval-bound document-fetch seam; bounded JSON-LD/Highwire,
JATS/XML, and ordinary HTML processing; selected-passage-only vectorization;
physical fetch accounting; and immutable query/tag/bridge feedback with
deterministic replay and fail-closed operational error semantics.

The overall P3.x goal remains `IN_PROGRESS`. The final real Hermes
natural-language Crossref lifecycle must still create an exact pending
interaction, show its execution-manifest hash to the user, receive a fresh grant
bound to that interaction, and complete `act → result`. A pre-run blanket
authorization cannot safely authorize an unknown future manifest.

## Time, Git, and publication state

- Work interval: 2026-08-08 19:41–20:23 CST.
- Base: `hermes-origin/main@dcd076d01061f36a2f9826deecefc274c9c3211d`.
- Branch: `agent/inspiration-generalization`.
- Covered-through pushed code/profile SHA: `697c297`.
- Draft PR:
  [#3 Generalize and harden the Hermes inspiration workflow](https://github.com/chimeraHHH/materials_screening_agent_hermes/pull/3).
- Push target: only `hermes-origin` / public
  `chimeraHHH/materials_screening_agent_hermes`; the original upstream was not
  pushed.

Published checkpoint commits in this interval:

- `1807da0` — froze the P3.3 experiment and failure contract;
- `fb98ab7` — hardened the pure multi-format extractors;
- `57f0314` — pinned the multiformat body fixtures and manifest;
- `e426ee3` — added strict immutable yield feedback;
- `a494857` — added disabled, fixture, and bounded network fetch implementations;
- `471d881` — integrated fetch lineage, feedback, report, and Gateway projection;
- `6f976b3` — added shared-document, transient-failure, and shared-budget Gates;
- `d2e4067` — froze P3.3 evidence boundaries in the Hermes profile;
- `697c297` — closed feedback-projection, expert-state, DNS, manifest-component,
  and live retry-accounting gaps.

## What changed

### Metadata-first passage path

- `DisabledDocumentFetcher` remains the default. Production request compilation
  fixes body requests/total bytes/per-response bytes to `0/0/0`; network search
  is still bounded Crossref metadata and abstracts only.
- Body retrieval is attempted only for a canonical document whose metadata is
  insufficient and whose frozen policy, URL, host, media type, and shared
  physical budgets permit it. Metadata-sufficient documents record a skip and
  consume no body request.
- Successful untrusted bytes are persisted and SHA-verified before extraction.
  Metadata passages point to raw-search Artifacts; body passages point to the
  exact fetched Artifact.
- JSON metadata, JSON-LD, Highwire metadata, JATS XPath, and ordinary HTML CSS
  locators pass through one runner seam. PDF MIME/magic, DTD/ENTITY-bearing XML,
  unsupported media, oversize responses, unsafe URLs, redirects, and network
  failures fail closed.
- Canonical document identity is deduplicated before fetch and extraction while
  retaining all raw-hit and query lineage. Only title, optional heading, selected
  passage, and normalized tags enter signed vector input.

### Immutable feedback

- `tag_feedback.json` is a strict internal Artifact bound to the frozen TagGraph,
  all input pointers/fingerprint, compiler snapshot, query plan, search/fetch
  attempts, passages, vectors, EvidenceCards, BridgePackets, and cost ledger.
- It publishes deterministic query/tag/bridge yields and physical cost
  allocations with `INCLUSIVE_NON_ADDITIVE` semantics; the `CostLedger` remains
  the only additive total.
- Its v1 wire boundary is `REVIEW_ONLY`, `expert_status=UNKNOWN`,
  `applies_to_tag_graph=false`, and `scientific_conclusion=false`. V1 accepts no
  expert-review input and contains no graph mutation operation.
- The compiler and injected fetcher enter the approval execution-component
  manifest. Production manifests now explicitly assert
  `disabled-document-fetcher` and `inspiration-tag-feedback-compiler`.
- `materials_result_get` returns the feedback Artifact pointer/hash through
  lineage, not the feedback rows. The Hermes Skill forbids claiming those rows
  were read.

## P3.3 evidence

The durable offline Gate executes one direct and all three reviewed bridge
queries. One metadata-sufficient direct document consumes zero body requests;
three metadata-insufficient bridge documents consume exactly three requests and
5,014 bytes.

| Metric | Result |
|---|---:|
| Search queries / attempts / bytes | 4 / 4 / 2,543 |
| Raw / unique documents | 4 / 4 |
| Body fetches / accepted bytes | 3 / 5,014 |
| Passages / vectors / embedding tokens | 6 / 6 / 270 |
| EvidenceCards / BridgePackets | 5 / 3 |
| Query / tag / bridge feedback rows | 4 / 12 / 3 |
| Reviewed bridges with primary calibration support | 3 / 3 |
| PDF reads | 0 |
| Internal LLM calls / input / output | 0 / 0 / 0 |

All three bridge rows are `SEARCH_SUPPORTED`, `UNKNOWN`, and review-only in the
primary calibration. Separate controls prove `EVIDENCE_INSUFFICIENT`, one fetch
for a DOI shared by two queries, inclusive attribution without double-processing,
shared physical request-budget exhaustion, and `EXTERNAL_FETCH_UNAVAILABLE` for
three transient body failures without a false scientific no-match.

Two independent workspaces reproduce every stage file byte-for-byte. The
TagGraph SHA remains
`8f2a4b40f6857b34e3827987155582f66a3cda68ec1de507b6cedee980320917`
before, after, and during replay. Primary feedback/report/bundle/stage hashes are:

- feedback: `d0b471cdf10ba9668f12b78b190717d16e3e0e304830fa8fafd1fa780d910293`;
- report: `8eb12e771d28a4670cde445841de66081cff2df8146a2300a8616ff2000b2158`;
- bundle: `0111f2538bfc59fe4efdb54aec4967e9d906160561dbcacb3d0edc533488a78f`;
- stage result: `ff9f88e78680bfba72db85d699c8e88979efcc00516e1a2af71d5e6b45591866`.

The full command, fixtures, fault results, and Artifact table are in
[`docs/runs/2026-08-08-p33-passage-tag-feedback.md`](../runs/2026-08-08-p33-passage-tag-feedback.md).

## Verification evidence

Broad P3.3/P3.2 regression:

```text
79 passed, 1 skipped in 4.28s
```

Final P3.3/profile targeted regression:

```text
40 passed in 1.83s
Hermes bundle valid
```

Complete non-live suite and environment checks:

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
```

Opt-in live Crossref regression:

```text
3 passed in 10.71s
```

The final live runner produced one candidate, two Passages, two EvidenceCards,
one supported bridge, one structure-valid proposal, 7,944 accepted search bytes,
zero body fetches, zero PDFs, and zero internal model calls.

## Failures found and repaired

- The first post-P3.3 live Gateway test still expected three raw query Artifacts,
  but P3.2 production compilation now plans four. It had already produced all
  four real Crossref responses; the test now binds four query-plan rows to four
  raw Artifacts.
- A second live test equated logical raw responses with physical attempts. A real
  Crossref 429 followed by a successful retry exposed the mistake: three query
  responses and four physical attempts are both correct. The test now reconciles
  query plans/raw Artifacts separately from the attempt ledger. The final rerun
  happened without a retry and passed.
- Independent review found that the initial profile wording implied a future
  expert Artifact could enter the current schema. Documentation and verifier now
  state the actual v1 fact: all expert fields are literal `UNKNOWN`, and v1
  accepts no expert-review input.
- Independent security review found that DNS preflight and the later urllib
  hostname connection are not address-pinned. Public body fetch therefore stays
  disabled; fixture coverage is not described as DNS, TLS, or peer-binding proof.

## Boundary and honest status

- `STRUCTURE_VALID` is structural QC only. Target material properties remain
  `UNKNOWN`; every P3.3 result has `scientific_conclusion=false`.
- Search support is support for a bridge hypothesis, not proof that a proposed
  material realizes the target property.
- No novelty, prior-art, patentability, or validated-property conclusion is made.
- Public-network body fetching is not released. The current safe default is
  Crossref metadata/abstract-only; PDF reads remain zero.
- Feedback is an engineering review Artifact, not expert acceptance and not an
  online TagGraph optimizer.
- The historical real Hermes fixture session and direct live Crossref tests do
  not replace the required final real Hermes natural-language Crossref session.

## Decision and next Gate

Decision: `GO` to the final release Gate. P3.3 supports the engineering
hypothesis: bounded offline multi-format passage processing and immutable yield
feedback can be integrated without increasing production body access, exposing
full text to embedding/LLM, mutating the TagGraph, or conflating provider failure
with scientific absence.

Next actions are narrowly ordered:

1. synchronize the installed Hermes profile with the tracked production factory
   and re-run the four-tool MCP check;
2. start exactly one new natural-language Hermes submission and stop at
   `INTERACTION_REQUIRED`;
3. show the exact run ID, interaction ID, approval question, action, and frozen
   execution-manifest hash to the user;
4. only after that exact approval, issue the one-time operator grant, resume the
   same session, execute Crossref, retrieve the verified result, and finish the
   GitHub release evidence.
