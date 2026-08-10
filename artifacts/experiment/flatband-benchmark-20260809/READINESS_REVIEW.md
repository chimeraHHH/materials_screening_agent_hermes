# Flat/Narrow-Band Research Readiness Review

Decision: **PILOT NO-GO**

Date: 2026-08-10 (Asia/Shanghai)

Scope: independent red-team review of the preregistration draft, annotation guide,
research contracts, metrics, statistics, source policy, and proposed 30/120-case design.
This is a full-flow contract/design review, not a benchmark result.

## What is credible now

- The public Draft PR and 20-source license/provenance audit are real release evidence.
- The target is explicitly limited to flat/narrow-band inspiration; novelty is excluded.
- `FB100`, `NB300`, `BORDER500`, the `<=1 eV` Fermi window, and SOC/magnetism/
  k-space coverage are separated instead of collapsed into one source label.
- Linear fixed-denominator `aNDCG@5`, exponential-gain sensitivity, repeated-cluster
  zeroing, ordinal Krippendorff alpha, Holm adjustment, cluster bootstrap, and sign
  randomization have executable local tests.
- Randomization now uses exact enumeration when `2^G<=100000`, otherwise 100,000
  fixed-seed Monte Carlo draws; paired inference requires at least ten independent
  connected components per requested stratum.
- The current draft artifacts are content-addressed and explicitly marked as drafts.
- Public protocol v2 has 16 explicit roots: safe protocol identities, the public
  source-catalog checkpoint, and aggregate projection/result only. Private custody
  v3 has 73 explicit roots, including a complete Pilot-round bundle and independently
  stored/transferred Main, model-native, Gold, Analysis, locked-lifecycle, HMAC-review,
  release-control, and Campaign artifacts. Leaf rows/refs remain nested.
- Synthetic invariant review accepts strict member/union chronology, deterministic
  replay, full-pool owner cover, fully-readdressed tamper rejection, conservative
  raw derivative-risk preservation, reviewer masking, denominator, and Gold closure.
- The full formal Main path is implemented. Semantic judgments use only large-model
  native work/response/receipt artifacts; local code is deterministic validation,
  hashing, parsing, replay, and fixed-metric machinery. E1-local is content-addressed
  as `NOT_RUN_USER_PROHIBITED` with zero invocation/output/execution counts.
- Locked component cells are derivation-only. FAILED comparison cells preserve five
  `SYSTEM_PACKET_INVALID/RUN_FAILED` zeros and create no review units or labels.
- Main annotation, scientific-review decision, and license/privacy/custody control
  payloads carry precommitted HMAC-SHA256 signatures. Signing keys and chain of thought
  are not persisted.

These facts establish an auditable implementation and tested formulas. They do not
establish a real source/structure/annotation/Campaign instance, provider execution,
retrieval recall, expert agreement, benchmark performance, scientific usefulness,
novelty, DFT validation, or a validated/discovered material.

## Why Pilot cannot start

### 1. Final identity and real private custody

The schemas and documents are still draft checkpoints, not an external registration.
No real raw-structure member release, fresh calibration/R1/R2 union, derivative roster/
assignments/reviews, all-`NOT` calibration release, COI map, or expert completion record
exists. Synthetic fixtures demonstrate invariants but cannot substitute for these
content-addressed natural-person and source artifacts.

### 2. External authenticity and publication authority

Internal HMAC exact replay is closed only against precommitted ephemeral keys. The
repository does not contain externally attested natural-person/institution authority
identity, external key custody, provider execution attestation, or external publication
permission. These fields remain `NOT_PROVIDED` or false. An internally self-consistent
authorization is not evidence that a real institution approved release.

### 3. Structure/derivative evidence boundary

The frozen structure algorithm is a deterministic leakage heuristic, not proof of
crystallographic or physical identity. The 8 A vacuum threshold may misclassify porous
cells; disordered occupancy is unsupported; threshold union and anonymous supercell
matching can overmerge; parent/transformation derivatives can evade the axes. Human
screening uses two independent raw reviews and distinct adjudication only on class
disagreement; any raw or final non-`NOT` class excludes calibration. Real evidence and
reviewers are still absent.

