# Hermes inspiration pilot — 2026-08-08

## Verdict

The first local, single-user inspiration vertical slice ran successfully through
the real four-tool MCP stdio boundary and produced one non-empty, hash-verified
structure proposal. The Gateway terminal state is `PARTIAL` because two curated
bridge rules lacked all required support tags; the contained
`InspirationBundle` is `SUCCEEDED`. This is the intended honest distinction
between workflow completion and incomplete evidence coverage.

The run proves a bounded engineering and provenance loop. It does **not** prove
the target electronic property, experimental feasibility, novelty, patentability,
or absence from prior art.

## Frozen software and interface

- material repository branch: `agent/hermes-inspiration`;
- material service commit: `74059b0`;
- reproducible MCP smoke client commit: `290cafe`;
- Hermes: tag `v2026.8.3`, package `0.20.0`, resolved commit
  `3c27eb6234bf91b8ceee9e9071591b31e9b148cb`;
- Python: `3.11.15` in `.venv`, `.venv-gateway`, and `.venv-hermes`;
- Gateway dependencies: `mcp==1.28.1`, `pydantic==2.12.5`,
  `pymatgen==2025.10.7`, `spglib==2.7.0`;
- Hermes config SHA-256:
  `0e73380da4f57236948fca8eaf275eb77ab50b6e23439f4d62061796546cc693`;
- Skill SHA-256:
  `2a7ba9341a94130654701bca205d2c520b4d328e02a86cfc5b9b3b509f405822`;
- generated SOUL SHA-256:
  `1a2b8ea088eb1719c11cac4884dfb03c5375dab097a66bdf7f10b22f5c158612`;
- canonical four-tool manifest SHA-256:
  `f3787e3a16f82df410adbde789dc4418d9137e5c898aa2cda951fe405176d091`.
  This is the SHA-256 of the exact `--manifest` stdout byte stream, including
  its trailing newline.

Hermes `mcp test materials` connected over stdio and discovered exactly:

```text
materials_inspiration_run
materials_run_get
materials_run_act
materials_result_get
```

Terminal, arbitrary file access, browser, web, memory mutation, native skill
mutation, delegation, MCP resources, and MCP prompts remain disabled in the
profile.

## Authorization evidence

The submit phase first attempted the exact approval action without an operator
grant. MCP rejected it and a subsequent read returned the unchanged
`INTERACTION_REQUIRED` state.

- submission ID: `hermes-release-20260808-v2`;
- run ID: `inspiration-403006308fdce7fa4e27bc56`;
- request SHA-256:
  `e45c8f641a21411c0cb60c78ee9597243cabd17a954ee769c897876008147cba`;
- frozen execution-manifest SHA-256:
  `647dd7e15468f32d6ff563bf2cc18be77d725885f580eb5673311c165b1365bd`;
- interaction ID: `interaction-52fc22e56ac80e3634e6d43b`;
- canonical interaction SHA-256:
  `b1be5ce0c56cd50c9ca4135a78afb365e1e96a08fac47bca57dfe0c66d7fd4d7`;
- exact action SHA-256:
  `196f42ef5c70443d537839f4429410e183fa0eb20c6953b85b05100c93762f6d`;
- one-time grant ID: `grant-1f1ee48c3bdb9d2b082e6371`.

The grant was issued outside MCP from the user's existing instruction to execute
this fixed pilot. It bound the canonical request, complete interaction, prepared
input/policy/tag graph, parent and registry pointers, search/vector snapshots,
the injected transformation-engine snapshot, and exact action. The action was
then consumed atomically before runner execution.

## MCP result

- Gateway state: `PARTIAL`;
- bundle outcome: `SUCCEEDED`;
- selected candidates: `1`;
- target-property status: `UNKNOWN`;
- structural status: `STRUCTURE_VALID`;
- deterministic transformation: complete S equivalence class `[1, 2]` replaced
  by Se using `SUBSTITUTE_EQUIVALENT_SITE_V1`;
- parent: `parent-candidate-tis2`;
- bridge domain: acoustic metamaterial;
- shared invariant: a local mode weakly coupled to the extended network remains
  weakly dispersive;
