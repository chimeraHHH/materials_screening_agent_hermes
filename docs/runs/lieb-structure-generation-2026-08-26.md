# Lieb fractional-valence flat-band STRUCTURE_GENERATION run

- Date: 2026-08-26
- Execution boundary: HOST_HERMES -> `materials_generic_research_run` -> internal
  DeepSeek materials research graph
- Scientific mode: database/literature/local-analysis only; no DFT requested or run

## Outcome

The r21 materials research service completed successfully and wrote an immutable
terminal result. It did not verify a material against all hard constraints.

- Service status: `SUCCEEDED`
- Scientific conclusion status: `REASONED_HYPOTHESIS`
- Property verification complete: `false`
- Verified candidates: **0**
- Reasoned hypotheses: **8**
- Evidence matrix: 2 `PASS`, 1 `FAIL`, 61 `UNKNOWN`
- Inference matrix: 36 `LIKELY_PASS`, 28 `LIKELY_FAIL`
- Registered transformation compile attempts: 1
- Accepted transformation plans: **0**
- Generated child CIFs: **0**
- Local/ML checks on generated child CIFs: **0**

The strongest reasoned hypothesis was K-intercalated K0.25NbOCl2, with an
`overall_promise_score` of 0.50. This is not a verified material. Its evidence row
contains eight `UNKNOWN` verdicts. DeepSeek's formal-valence hypothesis was
K(+1) + Nb(+3.75) + O(-2) + 2Cl(-1) = 0, interpreted as a 3:1 Nb4+:Nb3+
mixture; the run explicitly retained this as inference because no formal
oxidation-state evidence was retrieved.

## Exact goal and budgets

The host received the complete Shared scientific goal, Required workflow,
Acceptance/output section, and STRUCTURE_GENERATION overlay from
`docs/prompts/lieb-fractional-valence-flat-band-v1.md`.

- Goal characters: 5,375
- Goal bytes: 5,381
- Goal SHA-256: `4fc07cde008bc08b38d5b3d17de2702fb1b34336c6493b53e2e7642c9c3041ec`
- Host model: `deepseek-v4-flash`, reasoning `max`
- Internal role model: `deepseek-v4-pro`, reasoning `max`
- Native-search request ceiling: 16
- Authoritative-search request ceiling: 24
- Requested role-round ceiling: 30
- Completion ceiling per internal round: 32,768 tokens
- Internal response timeout: 600 seconds, at most 2 transport attempts
- Strict Hermes MCP timeout: 14,400 seconds
- Runtime DFT: disabled by goal and absent from the generic workflow

The nine role receipts account for 2,805,255 total tokens. Role receipts report:

| Role | Rounds | Tool calls | Total tokens |
|---|---:|---:|---:|
| requirements_analyst | 2 | 1 | 7,657 |
| query_strategist | 2 | 1 | 8,737 |
| native_search_scout | 4 | 16 | 106,791 |
| evidence_researcher | 7 | 24 | 718,611 |
| database_scout | 2 | 10 | 296,494 |
| mechanism_chemist | 3 | 9 | 539,181 |
| skeptic | 2 | 10 | 324,941 |
| hypothesis_reasoner | 5 | 1 | 609,500 |
| synthesist | 2 | 1 | 193,343 |

The requirements analyst needed one accepted contract repair for missing
`COMPOSITION` and `DIMENSIONALITY` constraint families. The repair kept the
scientific thresholds unchanged.

## Run and immutable artifacts

