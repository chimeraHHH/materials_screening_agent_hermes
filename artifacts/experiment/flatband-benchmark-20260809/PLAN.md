# Flat/Narrow-Band Inspiration Benchmark Plan

Status: execution plan v0; **not yet a preregistration or scientific result**

Date: 2026-08-09 (Asia/Shanghai)

Scope: flat-band and narrow-band materials inspiration only

Explicit exclusion: novelty, prior-art, patentability, and validated-property claims

Source audit v1 is frozen in [`SOURCE_AUDIT.md`](SOURCE_AUDIT.md). Its
machine-readable catalog SHA-256 is
`57c24de8f0cf616205b03ef16231def711f2dfa9fcac86d94beede9c01b0bb1f`.

## Objective

Measure whether bounded semantic reasoning, multi-source metadata retrieval, and
mechanism-driven cross-domain Tags improve the scientific usefulness of the
current Materials Inspiration baseline under a fixed search budget. The unit of
evaluation is a ranked top-5 inspiration result for one frozen research case.

The existing production-hardening artifacts are engineering evidence only. They
do not establish retrieval recall, cross-domain transfer correctness, or
scientific usefulness.

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
5. Require pilot Krippendorff's alpha >= 0.67 before scaling.
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
- **E2 multi-source retrieval**: add only sources that pass the license/access
  audit, while retaining the same total physical-request budget as B0. The
  frozen E2-A sources are Crossref + OpenAlex + arXiv; E2-B adds OpenAIRE.
- **E3 cross-domain Tag mechanism**: generate candidates through frozen mechanism
  families and adjacent-domain transfer rules; Tags are proposed offline and
  cannot self-promote into the production graph.
- **Fusion**: only components that pass their isolated development Gate.

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
- A 20% stratified subset is repeated three times to measure reasoning stability.
- Research calls are isolated from the production policy, whose internal model
  call budget remains zero until a later explicit production release decision.

## Metrics and promotion Gates

Primary endpoint: adjudicated macro `nDCG@5` using a 0--3 relevance grade:

- 0: invalid or unsupported;
- 1: related but weak;
- 2: plausible and evidence-backed;
- 3: strong, constraint-compatible, and testable.

Secondary endpoints: Success@5, evidence precision@5, bridge correctness,
exact/strict duplicate rate, mechanism-family coverage, fill/underfill rate,
IID/OOD strata, request count, token count, and latency.

Development promotion requires all of:

- delta nDCG@5 >= 0.03;
- evidence precision decreases by no more than 3 percentage points;
- duplicate rate increases by no more than 2 percentage points;
- Success@5 decreases by no more than 2 percentage points;
- no increase in the frozen physical-search-request budget.

Locked-test success requires delta nDCG@5 >= 0.05, paired case-level bootstrap
95% confidence-interval lower bound above zero, and Holm-corrected `p < 0.05`,
while satisfying all non-inferiority constraints above.

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

## Reproducibility and publication boundary

Version only schemas, source IDs/manifests, licenses, prompts, splits, hashes,
annotations that may legally be redistributed, evaluation code, and aggregate
results. Do not commit API keys, raw runtime databases/logs, licensed structures,
bulk snapshots, article bodies/PDFs, or model weights. Every result must bind the
Git SHA, source-catalog SHA, split SHA, annotation-release SHA, and model/prompt
identity.
