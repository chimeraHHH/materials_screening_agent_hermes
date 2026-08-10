# Flat/Narrow-Band Benchmark Checklist

This checklist tracks research evidence, not product feature completion.
Novelty is outside scope.

## Release the engineering baseline

- [x] Freeze research scope, systems, metrics, budgets, and stop rules in plan v0
- [x] Audit the dirty worktree for secrets, generated files, and unrelated changes
- [x] Run the complete offline engineering Gate
- [x] Correct superseded production documentation
- [x] Commit and push `research/inspiration-benchmark-v0`
- [x] Open public Draft PR #4 with explicit scientific and production limitations

## Open-data audit

- [x] Search official sources for flat/narrow-band labels, material records, and literature metadata
- [x] Record license, redistribution, access/auth, rate limits, version, update cadence, and useful fields
- [x] Define include/exclude decisions and cache/download boundaries
- [x] Freeze `source_catalog` and its SHA before corpus construction

## Preregistration and annotation

- [x] Complete independent preregistration/annotation/contract red-team and record Pilot NO-GO blockers
- [x] Complete the user manual review of definitions, workload, alpha thresholds, E2 selection, OOD meaning, guardrails, and COI (AI-assisted review adopted by the user, 2026-08-10); adopt the preregistration v0.5 / annotation-guide v0.6 / plan revisions
- [x] Run the post-adoption independent protocol red team (FAIL: B1/B2 plus ten majors); withdraw the binary downgrade mode and apply the v0.6/v0.7 repairs (2026-08-10)
- [x] Run the third-round independent red team (10/12 repairs verified, FAIL on residual N1); apply the v0.7/v0.8 repairs for N1/N2 and minors N3-N8 (2026-08-10)
- [ ] Close the three failing V3 adversarial rejection tests recorded by the 2026-08-11 partitioned gate (`437 passed, 3 failed`); research track paused by user decision for MVP priority
- [ ] Close and freeze case, reviewer-safe packet, judgment, evidence, bridge, candidate, execution-matrix, blinding, run-ledger, final-gold, duplicate-release, expert-registry, and adjudication schemas
- [ ] Freeze annotation manual with positive, negative, borderline, and conflict examples
- [ ] Freeze primary/secondary metrics, paired tests, multiplicity correction, missing-data policy, and stop rules
- [ ] Freeze model/prompt/token/request budgets and leakage controls
- [ ] Register two independent experts and one distinct adjudicator

## Pilot and benchmark

- [ ] Construct 30-case stratified pilot
- [ ] Blind and randomize system outputs for two independent expert reviews
- [ ] Adjudicate disagreements and compute Krippendorff's alpha
- [ ] Pass ordinal alpha >= 0.80 directly, or revise only the manual after alpha in `[0.667, 0.80)` and pass a disjoint 30-case R2 at >= 0.80
- [ ] Freeze 120 cases: development 60, locked IID 30, locked OOD 30
- [ ] Freeze family-disjoint split and all release hashes

## Experiments

- [ ] Run B0 and publish the complete denominator/error ledger
- [ ] Run E1 semantic-reasoning ablation under the B0 search budget
- [ ] Run E2 multi-source ablation under the B0 total request budget
- [ ] Run E3 mechanism-driven Tag ablation without online graph mutation
- [ ] Promote only passing isolated components
- [ ] Run fusion on development and freeze it
- [ ] Run the locked test once
- [ ] Complete stability, robustness, subgroup, and failure analysis
- [ ] Obtain independent scientific review and issue GO/NO-GO for production transfer
