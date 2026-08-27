# Flat/Narrow-Band Open-Source Data Audit

Status: source-policy and full-flow custody audit v3 draft checkpoint;
**not a scientific benchmark result**

Audit date: 2026-08-10 (Asia/Shanghai). The 20 catalog rows and their decisions
remain unchanged from the 2026-08-09 source audit; v3 retains the formal structure
and human derivative-screening boundary and adds Main execution, model-native
receipt, signed annotation, analysis, locked-unseal, and release-control custody.

Scope: open or publicly reachable sources useful for flat/narrow-band case
construction, local electronic-structure checks, mechanism strata, and bounded
literature retrieval. Novelty, patents, article full text, and bulk model training
are outside scope.

## Frozen identities

- Catalog: `source_catalog.jsonl`
- Catalog SHA-256:
  `57c24de8f0cf616205b03ef16231def711f2dfa9fcac86d94beede9c01b0bb1f`
- Row schema: `source_catalog.schema.json`
- Schema SHA-256:
  `a796757159679a02146ad00f306f8bc6db2642fdd3fc51c82b25aba36936efc7`
- Content-address sidecars: `source_catalog.sha256` and
  `source_catalog.schema.sha256`
- This report is separately content-addressed by adjacent `SOURCE_AUDIT.sha256`;
  the sidecar identifies this draft checkpoint and is not external registration.
- Rows: 20 unique sources
- Decisions: 12 `INCLUDE`, 5 `CONDITIONAL`, 3 `EXCLUDE`

The catalog is the machine-readable authority. This report explains the
scientific and governance decisions; it does not replace source-specific terms
or legal advice. Each license is represented as a scoped record: standard
expressions are restricted to the SPDX identifiers used by this catalog, while
mixed, restricted, or per-record rights use an explicit `LicenseRef-*` value.

## Audit method

The search covered four source families:

1. direct flat/narrow-band catalogs, recent model/candidate datasets, and their
   original papers or official repositories;
2. open crystal and electronic-structure databases suitable for reconstructing
   cases without restricted ICSD coordinates;
3. literature metadata, citation, topic, and open-access graphs;
4. plausible but restricted alternatives used to test whether a source must be
   excluded from the public benchmark.

Only primary or official pages were used for the final decision: source terms,
official API/data documentation, exact repository or dataset records, and
original papers. Search-result snippets and third-party summaries were not
treated as authority. The audit recorded:

- exact official URLs and accessed date;
- license and redistribution boundary;
- authentication, request limits, and cache boundary;
- source release, revision, DOI, or retrieval identity;
- fields allowed in the experiment;
- the maximum scientific claim the source can support;
- inclusion, conditional inclusion, or exclusion rationale.

No bulk dataset, article body, PDF corpus, ICSD structure, or provider-only data
was downloaded during this audit.

## Decision matrix

| Source layer | INCLUDE | CONDITIONAL | EXCLUDE |
|---|---|---|---|
| Flat-band and mechanism seeds | Crystal Net, ELF, Struct2Flat | Materials Flatband Database (identifier/link and independently authored label only) | ICSD structures |
| Structures/electronic records | JARVIS exact Figshare releases, C2DB/CMR, Materials Project core, COD, 2DMatPedia | NOMAD record-by-record, Materials Cloud record-by-record | AFLOW packaged data |
| Literature and graph metadata | Crossref, OpenAlex, OpenAIRE, arXiv metadata | CORE after written/data-rights Gate, Europe PMC only for a preregistered biological-interface stratum | Semantic Scholar response data |

`INCLUDE` does not mean that every field from a source may be copied into one
uniformly licensed artifact. Each record still has to pass the catalog's
provenance and redistribution policy.

## Frozen v0 research stack

### Literature retrieval

- **B0:** Crossref only. At most eight physical requests, metadata/available
  abstract only, no body/PDF, and top-5 output.
- **E2-A:** Crossref + OpenAlex + arXiv under the same total physical-request
  budget as B0. OpenAlex contributes topics/citations/OA fields; arXiv contributes
  recent `cond-mat` preprints.
