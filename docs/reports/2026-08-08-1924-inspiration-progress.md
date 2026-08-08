# Inspiration progress report — 2026-08-08 19:24 CST

## Verdict

P3.2 `Candidate Breadth & Diversity` is `PASSED`. The production Hermes path now
uses an operator-owned, SHA-pinned six-parent catalog, executes only reviewed
search-supported routes, merges exact outputs without losing lineage, and fills
a replayable Top-5 with zero exact or strict duplicates.

The overall P3.x goal remains `IN_PROGRESS`: P3.3 bounded multi-format fetch and
Tag feedback, the final real Hermes natural-language Crossref session on the
completed implementation, and release closeout have not passed.

## Time, Git, and publication state

- Work interval: 2026-08-08 18:37–19:24 CST.
- Base: `hermes-origin/main@dcd076d01061f36a2f9826deecefc274c9c3211d`.
- Branch: `agent/inspiration-generalization`.
- Covered-through pushed code/profile SHA: `03d7f56`.
- Draft PR:
  [#3 Generalize and harden the Hermes inspiration workflow](https://github.com/chimeraHHH/materials_screening_agent_hermes/pull/3).
- Push target: only `hermes-origin` / public
  `chimeraHHH/materials_screening_agent_hermes`; the original upstream was not
  pushed.

Published checkpoint commits in this interval:

- `7eccee3` — ranked the P3.2 implementation candidates;
- `f1353cb` — planned all reviewed bridge queries and froze the public budget;
- `ca98865` — added feasibility-aware selection and a complete diversity audit;
- `eb5dd66` — pinned and verified the six-entry parent catalog and operator output;
- `94be118` — integrated catalog preparation, multi-route execution, Top-5 replay,
  Gateway projection, and report provenance;
- `03d7f56` — synchronized the Hermes Skill and bounded diversity contract.

## Implemented contract

### Parent catalog and execution boundary

- `flat-band-parent-catalog-v1` accepts no path or payload. Package-resource
  loading verifies a canonical manifest, frozen SHA, every CIF byte string,
  pymatgen/spglib runtime versions, explicit oxidation states, charge neutrality,
  canonical structure identity, reproducible 2D dimensionality, space group,
  equivalent-site groups, substitution allowlist, route hash, structural QC, and
  expected output identity.
- Manifest SHA-256 is
  `09d563732717e05ccf216d3b8572b1bcd1d855dd3f5d0106a4cdbc15b9197b99`;
  the production registry SHA-256 is
  `0df10f816808f075bd2e571b018b46a1e1cf0831b73d4be58782e679625cd4fb`.
- Six physical routes produce five exact canonical outputs. The TiS2 reference
  and TiSSe control routes intentionally converge to the same output bytes while
  retaining distinct parent, plan, route, bridge, evidence, and family lineage.
- The catalog, all parent pointers, route hashes, reviewed bridge assignments,
  policy, request, and catalog-bound engine snapshot enter the approval/execution
  manifest. A caller cannot replace the catalog with an arbitrary structure.
- Every entry is `ENGINEERING_CALIBRATION`, has property status `UNKNOWN`, and
  fixes `scientific_conclusion=false`.

### Query and diversity semantics

- The public compiler now plans one direct query plus all three reviewed bridge
  queries before any counter query. One retry per logical query yields a physical
  search-attempt limit of eight; minimum document and passage budgets are four.
- `require_diverse_routes=true` with `top_k>=2` selects
  `MECHANISM_COVERAGE_WHEN_FEASIBLE`: two mechanisms are required when jointly
  feasible under strict-group and parent-family caps, and a two-physical-route
  floor is audited. `false` removes only the second-mechanism floor; exact/strict
  deduplication and MMR stay active.
- The original greedy mechanism-quota pass had a concrete overlap counterexample.
  Deterministic feasibility lookahead now reserves a jointly achievable
  mechanism set before MMR completion. Pool insufficiency and hard-quota
  infeasibility are separate audited states rather than silent diversity claims.
- `selection_audit.json` records requested/pool/selected counts, mechanisms,
  physical routes, parent families, multi-route groups, exact/strict duplicates,
  quota states, and underfill reasons. The same information appears in the
  authoritative Markdown report.
- Gateway candidate projection supports one or more bridge packets after exact
  merging and aggregates all bridge domains, invariants, failure conditions, and
  source lineage.

## P3.2 calibration result

The query-aware static Crossref transport exercises the production factory and
complete `run → grant → approve → result` lifecycle with four independent
metadata records. It is an offline deterministic Gate, not a replacement for
the already-passed P3.1 live Crossref Gate.

- Run ID: `inspiration-0c0f19ecf5984e6c8e0da92e`.
- Logical queries / attempts / retries: 4 / 4 / 0.
- Raw hits / unique documents / passages / vectors: 4 / 4 / 4 / 4.
- EvidenceCards / supported bridges: 3 / 3.
- Search bytes / embedding tokens: 2,665 / 207.
- Structure-valid plans / exact identities / selected: 6 / 5 / 5.
- Selected physical routes / parent families: 6 / 6.
- Requested / available / achieved mechanisms: 2 / 4 / 4.
- Exact-merge reduction: 1 proposal row.
- Selected exact / strict duplicates: 0 / 0.
- Fetch / PDF / internal LLM: 0 / 0 / 0.
- Underfill reasons: none; mechanism and route quota states are both `MET`.

An independent pairwise strict `StructureMatcher` audit rejects equivalence for
all ten pairs of selected CIFs. Two independent workspaces reproduce identical
catalog, plan, duplicate-group, selection-audit, report, bundle, and stage-result
hashes. Detailed hashes and the command are in
[`docs/runs/2026-08-08-p32-parent-catalog-top5.md`](../runs/2026-08-08-p32-parent-catalog-top5.md).

The scientific stage outcome is `SUCCEEDED`. Gateway returns `PARTIAL` because
the direct target-only passage deliberately has no mechanism tag; this warning
does not hide an underfilled selection or failed diversity quota.

## Verification evidence

Catalog, operator, and engine Gate:

```text
.venv/bin/python -m pytest -q \
  tests/unit/test_inspiration_parent_catalog.py \
  tests/unit/test_inspiration_transformations.py \
  tests/unit/test_inspiration_engine.py

24 passed in 0.79s
```

Focused runtime and report Gate:

```text
.venv/bin/python -m pytest -q \
  tests/integration/test_gateway_parent_catalog_top5.py \
  tests/integration/test_gateway_public_crossref.py \
  tests/integration/test_inspiration_runner.py \
  tests/unit/test_inspiration_request_compiler.py \
  tests/unit/test_inspiration_reporting.py

27 passed in 1.88s
```

Frozen replay run:

```text
.venv/bin/python -m pytest -q \
  --basetemp workspace/p32-top5-gate-20260808 \
  tests/integration/test_gateway_parent_catalog_top5.py

1 passed in 0.89s
```

Release-width local checks:

```text
.venv/bin/python integrations/hermes/scripts/verify_bundle.py
Hermes bundle valid

.venv/bin/python -m compileall -q src tests integrations/hermes/scripts
.venv/bin/python -m pip check
No broken requirements found.

.venv/bin/python -m pytest -q
732 passed, 14 skipped, 362 warnings in 16.91s
```

Skips are the explicit MCP/live-provider/real-ML opt-in environments. Warnings
are existing third-party pymatgen/spglib notices; there was no test failure.

## Failures found and repaired

- Equivalent TiSe2 outputs initially serialized site occupancies as `1` versus
  `1.0`, yielding different CIF hashes despite the same canonical structure ID.
  Canonical output serialization now normalizes every occupancy to float; the
  convergence control asserts equal structure ID, bytes, hash, and size.
- The previous greedy mechanism selector could consume the only candidate that
  made a later mechanism jointly feasible. A deterministic lookahead
  counterexample test now locks the repaired behavior.
- A same-output convergence control would have failed exact merging if its
  Artifact pointer differed. The catalog replay and Top-5 Gate now explicitly
  test equal output pointers plus two retained routes.
- The direct target-only document produces a conservative `NO_MECHANISM_TAG`
  warning. The report distinguishes this from mechanism quota, route quota, or
  selection underfill.

## Boundary and honest status

- `STRUCTURE_VALID` means deterministic structural QC only; it does not validate
  an electronic flat band.
- Reviewed route-to-bridge assignment is an operator applicability decision, not
  literature evidence. A route executes only when its assigned bridge has
  `SEARCH_SUPPORTED` evidence in that run.
- Target property remains `UNKNOWN`; every bundle and catalog entry has
  `scientific_conclusion=false`.
- Full-PDF reads and internal LLM calls remain zero.
- No novelty, prior-art, patent, or validated-property conclusion is produced.
- P3.3 body-fetch integration, format-constrained runner routing, yield feedback,
  immutable review artifacts, and calibration status are not implemented yet.
- The final real Hermes natural-language session must be rerun after P3.3; the
  historical fixed-request provider session cannot close this release.

## Decision and next Gate

Decision: `GO` to P3.3. P3.2 refutes the strongest remaining single-parent and
single-candidate alternative explanation: the bounded system can preserve six
physical routes, merge one exact convergence group, fill five strict-distinct
slots, cover multiple mechanisms, and replay its evidence and ranking hashes.

Next Gate: connect JSON metadata, JSON-LD/Highwire HTML, JATS/XML, and ordinary
HTML extraction through one injectable bounded runner fetch seam. The Gate must
prove metadata-first skip-fetch, HTTPS host/redirect/content-type/request/byte
budgets, PDF and internal LLM counts of zero, selected-passage-only vectorization,
immutable per-query/tag/bridge yield feedback, no online TagGraph mutation,
three-bridge calibration coverage, explicit `UNKNOWN` expert status, prompt-
injection resistance, and deterministic replay.
