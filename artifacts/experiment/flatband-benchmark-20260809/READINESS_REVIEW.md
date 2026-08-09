# Flat/Narrow-Band Research Readiness Review

Decision: **PILOT NO-GO**

Date: 2026-08-09 (Asia/Shanghai)

Scope: independent red-team review of the preregistration draft, annotation guide,
research contracts, metrics, statistics, source policy, and proposed 30/120-case design.
This is a design review, not a benchmark result.

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

These facts establish an auditable plan and tested formulas. They do not establish
retrieval recall, expert agreement, scientific usefulness, or a validated material.

## Why Pilot cannot start

### 1. Denominator and provenance closure

There is not yet one authoritative assembler for every expected `case × system` cell.
A valid release must include failed runs and empty rankings, map missing positions to
zero, and join budget manifest, terminal ledger, ranking, masked packet, two raw reviews,
adjudication/agreement, final gold, and metric rows. A caller must not be able to omit a
failed case or an entire system and still obtain a valid score.

### 2. Reviewer-safe projection

The draft blinding object still contains system/rank contributions. A reviewer-facing
schema must physically omit system/run/ranking/rank, provider adapter, system-proposed
duplicate group, peer label, and private identity map. It must instead provide a common
renderer and immutable, bounded evidence excerpts/scopes that experts can actually
verify without browsing. The study should claim identity masking, not perfect blinding,
and measure post-label system-origin guesses.

### 3. Leakage and OOD identity

Each case has multiple leakage axes, while statistical code accepts one cluster ID.
All composition/prototype/fingerprint/article/mechanism relations must first form a
graph; its frozen connected component becomes the single split/resampling identity.
Every OOD case must hit a declared holdout family and every development/IID case must
not. OOD is relative to benchmark development families, not proof of absence from LLM
pretraining or historical databases.

### 4. Expert-label and duplicate closure

The draft permits some labels forbidden by the guide, lacks exact evidence-link
coverage, and does not yet verify that adjudication references the real two raw reviews,
registry, guide, packet, and disagreement. Duplicate grouping is relational and cannot
be a system-supplied or per-unit free string; experts must partition all anonymous
packets within one case, and a final adjudicated partition must be the only input to
duplicate-aware metrics.

### 5. Run identity and matched budgets

Ranking and terminal cost records need a one-direction hash chain rather than mutual
references. Literal `SystemConfig` arms must enforce B0/E1/E2-A/E2-B/E3 intervention,
source allocation, LLM/local-model state, TagGraph, query plan, page size, record/byte/
document caps, and cache policy. Eight physical requests is a matched request cap, not
automatically a matched information budget.

### 6. Expert registration and conflicts

A boolean calibration flag is insufficient. Each expert needs a content-addressed
completion record binding role, guide, disjoint calibration set, pre-discussion raw
answers, and time. Work/case-level conflict-of-interest, recusal, and substitute-expert
rules must be frozen before outputs; otherwise recognizable papers can shrink the
denominator asymmetrically.

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
2. Implement reviewer-safe packet/evidence projection and real identity masking.
3. Implement leakage connected components, split quotas, OOD closure, and power/MDE audit.
4. Implement literal system configs, one-way run result manifest, and full execution matrix.
5. Implement raw/adjudication/final-gold and case-level duplicate partition releases.
6. Add adversarial contract tests and one end-to-end synthetic denominator-closure test.
7. Freeze guide, calibration set, expert/COI registry, system/prompt/model/query/cache
   identities, Pilot R1 manifest, analysis seeds, and final preregistration SHA.
8. Only then collect the 30-case Pilot. Do not construct or inspect Main labels first.

## Engineering track remains separate

The GitHub CI failure observed before this review was an engineering portability bug:
GNU `ps` did not provide stable process identity. Linux now uses `/proc/<pid>/stat`
start ticks plus raw `/proc/<pid>/cmdline`, with PID-reuse double-read and fail-closed
schema v3. This repair is necessary for a green Draft PR, but it does not change the
scientific Pilot NO-GO decision. Production also remains NO-GO until parent hard-kill
cannot orphan an independently-sessioned action child.
