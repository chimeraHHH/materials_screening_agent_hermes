# Materials Gateway v1 contract

Use only these coarse-grained tools. Tool names below are server-native MCP names;
Hermes may display them with the `mcp__materials__` prefix.

## `materials_inspiration_run`

Create or idempotently recover one inspiration run.

Required inputs:

- `submission_id`: stable caller-selected identifier for this user intent;
- `goal`: concise materials objective;
- `constraints`: structured, bounded constraints accepted by the schema.

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
or the action is not currently legal.

## `materials_result_get`

Read a bounded, hash-verified result projection for a terminal run. A successful
response includes an `InspirationBundle` summary, evidence lineage, validation
boundaries, cost ledger, and authoritative result SHA-256. A legacy or hashless
artifact must never be presented as verified.

## State and evidence vocabulary

- `SEARCH_SUPPORTED`: selected source passages support the stated bridge.
- `STRUCTURE_VALID`: the registered deterministic operator produced a structure
  that passed structural checks.
- `UNKNOWN`: the property was not computed; do not convert this to pass/fail.
- `PARTIAL`: some auditable output exists, but one or more planned outputs failed.
- `scientific_conclusion=false`: the bundle contains hypotheses for downstream
  validation, not a final scientific claim.

Never introduce novelty, patentability, or prior-art fields into a request or
response.