- **E2-B:** E2-A + OpenAIRE, still under the same total budget. OpenAIRE tests the
  incremental value of a separately attributed research graph.
- CORE, Semantic Scholar, and Europe PMC do not enter the primary locked
  comparison. A later source-sensitivity analysis requires a separate license
  and stratum decision.

This design isolates source value from request-volume value. Multiple databases
that resolve the same DOI/arXiv work are one evidence item with multiple
provenance records, not independent confirmations.

### Case and structure seeds

- Prefer COD, Materials Project core, exact JARVIS Figshare records, C2DB, and
  2DMatPedia for openly traceable structures or computed properties.
- Keep C2DB-derived artifacts in a separately attributed CC BY-SA 4.0 package.
- Use Crystal Net only as a mechanism weak label and family/OOD seed.
- Use Struct2Flat scores as continuous weak labels and candidate strata, not as
  binary truth.
- Use ELF fingerprints/clusters for duplicate control and OOD strata, not as a
  second independent positive label.
- Use JARVIS-WTB or a later independently frozen local calculation for
  electronic-structure checks, with interpolation-quality metadata.
- Use TQC/Materials Flatband Database identifiers only as curated anchors unless
  written redistribution permission is obtained. Resolve any publishable
  structure independently through COD or another compatible source.

## Scientific label hierarchy

The source catalog does **not** create benchmark gold labels. It creates sampling
and audit strata:

- `CURATED_SEED`: human-curated TQC anchor, stored as pointer/provenance only;
- `CANDIDATE_POSITIVE`: a source-designated computational candidate;
- `DFT_RECHECKED_SEED`: a candidate with a documented source-side calculation;
- `CONTINUOUS_WEAK`: a score such as Struct2Flat flatness;
- `MECHANISM_WEAK`: an idealized lattice/motif indication such as Crystal Net;
- `CLUSTER_OOD`: ELF structural/electronic cluster or duplicate group;
- `CONFIRMED_NEGATIVE`: allowed only after an independent, frozen calculation or
  expert determination under declared energy-window, SOC, magnetic-state,
  k-path, and numerical-quality conditions.

The first six levels guide sampling. Two independent experts and a distinct
adjudicator create the relevance, evidence, mechanism-transfer, and conflict
labels used for evaluation. Unselected or low-scoring materials are unlabeled,
not negative.

## Why weak labels cannot be treated as truth

- An ideal kagome, Lieb, line-graph, or pyrochlore sublattice can support a flat
  band in a simplified uniform nearest-neighbor model, while orbital content,
  farther-neighbor hopping, SOC, correlations, disorder, and other sublattices
  make the real band dispersive.
- Struct2Flat is a model/preprint candidate source. Its scores and selected DFT
  examples have selection bias and must not define the system's target metric.
- ELF re-encodes a preselected candidate pool. Its cluster membership is useful
  for leakage and OOD control, but is correlated with the upstream selection.
- A Wannier interpolation may be accurate on a dense mesh yet less reliable on
  an independent high-symmetry path. Narrow-band conclusions therefore require
  an interpolation/error threshold and a declared k-space protocol.
- Database absence, filter rejection, or failure to appear in a published list
  is missing annotation, never a confirmed negative.

## Split and leakage policy

Cases must be grouped before splitting by all available identities:

- normalized composition and upstream material/structure identifier;
- structure prototype or graph-isomorphism family;
- duplicate/fingerprint cluster;
- evidence-backed fine mechanism lineage. The broad mechanism vocabulary
  (kagome, Lieb, pyrochlore, moire/superlattice, confinement, orbital
  frustration, correlation-driven narrowing, interface/defect, and related
  strata) is retained for balanced sampling and OOD taxonomy, but is not itself
  treated as an independence edge;
- shared publication or database-derived candidate family.

