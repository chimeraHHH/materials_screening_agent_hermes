# Flat/Narrow-Band Inspiration Benchmark Plan

Status: preregistration protocol draft; **not yet registered and not a scientific result**

Date: 2026-08-09 (Asia/Shanghai)

Scope: flat-band and narrow-band materials inspiration only

Explicit exclusion: novelty, prior-art, patentability, and validated-property claims

Source audit v1 is frozen in [`SOURCE_AUDIT.md`](SOURCE_AUDIT.md). Its
machine-readable catalog SHA-256 is
`57c24de8f0cf616205b03ef16231def711f2dfa9fcac86d94beede9c01b0bb1f`.

The audience-split public-protocol/private-custody bundles, preregistration, and
annotation guide are content-addressed drafts; their current digests are recorded
in adjacent `.sha256` files.  The V3 candidate/eligibility/execution chain and
reviewer-to-Gold/Pilot-agreement closure have local adversarial tests, but the
formal 2D/3D structure-group computation, final projection, and cross-release
near-duplicate union are not implemented.  These draft hashes are not an
assertion that the schemas are frozen, registration has occurred, or Pilot may
start.

## Objective

Measure whether bounded semantic reasoning, multi-source metadata retrieval, and
mechanism-driven cross-domain Tags improve the scientific usefulness of the
current Materials Inspiration baseline under a fixed search budget. The unit of
evaluation is a ranked top-5 inspiration result for one frozen research case.

The existing production-hardening artifacts are engineering evidence only. They
do not establish retrieval recall, cross-domain transfer correctness, or
scientific usefulness.

The current independent Pilot-readiness decision and required closure sequence
are recorded in [`READINESS_REVIEW.md`](READINESS_REVIEW.md).

## Ordered execution

1. Publish the current engineering baseline as a public Draft PR, with the known
   production blockers and scientific limitations visible.
2. Audit open flat/narrow-band datasets and literature indexes across the public
   web. Record license, redistribution, access, version, fields, update cadence,
   and inclusion decision before downloading bulk data.
3. Freeze a preregistration, case schema, annotation manual, source manifest,
   model/prompt identity, token/request budget, and statistical analysis plan.
4. Build and double-annotate a 30-case pilot; use a distinct adjudicator for
   disagreements and revise only the manual, not the hidden test labels.
5. Require pilot ordinal Krippendorff's alpha >= 0.80 before scaling. An alpha
   in `[0.667, 0.80)` permits one annotation-manual-only revision followed by a
   new, non-overlapping 30-case Pilot R2; R2 must reach 0.80. Alpha below 0.667,
   or a failed R2, stops scaling.
6. Freeze a 120-case benchmark: 60 development, 30 locked IID test, and 30 locked
   OOD test cases, split by material and mechanism family.
7. Run B0, then isolated E1/E2/E3 ablations under matched budgets.
8. Promote only passing components into a fusion system; run the locked test once,
   followed by robustness/error analysis and independent scientific review.

## Systems under comparison

- **B0**: current Crossref-only retrieval, curated Tags, lexical passage features
  and deduplication, at most eight physical search attempts, title/abstract/
  keywords only, no full-text/PDF, top-5 output.
- **E1 semantic reasoning**: the language model receives only bounded metadata
  packets, reasons locally, and returns a strict structured mechanism mapping.
  No external embedding API or trainable model is introduced in the first pass.
  A separately identified, local-only semantic embedding is an isolated
  sensitivity variant and cannot be silently folded into E1.
- **E2 multi-source retrieval**: add only sources that pass the license/access
  audit, while retaining the same total physical-request budget as B0. The
  frozen E2-A sources are Crossref + OpenAlex + arXiv; E2-B adds OpenAIRE.
- **E3 cross-domain Tag mechanism**: generate candidates through frozen mechanism
  families and adjacent-domain transfer rules; Tags are proposed offline and
  cannot self-promote into the production graph.
- **Fusion**: only components that pass their isolated development Gate.

All systems retain a total budget of eight physical metadata requests per case.
B0, E1, and E3 allocate all eight to Crossref. E2-A freezes a `3/3/2`
Crossref/OpenAlex/arXiv allocation; E2-B freezes `2/2/2/2` across Crossref,
OpenAlex, arXiv, and OpenAIRE. Unused allocations cannot be transferred after
seeing case results. Article-body fetches and full-PDF reads remain zero.

## Target definition and scientific strata

