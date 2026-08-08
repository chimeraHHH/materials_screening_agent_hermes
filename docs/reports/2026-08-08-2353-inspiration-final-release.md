# Inspiration final release-candidate report — 2026-08-08 23:53 CST

## Verdict

The P3.x inspiration-generator engineering claim is `SUPPORTED` for the scoped,
single-user Hermes beta. The system now converts supported natural-language
flat/narrow-band requests into an approval-bound, budgeted Crossref metadata
search, selected passage vectors, evidence/bridge records, deterministic
operator-owned structure transformations, run-internal deduplication/diversity
selection, and a hash-closed review result.

The exact approved release run reached Gateway `PARTIAL` with a bundle-level
`SUCCEEDED` outcome. `PARTIAL` records four evidence warnings; it is not a
scientific failure and must not be rewritten as full scientific validation.
The selected material property remains `UNKNOWN` and
`scientific_conclusion=false`.

Novelty, prior art, patentability, and absence-from-literature analysis are
explicitly out of scope.

## Exact authorization and execution

- run: `inspiration-2c470e4810392aca2c9a7c4d`;
- request SHA-256:
  `0598117ef45e17ec44f328f3effff5722166e2df589b8695ff0ff1c5a25220c6`;
- interaction: `interaction-f2d2ab3eaa402a5ed4481e69`;
- interaction SHA-256:
  `e4124c90db33375f83ea87af04f937fb791130d3933c083224c2d9c52fc972e0`;
- execution-manifest SHA-256:
  `e6b0d901fa0a8ef60a2797e35ff4b6cc21c78f175c04258940a3e9f40324beda`;
- action SHA-256:
  `d38358ce205391fe24321add1f711954b0c74e4dbdb635a3b1cb3edc42dcf73f`;
- exact confirmation reference:
  `codex-user-exact-approval:2026-08-08-final-crossref-v2`;
- one-time grant: `grant-56f454ee3aa4f5f2cb96156c`, consumed once,
  recovery count zero.

The durable Materials sequence was one persistent Gateway run and one frozen
request/interaction/manifest/grant binding. Hermes top-level one-shot `-z`
ignored the intended `--resume`, so `act → result` ran in host session
`20260808_225053_976ca7`, not the submit session
`20260808_204029_977de9`. The original session was subsequently resumed only
for one `run_get` and one `result_get`; it issued no run, approval action, or
network request. This host-session deviation is accepted and does not weaken
the persisted approval boundary. Future operations should use
`hermes chat -q --resume`, not top-level `-z`, when host conversation continuity
matters.

## Exact bounded result

| Metric | Result |
|---|---:|
| Planned logical queries | 4 |
| Physical Crossref attempts / HTTP 200 | 4 / 4 |
| Frozen physical-attempt ceiling | 8 |
| Raw Crossref response bytes | 12,999 |
| Raw hits / unique documents | 4 / 4 |
| Selected passages / 32-d vectors | 2 / 2 |
| Embedding input tokens | 167 |
| EvidenceCards / BridgePackets | 2 / 1 |
| Transformation plans / generated candidates / selected | 2 / 2 / 1 |
| Exact / strict selected duplicates | 0 / 0 |
| Article-body fetch requests / bytes | 0 / 0 |
| PDF reads / PDF Artifacts | 0 / 0 |
| Internal materials-model calls | 0 |

The final Top-1 route uses local resonance. It does not by itself demonstrate
selected cross-mechanism diversity; that claim is supported by the separate
P3.2 Top-5 Gate, not by this exact Top-1 run.

## How the three product questions are implemented

### Deduplication and diversity

Search hits are grouped by normalized DOI/arXiv/URL identity while retaining
all query and raw-hit lineage. A document's passage and vector are processed
once per run. Candidate identity uses canonical structure plus mechanism/route
signatures; exact structural duplicates are merged with their routes retained,
strict duplicates are audited, and deterministic MMR selects the requested
Top-K. `require_diverse_routes` is reported as an auditable policy constraint,
not treated as permission to fabricate missing mechanisms.

### Low-cost search-result reading