No replayed independence group may cross development and locked test. OOD test
cases hold out complete preregistered broad-mechanism or structure families,
not merely element substitutions; the corresponding fine lineages and
structure groups must also remain disjoint. Source weak labels, source ranks,
and hidden expert labels are unavailable to the systems under test.

Structure independence is recomputed from private raw structures rather than
caller-supplied group strings. For Pilot R1, the fresh union contains the full
calibration and full R1 candidate pools; R2 additionally contains the full prior
R1 candidate pool, including replacements. A zero cross-owner component count
means only that no edge was found under the frozen code, parameters, and runtime;
it is not proof of crystallographic or physical independence.

The ordered-occupancy V2 structure policy requires exactly one geometric vacuum
axis and at least an 8 A source gap for 2D, normalizes to 15 A padding, and uses
layer-group symprec 0.05/0.10 A. Three-dimensional signatures use symprec
0.01/0.05/0.10 A and a conservative threshold union. Anonymous matching uses a
bidirectional conservative OR with bounded supercell ratios (2D 9; 3D 8), at
most 128 sites and six species per structure. Pilot computation is capped at 96
candidates and 32 union members; PreBudget owner projection is capped at 84.
Main requires a separately versioned capacity decision and dry-run.

These choices may conservatively overmerge or miss relations: the 8 A rule can
misclassify porous or large-vacuum cells, disordered occupancy is unsupported,
threshold bridging and supercell matching can create false positives, and
parent/transformation derivatives can evade structure axes. Calibration therefore
also requires two independent natural-person raw derivative reviews and a distinct
adjudicator only on class disagreement. Any raw or final class other than `NOT`
excludes the case; adjudication cannot erase a raw derivative-risk observation.
This human screening is not automated truth or experimental/DFT validation.

## Public/private data boundary

The public benchmark may contain:

- stable source identifiers, canonical URLs, factual bibliographic fields,
  source/retrieval/version identities, queries, and hashes;
- compatible open structures and selected computed fields with attribution;
- project-authored expert labels, blinded system outputs, prompts, schemas,
  split manifests, code, and aggregate results;
- derived numeric features only when their upstream license permits publication.
- after exact internal review and release authorization, only the sanitized aggregate
  `PublicBenchmarkProjectionV1`/`PublicBenchmarkResultReleaseV1`; these contain refs
  to authorization/review but not their HMAC signatures, key commitments, raw labels,
  private identity evidence, restricted source text, or provider transcripts.

Public Schema definitions, verifier code, frozen parameters, and empty or synthetic
examples do not authorize publication of an active private-custody instance.

The public benchmark must not contain by default:

- ICSD CIF/POSCAR/coordinates or bulk TQC exports;
- AFLOW, Semantic Scholar, or unapproved CORE response data;
- GNoME BY-NC, unknown-origin MPContribs, private/embargoed NOMAD, or
  incompatible Materials Cloud records;
- Crossref/arXiv/Europe PMC raw abstract or full-text strings when record rights
  are not explicitly compatible;
- article bodies, PDFs, bulk snapshots, API keys, runtime databases, or model
  provider transcripts.
- raw structure bytes/base64 payloads, private artifact URIs, active structure
  member/union releases, or native runtime-path evidence;
- derivative reviewer identity evidence, roster/assignment, raw reviews,
  adjudications, rationales, and active screening releases.
- Main candidate/eligibility/frozen/structure-union/pre-budget instances; phase
  authorizations and execution receipts; model-native visible request/response;
  reviewer manifests/private maps; raw labels and duplicate partitions; Gold and
  Analysis before authorization; locked seals/ledgers/unseal artifacts; authority
  policies, HMAC signatures/key commitments, scientific-review attestations, release
  controls, and the private Campaign root.

