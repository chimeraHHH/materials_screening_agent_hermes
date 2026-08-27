# Flat/Narrow-Band Inspiration Benchmark Plan

Status: full-flow contract draft implemented; **real execution not run, not registered, not a scientific result**

Date: 2026-08-10 (Asia/Shanghai)

Scope: flat-band and narrow-band materials inspiration only

Explicit exclusion: novelty, prior-art, patentability, and validated-property claims

Source/custody audit v3 is recorded in [`SOURCE_AUDIT.md`](SOURCE_AUDIT.md). Its
machine-readable catalog SHA-256 is
`57c24de8f0cf616205b03ef16231def711f2dfa9fcac86d94beede9c01b0bb1f`.

The audience-split public-protocol v2/private-custody v3 bundles and all five protocol
documents are content-addressed drafts; current digests are recorded in adjacent
`.sha256` files. The code now implements the formal path from Pilot custody through
Main sampling/freeze, model-native Arms, Execution, signed Gold, Analysis V2,
development promotion, locked seal/authorization/unseal, claim support, signed review
and release controls, to a sanitized public result and one private Campaign root.
This is contract/replay capability with synthetic adversarial evidence. It is not a
real structure/annotation/Campaign instance, model call, benchmark release, schema
registration, or authorization to start Pilot.

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
   structure runtime/parameters, derivative policy/roster, model/prompt identity,
   token/request budget, and statistical analysis plan.
4. Build the disjoint calibration set; require two independent raw derivative
   reviews and a distinct adjudicator only on class disagreement, while rejecting
   calibration if any raw or final class is not `NOT`. Then double-annotate a
   30-case pilot and revise only the manual, not hidden test labels.
5. Require pilot ordinal Krippendorff's alpha >= 0.80 before scaling. An alpha
   in `[0.667, 0.80)` permits one annotation-manual-only revision followed by a
   new, non-overlapping 30-case Pilot R2; R2 must reach 0.80. Alpha below 0.667,
   or a failed R2, stops scaling.
6. Freeze a 120-case design: 60 development, 30 locked IID test, and 30 locked
   OOD test cases, split by the full leakage graph. Main structure capacity must
   have a separately versioned decision and dry-run; Pilot caps cannot be reused.
   The OOD holdout selection algorithm is constraint-aware with a frozen
   deterministic fallback order (see preregistration section 5.4).
7. When separately authorized in the future, run development B0/E1/E2/E3 through
   model-native receipts, then development Fusion. `E1-local` is formally closed as
   `NOT_RUN_USER_PROHIBITED` and cannot be promoted.
8. Freeze locked component derivations, B0/Fusion and three Fusion-minus roles before
   unseal; run locked labels through Gold/Analysis once, then require internally signed
   scientific review and license/privacy/custody release controls. No such real run is
   part of the present implementation checkpoint.

## Systems under comparison

- **B0**: current Crossref-only retrieval, curated Tags, lexical passage features
  and deduplication, at most eight physical search attempts, title/abstract/
  keywords only, no full-text/PDF, top-5 output.
- **E1 semantic reasoning**: the language model receives only bounded metadata
  packets, uses the frozen large-model native reasoning path, and returns a strict
  structured mechanism mapping with exact visible request/response receipts.
  No external embedding API, local semantic model, or trainable model is part of
  formal E1. The separately identified E1-local branch is not run.
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

## Structure and derivative leakage Gate

Private raw structures are content-addressed before computation. Ordered 2D inputs
must have exactly one geometric vacuum axis with source gap at least 8 A, are
normalized to 15 A padding, and use layer-group symprec 0.05/0.10 A. Ordered 3D
inputs use symprec 0.01/0.05/0.10 A with a conservative cross-threshold union.
Anonymous `StructureMatcher` fitting is a bidirectional conservative OR with
`attempt_supercell=true`, bounded ratios 9 for 2D and 8 for 3D, at most 128 sites
and six species per structure. Pilot V0 allows at most 96 compute candidates,
32 union members, and 84 PreBudget owner rows. The 96 cap covers calibration at
most 12 plus Pilot R1/R2 at most 36 each; it does not authorize Main. Runtime
identity binds the platform/Python, lockfile, scientific packages, native spglib
extension/resolved `libsymspg`, and grouping-module hashes; formal native replay
currently supports Darwin and Linux only.

