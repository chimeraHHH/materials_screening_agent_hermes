# Fractional-valence Lieb lattice — LITERATURE_MECHANISM run

- Date: 2026-08-26
- Canonical run status: `SUCCEEDED`
- Scientific-conclusion status: `REASONED_HYPOTHESIS`
- Property verification complete: `false`
- Verified candidates: **0**
- Ranked reasoned hypotheses: **6**

## Result boundary

The r22 run completed the real Hermes `materials-inspiration-research` path and
produced a canonical result, research graph, Markdown report, report manifest,
role checkpoints, resolved literature records, federated database records and
structure artifacts. It did not run DFT and did not use a DFT fallback.

No candidate satisfies the acceptance contract at the available evidence level.
Across 6 candidates × 8 compiled hard-constraint IDs, the deterministic evidence
matrix contains 0 `PASS`, 3 `FAIL` and 45 `UNKNOWN` cells. DeepSeek's
`LIKELY_PASS` / `LIKELY_FAIL` assessments are retained in a separate inference
matrix and are not promoted to verified evidence.

The best-ranked hypothesis is an O-to-F substituted Nb oxychloride child,
`NbO(1-x)F(x)Cl2`. This is a falsifiable proposal, not a verified material: its
operator plan is `PLANNED` / `REQUIRES_REVIEW`, no child CIF was emitted, and no
local/ML property check was run.

## Frozen invocation

- Hermes profile: `materials-inspiration-research`
- Hermes host session: `20260826_140222_be28b6`
- Host mode: explicit `.venv-hermes/bin/hermes -p
  materials-inspiration-research`, one-shot host, exactly one
  `materials_generic_research_run` call
- Provider/model: `deepseek` / `deepseek-v4-flash`
- Workspace: `workspace/lieb-literature-mechanism-r22-20260826`
- Project: `lieb-literature-mechanism-r22`
- Submission: `lieb-literature-mechanism-20260826-r22`
- Internal research run: `generic-2167f273c06b3276b42240c9`
- Implementation revision: `generic-research-20260826-r22`
- Route overlay: `LITERATURE_MECHANISM`
- Complete goal length: 5,304 characters
- Exact assembled goal SHA-256:
  `427c64b30e3508d1e7a2af9a1861ae99d86a7942edca0d07e649284866299729`
- Source prompt file SHA-256:
  `0e3b9a9c945c904fc9b2b747c3e3b2966fb26adb393890e29c003725e7aae4ef`
- Runtime canonical goal SHA-256:
  `3b6d433cb187b351e581a39071cb5a32bdd56c4259dd750a55b6c5021d3c1fff`
- Request SHA-256:
  `2167f273c06b3276b42240c911a82b41b0fac75855d72688b66783a616691af8`
- Requested budgets: native search `16`; authoritative search `24`; maximum
  agent rounds per role `30`; reasoning effort `max`; publication years
  `1960–2026`
- DFT: prohibited and not invoked

All nine scientific roles committed artifacts: requirements analyst, query
strategist, native search scout, evidence researcher, database scout, mechanism
chemist, skeptic, hypothesis reasoner and synthesist. The requirements contract
needed one accepted repair. The r22 dangling-native-lead normalizer was available,
but this fresh run did not need it; the only recorded deterministic normalization
was `CANONICALIZED_UNKNOWN_CONSTRAINT_JOIN`.

## Canonical artifacts and hashes

| Artifact | URI / path | SHA-256 |
|---|---|---|
| Result | `artifact://generic_research/generic-2167f273c06b3276b42240c9/result.json` | `2ef4f4c515ad320d6713a5d13d90e0132fd2e4944bc5d6811266473c66e910c0` |
| Research graph (canonical hash embedded in result) | `result.json#/research_graph` | `f17d3b393ead5ee54e3a9a94db9ee28721422407312afa3de2674cb5ec41b287` |
| Scientific report | `artifact://generic_research/generic-2167f273c06b3276b42240c9/report-v2.md` | `08b85b223b869c7c2e5fba8f532d5ab1f111aba0fd510f37f7b206650fef8207` |
| Report manifest | `artifact://generic_research/generic-2167f273c06b3276b42240c9/report_manifest_v3.json` | `c5f0eae8a4781317518361016a2703291ec700403ca98d3c0406c55f8ac61922` |
| Hermes usage closeout artifact | `workspace/lieb-literature-mechanism-r22-20260826/hermes-usage-lieb-literature-r22.json` | `5cb622ade56add3bf66476c4cb172551ba36966f2173b23ef47b4f251fd0694d` |
| Hermes profile state/usage ledger | `workspace/lieb-literature-mechanism-r22-20260826/.hermes/profiles/materials-inspiration-research/state.db` | `b16896375732be4e747891ddbcd7f316ee04f6914adc7c65612e173cfe5b4dd2` |

