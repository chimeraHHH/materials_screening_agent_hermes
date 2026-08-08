# Hermes inspiration pilot — 2026-08-08

## Verdict

The first local, single-user inspiration vertical slice ran successfully through
both the real four-tool MCP stdio boundary and an authorized natural-language
Hermes session. It produced one non-empty, hash-verified structure proposal.
The Gateway terminal state is `PARTIAL` because two curated bridge rules lacked
all required support tags; the contained `InspirationBundle` is `SUCCEEDED`.
This is the intended honest distinction between workflow completion and
incomplete evidence coverage.

The run proves a bounded engineering and provenance loop. It does **not** prove
the target electronic property, experimental feasibility, novelty, patentability,
or absence from prior art.

## Frozen software and interface

- material repository branch: `agent/hermes-inspiration`;
- material service/Gateway implementation commit: `74059b0`;
- reproducible MCP smoke client commit: `290cafe`;
- post-debug Skill contract commit: `d95579c`;
- Hermes: tag `v2026.8.3`, package `0.20.0`, resolved commit
  `3c27eb6234bf91b8ceee9e9071591b31e9b148cb`;
- Python: `3.11.15` in `.venv`, `.venv-gateway`, and `.venv-hermes`;
- Gateway dependencies: `mcp==1.28.1`, `pydantic==2.12.5`,
  `pymatgen==2025.10.7`, `spglib==2.7.0`;
- Hermes config SHA-256:
  `0e73380da4f57236948fca8eaf275eb77ab50b6e23439f4d62061796546cc693`;
- first MCP/natural-run Skill SHA-256:
  `2a7ba9341a94130654701bca205d2c520b4d328e02a86cfc5b9b3b509f405822`;
- first MCP/natural-run generated SOUL SHA-256:
  `1a2b8ea088eb1719c11cac4884dfb03c5375dab097a66bdf7f10b22f5c158612`;
- post-debug release Skill SHA-256:
  `6149f3a666d3123bc8c3327d000184fd97717c08835464ce180fc83a010870ba`;
- post-debug release generated SOUL SHA-256:
  `7935f28be25ebb7d12c39bce6b9d483aecddc30ff1b1d016458ccefbdeffcf24`;
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
- Gateway materials-service search requests / response bytes / unique documents:
  `3 / 9,456 / 1`;
- Gateway materials-service fetch requests / full PDFs / LLM calls: `0 / 0 / 0`;
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

## Natural-language Hermes Gate and debugging

The user completed the OpenAI Codex device authorization and then explicitly
continued the pending approval. The credential remains only in Hermes's ignored
local credential store; no token was read, printed, or committed.

- provider / model: `openai-codex` / `gpt-5.6-sol`;
- Hermes session: `20260808_165305_c8f7ed`;
- submission ID: `hermes-natural-language-20260808-v3`;
- run ID: `inspiration-132cd2868dadf6674c2809f4`;
- request SHA-256:
  `e45c8f641a21411c0cb60c78ee9597243cabd17a954ee769c897876008147cba`;
- interaction ID: `interaction-86f57f1b8f76eec545b682a9`;
- canonical interaction SHA-256:
  `b0b905fa21cdbb647699b2898cff0f2d6df9a46f1f7f09efcd6c109e5954b028`;
- frozen execution-manifest/input SHA-256:
  `b256e443c1d876099ba56d4885bf6ec0ecc412a9bcb3abc4e43cb390af88e280`;
- exact action SHA-256:
  `5600d14fa5d83c03eed63114ddb223c3e1d760163c839d347e517b13fff89e44`;
- operator grant: `grant-c7f278598445db5a88bcac06`, confirmation reference
  `codex-user-resume:2026-08-08-natural-language-v3`.

The first model-generated run call placed `budget` at the tool's top level.
Strict MCP schema validation rejected the unknown field before a run existed.
Hermes then reused the same stable submission ID, nested the budget under
`constraints`, and used the canonical fixed goal. The Gateway created exactly
one run and returned `INTERACTION_REQUIRED`; Hermes did not self-approve. After
the user's explicit decision, the operator issued the bound grant out of band.
The resumed session called `materials_run_get`, `materials_run_act`, and
`materials_result_get`. Although one CLI resume printed only its session ID, the
Hermes tool trace, consumed-grant record, persistent Gateway state, and a later
read-only result fetch independently confirm the three calls and terminal result.

The natural run returned:

- Gateway state / bundle outcome: `PARTIAL / SUCCEEDED`;
- candidate: `candidate-c367f55d8bf5a1f3407b6752` from
  `parent-candidate-tis2`;
- transformation: `SUBSTITUTE_EQUIVALENT_SITE_V1`, replacing the complete S
  equivalence class `[1, 2]` with Se;
- bridge / invariant: Acoustic metamaterial / “A local mode weakly coupled to
  the extended network remains weakly dispersive.”;
- structure / property: `STRUCTURE_VALID / UNKNOWN`;
- `scientific_conclusion=false`;
- report SHA-256:
  `9f5dc5bd4cf5f01218236cb3418ff0bfd25dcd01fde4fd2e5431fdaa79bec600`;
- canonical Gateway result SHA-256:
  `6b1bb9c2df0bcd2ab7e8f48b7d03ba074646aa3ab15b49b63041b5c81526bfa2`;