The benchmark uses project-defined operational strata, not a claim that the
field has one universal flat-band threshold:

- `FB100`: tracked bandwidth `W <= 0.10 eV`;
- `NB300`: `0.10 < W <= 0.30 eV`;
- `BORDER500`: `0.30 < W <= 0.50 eV`, retained for boundary/error analysis but
  not a positive target class;
- main near-Fermi window: absolute distance from the Fermi level `<= 1.0 eV`.

Isolation gap, SOC treatment, magnetic order/spin channel, Hubbard U, band
tracking method, and Brillouin-zone coverage are recorded as orthogonal axes.
A high-symmetry-line bandwidth is not relabelled as a full-BZ bandwidth, and a
source database tag is a sampling stratum rather than benchmark truth.

## LLM and local-compute boundary

- Local deterministic work first: DOI/title normalization, exact deduplication,
  BM25/TF-IDF features, graph features, metric computation, and bootstrap tests.
- The LLM may inspect at most the top 20 bounded metadata packets per case and
  must emit strict JSON containing mechanism ID, source domain, transfer
  principle, conditions, supporting span IDs, contradictions, mechanism
  signature, and confidence.
- Initial budget: at most two model calls and 12,000 input tokens per case.
- No article body, PDF, arbitrary URL content, hidden expert label, or locked-test
  annotation is provided to the model.
- Research calls are isolated from the production policy, whose internal model
  call budget remains zero until a later explicit production release decision.

## Metrics and promotion Gates

Primary endpoint: adjudicated macro absolute normalized discounted utility at
five (`aNDCG@5`) using a 0--3 relevance grade:

- 0: invalid or unsupported;
- 1: related but weak;
- 2: plausible and evidence-backed;
- 3: strong, constraint-compatible, and testable.

The primary gain is linear (`gain = grade`), and the fixed denominator is five
grade-3 hypotheses, rather than the best pooled output observed from the systems.
Missing positions and every repeated strict-hypothesis cluster after its first
occurrence receive zero gain. Exponential gain (`2^grade - 1`) is sensitivity
analysis only. This is deliberately named `aNDCG`, not classic query-relative
`nDCG`.

Secondary endpoints: Success@5, StrongSuccess@5, EvidenceValid@5, bridge correctness,
exact/strict duplicate rate, mechanism-family coverage, fill/underfill rate,
IID/OOD strata, request count, token count, and latency.

Development promotion requires all of:

- delta aNDCG@5 >= 0.03;
- EvidenceValid@5 decreases by no more than 3 percentage points;
- duplicate rate increases by no more than 2 percentage points;
- Success@5 decreases by no more than 2 percentage points;
- no increase in the frozen physical-search-request budget.

The single locked primary comparison is Fusion versus B0. Its effect is the
equal-weight mean of locked-IID and locked-OOD paired case deltas. Success
requires delta `aNDCG@5 >= 0.05`, a 50,000-replicate leakage-group bootstrap
95% confidence-interval lower bound above zero, and a one-sided 100,000-draw
paired leakage-group randomization `p < 0.05`. Because there is one primary
hypothesis, it is not Holm adjusted. The declared secondary family (IID, OOD,
and leave-one-component comparisons) is Holm adjusted.

Locked descriptive safety guardrails are `-0.05` for Success@5 and
EvidenceValid@5, and `+0.05` for duplicate rate. They are point-estimate
guardrails, not inferential non-inferiority claims. Development-set component promotion remains
stricter: `aNDCG@5` delta at least 0.03, EvidenceValid@5 decrease no more than
0.03, duplicate-rate increase no more than 0.02, and Success@5 decrease no more
than 0.02. No threshold may be changed after viewing locked-test annotations.

## Candidate mechanism families

The initial frozen candidate vocabulary covers kagome, Lieb, line-graph and
pyrochlore motifs; destructive interference and compact localized states;
orbital frustration/hybridization; symmetry-induced flattening; moire and
superlattice effects; confinement; correlation-driven narrowing; and strain,
interface, or defect effects. Adjacent evidence domains may include photonic,
acoustic, mechanical, circuit, and cold-atom systems.

These are query and annotation strata, not accepted scientific truths. The
formal Tag release requires expert decisions and a versioned graph hash.

## Current blockers and stop rules

- The repository currently contains metric/review/semantic engineering schemas
  and synthetic tests, but no real adjudicated benchmark. No scientific score is
  claimed before the expert Gate.