The result schema is `materials-generic-research-run-v7`. It has no separate
`stop_reason` member; the canonical terminal status is `SUCCEEDED` and the
scientific boundary is `REASONED_HYPOTHESIS`.

## Candidate ranking: verified evidence versus inference

| Rank | Candidate | DeepSeek promise score | Deterministic evidence cells | Structure mapping | Boundary |
|---:|---|---:|---|---|---|
| 1 | `candidate-nboclof-mixed-valence`, `NbO(1-x)F(x)Cl2` | 0.45 | 8 `UNKNOWN` | MC3D NbCl2O parent only; no child CIF | Reasoned hypothesis; proposed Nb3+/Nb4+ ledger `v(Nb)=4-x` is unverified |
| 2 | `candidate-nbocl2-flatband`, NbOCl2 | 0.35 | 8 `UNKNOWN` | MC3D structure resolved | Literature mechanism/structure anchor; integer Nb4+ parent |
| 3 | `candidate-taocl2-analogue`, TaOCl2 | 0.35 | 8 `UNKNOWN` | References the NbCl2O parent as an analogue anchor, not a TaOCl2 CIF | Reasoned 5d/SOC analogue; integer Ta4+ and no registered route |
| 4 | `candidate-ptp-lieb-mif`, PtP | 0.25 | 8 `UNKNOWN` | No database/CIF mapping | Literature-only near miss; metallic Au(111) substrate makes Fermi isolation a likely failure |
| 5 | `candidate-ba-cuo4-cuprate`, Ba(CuO4)2 | 0.20 | 8 `UNKNOWN` | C2DB structure resolved | Structure not yet proven to be a periodic Lieb graph |
| 6 | `candidate-ba2cuo3-delta`, Ba2CuO3.2 | 0.15 | 3 `FAIL`, 5 `UNKNOWN` | No database/CIF mapping | Elimination benchmark; Lieb sublattice, dimensionality and composition cells fail in the frozen matrix |

The inference matrix contains 29 `LIKELY_PASS` and 19 `LIKELY_FAIL`
assessments. These probabilities and mechanism arguments are DeepSeek outputs;
they do not alter the 0/3/45 `PASS`/`FAIL`/`UNKNOWN` evidence totals.

## Literature evidence and source receipts

The run froze 152 native leads. Four were resolved directly and 148 remained
unresolved leads. The authoritative layer produced 103 resolved evidence records:
47 Semantic Scholar, 45 OpenCitations and 11 Crossref records. Their evidence
scope is metadata or abstract only: 100 have full text `NOT_REQUESTED` and 3 have
full text `UNAVAILABLE`. Thus titles/abstracts can support mechanism hypotheses,
but they cannot supply missing numerical bandwidth, Fermi-crossing, PDOS or
spin/SOC verification.

Candidate-level retrieval ran before the skeptic and added three evidence IDs
without failures. Principal candidate-linked records include:

| Candidate family | Evidence ID | Resolved source |
|---|---|---|
| NbOCl2 / proposed NbO(1-x)F(x)Cl2 / TaOCl2 analogue | `evidence-7187f9dc392184c2b1269ec0` | *Robust Orbital-Selective Flat Bands in Layered Transition-Metal Oxyhalides at Room Temperature*, DOI `10.1103/p3dw-tbqp`, arXiv `2510.15080`, metadata/abstract only |
| NbOCl2 net context | `evidence-f7b72a1f1ebf084d02908e21` | *Crystal net catalog of model flat band materials*, DOI `10.1038/s41524-024-01220-x`, arXiv `2303.02524` |
| PtP | `evidence-35ca035d1df887b6b44d4814` | *Realization of a 2D Lieb Lattice in a Metal–Inorganic Framework with Partial Flat Bands and Topological Edge States*, DOI `10.1002/adma.202405615` |
| PtP / binary MIF context | `evidence-17edb55a470bc020c8d1afcc` | *Exploring Stable Lieb Lattices In Two-Dimensional Binary Metal-Inorganic Frameworks*, DOI `10.1038/s41524-025-01877-y` |
| Ba2CuO3+delta | `evidence-18547258f16b9457cf7c6176` | brick-wall `t-J` model, DOI `10.1103/physrevb.101.180509`, arXiv `1912.12581` |
| Ba2CuO3+delta | `evidence-96c8cd44d12add484c6e0ec4` | ordered oxygen vacancies, DOI `10.1103/physrevmaterials.4.044801`, arXiv `1909.08304` |
| Cu oxidation-state context | `evidence-8ca76173b36cff6e2bd3b7e5` | ternary copper oxide oxidation states, DOI `10.1016/0022-4596(89)90217-x` |

