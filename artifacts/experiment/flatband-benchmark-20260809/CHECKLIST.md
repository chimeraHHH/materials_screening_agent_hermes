# Flat/Narrow-Band Benchmark Checklist

This checklist tracks research evidence, not product feature completion.
Novelty is outside scope.

## Release the engineering baseline

- [x] Freeze research scope, systems, metrics, budgets, and stop rules in plan v0
- [x] Audit the dirty worktree for secrets, generated files, and unrelated changes
- [x] Run the complete offline engineering Gate
- [ ] Correct superseded production documentation
- [ ] Commit and push `research/inspiration-benchmark-v0`
- [ ] Open a public Draft PR with explicit scientific and production limitations

## Open-data audit

- [ ] Search official sources for flat/narrow-band labels, material records, and literature metadata
- [ ] Record license, redistribution, access/auth, rate limits, version, update cadence, and useful fields
- [ ] Define include/exclude decisions and cache/download boundaries
- [ ] Freeze `source_catalog` and its SHA before corpus construction

## Preregistration and annotation

- [ ] Freeze case, judgment, evidence, bridge, candidate, and adjudication schemas
- [ ] Freeze annotation manual with positive, negative, borderline, and conflict examples
- [ ] Freeze primary/secondary metrics, paired tests, multiplicity correction, missing-data policy, and stop rules
- [ ] Freeze model/prompt/token/request budgets and leakage controls
- [ ] Register two independent experts and one distinct adjudicator

## Pilot and benchmark

- [ ] Construct 30-case stratified pilot
- [ ] Blind and randomize system outputs for two independent expert reviews
- [ ] Adjudicate disagreements and compute Krippendorff's alpha
- [ ] Pass alpha >= 0.67 or execute the predeclared manual-only revision loop
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
