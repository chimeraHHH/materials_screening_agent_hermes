---
name: materials-inspiration-evolution
description: Review bounded materials-inspiration outcomes and turn durable workflow lessons into approval-gated Hermes memory or draft skills without changing scientific state.
---

# Materials Inspiration Evolution

Use Hermes as the learning and coordination plane. Reuse its native `memory`,
`skill_manage`, and `delegate_task` capabilities; do not build a second memory,
skill, or subagent framework in the materials service.

## Hard authority boundary

- This profile is review-only. It may call only `materials_run_get` and
  `materials_result_get` for a run ID supplied by the operator.
- It cannot create, approve, retry, cancel, or execute a scientific run.
- A completed Inspiration result is evidence to review, not permission to claim
  novelty, stability, a target property, or experimental validation.
- Never put API keys, credentials, raw provider payloads, private paths, or
  unbounded article text into memory or a skill.
- Delegated children receive a bounded textual review packet, not Materials MCP
  access. They must not infer missing evidence.

## Review and evolve

1. Read the supplied terminal run state and bounded result. Stop if the run is
   missing, nonterminal, hash-invalid, or lacks the evidence needed for the
   requested review.
2. Separate observations into three bins: reusable workflow lesson, scientific
   hypothesis requiring validation, and run-specific noise. Only the first bin
   is eligible for learning.
3. When independent criticism helps, use one flat batch of at most two native
   leaf delegates: one contract/evidence critic and one workflow-reuse critic.
   Give them only the minimum bounded facts and ask them to identify uncertainty.
4. For a short durable fact, propose a native memory write. For a repeatable
   procedure, create a new narrowly named draft skill or patch an agent-created
   draft skill with `skill_manage`. Do not patch the distribution-owned
   `materials-inspiration` production skill.
5. All memory and skill writes are staged by Hermes for human approval. Report
   the pending ID and rationale; never tell the operator that a staged change is
   active before it is approved.
6. A learned draft is not a production release. Promotion requires an explicit
   human-reviewed source diff, the Hermes bundle verifiers, relevant offline
   tests, and any scientific evaluation gate affected by the change.

## Prefer small, testable lessons

Good learning records explain a reproducible failure signature, the bounded fix,
and the evidence that distinguishes it from a coincidence. Reject lessons that
encode one candidate as generally valid, loosen an approval boundary, bypass a
provider or model health check, or turn an `UNKNOWN` result into a positive
claim.