- cheapest falsification: compute the target-band dispersion for the verified
  output CIF;
- passages / evidence cards / bridge packets: `3 / 2 / 1`;
- transformation proposals / internal unique groups / selected candidates:
  `1 / 1 / 1`;
- search requests / response bytes / unique documents: `3 / 9,456 / 1`;
- fetch requests / full PDFs / LLM calls: `0 / 0 / 0`;
- vectorized passages / embedding input tokens: `3 / 209`.

Authoritative hashes:

- report SHA-256:
  `395342e7fd48730dc1496d3238186063012718907509133e2e2a08f7044d914d`;
- canonical Gateway result SHA-256:
  `80f82f689c843cd4e27da1d922abc7fe1ca7e30c0fd8b1fb17628e86a0e47b1b`;
- `stage_result.json` SHA-256:
  `d438a0e7c699256c76fe496c4e3bb163d9848326bb281f5f809356af3e09673a`;
- `inspiration_bundle.json` SHA-256:
  `edb379987300b662f4ac1f8cd100238d2843ff5ec14363a74655600bceef31ec`;
- output CIF SHA-256:
  `251370537e7fcbcd815d0be40512b8725732c5c2810403f6f48f3e1f13d36806`;
- output CIF size: `962` bytes;
- recursive regular-file size of the complete stage directory at capture:
  `55,957` bytes.

Warnings that caused the `PARTIAL` Gateway state were preserved rather than
discarded: two curated photonic/magnon bridge routes lacked their required
support tags, one passage had no mechanism tag, and the associated query rules
were skipped. The selected acoustic bridge retained complete required support.

## Complementary public-network Gate

The fixed Hermes pilot intentionally uses SHA-pinned source fixtures. A separate
opt-in live Crossref Gate exercised the same metadata-first extraction and
runner path against the public Crossref API twice:

- search requests / raw bytes: `3 / 7,944`;
- unique raw documents: `3`;
- passages / evidence cards / bridges: `2 / 2 / 1`;
- vectorized passages / embedding input tokens: `2 / 142`;
- valid proposals / selected candidates: `1 / 1`;
- body fetches / PDFs / LLM calls: `0 / 0 / 0`;
- bundle SHA-256:
  `c001b78680ab76d44a6bd408885100507a614dc7bc70cf699cad25670f6ad0fa`;
- stage-result SHA-256:
  `4d99a06d0ab81963fee4d9a0b9547ac9be404727ca2c8158fb7c9df2c53c8c2e`.

This separates two claims cleanly: Crossref proves the real public metadata
boundary; the fixture-backed Hermes run proves the controlled Agent/MCP/
approval/persistence/structure-output boundary.

A final durable release rerun also passed (`2 passed in 5.18s`) with the same
counts and budgets. Its bundle SHA-256 was
`61aa3fa520987ed8cacfccf51550acbc31c59f1a6cb9be2a6908c236a28dbbd5`
and its stage-result SHA-256 was
`9b06a81557f17d9de5f998476a85d96e2d21f0425ec2d8a463f13263182f8338`.

The three exact query/response bindings for that durable rerun were:

- DIRECT `electronic flat band` →
  `artifact://stages/inspiration/run-live-crossref/raw_search/query-b39535bd67453f11af089c6d.json`,
  `2,900` bytes, SHA-256
  `5ac0c59b13ed4fc094e33543e2d44772261bb7453615e8381b5bd0f8caaa72ca`;
- BRIDGE `acoustic metamaterial local resonance flat band weak dispersion` →
  `artifact://stages/inspiration/run-live-crossref/raw_search/query-48ff5caf7ad2b176bbb68ec0.json`,
  `2,360` bytes, SHA-256
  `f9f2d04d0e9ea901e1484149ce6d51a0933223d3ab2febf7b097edd23381df46`;
- COUNTER `acoustic metamaterial local resonance flat band weak dispersion
  failure strong hybridization that delocalizes the local mode` →
  `artifact://stages/inspiration/run-live-crossref/raw_search/query-43d84113f219ad76ebe62de6.json`,
  `2,684` bytes, SHA-256
  `5ff4db7737b4af5160f1c85bcfb4007cec0e6ea9d0c47f3046406a80c4a85f4e`.

