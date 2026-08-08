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

The source-controlled pilot factory currently accepts only the complete frozen
request shown in `SKILL.md`, including every constraint and budget field, with
only `submission_id` selected by the caller. Its exact goal is:
`Find bounded mechanism-guided structure proposals for a layered transition-metal compound.`
Do not paraphrase the goal or vary the frozen constraints for a matching request.
Report `UNSUPPORTED_FIXTURE_REQUEST` for other intents instead of changing the
user's requirements to force a match.

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

`require_diverse_routes=true` enables diversity-aware selection among routes that
survive validation, subject to `top_k`; it does not guarantee multiple routes
when `top_k=1` or only one valid route is available.

The result's cost ledger covers the Gateway materials service only. Hermes host
provider calls and tokens, when auditable, are a separate ledger. A zero or
unavailable provider cost is not evidence that provider execution was free.

Never introduce novelty, patentability, or prior-art fields into a request or
response.