The exact canonical URL, provider, raw-response artifact URI/hash and supported
constraint IDs for every record remain in `result.json#/research_graph/resolved_evidence`.

## Federated database and structure mapping

All four configured database adapters returned `SUCCEEDED` receipts. Across three
queries per source they fetched 96 raw records; 31 passed per-query local filters,
and canonical-structure deduplication yielded 24 federated candidates.

| Source | Raw | Accepted before cross-source dedup | Receipt notes |
|---|---:|---:|---|
| C2DB | 24 | 23 | all three query receipts succeeded |
| MC3D | 24 | 2 | one query had all records rejected by local structure filters |
| NOMAD | 24 | 4 | one query had all records rejected by local structure filters |
| Materials Project | 24 | 2 | one query had all records rejected by local structure filters |

Two mappings are central to the final hypotheses:

- NbOCl2 parent: MC3D source material
  `691238a9-775a-40aa-9362-0f108025490d`, database candidate
  `db-candidate-0b87ae9de86adc6358878b1e`, canonical structure
  `str_8ee6039ea860f76ac7a4ef17`, CIF
  `artifact://research/generic-2167f273c06b3276b42240c9/database/mc3d/source-8e699abc29602604129af5c6.cif`,
  SHA-256 `e0b09fb931e1d86a0848b7b9f456f06ed5ca0149efb7473ce2539285b5752844`.
  Dimensionality is resolved as 2D. Its direct-TM connectivity proxy is 0.0, so
  the ligand-bridged Lieb graph remains `UNKNOWN` pending a periodic
  coordination-graph calculation.
- Ba(CuO4)2 parent: C2DB `1BaCu2O8-1`, database candidate
  `db-candidate-75c9767127511dc450b1f441`, canonical structure
  `str_d1afb85ffd60d594ab883016`, CIF
  `artifact://research/generic-2167f273c06b3276b42240c9/database/c2db/source-867e32cbda725a46102fba81.cif`,
  SHA-256 `afb589e31867ca5ddc8d9f7749dadbb104eee2eaf2f38e340744fc2333b82565`.
  It is resolved as 2D with `band_gap_ev=0.0`, but its direct-TM connectivity
  proxy is also 0.0; neither Lieb topology nor Fermi isolation is verified.

PtP and Ba2CuO3.2 have no retrievable database structure in this run. TaOCl2 has
no TaOCl2 structure artifact; the candidate record only points to the NbOCl2
parent as an analogue. This distinction is preserved rather than silently
claiming a structure mapping.

## Operator/compiler receipts

The transformation audit is hash-pinned to operator registry
`795207704dded5689ba5e50dd1156476aec5bb36b1fdba96624eb4b7bff55b66`
and substitution registry
`0df10f816808f075bd2e571b018b46a1e1cf0831b73d4be58782e679625cd4fb`.
It records four compile attempts:

- One accepted plan binding:
  `candidate-nboclof-mixed-valence` → `plan-481eaa56bc12682363c23d49`,
  operator `SUBSTITUTE_EQUIVALENT_SITE_V1`, O sites `[6,7]` → F,
  specification SHA-256
  `ac505d74daa7083f7e174482b6a1fb99a7816d69f3bae1a9e5ba1bcebc9095ee`.
  The plan status is `PLANNED`; its compile prior is `REQUIRES_REVIEW` with
  `EXPLICIT_CHARGE_REQUIRES_REVIEW`, `SMACT_REQUIRES_REVIEW` and
  `TARGET_COMMON_VALENCE_PASS`.
