# Final Hermes Crossref approval checkpoint — 2026-08-08

## Status

`WAITING_FOR_EXACT_USER_APPROVAL`. The real Hermes natural-language submit phase
has passed, but no one-time grant has been issued and Crossref execution has not
started. This record is intentionally not a final result record.

The first interaction was safely superseded because its generic approval prompt
said `offline companion runner` although the frozen production policy permitted
public Crossref metadata access. That run remains pending with zero grants and
zero results. Commit `ec1d415` made the live approval prompt disclose the actual
network and cost boundary; a clean v2 workspace then produced the approval point
recorded below.

## Git and runtime

- Branch: `agent/inspiration-generalization`.
- Code/profile checkpoint:
  `ec1d4158e31458f68c95c89244774e1611669868`.
- Public draft PR:
  [#3 Generalize and harden the Hermes inspiration workflow](https://github.com/chimeraHHH/materials_screening_agent_hermes/pull/3).
- Hermes: `0.20.0` / release `v2026.8.3` / provider `openai-codex` /
  model `gpt-5.6-sol`.
- Workspace:
  `workspace/hermes-final-crossref-20260808-v2`.
- Crossref contact email: explicitly unset; public pool selected.

Tracked and installed `config.yaml`, `SOUL.md`, and `SKILL.md` were byte-identical
before the submit. Profile hashes were:

- config: `b0c4266ce7c23244f5c6552c621182cc9c7ecd39e83d196e26de90408fa6da4b`;
- SOUL: `e4667a8b0a7d4d961f5549cbc68794cde1288db05f6898747f0d6c317840f110`;
- current Skill source hash:
  `bdf961bcf5ad11679bb47d7d6f13a06a50f391c47fa0115c49528393433d302d`.

## Pre-submit Gates

```text
.venv/bin/python integrations/hermes/scripts/verify_bundle.py
Hermes bundle valid

.venv/bin/python -m pytest -q -p no:cacheprovider
777 passed, 14 skipped, 362 warnings in 17.41s

.venv/bin/python -m compileall -q src tests integrations/hermes/scripts
.venv/bin/python -m pip check
No broken requirements found.

uv pip check --python .venv-gateway/bin/python
Checked 106 packages; all installed packages are compatible.

uv pip check --python .venv-hermes/bin/python
Checked 71 packages; all installed packages are compatible.
```

The isolated MCP smoke used the v2 workspace and production factory:

```text
HERMES_HOME="$PWD/.hermes-runtime" \
MATERIAL_AGENT_PYTHON="$PWD/.venv-gateway/bin/python" \
MATERIAL_AGENT_WORKSPACE="$PWD/workspace/hermes-final-crossref-20260808-v2" \
MATERIAL_AGENT_PROJECT_ID="materials-inspiration" \
  .venv-hermes/bin/hermes -p materials-inspiration mcp test materials

Connected; tools discovered: 4
```

The four tools were exactly `materials_inspiration_run`, `materials_run_get`,
`materials_run_act`, and `materials_result_get`. Before submit, the v2 databases
contained `0 run / 0 result / 0 grant`.

## Natural-language submit

Hermes received a natural-language instruction to call
`materials_inspiration_run` exactly once with the canonical supported request,
stop at `INTERACTION_REQUIRED`, verify the required disclosure, and never call
`materials_run_act`. The exact structured request was:

```json
{
  "submission_id": "hermes-final-crossref-20260808-v2",
  "goal": "Find a reviewable narrow-band mechanism using bounded public metadata.",
  "constraints": {
    "required_elements": ["Se", "Ti"],
    "excluded_elements": ["Pb"],
    "material_classes": ["layered transition-metal dichalcogenide"],
    "dimensionality": "2D",
    "target_features": ["narrow electronic band"],
    "top_k": 1,
    "require_diverse_routes": true,
    "budget": {
      "max_search_requests": 8,
      "max_unique_documents": 4,
      "max_passages": 4,
      "max_model_calls": 0,
      "max_walltime_seconds": 300,
      "allow_full_pdf": false,
      "allow_expensive_computation": false
    }
  }
}
```

Hermes session and Gateway identity:

- Session ID: `20260808_204029_977de9`.
- Run ID: `inspiration-2c470e4810392aca2c9a7c4d`.
- Submission ID: `hermes-final-crossref-20260808-v2`.
- Canonical request SHA-256:
  `0598117ef45e17ec44f328f3effff5722166e2df589b8695ff0ff1c5a25220c6`.
- Interaction ID: `interaction-f2d2ab3eaa402a5ed4481e69`.
- Execution-manifest SHA-256:
  `e6b0d901fa0a8ef60a2797e35ff4b6cc21c78f175c04258940a3e9f40324beda`.
- Allowed actions: `approve`, `reject`, `cancel`.

Complete approval prompt:

```text
Freeze this bounded inspiration request before execution? Approval permits
bounded public Crossref metadata/abstract network access with at most 8 physical
search attempts; article-body fetch requests=0, full-PDF reads=0, and internal
model calls=0.
```

Hermes explicitly returned disclosure verification `PASS`. Its durable message
sequence was `tool_describe → materials_inspiration_run → stop`; there was one
Materials run call and no act call.

## Frozen pre-execution inputs

| Artifact | SHA-256 | Bytes |
|---|---|---:|
| Requirement | `6fcdc64cf18636e61b2a16a01128e502511f52cbd29bb4d84052b3646f1eda70` | 5,209 |
| Policy | `673afd6199fdce357dff2d230af52a7ecfad6bfc747e61eb509a9f433ac1ec3a` | 1,538 |
| TagGraph | `8f2a4b40f6857b34e3827987155582f66a3cda68ec1de507b6cedee980320917` | 6,612 |
| Substitution registry | `0df10f816808f075bd2e571b018b46a1e1cf0831b73d4be58782e679625cd4fb` | 518 |
| Parent-catalog manifest | `09d563732717e05ccf216d3b8572b1bcd1d855dd3f5d0106a4cdbc15b9197b99` | 8,548 |

The v2 databases were re-read after Hermes stopped and contained exactly
`1 run / 0 result / 0 grant`. The v1 superseded workspace independently remained
`INTERACTION_REQUIRED / 0 result / 0 grant`.

## Hermes host usage so far

The authoritative session database records three provider API calls, 11,047
non-cached input tokens, 16,384 cache-read tokens, 0 cache-write tokens, 538
output tokens, and 85 reasoning tokens. Its cost status is `included`; the
stored `0.0` estimate is not interpreted as free execution. The Gateway
materials-service runner has not executed and therefore has no final cost
ledger yet.

## Superseded v1 interaction

- Session: `20260808_203007_4ddaa0`.
- Run: `inspiration-0f1d721c6965e9277ee0017a`.
- Interaction: `interaction-696b75b899b7d74cd4398e94`.
- Manifest: `54d45aa38096c14123c0f42675727ecfda43bf1493f8d2c80efaf4aa33312048`.
- Defect: prompt said offline execution while the frozen policy was public
  metadata API mode.
- Disposition: never approve, never grant, never act; retain as audit evidence.

## Authorization boundary and next action

No earlier generic continuation instruction can bind a grant to the v2
interaction because the exact interaction and manifest did not yet exist. The
required explicit decision is:

```text
Approve manifest e6b0d901fa0a8ef60a2797e35ff4b6cc21c78f175c04258940a3e9f40324beda
for run inspiration-2c470e4810392aca2c9a7c4d and interaction
interaction-f2d2ab3eaa402a5ed4481e69, action approve.
```

Only after that exact decision may the trusted operator CLI issue a one-time
grant and resume this same Hermes session. The final act/result phase must then
verify Crossref attempts, Artifact closure, hashes, costs, PDF/body/LLM zeros,
property `UNKNOWN`, `scientific_conclusion=false`, and every warning.

This checkpoint makes no scientific, novelty, prior-art, patent, or
validated-property claim.