- Independent preregistration review remains `Pilot NO-GO`.  The current first
  blocker is structure leakage: raw structures and dimensionality evidence can be
  replayed locally, but layer-group/3D multi-tolerance components, final-case
  projection, and calibration/R1/R2 cross-release union are still absent.  The
  V3 reviewer, failure-denominator, raw/adjudication/Gold, and agreement closures
  do not compensate for an invalid independence graph, and the draft contract
  bundles are not a completed schema freeze.
- Public production remains NO-GO. In particular, a hard-killed parent worker can
  leave its independently-sessioned action child alive until the child's own
  deadline; the parent-death cleanup contract is not yet closed.
- A real provider-backed one-command production deployment remains unverified.
- The public repository has CI workflow code but no protected-branch or required-
  review evidence.
- Stop and reassess if the pilot agreement Gate fails twice after one manual-only
  revision, if licenses prevent a reproducible benchmark, or if improvements
  require unequal search budgets or hidden-label leakage.
- Source-side candidate, motif, cluster, or model outputs are sampling strata,
  never gold labels. Gold judgments come only from the preregistered independent
  expert/adjudication process.

## Decision checkpoint: repair before Pilot

- **Verdict:** `PILOT_NO_GO`; canonical action `iterate`.
- **Decisive evidence:** independent annotation/contract red-team findings in
  [`READINESS_REVIEW.md`](READINESS_REVIEW.md), plus executable counterexamples
  showing identity leakage, incomplete denominator closure, one-sided invalid
  labels disappearing from grade alpha, and caller-controlled duplicate groups.
- **Action:** preserve the current draft and source audit, implement the bounded
  reviewer projection, leakage connected components, execution matrix, one-way
  budget/ranking/terminal chain, and raw-review/final-gold/duplicate closure; then
  obtain a fresh independent Pilot-readiness review.
- **Rejected alternative:** starting source adapters, LLM calls, or expert Pilot
  now would create outputs whose denominator, masking, independence unit, and gold
  provenance cannot be reconstructed; those outputs would be scientifically
  unusable even if their processes completed successfully.
- **Next direction:** only a fresh `PILOT_GO` decision may activate the frozen
  30-case R1 manifest. Until then, no real case construction or performance
  comparison is authorized by this research plan.

### 2026-08-09 provenance-closure checkpoint

- **Verdict/action:** retain `PILOT_NO_GO` / `iterate`; this is a repair of the
  scientific denominator and provenance chain, not a production-hardening Gate.
- **New evidence:** the current research unit/adversarial subset passes 91 tests
  and the already-pushed Draft PR head has a successful GitHub Actions run, but
  the live schema generator parity check fails because local research contracts
  have moved beyond the committed draft bundle.
- **Remaining first-order blockers:** authoritative full-case and pre-run
  eligibility releases; reviewer-safe case constraints; exact Execution-to-
  reviewer-to-raw-to-adjudication-to-pooled-Gold coverage; replayable leakage
  grouping; exact Pilot agreement inputs; private expert-independence and
  calibration-disjointness evidence; receipt-to-span evidence provenance;
  campaign prerequisites; and analysis rows derived only from Execution and
  Gold.
- **Rejected interpretation:** neither the 91 passing local tests nor the green
  CI for the older pushed head is evidence that preregistration is frozen or
  that real Pilot labeling may begin.
- **Leakage repair decision:** the first V2 replay exposed a mathematical
  contradiction: using the ten-value broad `MechanismFamily` enum itself as a
  connected-component edge caps the complete study at ten independent units,
  so it cannot coexist with the preregistered Main `20/10/10` component minima.
  Broad mechanism remains a sampling stratum and typed OOD holdout taxonomy;
  the independence graph must instead use a finer, frozen, evidence-backed
  mechanism-lineage identity.  The component minima will not be lowered and
  caller-created per-case mechanism groups are forbidden.  Formal Main and
  AnalysisInput remain stopped until an honest Main120 positive fixture and
  adversarial lineage/holdout tests pass.

## Reproducibility and publication boundary

Version only schemas, source IDs/manifests, licenses, prompts, splits, hashes,
annotations that may legally be redistributed, evaluation code, and aggregate
results. Do not commit API keys, raw runtime databases/logs, licensed structures,
bulk snapshots, article bodies/PDFs, or model weights. Every result must bind the
Git SHA, source-catalog SHA, split SHA, annotation-release SHA, and model/prompt
identity.