- Hermes submission: `lieb-structgen-host-20260826-r21`
- Materials run ID: `generic-026cb14ab9d5fd1cb902b886`
- Implementation: `generic-research-20260826-r21`
- Request SHA-256: `026cb14ab9d5fd1cb902b886d64fc29feec240880a7a40d9c335f57188e9851a`
- Research graph SHA-256: `ed159769846734feb2599ace5bb1067b9b563c212237ef5471bdcdffb97acff8`
- Result URI: `artifact://generic_research/generic-026cb14ab9d5fd1cb902b886/result.json`
- Result file SHA-256: `dbafde797e2846763feee5dcfe8bf21b3677d5889850f5aec9c5cfd7595f2830`
- Report URI: `artifact://generic_research/generic-026cb14ab9d5fd1cb902b886/report-v2.md`
- Report file SHA-256: `ecda8ee444f06f0ca5ed8205c6873092871d0ed89a37de55ce10009660232253`
- Report manifest URI: `artifact://generic_research/generic-026cb14ab9d5fd1cb902b886/report_manifest_v3.json`
- Manifest file SHA-256: `588eee37dbc9096b01fdd5a32ee0598f40a49731c5ce00a32c86a3d94089b9af`

The generated report includes three CIF structure views, a scalar-property plot,
and two C2DB PBE band plots with their compressed numeric source objects.

The result schema has no explicit `stop_reason` field. The exact terminal state is
therefore `status=SUCCEEDED`, `scientific_conclusion_status=REASONED_HYPOTHESIS`,
and `property_verification_complete=false`; no additional stop reason is invented.

## Federated database stage

All four configured sources were called for each of four query ordinals:
C2DB, MC3D, NOMAD, and Materials Project.

- Federation receipts: 16 total
- `SUCCEEDED`: 14
- `EMPTY`: 1
- `FAILED`: 1 (C2DB `ProxyError` on query ordinal 2)
- Deduplicated database candidates: 3
- Source records: 3
- Exact/equivalent merges: 0

Retrieved structures:

| ID | Source ID | Formula | Source | 2D | TM connectivity proxy |
|---|---|---|---|---:|---:|
| `db-candidate-f63ce6b40b0744eab420414c` | `2NbOCl2-1` | NbCl2O | C2DB | yes | 0.0 |
| `db-candidate-0b87ae9de86adc6358878b1e` | `691238a9-775a-40aa-9362-0f108025490d` | NbCl2O | MC3D | yes | 0.0 |
| `db-candidate-06ec879b90008a48cc51dbda` | `2OTaCl2-1` | TaCl2O | C2DB | yes | 0.0 |

The database reviewer selected none of these as satisfying the task. Their CIFs
and scalar/band artifacts were retained as parent evidence, but the periodic
transition-metal Lieb graph, mixed formal valence, three-band manifold, orbital
projection, spin, and SOC conditions remained unresolved.

## Verified candidates

None. No candidate has all eight deterministic evidence assessments at `PASS`.

Candidate evidence counts:

| Candidate | PASS | FAIL | UNKNOWN |
|---|---:|---:|---:|
| K0.25NbOCl2 | 0 | 0 | 8 |
| Cs0.25TaOCl2 | 0 | 0 | 8 |
| NbOCl1.875 vacancy route | 0 | 0 | 8 |
| gated TaOCl2 | 1 | 0 | 7 |
| gated NbOCl2 | 1 | 1 | 6 |
| 1% tensile NbOCl2 | 0 | 0 | 8 |
| Pt-P MIF Lieb lead | 0 | 0 | 8 |
| MPc-MOF Lieb lead | 0 | 0 | 8 |

The only evidence `FAIL` was the retrieved ~100 meV NbOCl2 bandwidth for the
gate-only route, above the fixed 50 meV threshold.

## Ranked reasoned hypotheses

| Rank | Candidate ID | Promise score | LIKELY_PASS / LIKELY_FAIL |
|---:|---|---:|---:|
| 1 | `candidate-k025-nbocl2-flatband` | 0.50 | 6 / 2 |
| 2 | `candidate-cs025-taocl2-flatband` | 0.40 | 5 / 3 |
| 3 | `candidate-nbocl2-tensile-strain` | 0.40 | 6 / 2 |
| 4 | `candidate-nbocl2-clvac-mixedvalence` | 0.35 | 4 / 4 |
| 5 | `candidate-ptp-mif-lieb` | 0.35 | 3 / 5 |
| 6 | `candidate-nbocl2-gated-flatband` | 0.30 | 5 / 3 |
| 7 | `candidate-taocl2-gated-flatband` | 0.25 | 4 / 4 |
| 8 | `candidate-mpc-mof-lieb` | 0.25 | 3 / 5 |

