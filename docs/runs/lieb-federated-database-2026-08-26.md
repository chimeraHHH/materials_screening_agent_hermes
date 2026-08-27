# Lieb fractional-valence flat-band: FEDERATED_DATABASE route

- Date: 2026-08-26
- Route: `FEDERATED_DATABASE`
- Status: r1 `ENGINEERING_BLOCKED_NO_RESULT`; r2 `SUCCEEDED / REASONED_HYPOTHESIS`
- DFT: disabled and not exposed by the selected Hermes profile

## r1: orphaned pre-r21 attempt

## Immutable request identity

- Hermes profile: `materials-inspiration-research`
- Hermes tool: `materials_generic_research_run`
- isolated workspace: `/Users/yiminghua/2026Summer/FDU/materials_screening_agent/workspace/lieb-federated-database-20260826`
- project ID: `lieb-federated-db-20260826`
- submission ID: `lieb-feddb-20260826-r1`
- expected run ID: not returned; no Hermes scientific result exists
- complete routed goal size: 5,260 bytes
- complete routed goal SHA-256: `b7f3e45b74e25ba3712520476cfe8bab9678f8e36255b4860ab794bd1b6793bb`
- source task-contract SHA-256: `0e3b9a9c945c904fc9b2b747c3e3b2966fb26adb393890e29c003725e7aae4ef`
- research-profile SOUL SHA-256: `ff973552be30866b025fda86e6df8b4399476009d5a273e4da4af2550d724b4e`
- research skill SHA-256: `79d83bef1dd6f3730d17d7484467beb410874e41d606eca763da2a6cc67a9edb`

The `goal` was assembled without paraphrase from the complete `Shared scientific
goal`, `Required workflow`, and `Acceptance and output` sections of
`docs/prompts/lieb-fractional-valence-flat-band-v1.md`, followed by exactly the
`FEDERATED_DATABASE` overlay. The 12,000-character r20 request ceiling accepted
the 5,260-byte value; this run did not hit the historical 4,000-character gate.

## Budget and provider settings

| Setting | Value |
|---|---:|
| outer Hermes reasoning | `max` |
| internal scientific reasoning | `max` |
| native-search calls | 16 |
| authoritative-search calls | 24 |
| agent rounds per role | 30 |
| publication years | 1960--2026 |
| DFT fallback | forbidden |
| MCP tool timeout | 1,800 s |

DeepSeek and Materials Project credentials were resolved from macOS Keychain.
No secret bytes were written to the repository, report, command template, usage
artifact, or runtime log.

## Preflight evidence

The research bundle verifier returned `Hermes research bundle valid`. The
independent lifecycle health result reported a healthy dashboard, monitor,
worker, shared databases, and shared MCP Hub. The Hub health endpoint returned:

```json
{"ok":true,"schema_version":"materials-mcp-http-hub-v1","servers":["materials","research"]}
```

The host initially found Node v20.20.2, below the locked minimum. ChatGPT's
bundled Node v24.19.0 then failed to load the Hermes Rolldown native binding
because the executable and addon had different macOS Team IDs. The run therefore
used the official Node v22.22.0 Darwin arm64 archive inside this isolated
workspace. Its downloaded archive passed the official `SHASUMS256.txt` check.
No global Node installation was changed.

## Execution observation and blocker

Hermes invoked `materials_generic_research_run` through the strict profile. The
Gateway produced repeated Pymatgen structure-normalization warnings, showing that
the full request had passed pre-role schema validation and reached real database
processing. Process sampling later showed the scientific worker blocked in
`_ssl__SSLSocket_read` while waiting for a DeepSeek response. Its outbound proxy
source port changed during the run, consistent with request-stage changes or
retries.

No scientific checkpoint was written. After more than 49 minutes:

- the 1,800-second MCP timeout had been exceeded by about 20 minutes;
- the Hermes host and Gateway processes were still alive;
- the Hermes-to-MCP request connection and Gateway outbound HTTPS connection
  were no longer present;
- two `netstat` samples five seconds apart showed only the MCP listen socket,
  with zero receive/send queues and no byte-counter progress;