### 4. Runtime, source, system, and expert instances

Formal structure instances must bind the exact code, platform, Python, lockfile,
pymatgen/spglib/numpy/scipy, native spglib extension and resolved `libsymspg` hashes.
System configs, prompt/model/tokenizer, source snapshots, queries, byte/document/cache
caps, expert registry, COI/recusal, annotation signing commitments, release-control
evidence, and the final guide SHA remain to be instantiated.
Local UTC/monotonic chronology has no external timestamp attestation.

### 5. Main capacity and fresh decision

Pilot V0 caps computation at 96 candidates and union at 32 members, with 84 PreBudget
owner rows. They cover calibration at most 12 plus R1/R2 at most 36 each and cannot be
reused for Main. Main needs a separately versioned capacity policy, honest Main120
structure/union and expert instance, and independent readiness review. The separately
versioned capacity and full-flow contracts now exist, but no real Main120 run does.
`AnalysisInputReleaseV1` remains retired; only exact-replayed `AnalysisInputReleaseV2`
is active.

## Frozen intended analysis after closure

- Pilot R1: 30 cases, 15/15 FB100/NB300, 15/15 2D/3D, at least five mechanisms,
  maximum six cases per mechanism, and at least ten independent components.
- Agreement: raw pre-adjudication ordinal alpha; `>=0.80` pass; `[0.667,0.80)` permits
  one guide-only revision plus a disjoint 30-case R2; lower or failed R2 stops.
- Main: development 60, locked IID 30, locked OOD 30; at least 20/10/10 independent
  components, with family-disjoint splits.
- Primary: Fusion versus B0, equal IID/OOD `aNDCG@5` delta; observed delta `>=0.05`,
  50,000 cluster-bootstrap CI lower bound `>0`, and one-sided cluster sign test `p<0.05`.
- Success/EvidenceValid/duplicate thresholds are descriptive safety guardrails, not
  inferential non-inferiority claims.
- E2 selection: prefer E2-A unless E2-B passes all Gates and improves aNDCG by at least
  0.01; Fusion is the fixed combination of all eligible components, not a searched
  subset, and must itself pass development before locked labels are released.

## Required next sequence

1. User manually reviews the scientific definitions, Pilot workload, alpha thresholds,
   E2 selection rule, OOD meaning, safety guardrails, and expert COI policy.
2. Close the public-v2/private-v3 schema and five-document hash chain. Keep all active
   private instances outside GitHub and seal them through the operational
   `PrivateArtifactEnvelopeV1` 0600 wrapper; that wrapper is not a research root.
3. Create real private structure member releases and fresh full-pool union; create the
   derivative policy, three-person roster, exact assignments, two raw reviews per case,
   disagreement-only adjudications, and conservative all-`NOT` calibration release.
4. Obtain external authority identity/key-custody/publication evidence or continue to
   report them explicitly as unavailable; internal HMAC cannot fill that gap.
5. Freeze expert/COI registry, system/prompt/model/query/cache identities, Pilot R1
   manifest, analysis seeds, and final preregistration identity; repeat independent
   release review.
6. Only a resulting `PILOT_GO` may activate the 30-case R1 system run. Do not construct
   or inspect Main labels first.

Performance and benchmark measurement are deliberately deferred by the user and are
not part of this contract-implementation readiness decision.

## Engineering track remains separate

The GitHub CI failure observed before this review was an engineering portability bug:
GNU `ps` did not provide stable process identity. Linux now uses `/proc/<pid>/stat`
start ticks plus raw `/proc/<pid>/cmdline`, with PID-reuse double-read and fail-closed
schema v3. This repair is necessary for a green Draft PR, but it does not change the
scientific Pilot NO-GO decision. Production also remains NO-GO until parent hard-kill
cannot orphan an independently-sessioned action child.