- stage-result SHA-256:
  `8a401a7346db9ada758e12aacfed13007587089b4b62cf1474bf68fee9c0075d`;
- bundle SHA-256:
  `9d5c8ccecc0c6c7ea8f1c93ce1c2c64b69caa6d374f0cfcea1eb411f642d8540`;
- output CIF SHA-256:
  `251370537e7fcbcd815d0be40512b8725732c5c2810403f6f48f3e1f13d36806`.

All five terminal warnings are authoritative and must be reported individually:

```text
BRIDGE_SKIPPED:magnon-line-graph-to-electronic-flat-band:required SUPPORT tags missing: ['line-graph-localization']
BRIDGE_SKIPPED:photonic-interference-to-electronic-flat-band:required SUPPORT tags missing: ['compact-localized-state', 'destructive-interference']
NO_MECHANISM_TAG:passage-0d5af4ada451123f96d25615
QUERY_RULE_SKIPPED:magnon-line-graph-to-electronic-flat-band
QUERY_RULE_SKIPPED:photonic-interference-to-electronic-flat-band
```

The original conversational summary did not enumerate all five warnings. This
was treated as a reporting defect, not hidden as a successful check. Commit
`d95579c` now requires complete warning reporting, distinguishes `PARTIAL` from
bundle `SUCCEEDED`, and regression-checks the guidance.

### Separate cost ledgers

The Gateway materials-service ledger covers only deterministic inspiration
execution: `3` search requests, `9,456` response bytes, `1` unique document,
`3` extracted passages, `3` vectors, `209` vectorizer input tokens, no body
fetches or PDFs, and `0` materials-service model calls/input/output tokens.
`walltime_ms=0` is a documented deterministic artifact placeholder while the
configured 300-second ceiling remains enforced; it is not a timing measurement.

Hermes host audit for session `20260808_165305_c8f7ed` is separate: `11`
provider API calls, `36,240` non-cached input tokens, `80,384` cache-read tokens,
`2,584` output tokens, and `213` reasoning tokens. The recorded estimated and
actual costs are `0.0`, but the provider supplied no reliable billing value;
this must not be interpreted as free execution. The session's `ended_at` and
`end_reason` fields remained null, a host-metadata gap, while its tool trace and
Gateway state were complete.

`require_diverse_routes=true` enabled diversity-aware selection but did not
promise multiple routes: the frozen request used `top_k=1`, and only one valid
route survived. The Skill and contract now state this explicitly.

### Prompt-contract forward tests and post-run transport failure

The first forward test after nesting `budget` correctly still allowed Hermes to
paraphrase the frozen goal and therefore ended with
`UNSUPPORTED_FIXTURE_REQUEST`. The next test, session
`20260808_170431_6f2b15`, generated the complete canonical request on its first
run call, recovered request SHA-256 `e45c8f...`, and reached
`INTERACTION_REQUIRED` as run `inspiration-3475d1808c6c21cd276fd9ff`.
No fresh user decision was requested, so that regression run was not approved.

The final source-controlled Skill contains the entire accepted request rather
than a partial example. `verify_bundle.py` parses it and unit tests validate it
against `HERMES_FIXTURE_GOAL`, `HERMES_FIXTURE_CONSTRAINTS`, the fixture factory,
and the canonical request hash.

After the successful main Gate, three additional read-only reporting sessions
each completed `materials_run_get` and `materials_result_get` (with schema
description calls) but then failed with provider `Broken pipe` before producing
the final natural-language report. Their usage records therefore show failed
provider completion even though Gateway reads succeeded. The Gateway remained
exactly one run and one result, with no `run` or `act` mutation. This intermittent
provider transport failure is retained as a post-pilot hardening item; it
neither overwrote nor retroactively invalidated the completed main run.

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

## Final validation

- complete offline suite after the Skill regression fixes:
  `668 passed, 13 skipped, 362 warnings`;
- isolated Gateway MCP regression: `4 passed`;
- main, Gateway, and Hermes environment dependency checks: passed;
- Skill Creator quick validation and Hermes source/profile/SOUL/tool-manifest
  bundle verifier: passed;
- Hermes MCP connection: passed, with exactly the four allowlisted tools;
- final durable public Crossref Gate: `2 passed in 5.18s`;
- authorized natural-language Hermes main Gate: passed with one persistent run,
  one consumed grant, and one verified result;
- public PR `#1`: GitGuardian succeeded and merge commit
  `e044df1605b8f3711f6ab9af4e6c7d37f2967d35` reached the public default branch;
- `git diff --check`: passed before the documentation milestone commit.

## Current release boundary

Provider authorization and the natural-language Gate are no longer blockers.
The OAuth credential stays in the ignored Hermes runtime credential store; it
was not inspected or included in the repository. The tracked diff contains no
credential material. The implementation and evidence are published in the
public `chimeraHHH/materials_screening_agent_hermes` repository.

The result remains a single-user, local, fixed-request pilot. Its natural run is
offline/fixture-backed; public Crossref search is a separate live Gate. It has no
novelty/prior-art conclusion, no validated target property, no multi-user/RBAC
boundary, no provider-specific 429/backoff policy, and an observed intermittent
provider `Broken pipe` after the successful main session. Those limitations are
future hardening work, not claims hidden by the successful pilot verdict.