- Three rejected proposals: Ta substitution
  (`OPERATION_PRIOR_REJECTED`), NbOCl2 carrier doping
  (`PARENT_OR_ROUTE_INTEGRITY_FAILURE`) and Ba(CuO4)2 carrier doping
  (`PARENT_OR_ROUTE_INTEGRITY_FAILURE`).

The accepted plan retains the parent CIF/hash, but `output_structure_artifact`
and `output_structure_id` are null and `validation_checks` is empty. Therefore no
child CIF, oxidation validator, periodic Lieb-graph validator or local/ML band
result exists. This is the main unfinished workflow edge after hypothesis
generation.

## Visual/report assets

The report manifest contains 49 entries:

- 24 `STRUCTURE_THREE_VIEW` images, all `COMPLETE` and rendered locally from the
  hash-pinned CIFs;
- 1 `SCALAR_OVERVIEW`, `COMPLETE`;
- 24 requested `BAND_STRUCTURE` assets: 14 `FETCH_FAILED`, 5 `NOT_AVAILABLE`
  and 5 `RENDER_FAILED`.

For the five C2DB `RENDER_FAILED` cases numerical band data were obtained but the
local renderer raised `ValueError`; for the other candidates no auditable plotted
band object was produced. No failed image is treated as band evidence.

## Remaining UNKNOWNs and smallest next checks

The frozen skeptic matrix stores an explicit `next_verification` for each of the
45 `UNKNOWN` cells. The synthesist distilled them to the following smallest
non-DFT actions:

1. Review and execute `plan-481eaa56bc12682363c23d49` to emit a hash-pinned
   NbO(1-x)F(x)Cl2 child CIF.
2. Run the registered charge-neutrality/oxidation ledger and bond-valence check
   on that child to distinguish Nb3+/Nb4+ mixed valence from an F-localized or
   ligand-hole assignment.
3. Run the periodic coordination graph on the parent and child, explicitly
   testing primitive Lieb motif, 4/2/2 coordination, site equivalence,
   dimensional connectivity and distortion.
4. Run only an applicable registered local/ML band check on the child for
   bandwidth `W <= 50 meV`, closest edge within 50 meV, the 100 meV sensitivity
   window and external Fermi crossings.
5. Resolve numerical band structure and PDOS for NbOCl2 from the cited record or
   a database artifact, keeping non-SOC, SOC and magnetic spin settings separate.
6. Run the periodic graph check on the C2DB Ba(CuO4)2 CIF.
7. Retrieve SOC-resolved TaOCl2 bands/PDOS and an actual TaOCl2 structure before
   treating the analogue as mapped.
8. Retrieve freestanding or substrate-decoupled PtP ARPES/STS evidence before
   testing Fermi isolation.
9. Retrieve an experimental Ba2CuO3.2 structure only if revisiting the eliminated
   brick-wall/ladder candidate.

## Hermes delivery limitation

The canonical service result and all report artifacts were complete by 15:36:03.
After that, the Hermes CLI host remained idle while attempting to receive/serialize
the large MCP response: there was no active MCP or external socket, both host and
Gateway were at 0% CPU, and artifact mtimes no longer changed. Only the owned
Hermes host was interrupted after this post-terminal condition was confirmed; the
Gateway was not stopped and the scientific work was not rerun.

The profile state ledger is the authoritative host-usage record for session
`20260826_140222_be28b6`: 2 Hermes API calls, 5,907 input tokens, 1,571 output
tokens, 177 reasoning tokens, estimated cost USD 0.0012944568. The separate
`--usage-file` closeout artifact contains `failed=true` / `KeyboardInterrupt()`
because the wrapper was interrupted during post-terminal delivery. That flag is
a delivery-closeout limitation and does not override the hash-pinned canonical
`result.json` status `SUCCEEDED`.

## Diagnostic history: r3 under revision r21

The earlier r3 attempt (`generic-808c0b8987899741626c4f3f`) failed closed in
`native_search_scout` because its review referenced unknown lead
`lead-341500d387825653eaa65438`. Two bounded repairs retained the invalid ID and
the exact validator ended with:

```text
contract repair exhausted for native_search_scout:
native-search review references an unknown lead
```

r22 is a fresh project/submission/run and did not reuse that scientific
checkpoint. It passed the former gate and completed all roles. The r3 failure is
retained only as engineering provenance; none of its unresolved leads were
promoted into r22 evidence.