R1 recomputes one fresh raw-structure union over the full calibration and current
candidate pools. R2 adds the full prior-R1 candidate pool; replacements remain in
scope even when not selected. Caller group keys and selected-case-only checks are
not evidence of independence. A zero cross-owner count means only no edge under
the frozen algorithm/runtime, not physical or crystallographic independence.

Calibration derivative screening uses two independent natural-person raw reviews
per exact case and one distinct adjudicator if and only if their classes disagree.
The taxonomy is `NOT`, `VACANCY`, `INTERCALATION`, `NON_STOICHIOMETRIC`, and
`ORDERED_DEFECT`; evidence is an exact nonempty subset of that case's source
records. Any raw or final non-`NOT` class excludes the case. Adjudication remains
auditable but cannot wash a raw risk back into calibration. Human screening is not
automated truth, experimental validation, or DFT validation.

Formal structure chronology is `input seal < start <= completion <= computation
creation < final-case declaration <= private release`; union chronology requires
all members before its seal and `seal < start <= completion <= computation creation
< verification < PreBudget < every budget`. UTC and monotonic clocks are local and
carry no external timestamp attestation. Known limitations include porous/large-
vacuum false classification, unsupported disordered occupancy, threshold bridging,
conservative supercell false positives, and derivative relations that evade the
frozen evidence axes.

## LLM and local-compute boundary

- Semantic judgment, mechanism transfer, evidence synthesis, and structured ranking
  are performed only by the frozen large-model native path. Work-item, response, and
  receipt artifacts bind exact visible JSON bytes, model/revision, usage, and time;
  chain of thought is neither requested nor stored.
- Local deterministic code is limited to DOI/title normalization, exact deduplication,
  bounded retrieval/projection, schema parsing, hashing, exact replay, fixed metric
  computation, and preregistered statistical procedures. It is not a semantic reasoner.
- The large model may inspect at most the top 20 bounded metadata packets per case and
  must emit strict JSON containing mechanism ID, source domain, transfer
  principle, conditions, supporting span IDs, contradictions, mechanism
  signature, and confidence.
- Initial budget: at most two model calls and 12,000 input tokens per case.
- No article body, PDF, arbitrary URL content, hidden expert label, or locked-test
  annotation is provided to the model.
- Research calls are isolated from the production policy, whose internal model
  call budget remains zero until a later explicit production release decision.
- E1-local has a content-addressed `NOT_RUN_USER_PROHIBITED` release with zero
  execution, output, and local-model invocation counts and no promotion/locked use.

Seven formal Main execution releases cover development ablation/Fusion, locked
component derivation, locked B0/Fusion, and the three Fusion-minus roles. The 240
locked component cells are complete derivation preimages only, never Gold/Analysis
comparison arms or denominator entries. Every other authorized case-role, including a
FAILED cell, retains five positions. FAILED produces no reviewer task or raw label;
Gold/Analysis deterministically assign five `SYSTEM_PACKET_INVALID/RUN_FAILED` zeros.

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