- the Gateway log contained no retry/error receipt, only six Pymatgen
  `FutureWarning` messages;
- no Hermes usage file was finalized;
- the project contained zero non-`.gateway` scientific artifact files.

This is an orphaned synchronous tool/future condition, not a scientific empty
result. The valid call was not interrupted while it still had an active socket.

Runtime log:

`workspace/lieb-federated-database-20260826/.materials-inspiration-ops/gateway-worker.log`

## Artifact and candidate accounting

| Item | Result |
|---|---|
| result artifact URI / SHA-256 | not produced |
| report artifact URI / SHA-256 | not produced |
| report manifest URI / SHA-256 | not produced |
| database/source receipts | not returned |
| verified candidates | not returned |
| reasoned hypotheses | not returned |
| evidence matrix | not returned |
| inference matrix | not returned |
| scientific `UNKNOWN` cells | not returned |
| stop reason | engineering timeout/orphan; no scientific stop receipt |

It would be incorrect to convert these missing outputs into all-`UNKNOWN`, zero
candidates, a failed Lieb hypothesis, or a database-empty conclusion.

## Exact non-secret invocation shape

The independent lifecycle was started with:

```bash
./deploy/materials-inspiration \
  --workspace /Users/yiminghua/2026Summer/FDU/materials_screening_agent/workspace/lieb-federated-database-20260826 \
  --project lieb-federated-db-20260826 \
  --profile materials-inspiration \
  --provider deepseek \
  --model deepseek-v4-flash \
  --dashboard-port 9139 \
  --monitor-port 9140 \
  deploy
```

The strict profile was then installed into the same independent Hermes home:

```bash
HERMES_HOME=/Users/yiminghua/2026Summer/FDU/materials_screening_agent/workspace/lieb-federated-database-20260826/.hermes-runtime \
MATERIAL_AGENT_MCP_BASE_URL=http://127.0.0.1:9141 \
MATERIAL_AGENT_WORKSPACE=/Users/yiminghua/2026Summer/FDU/materials_screening_agent/workspace/lieb-federated-database-20260826 \
MATERIAL_AGENT_PROJECT_ID=lieb-federated-db-20260826 \
.venv-hermes/bin/hermes profile install \
  integrations/hermes/profiles/materials-inspiration-research \
  --name materials-inspiration-research --force --yes
```

The oneshot used the strict profile and instructed the host to call
`materials_generic_research_run` exactly once, pass the delimited goal verbatim,
wait for completion, and report only returned scientific content. Provider keys
were injected into that process from Keychain and are intentionally omitted here.

## Smallest engineering next step

Before spending another maximum-budget run, add or expose role-boundary
checkpoints and a terminal timeout/error receipt around the synchronous generic
research MCP call. A rerun must use a new project and submission ID and must
restart the Gateway so the r20 request-ceiling implementation is loaded. Do not
relax the scientific goal or substitute a direct backend/DeepSeek call for this
Hermes-profile path.

## r2: r21 terminal run

The r2 retry used the unchanged 5,260-byte scientific goal and the real strict
Hermes `materials-inspiration-research` profile. Hermes called
`materials_generic_research_run` once. The service produced a terminal result;
no direct DeepSeek/backend shortcut and no DFT execution was used. The later
outer-host receipt recovery reused this terminal result from cache.

### Run identity and canonical artifacts