Each query made one bounded HTTPS request and succeeded on its first attempt;
the current adapter performs no automatic retry or 429 backoff. Provider-specific
backoff remains a post-pilot hardening item. Public metadata is mutable, so each
run persists and hashes the exact response it used instead of claiming byte
stability across network calls.

The exact raw bytes from the durable rerun remain in the ignored local capture
`workspace/live-crossref-release-v3/test_live_crossref_full_inspir0/`; treat
that directory as read-only historical evidence because pytest deletes an
existing `--basetemp` before a run. The logical Artifact URIs and hashes above
are the repository-tracked audit record.

## Reproduction commands

All commands below run from the repository root. The fixed MCP release used:

```bash
.venv-gateway/bin/python integrations/hermes/scripts/run_gateway_pilot.py submit \
  --workspace workspace/hermes-release-v2 \
  --project materials-inspiration \
  --submission-id hermes-release-20260808-v2
.venv-gateway/bin/python -m material_agent.integration.operator_approval \
  --workspace workspace/hermes-release-v2 \
  --project materials-inspiration \
  --run-id inspiration-403006308fdce7fa4e27bc56 \
  --confirmation-reference codex-user-goal:2026-08-08-hermes-pilot
.venv-gateway/bin/python integrations/hermes/scripts/run_gateway_pilot.py finish \
  --workspace workspace/hermes-release-v2 \
  --project materials-inspiration \
  --submission-id hermes-release-20260808-v2
```

Those exact values now identify a terminal historical run. Use a fresh empty
workspace and submission ID for a full replay; reuse the pair only to resume an
interrupted phase. The historical confirmation reference is audit evidence, not
authority for a new run. Before each replay approval, show that run's current
interaction and frozen execution-manifest hash to the user, obtain a new
explicit decision, and issue a new unique confirmation reference through the
operator CLI.

To reproduce the non-provider Gates safely, use a fresh live-capture directory:

```bash
PYTHONDONTWRITEBYTECODE=1 MPLCONFIGDIR=/tmp/material-agent-mpl \
  .venv/bin/python -m pytest -q -p no:cacheprovider
.venv/bin/python -m pip check
uv pip check --python .venv-gateway/bin/python
uv pip check --python .venv-hermes/bin/python

PYTHONDONTWRITEBYTECODE=1 MPLCONFIGDIR=/tmp/material-agent-mpl \
  .venv/bin/python -m pytest -q -s -p no:cacheprovider \
  --basetemp workspace/<fresh-live-crossref-capture> \
  tests/live/test_live_crossref_inspiration.py \
  tests/live/test_live_crossref_inspiration_runner.py \
  --run-live-crossref

.venv/bin/python integrations/hermes/scripts/verify_bundle.py
HERMES_HOME="$PWD/.hermes-runtime" \
MATERIAL_AGENT_PYTHON="$PWD/.venv-gateway/bin/python" \
MATERIAL_AGENT_WORKSPACE="$PWD/workspace/hermes-release-v2" \
MATERIAL_AGENT_PROJECT_ID=materials-inspiration \
  .venv-hermes/bin/hermes -p materials-inspiration mcp test materials
git diff --check
```

## Final non-provider validation

- complete offline suite: `666 passed, 13 skipped, 362 warnings`;
- main, Gateway, and Hermes environment dependency checks: passed;
- Hermes source/profile/SOUL/tool-manifest bundle verifier: passed;
- Hermes MCP connection: passed, with exactly the four allowlisted tools;
- final durable public Crossref Gate: `2 passed in 5.18s`;
- `git diff --check`: passed before the documentation milestone commit.

## Remaining release boundary

Natural-language Hermes invocation requires a one-time provider device login.
Tool discovery, the real MCP subprocess, out-of-band user authorization,
process restart, deterministic runner execution, and final result verification
are already exercised without provider credentials. Until device login succeeds,
do not describe the natural-language Hermes turn itself as passed.

Repeated OpenAI Codex device-login attempts expired without a completed sign-in.
No provider credential is present in the isolated Hermes profile, printed in
this record, or committed. This is the sole remaining external blocker; the
draft PR intentionally remains unmerged.