The Fusion combination operator is frozen per component subset before Pilot: the
retrieval layer uses the selected E2 variant's frozen request allocation when E2
is promoted (otherwise B0's Crossref 8); the candidate layer unions B0 lexical
routes with the frozen E3 TagGraph routes when E3 is promoted; the ranking layer
applies E1's frozen semantic rerank within the same 2-call/12,000-token budget
and 20-packet deterministic selection rule when E1 is promoted; top-5 selection
always uses B0's frozen dedup/diversity selector. Subsets containing E2 are
frozen separately for the E2-A and E2-B variants, giving 12 content-addressed
configurations before Pilot.

A companion descriptive alpha restricted to double-`ASSESSABLE` units, with the
count and share of `(0,0)` invalid-pair units, is always reported and is not a
Gate; if the Gate alpha passes while the companion alpha falls below 0.667, the
mechanical-agreement contribution must be quantified and a data-integrity review
completed before continuing. The binary-gain endpoint downgrade drafted in
preregistration v0.5 was withdrawn after the 2026-08-10 protocol red team: its
claimed 1.5x worst-case threshold conversion was mathematically wrong (the true
worst case, grade 1-to-2 transitions, amplifies 3x). The agreement Gate is
strictly graded alpha with no downgrade branch.

## Candidate mechanism families

The initial frozen candidate vocabulary covers kagome, Lieb, line-graph and
pyrochlore motifs; destructive interference and compact localized states;
orbital frustration/hybridization; symmetry-induced flattening; moire and
superlattice effects; confinement; correlation-driven narrowing; and strain,
interface, or defect effects. Adjacent evidence domains may include photonic,
acoustic, mechanical, circuit, and cold-atom systems.

These are query and annotation strata, not accepted scientific truths. The
formal Tag release requires expert decisions and a versioned graph hash.

## Expert resources and workload budget

Pilot R1 has an upper bound of 30 cases x 4 systems x top-5 = 600 pooled units;
exact-packet pooling merges only byte-identical packets, and the realized unique
unit count is recorded after execution. The frozen planning assumption is 5-10
minutes per unit, a 10-20 minute per-case duplicate partition, and a 10-15
minute per-case pre-run audit, giving an R1 budget of 60-118 hours per reviewer
on a consistent per-item basis (lower bound 50+5+5, upper bound 100+10+7.5
rounded up) with no pooling discount, plus 5-10 calibration hours; a triggered
R2 approximately doubles the R1 part. No undeclared pooling assumption may
lower this budget. Main review volume is roughly 4-5x Pilot. The Main capacity
decision must recompute the Main budget from measured Pilot per-unit times, and
Main must not start if that budget exceeds the experts' written time
commitments.

The calibration set doubles as a timing pilot: every calibration completion
record stores actual per-unit time, and the median `m` (minutes per unit) pools
both reviewers' calibration units. Two frozen reduction levels apply. Level 1:
if `m > 8`, a pre-registered scope-reduction revision (new protocol SHA, before
Pilot R1) drops E2-B from the Pilot system set, lowering the unit cap to 450.
Level 2: with projected hours `H = (unit cap x m)/60 + 18` (partition and audit
allowance covering the 17.5-hour sub-item maximum, rounded up), if `H` still
exceeds any reviewer's written commitment after level 1, E1 is dropped and
B0/E3 are kept (E3's cross-domain bridge packets stress the manual hardest and
must keep human coverage), lowering the unit cap to 300; if `H` still exceeds
the commitment, the scope is renegotiated as a pre-registration decision.
Level 2 is evaluated independently of whether level 1 triggered: if `m <= 8`
but `H` at the current unit cap exceeds the commitment, level 1 is applied
first, `H` recomputed, and level 2 applied only if it still exceeds. Case count (30) and top-5 depth are never reduced.
If E2-B is dropped, the absence of multi-source packet styles from the Pilot is
a recorded limitation, and the calibration recheck before Main annotation must
include at least two multi-source-style units. Expert time commitments and
compensation or acknowledgement terms are recorded in private
`ExpertStudyRegistryV2` fields, including explicit unpaid commitments.

## Current blockers and stop rules

- The full formal contract path and its audience-split schemas exist, but no real
  private Main structure/union, human annotation, signed Campaign, provider model
  call, adjudicated benchmark, or scientific score exists.
- Independent preregistration status remains `Pilot NO-GO`. The remaining blockers
  are real source/structure/expert/authority custody inputs, externally verifiable
  identities and permissions, and a fresh user decision after those inputs exist.
  Performance and benchmark measurement are intentionally deferred and are not a
  blocker for this contract-implementation checkpoint.
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
- **Decisive evidence:** the invariant red team accepts the implemented formal chain,
  including exact execution preimages, HMAC-authenticated Main annotations/reviews,
  FAILED denominator closure, derivation-only locked components, one-shot unseal
  custody, and sanitized public projection. This is implementation evidence only.
- **Action:** freeze the 16-root public and 73-root private schema/document identities;
  later create real private source/structure/expert/authority inputs and obtain a fresh
  independent Pilot-readiness decision. Do not run performance or benchmark work now.
- **Rejected alternative:** starting source adapters, LLM calls, or expert Pilot
  now would create outputs whose denominator, masking, independence unit, and gold
  provenance cannot be reconstructed; those outputs would be scientifically
  unusable even if their processes completed successfully.
- **Next direction:** only a fresh `PILOT_GO` decision may activate the frozen 30-case
  R1 system run. Until then, no model call or performance comparison is authorized.

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

### 2026-08-10 structure/derivative closure checkpoint

- **Invariant result:** structure chronology, warm-cache replay, union owner/clock/
  fully-readdressed attacks, calibration exact/foreign/late/raw-wash attacks, and
  the shared R1 matrix semantics passed independent review.
- **Historical result:** this checkpoint retained `PILOT_NO_GO` while the full Main
  path was incomplete. Its resource observations are not an active Gate for the
  present user-directed full-flow implementation pass.
- **Historical schema decision:** public v1/private v2 was superseded by the full-flow
  audience split below; legacy bundle files remain immutable history.

### 2026-08-10 full-flow contract checkpoint

- **Implementation result:** Main candidate/eligibility/freeze/pre-budget custody,
  model-native work/response/receipt/Arm traces, seven formal execution releases,
  signed Main Gold and exact Analysis V2 adapters, promotion/Fusion, locked seal/plan/
  authorization/ledger/unseal, claim support, signed reviewer/control decisions,
  sanitized public result, and complete private Campaign assembly are implemented.
- **Semantic-compute boundary:** large-model native calls are the only formal semantic
  reasoner. Deterministic local code only validates, hashes, parses, replays, and
  computes fixed metrics. E1-local is `NOT_RUN_USER_PROHIBITED` with zero artifacts.
- **Denominator boundary:** locked components are derivation-only. Every comparison
  case-role keeps five positions; FAILED cells generate five fixed zeros and no human
  review task.
- **Authenticity boundary:** annotation, scientific-review, and release-control HMACs
  are exact internal replay against precommitted ephemeral keys. External authority
  identity, external key custody, provider execution, and publication permission are
  not provided; no signing key or chain of thought is stored.
- **Audience/schema decision:** public protocol v2 has 16 active roots, including the
  source-catalog checkpoint and aggregate projection/result. Private custody v3 has 73
  roots, including one complete Pilot-round bundle and independently transferred Main/
  lifecycle artifacts. Leaf rows/refs remain nested. `PrivateArtifactEnvelopeV1` is an
  operational 0600 storage wrapper, not a research-schema root; active private instances
  should be sealed in that envelope outside Git.
- **Evidence ceiling:** no real private Campaign, model execution, benchmark value,
  scientific finding, novelty, DFT validation, or material discovery is claimed.

### 2026-08-10 user-review adoption checkpoint

- **Scope:** the user-directed manual review required by `READINESS_REVIEW.md`
  step 1 (scientific definitions, Pilot workload, alpha thresholds, E2 selection,
  OOD meaning, safety guardrails, expert COI policy) was performed by an AI
  assistant and its findings were adopted by the user on 2026-08-10.
- **Adopted changes:** an expert workload budget and calibration timing pilot
  with a frozen scope-reduction order; a per-subset frozen Fusion operator;
  operational COI rules, a backup-expert calibration precondition, and a frozen
  aggregate expert-description template; `min_k |E(k)-E_F|` and
  single-tracked-band `W` definitions with the 1.0 eV window rationale; a
  companion double-`ASSESSABLE` alpha and `(0,0)` share report; a
  constraint-aware OOD holdout fallback order; and a single pre-declared
  binary-gain endpoint downgrade evaluated only after R2.
- **Boundary:** this review is analysis evidence adopted by the user; it is not
  an independent red team, external authority, or expert instance. `PILOT_NO_GO`
  and the remaining real-custody blockers are unchanged. The revised protocol
  drafts (preregistration v0.5, annotation guide v0.6, this plan) require a
  fresh independent red-team pass before any `PILOT_GO` decision.

### 2026-08-10 post-adoption protocol red-team checkpoint

- **Verdict:** the fresh independent protocol red team on preregistration v0.5 /
  annotation guide v0.6 returned FAIL with two blockers and ten major findings;
  `PILOT_NO_GO` is retained.
- **B1:** the binary-downgrade threshold conversion claimed 1.5x worst-case
  equivalence, but the true worst case (grade 1-to-2 transitions) amplifies 3x;
  the downgrade mode was therefore withdrawn entirely rather than re-derived.
- **B2:** reviewer-assigned `SYSTEM_PACKET_INVALID` criteria overlapped the
  guide's own ASSESSABLE grade-0 hard-fail criteria while any single-sided
  invalid failed the whole round; the criteria are now narrowed to
  mechanical/structural failure, and single-sided invalids enter alpha as
  `(0, g)` with per-unit logging and a 5% integrity-review trigger.
- **Majors fixed:** cross-document downgrade contradictions (M1, resolved by
  withdrawal); unified `CASE_INVALID` handling for Pilot and Main (M2);
  workload arithmetic corrected to a 60-115 hour no-pooling upper bound with an
  evaluable two-level reduction trigger and a fixed second-level drop order
  (M3/M4); Fusion configurations split per E2 variant into 12 (M5); frozen
  E_F, spin-channel, multi-band, and missing-coverage stratum-assignment rules
  (M6); pairwise COI relation constraints, a backup adjudicator, signed
  self-attestations, and recusal timing (M7/M8); near-Fermi distance strata
  added to pre-declared secondary reporting (M9); companion-alpha guards
  retained with the downgrade removed (M10). Minor findings
  m1/m2/m4/m5/m7/m9/m10 were also applied.
- **Boundary:** these repairs are protocol-text changes (preregistration v0.6,
  annotation guide v0.7). They require another independent red-team pass; real
  expert, structure, and custody instances remain absent and blocking.

### 2026-08-10 third-round protocol red-team checkpoint

- **Verdict:** the third independent red team verified 10 of 12 second-round
  repairs closed, with all arithmetic re-derived and confirmed (the 3x
  worst-case binary amplification, unit caps 600/450/300, 12 Fusion
  configurations, both frozen metric denominators, and the three near-Fermi
  strata); the protocol layer remained FAIL on one missed spot.
- **N1 (blocker, fixed):** annotation-guide section 13 still carried the
  pre-repair "single-sided invalid / any CASE_INVALID fails the whole round"
  sentence, contradicting the repaired section 4 and preregistration section
  8; the sentence is now aligned (guide v0.8).
- **N2 (major, fixed):** the invalid-case cap was only defined for the locked
  60; Pilot rounds now fail direct passage when symmetric `CASE_INVALID`
  exclusions exceed 10% of 30 (a fresh disjoint round is required), and
  development promotion halts above 5% of 60 (preregistration v0.7).
- **Minors fixed:** workload bounds restated on a consistent per-item basis as
  60-118 hours with an 18-hour allowance (N3); malformed packets routed
  exclusively to `SYSTEM_PACKET_INVALID` (N4); level-2 reduction evaluated
  independently of level 1 (N5); records lacking both E_F and a gap fail
  closed out of positive strata (N6); adjudicator plus backup-adjudicator
  double recusal excludes the unit symmetrically (N7); the Main-side
  consequence of "cannot pass directly" is defined as blocking Gold/Analysis
  assembly until the integrity review is signed (N8).
- **Boundary:** these are protocol-text repairs (preregistration v0.7,
  annotation guide v0.8) pending a further independent verification pass;
  real expert, structure, and custody instances remain absent and blocking.

## Reproducibility and publication boundary

Version only schemas, source IDs/manifests, licenses, prompts, splits, hashes,
annotations that may legally be redistributed, evaluation code, and aggregate
results. Do not commit API keys, raw runtime databases/logs, raw structure bytes or
private URIs, active structure member/union releases, derivative identity/roster/
raw-review/adjudication instances, licensed structures, bulk snapshots, article
bodies/PDFs, or model weights. Every result must bind the
Git SHA, source-catalog SHA, split SHA, annotation-release SHA, and model/prompt
identity.
