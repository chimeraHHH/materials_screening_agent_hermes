# Materials Gateway v1 contract

Use only these coarse-grained tools. Tool names below are server-native MCP names;
Hermes may display them with the `mcp__materials__` prefix.

## `materials_inspiration_run`

Create or idempotently recover one inspiration run.

Required inputs:

- `submission_id`: stable caller-selected identifier for this user intent;
- `goal`: concise materials objective;
- `constraints`: structured, bounded constraints accepted by the schema.

`budget` is a field of `constraints`, never a fourth top-level input. Unknown
top-level fields fail validation before a run is created. Correct a malformed
call and reuse the same stable `submission_id`.

The source-controlled production factory compiles a narrow structured request
family. `goal` is approval-bound rationale: it is preserved and hashed but never
parsed to infer scope. Scientific execution comes only from these reviewed
constraints:

- `target_features` is non-empty and contains only `electronic flat band`,
  `electronic narrow band`, `flat electronic band`, or
  `narrow electronic band`;
- `material_classes`, when present, contains only
  `layered transition metal compound`,
  `layered transition metal dichalcogenide`, or
  `transition metal dichalcogenide`;
- case, whitespace, and hyphens are normalized; fuzzy matching is forbidden;
- `dimensionality` is exactly `2D`;
- the operator-owned, SHA-pinned engineering catalog is the only source of parent
  structures and routes; callers cannot provide a CIF or path, output elements
  are Ti/Se, and excluded elements do not include Ti or Se;
- the budget allows at least eight physical search attempts, four unique
  documents, four passages, zero model calls, and 180 seconds; full PDFs and
  expensive computation remain disabled.

Unsupported structured requests fail before approval and network access with
`UNSUPPORTED_INSPIRATION_REQUEST`. Do not change the user's requirements to make
them pass.

The same `submission_id` and canonical request must return the same run. Reusing
the ID with different content is a conflict and must not be worked around by the
agent.

## `materials_run_get`

Read a safe status projection by `run_id`. It can return a terminal result,
progress, a bounded interaction request, or a public failure. Raw checkpoint
payloads and arbitrary artifact URIs are never part of this projection.

## `materials_run_act`

Apply exactly one schema-valid action to one pending interaction. Allowed action
variants are answer, approve, reject, resume, retry, or cancel as advertised by
the current interaction. Require a fresh user decision for approval, expensive
work, cancellation, and sensitive retry. Fail closed if the interaction is stale
or the action is not currently legal. The production Gateway also atomically
consumes an out-of-band operator grant bound to the request, complete interaction
(including the frozen execution-manifest hash), and exact action. A caller-set
`confirmed_by_user` field is necessary schema data, never proof of approval.

If Crossref exhausts bounded transient retries, the run terminates with
`EXTERNAL_SEARCH_UNAVAILABLE` and `retryable=true`. It has already consumed its
approval and must not be replayed automatically. After an explicit user decision,
create a new submission with a new `submission_id` and obtain a fresh approval.
Crossref schema drift and other permanent adapter failures are nonretryable.

## `materials_result_get`

Read a bounded, hash-verified result projection for a terminal run. A successful
response includes an `InspirationBundle` summary, evidence lineage, validation
boundaries, cost ledger, the authoritative report SHA-256, and the independently
verified canonical structured-result SHA-256. A legacy or hashless artifact must
never be presented as verified.

The v1 projection exposes the feedback Artifact's URI and hash through lineage,
but not its query, tag, or bridge rows. Hermes must not claim to have inspected
or quote those rows unless a later live tool schema explicitly returns them.

## State and evidence vocabulary

- `SEARCH_SUPPORTED`: selected source passages support the stated bridge.
- `STRUCTURE_VALID`: the registered deterministic operator produced a structure
  that passed structural checks.
- `UNKNOWN`: the property was not computed; do not convert this to pass/fail.
- `PARTIAL`: some auditable output exists, but one or more planned outputs failed.
- `scientific_conclusion=false`: the bundle contains hypotheses for downstream
  validation, not a final scientific claim.

With `top_k>=2`, `require_diverse_routes=true` requests two supported mechanisms
when they are jointly feasible under strict-structure and parent-family quotas,
and audits a two-physical-route floor. `false` removes only the second-mechanism
floor; exact/strict deduplication and MMR remain active. Pool insufficiency and
hard-quota infeasibility are explicit selection-audit outcomes, never silent
claims that the requested diversity was achieved.

The result's cost ledger covers the Gateway materials service only. Hermes host
provider calls and tokens, when auditable, are a separate ledger. A zero or
unavailable provider cost is not evidence that provider execution was free.

`tag_feedback.json` is an immutable, review-only internal Artifact. Its
per-query, per-tag, and per-bridge allocations use inclusive, non-additive
attribution, so shared requests or documents can occur in more than one row.
Only the Gateway `CostLedger` is additive. Feedback never mutates the curated
TagGraph or current-run plan. Its v1 wire values are
`review_disposition=REVIEW_ONLY`, `applies_to_tag_graph=false`,
`scientific_conclusion=false`, and
`aggregation_semantics=INCLUSIVE_NON_ADDITIVE`. The top level and every bridge
row use `expert_status=UNKNOWN`; v1 accepts no expert-review input. Any change
requires a separately versioned, verified review workflow.

Never introduce novelty, patentability, or prior-art fields into a request or
response.

The production adapter reads only bounded Crossref metadata and abstracts. Its
body-fetch request budget is zero, and it never follows article, full-text, or
PDF links. JSON-LD/Highwire, JATS/XML, and HTML passage extraction has an
operator-owned offline fixture Gate only; this is not evidence that
public-network body fetching is enabled. Public body fetching remains disabled
until DNS validation is bound to the actual connection address and the complete
redirect, host, content-type, request, and byte-budget release Gate passes.
`MATERIALS_CROSSREF_CONTACT_EMAIL` is optional operator-owned
environment state for Crossref's polite pool and must never enter an Artifact,
manifest, report, or model-visible result.