Raw responses used internally are isolated from public artifacts. Every selected
record must carry source, record ID, canonical URL, accessed time, exact release
or revision, license, redistribution class, upstream provenance, fields used,
and raw-response/file hash. Unknown license, noncommercial restriction, ICSD
origin, private/embargo status, or unknown upstream origin fails closed.
Structure and derivative artifacts remain private even when their upstream source
would permit redistribution: this prevents premature release of candidate ownership,
replacement lineage, reviewer identity, and pre-registration screening decisions.
Their clocks are locally replayed UTC plus monotonic observations with
`external_timestamp_attestation=false`; they are not third-party timestamps.
Active private instances should be sealed outside Git with the operational
`PrivateArtifactEnvelopeV1` 0600 wrapper. The wrapper carries a generic payload map and
is deliberately not an active research-schema root; the enclosed artifact must still
validate as one of the 73 private custody roots.

Semantic reasoning in the formal Main path is supplied only by the frozen large-model
native receipt chain. Local code is deterministic custody/replay machinery, not a
semantic reasoner. `E1-local` is closed as `NOT_RUN_USER_PROHIBITED`: zero local-model
invocations, outputs, or execution releases, with no promotion or locked use. The
locked component execution is derivation-only and never an Analysis/Gold comparison
arm. FAILED case-role cells remain in the fixed five-position denominator as
`SYSTEM_PACKET_INVALID/RUN_FAILED` zeros without creating review tasks.

Annotation and release-control HMACs prove exact replay only against internally
precommitted ephemeral keys; key material is not persisted. External natural-person/
institution authority identity, external key custody, provider execution attestation,
and external publication permission are `NOT_PROVIDED` or false. No real private Main
structure/union/annotation/Campaign instance, model call, benchmark score, scientific
finding, novelty claim, DFT validation, or material-discovery result exists in this
repository checkpoint.

## Source-specific notes

- **Materials Flatband Database/TQC:** strongest curated seed source, but the
  public site does not provide a clear database redistribution license and is
  based on ICSD. Pointer-only in v0.
- **ICSD:** official terms restrict automated extraction and redistribution;
  excluded as a structure/data source.
- **Crystal Net:** article/supplement CC BY 4.0 and code MIT; mechanism/OOD only.
- **JARVIS:** include only exact CC BY 4.0 Figshare article versions, not every
  third-party alias exposed by `jarvis-tools`.
- **C2DB/CMR:** CC BY-SA 4.0; preserve share-alike isolation and attribution.
- **Materials Project:** core CC BY 4.0 with record-level origin checks; exclude
  GNoME BY-NC and unknown MPContribs by default.
- **COD:** CC0 and preferred for openly redistributable crystal structures;
  preserve scientific citation and record revision despite the waiver.
- **NOMAD/Materials Cloud:** include only individually compatible public records;
  OPTIMADE is a transport interface, not a license.
- **AFLOW:** official scientific/academic/noncommercial terms are unsuitable for
  the default public benchmark package.
- **2DMatPedia:** open 2D structure/property substrate; retain upstream
  provenance for top-down Materials Project structures.
- **ELF/Struct2Flat:** useful open candidate features, clusters, and scores; never
  promote their model outputs to independent gold truth.
- **Crossref:** factual metadata baseline; raw abstracts stay local unless rights
  are compatible.
- **OpenAlex:** CC0 metadata, primary multi-source/citation/topic addition.
- **OpenAIRE:** CC BY graph, separately attributed and deduplicated.
- **arXiv:** descriptive metadata is open; e-print/PDF/source rights are
  work-specific.
- **CORE/Europe PMC:** conditional because underlying text rights are mixed.
- **Semantic Scholar:** excluded because the API license restricts response data
  to internal noncommercial research/education and prohibits third-party
  sharing without expanded permission.

## Gate to corpus construction

Corpus construction may start only after the preregistration freezes:

1. the case and annotation schemas;
2. source-specific query/selection rules and exact request budgets;
3. structure-family and mechanism-family split logic;
4. the list of public fields and private-only fields;
5. the expert manual and adjudication procedure;
6. model/prompt identity and leakage controls.

Any source-policy change after the 30-case pilot creates a new catalog version
and SHA. No silent replacement by a newer database snapshot is allowed.