These rankings are DeepSeek inference and are not evidence upgrades.

## Structure generation and operator receipts

The generic research graph reached the registry compiler but did not reach
structure execution:

- Registry status: `HASH_PINNED`
- `compile_attempt_count`: 1
- Accepted `plans`: 0
- Candidate/plan `bindings`: 0
- Rejected proposal: chlorine-vacancy operation on MC3D NbOCl2
- Rejection code: `OPERATION_PRIOR_REJECTED`

There is no generated child CIF, no child structure hash, no accepted operator
receipt, and no local/ML evidence written for a child. The only CIFs in the run are
the three retrieved database parent structures.

K intercalation and tensile strain were returned as next scientific hypotheses,
but neither was compiled in this run. They must not be described as executed.

## Engineering execution chain and failures

### HOST_HERMES

The valid r21 chain was:

1. isolated `materials-inspiration` lifecycle and shared loopback MCP Hub;
2. isolated `materials-inspiration-research` profile exposing only
   `materials_generic_research_run`;
3. Hermes DeepSeek V4 Flash/max tool selection;
4. one `materials_generic_research_run` call with the exact goal and budgets;
5. internal DeepSeek V4 Pro/max nine-role graph;
6. immutable r21 result, Markdown report, manifest, figures, and raw band objects.

The scientific service completed. Hermes host then stalled after the terminal
artifact was written but before persisting the large tool-result message/final
assistant receipt. The host had no active socket or CPU work and was waiting on an
internal condition queue. A second host invocation using the same immutable cached
request reproduced the same post-terminal delivery stall. Both host processes were
stopped; the healthy Gateway and immutable scientific result were retained.

Consequences:

- the materials result is terminal and hash-verifiable;
- the Hermes host invocation is proven by its persisted user/tool-call messages;
- there is no completed Hermes usage receipt or final host message;
- the missing host receipt must not be confused with a missing materials result.

### DIRECT_BACKEND_DIAGNOSTIC

Earlier direct backend attempts were diagnostics only and are not counted as the
HOST_HERMES scientific result:

- r20 first passed the outer 12,000-character request model but exposed a second
  4,000-character limit in `MaterialsResearchDirector`; DeepSeek did not start.
- a direct r20 backend smoke progressed through requirements checkpoints, then was
  intentionally stopped when host-only execution was required.
- an old r20 host run reached native search but reproduced an unbounded response
  wait. It was terminated after r21 introduced 600-second response timeouts and
  two-attempt transport bounds.

## Capability gap and next platform action

The primary scientific workflow gap is now explicit: generic research can produce
reasoned operator hypotheses and a transformation audit, but it does not hand its
unaccepted hypotheses into `ScientificCandidateSearchLoop` to register missing
operators, generate a hash-pinned child CIF, execute local/ML checks, write evidence
to research memory, and iterate after parent elimination.

For this run, the exact handoff state is:

`8 reasoned hypotheses -> 1 rejected compile attempt -> 0 plans -> 0 child CIFs -> 0 local/ML child checks`.

The smallest platform continuation is therefore not a scientific guess. It is an
engineering handoff that lets DeepSeek re-propose the top hypothesis through the
dynamic registry compiler, then feeds an accepted `TransformationPlanV1` into the
existing ML-only iterative search loop. The deterministic layer should continue to
validate hashes, registry applicability, chemistry priors, geometry, budgets, and
evidence ceilings only.

The second engineering gap is Hermes post-terminal delivery of a ~610 KB generic
result. The MCP/host path needs bounded result compaction or artifact-pointer return
semantics so a completed materials result produces a durable Hermes tool receipt
and final message without embedding the entire research graph in the host context.
