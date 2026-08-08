# P3.2 Candidate Board

| Candidate ID | Level | Parent | Strategy | Status | Expected Gain | Observed Result | Promote / Archive |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `p32-manifest-route-matrix` | brief | `3ecdcbe` | exploit | promoted | SHA-bound multi-parent routes, honest multi-mechanism lineage, replayable Top-5 | Read-only prototypes reached 6 valid routes, 5 exact outputs; selection audit found one fixable greedy counterexample | promote |
| `p32-hardcoded-rotation` | brief | `3ecdcbe` | explore | archived | Small implementation surface | Bridge assignment would be an unexplained index rotation and provenance would remain scattered | archive |
| `p32-route-hypothesis-v2` | brief | `3ecdcbe` | explore | held | Clean separation of physical and hypothesis routes | Requires broad model/Gateway/report migration beyond the P3.2 acceptance surface | hold for a later contract version |

## Ranking

1. `p32-manifest-route-matrix` — highest feasibility and auditability with
   current V1 contracts. It binds an operator-owned catalog and one reviewed
   primary bridge to each real physical transformation route, then uses existing
   exact-output merging.
2. `p32-route-hypothesis-v2` — semantically strongest long-term design, but its
   public-contract blast radius is disproportionate to this Gate.
3. `p32-hardcoded-rotation` — fastest prototype, but not acceptable as a durable
   scientific execution boundary.

## Promoted implementation line

`pinned catalog manifest -> verified 2D parents -> reviewed bridge assignment per
physical route -> allowlisted substitution -> exact-output lineage merge ->
strict-group-safe selection with feasibility lookahead -> selection audit`

The current implementation pass is `exploit`. Only one line is promoted so the
frozen replay comparison remains interpretable.