| Field | Exact value |
|---|---|
| isolated workspace | `/Users/yiminghua/2026Summer/FDU/materials_screening_agent/workspace/lieb-federated-database-20260826-r2` |
| project ID | `lieb-federated-db-20260826-r2` |
| submission ID | `lieb-feddb-20260826-r2` |
| run ID | `generic-852a1ca52a41d66d67c0839f` |
| result status | `SUCCEEDED` |
| scientific conclusion status | `REASONED_HYPOTHESIS` |
| property verification complete | `false` |
| schema / revision | `materials-generic-research-run-v7` / `generic-research-20260826-r21` |
| request SHA-256 | `852a1ca52a41d66d67c0839f9fcec787bc17cd9bca68a7a90617df5c5cb627e3` |
| raw routed-goal SHA-256 | `b7f3e45b74e25ba3712520476cfe8bab9678f8e36255b4860ab794bd1b6793bb` |
| research-graph goal SHA-256 | `4ea2d2f21d4017b74380a2f5679dc098b51461f40474f4a14be22e482804eb35` |
| research graph SHA-256 | `b41acdc2637aae4f5734136aa1d3855c1f29bf030f4c67cba2e1a9a1a3b00898` |
| result artifact | `artifact://generic_research/generic-852a1ca52a41d66d67c0839f/result.json` |
| result file SHA-256 | `7de306af8d81cfcb7408f7bf9b8552fa930bb9b27ed7dd238d5ae815ae0a6853` |
| Markdown report | `artifact://generic_research/generic-852a1ca52a41d66d67c0839f/report-v2.md` |
| Markdown report file SHA-256 | `1efee001005007a223deeafef6ceab4de71881786bb58087d8258a66f132d166` |
| report manifest | `artifact://generic_research/generic-852a1ca52a41d66d67c0839f/report_manifest_v3.json` |
| report manifest file SHA-256 | `1a449a8dd2ef47e6a4dd71b8cab361ed5b1c934ce5482520a3e92b2f867f6eb3` |

The result contains no explicit `stop_reason` field. This absence is preserved;
it is not reconstructed from the scientific conclusion text.

### Requested budget and actual audited use

| Budget or receipt | Value |
|---|---:|
| native-search maximum / actual calls | 16 / 16 |
| authoritative-search maximum / actual calls | 24 / 23 |
| maximum rounds per role | 30 |
| role receipts / aggregate rounds | 9 / 32 |
| role tool calls | 70 |
| role transport retries | 0 |
| role finalization retries | 5 |
| outer Hermes reasoning | `max` |
| internal scientific reasoning | `max` |
| strict MCP timeout | 14,400 s |

The cached-result recovery receipt is
`workspace/lieb-federated-database-20260826-r2/hermes-usage-lieb-feddb-r2-recovery.json`
(SHA-256
`94d756d713a65fc01feffea6e819a4e394544ff303b00b79379e8379ab50ab98`).
It reports a completed, non-failed outer Hermes session with 3 API calls,
9,035 input tokens, 4,531 output tokens, 18,432 cache-read tokens, 2,373
reasoning tokens, and 31,998 total tokens.

### Federated database receipts

The service returned 24 federated candidates from C2DB, MC3D, NOMAD and
Materials Project, 24 source records, zero exact/equivalent merges, and 12
successful source-query receipts. Receipt-level accepted counts repeat across
queries and therefore are not unique-candidate counts.

| Source | Receipts | Raw records | Accepted records | Locally filtered to empty |
|---|---:|---:|---:|---:|
| C2DB | 3 | 24 | 24 | 0 |
| MC3D | 3 | 24 | 3 | 1 |
| NOMAD | 3 | 24 | 0 | 3 |
| Materials Project | 3 | 24 | 1 | 2 |

All 12 receipts have status `SUCCEEDED`; empty accepted sets are explicitly
recorded as `ALL_RECORDS_REJECTED_BY_LOCAL_STRUCTURE_FILTERS`, not silently
treated as source failure.

### Service-returned candidate/evidence accounting

No item met the acceptance contract. The result has zero verified candidates
and seven ranked reasoned hypotheses:

1. `candidate-cuni2o4-to-cu3o4`
2. `candidate-pt3p2o8-biaxial-strain`
3. `candidate-pt3p2se8-hole-dope`
4. `candidate-pt3p2s8-hole-dope`
5. `candidate-li3ni5of11-hole-dope`
6. `candidate-cuo2-hole-doped`
7. `candidate-nio2-oxygen-vacancy`

The deterministic evidence and DeepSeek inference matrices remain separate:

| Candidate | Evidence across 12 constraints | Inference across 12 constraints |
|---|---:|---:|
| `candidate-cuni2o4-to-cu3o4` | 12 `UNKNOWN` | 12 `LIKELY_FAIL` |
| `candidate-pt3p2o8-biaxial-strain` | 12 `UNKNOWN` | 12 `LIKELY_FAIL` |
| `candidate-pt3p2se8-hole-dope` | 12 `UNKNOWN` | 12 `LIKELY_FAIL` |
| `candidate-pt3p2s8-hole-dope` | 12 `UNKNOWN` | 12 `LIKELY_FAIL` |
| `candidate-li3ni5of11-hole-dope` | 12 `UNKNOWN` | 12 `LIKELY_FAIL` |
| `candidate-cuo2-hole-doped` | 12 `UNKNOWN` | 12 `LIKELY_FAIL` |
| `candidate-nio2-oxygen-vacancy` | 12 `UNKNOWN` | 12 `LIKELY_FAIL` |

Thus the matrix contains 84 evidence-level `UNKNOWN` cells and 84 independent
inference-level `LIKELY_FAIL` cells. It also records 128 resolved evidence
objects, 155 lead resolutions, 8 selected evidence IDs and 98 rejected evidence
IDs. The complete per-cell rationale, probabilities, assumptions and decisive
falsifiers are in the canonical result and generated Markdown report; this
work report does not replace them with supervising-agent interpretations.

### Operators, lineage and repair receipt

Seven operation proposals were compiled. Two reached `PLANNED`:

| Candidate | Plan | Registered operator | Parent CIF SHA-256 | Child CIF |
|---|---|---|---|---|
| `candidate-cuni2o4-to-cu3o4` | `plan-dea29b9091fb5424a0007839` | `SUBSTITUTE_EQUIVALENT_SITE_V1` | `d37b095700dfd0d570b355c4099acf072ca9281234bc6b325310ffdd974d328f` | not produced |
| `candidate-pt3p2o8-biaxial-strain` | `plan-6455e4215d73206e225a626c` | `APPLY_HOMOGENEOUS_STRAIN_V1` | `33fddf1d1486d0df611bd3d0b1bcf249fa772e9ce81d65ed6976881deab3f4bb` | not produced |

Five proposals were rejected: one with `OPERATION_PRIOR_REJECTED` and four with
`PARENT_OR_ROUTE_INTEGRITY_FAILURE`. Because both accepted plans have null
`output_structure_artifact`, the workflow did not generate a hash-pinned child
CIF and did not execute downstream registered local/ML checks on a child.

One validator repair was accepted for `requirements_analyst`. It repaired
`CONSTRAINT_COVERAGE` after the original constraint graph omitted the requested
`COMPOSITION` family. The repaired output SHA-256 is
`a010e85d9f989fd16e9723453d393d59db93cd6d5cce3228cd8de201eccf0a6f`.

### Generated report assets

The v3 report manifest contains 50 asset records:

- 24 complete CIF three-view images;
- 1 complete scalar overview;
- 1 complete band-structure image and its complete numeric data payload;
- 23 other band-image slots with explicit `FETCH_FAILED`, `NOT_AVAILABLE`, or
  `RENDER_FAILED` status.

The reporter did not synthesize curves for unavailable database endpoints.

### Exact contract/completion limitations

The outer Hermes host initially stalled after the service wrote the terminal
result at 14:34:37. Only that stalled host was stopped. Repeating the exact same
request and submission returned the cached terminal run in 28.6 seconds; it did
not start another scientific run. The outer host reported that its 1,164,123
character tool payload had been transport-truncated, so its natural-language
receipt could expose only the leading status/run fields. The canonical local
`result.json`, report and manifest above are complete and hash-pinned.

Finally, although the goal expressly forbids running **or requesting** DFT, the
returned `synthesis.required_next_computations` and recommendation text request
spin/SOC-resolved DFT. No DFT was executed, but this is an exact goal-compliance
defect in the returned next-task plan. Those requests must not be scheduled by
this route; they remain recorded only as returned content for audit.

After terminal receipt capture, the isolated r2 dashboard, monitor and Gateway
were stopped cleanly (`dashboard_stopped=true`, `monitor_stopped=true`,
`worker_stopped=true`). All result artifacts remain on disk.
