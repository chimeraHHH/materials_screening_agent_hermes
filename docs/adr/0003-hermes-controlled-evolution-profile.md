# ADR 0003: Separate Hermes-native controlled evolution from scientific execution

- Status: Accepted
- Date: 2026-08-11

## Context

The interaction layer needs to learn reusable workflow lessons and use multiple
agents without duplicating Hermes's native memory, skill-management, background
review, curator, and delegation machinery. Giving those mutating learning tools
to the production scientific profile would, however, allow conversation-derived
state to drift the reviewed control contract.

Scientific artifacts, candidate state, evidence levels, approvals, and provider
health remain owned by the versioned material-agent service. A conversational
learning record is not scientific evidence and cannot authorize a new run.

## Decision

Keep two source-controlled Hermes profiles on the same pinned Hermes 0.20.0
runtime:

1. `materials-inspiration` is the production control plane. It exposes the four
   reviewed Materials MCP tools and keeps Hermes memory, skills, and delegation
   disabled.
2. `materials-inspiration-evolution` is an isolated learning plane. It enables
   Hermes-native `memory`, `skills`, and flat `delegation`, but its Materials MCP
   allowlist contains only `materials_run_get` and `materials_result_get`.

The evolution profile has the following mandatory controls:

- memory and skill writes use Hermes's native write-approval queues;
- the agent-created skill content scanner remains enabled;
- delegated children do not inherit MCP toolsets, delegation depth is one, and
  fan-out is at most two;
- terminal, file, code-execution, web, and browser toolsets remain disabled;
- automatic LLM curator consolidation remains disabled;
- provider credentials and raw provider payloads are forbidden from learned
  memory and skills;
- runtime learning never edits the distribution-owned production Skill. A
  maintainer must translate an approved lesson into a human-reviewed source
  diff and rerun both bundle verifiers and affected scientific gates.

## Consequences

Hermes is the main interaction and agent-evolution framework; the materials
repository only defines the domain contract, profile policy, and promotion
gates. Learning is usable across sessions after approval but cannot silently
change a scientific run, evidence level, or production prompt. Production and
evolution may use separate profile homes and API ports, so deployment must
manage their credentials and lifecycle independently.

This decision completes the framework boundary and installable profile, not a
claim that an autonomous learning benchmark or production rollout has passed.
