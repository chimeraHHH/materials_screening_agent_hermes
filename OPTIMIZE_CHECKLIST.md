# P3.2 Optimize Checklist

- [x] Read the durable P3.x baseline, PLAN, CHECKLIST, P3.1 report, and current code
- [x] Select primary optimize submode: `rank`
- [x] Confirm current pass: `exploit`
- [x] Review recent optimization memory through Git history and P3.1 artifacts
- [x] Check brief slate across catalog, route, and contract mechanism families
- [x] Candidate briefs updated in `CANDIDATE_BOARD.md`
- [x] Candidate ranking recorded
- [x] Promote one durable implementation line
- [x] Record the implemented candidate pool and frozen catalog hash
- [x] Run focused smoke queue: catalog, engine, identity, selection, compiler, projector
- [x] Run full evaluation queue: P3.2 replay Gate and complete regression
- [x] Classify current failure: single-parent breadth plus greedy coverage failure
- [x] Stagnation check: no repeated implementation failure yet
- [x] Family-shift trigger: not triggered; the manifest line is feasible in current V1
- [x] Fusion eligibility: catalog provenance and selection audit are complementary and fused
- [x] Next concrete action completed: implement `p32-manifest-route-matrix`

## Promotion decision

Promote only `p32-manifest-route-matrix`. The hard-coded rotation alternative is
archived because it cannot justify route applicability; the V2 hypothesis-route
split is held because it expands public contracts without being required for the
P3.2 Gate.

## Frozen result

- Catalog manifest SHA-256:
  `09d563732717e05ccf216d3b8572b1bcd1d855dd3f5d0106a4cdbc15b9197b99`.
- Six reviewed physical routes produced five exact identities and a full Top-5.
- The deterministic selection audit recorded four achieved mechanisms, six
  selected physical routes, and zero selected exact or strict duplicates.
- Full non-opt-in evaluation: `732 passed, 14 skipped`.