The production Crossref adapter requests only
`DOI,title,author,published,URL,abstract,subject`, with `rows=1`, bounded response
bytes, and bounded retries. Metadata-sufficient records never trigger body
fetch. The P3.3 seam supports bounded JSON metadata, JSON-LD/Highwire, JATS/XML,
and relevant HTML sections for controlled offline/future use, but public body
fetch remains disabled in this release and PDF remains fail-closed. Only
selected passage packages are vectorized; raw pages or whole articles are not
sent to an internal model.

The completed-run verifier binds each Crossref response to
`items-per-page=1` and exactly one item, caps the raw abstract at 20,000
characters, enforces the requested-field/nested-shape allowlist, and rejects
declared fetched-body/PDF Artifacts, raw PDF signatures, PDF data/signature
markers, and document-level HTML markers in allowed strings. These are
structural and marker boundaries, not semantic recognition of arbitrary plain
prose mislabeled by a provider as an abstract.

### Cross-domain tag guidance

The model is not allowed to invent an unconstrained search vocabulary. A
versioned curated TagGraph supplies target, support, analogy, and exclusion
relationships. Deterministic bridge rules translate mechanism-level concepts
such as local resonance or destructive interference into bounded query plans;
evidence must carry the required support tags before a bridge can be used.
Per-query/tag/bridge yield is persisted in a review-only feedback Artifact.
Expert status remains `UNKNOWN`, and a run cannot edit the curated graph
online. This gives future development a measurable tag-tuning loop without
letting model intuition silently become policy.

## Independent closeout verification

The release verifier opens both SQLite stores immutable/query-only, binds the
exact confirmation reference and approval hashes, verifies the complete
declared Artifact/URI/lineage closure, replays the frozen deterministic
pipeline, recomputes ledgers/report/bundle/stage/Gateway projection, and
compares project-tree metadata and bytes before and after.

Final results in the lock-synchronized Gateway environment:

```text
Standalone verifier matrix
46 passed in 59.15s
37 test functions = 3 positive controls + 43 negative/adversarial cases

Related Gateway/runner/approval regression
61 passed in 64.13s

Full repository suite
825 passed, 12 skipped, 710 warnings in 78.86s

Exact v2 verifier
exit 0
artifact_closure_verified=true
no_workspace_writes=true
```

Additional release Gates passed: exact `uv pip sync` Gateway bootstrap;
dependency checks in development, Gateway, and Hermes environments; Python
compileall; Hermes bundle validation; isolated MCP stdio discovery of exactly
the four allowed tools; Ruff format/lint on the four changed Python files;
`git diff --check`; and a targeted secret-pattern scan of every intended
tracked/untracked change. The exact v2 verifier passed once more after the
runtime sync.

Two independent final audits reproduced the critical type-confusion,
confirmation, unknown-field, allowed-abstract payload, oversized-abstract,
second-item, and request-page-contract probes. No P1/P2 code blocker remained.
The matrix is representative, not an assertion that every composition of
attacks has its own test.

## Hermes as the system boundary

Introducing Hermes is justified for this continuing system rather than for the
scientific transformation alone. Hermes supplies a stable four-tool operator
surface, explicit interaction stop, exact approval handoff, durable run/result
retrieval, environment separation, and an inspectable Skill/SOUL contract.
Materials logic remains in the Gateway and source-controlled deterministic
components; Hermes does not become an unbounded scientific planner. This split
is the useful normalization boundary for later development.

## Remaining limitations and next release work

- This is a single-user local beta with no branch protection, required hosted
  CI, RBAC, service queue, or multi-user approval policy.
- Crossref is the only production public metadata provider.
- The current vectorizer is deterministic signed hashing, not a remote semantic
  embedding service.
- Tag feedback is review-only and has no expert acceptance evidence.
- Public body fetching and PDF extraction remain disabled.
- Any property claim requires downstream calculations and expert review; this
  release only produces a reviewable hypothesis.

At report freeze, technical verification is complete and GitHub publication is
the remaining operation. PR/check/merge and anonymous public-read evidence are
recorded in the PR closeout and the publication checkpoint rather than claimed
in advance here.
