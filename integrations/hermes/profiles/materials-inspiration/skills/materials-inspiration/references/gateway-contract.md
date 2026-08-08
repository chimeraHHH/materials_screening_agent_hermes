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

Never introduce novelty, patentability, or prior-art fields into a request or
response.

The adapter reads only bounded Crossref metadata and abstracts; it never follows
full-text links. `MATERIALS_CROSSREF_CONTACT_EMAIL` is optional operator-owned
environment state for Crossref's polite pool and must never enter an Artifact,
manifest, report, or model-visible result.
