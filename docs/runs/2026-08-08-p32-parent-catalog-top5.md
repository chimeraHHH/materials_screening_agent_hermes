# P3.2 parent-catalog Top-5 calibration run — 2026-08-08

## Scope

This run is the frozen P3.2 engineering calibration Gate. It exercises the
production request compiler, approval manifest, Crossref adapter boundary,
Hermes Gateway lifecycle, parent-catalog preparer, pymatgen transformation
engine, exact identity merge, strict grouping, diverse selection, projection,
and authoritative report.

The HTTP transport is a deterministic query-aware test transport, not a live
internet result. Each of the four planned Crossref queries receives one distinct
metadata record. P3.1 separately records the real Crossref lifecycle Gate.

## Git and command

- Branch: `agent/inspiration-generalization`.
- Code/profile checkpoint: `03d7f56665c4b1d05b68578f6003cfefc1f24a2b`.
- Public draft PR:
  [#3 Generalize and harden the Hermes inspiration workflow](https://github.com/chimeraHHH/materials_screening_agent_hermes/pull/3).

```text
.venv/bin/python -m pytest -q \
  --basetemp workspace/p32-top5-gate-20260808 \
  tests/integration/test_gateway_parent_catalog_top5.py

1 passed in 0.89s
```

The test executes the complete `run → grant → approve → result` lifecycle in
two independent workspaces with the same frozen input.

## Frozen input

- Project ID: `hermes-parent-catalog-top5`.
- Submission ID: `parent-catalog-top5-submission`.
- Run ID: `inspiration-0c0f19ecf5984e6c8e0da92e`.
- Catalog ID/version: `flat-band-parent-catalog-v1` / `1`.
- Catalog manifest SHA-256:
  `09d563732717e05ccf216d3b8572b1bcd1d855dd3f5d0106a4cdbc15b9197b99`.
- Production substitution-registry SHA-256:
  `0df10f816808f075bd2e571b018b46a1e1cf0831b73d4be58782e679625cd4fb`.
- Request: `top_k=5`, `require_diverse_routes=true`, four logical metadata
  queries, room for eight physical attempts, four documents, four passages,
  zero model calls, and 300 seconds.

The catalog contains six operator-owned `ENGINEERING_CALIBRATION` parents. All
are verified as ordered, explicitly oxidation-state annotated, charge neutral,
2D structures before execution. No caller path or CIF is accepted.

## Search, evidence, and cost

| Metric | Value |
|---|---:|
| Logical queries / physical attempts / retries | 4 / 4 / 0 |
| Raw hits / unique documents | 4 / 4 |
| Extracted / vectorized passages | 4 / 4 |
| EvidenceCards / supported BridgePackets | 3 / 3 |
| Search response bytes | 2,665 |
| Embedding input tokens | 207 |
| Fetch requests / fetch bytes | 0 / 0 |
| PDF reads | 0 |
| Internal LLM calls / input / output tokens | 0 / 0 / 0 |

The three supported bridge rules cover acoustic local resonance, frustrated
magnetism/line-graph localization, and photonic destructive interference. The
direct target-only passage deliberately has no mechanism tag; the stage warning
is `NO_MECHANISM_TAG:passage-dc7630b18629413d0f3105ef`.

## Transformation and identity result

- Six reviewed physical routes executed with
  `SUBSTITUTE_EQUIVALENT_SITE_V1`; all six reached `STRUCTURE_VALID`.
- Six route hashes and six parent structure IDs are distinct.
- The TiS2 reference route and TiSSe control route converge to the same exact
  canonical TiSe2 output while retaining distinct parent, plan, route, bridge,
  and evidence lineage.
- Exact merging reduces six proposal rows to five candidate identities.
- All five identities are selected; one selected identity retains both
  convergent physical routes.
- The selected set contains six physical routes and six parent families.
- Requested/available/achieved mechanism counts are 2 / 4 / 4. The deterministic
  feasibility witness contains two mechanisms and the quota status is `MET`.
- Physical-route floor status is `MET`.
- Selected exact duplicates: 0.
- Selected strict duplicates: 0.
- Underfill reasons: none.

An independent pairwise pymatgen `StructureMatcher` audit uses
`ltol=1e-6`, `stol=1e-5`, `angle_tol=1e-5`, no primitive conversion, no scale,
no supercell attempt, no subset, and `SpeciesComparator`; all ten selected
structure pairs return `fit=False`.

## Outcome and authoritative hashes

- Scientific stage outcome: `SUCCEEDED`.
- Gateway terminal state: `PARTIAL`, solely because the direct target-only
  passage has no mechanism tag. The Top-5 and both diversity quotas passed.
- Target property status: `UNKNOWN`.
- `scientific_conclusion=false`.

| Artifact | SHA-256 |
|---|---|
| Catalog manifest | `09d563732717e05ccf216d3b8572b1bcd1d855dd3f5d0106a4cdbc15b9197b99` |
| Transformation proposals | `286f102c7ddeebaf613db75a8dd3db32e189baf63c747c40430a7272eb27769c` |
| Internal duplicate groups | `de7aabd0f32fe038d83e50c35b7c1b227e1a603e154d2e362bc1ed5bf76b3ae2` |
| Selection audit | `f46a992a8908abd68d166256f47d6b293e5679a90254a2b1a981826b4106328d` |
| Report | `9dc8c0df690031807e209c84385b8efcd85dde01409c8da327a0abdcb6f00010` |
| Inspiration bundle | `46c912fa42e94fe5e809c8a2cc6cc45b999c567945e6d952dd9e961e887d420c` |
| Stage result | `c2fe9b62087ff0ad51839c7235ad59fb79c48c5c086e445c2498dea48d537154` |

The catalog, proposals, duplicate groups, selection audit, report, bundle, and
stage-result hashes are identical in both independent workspaces.

## Boundary

This Gate proves bounded engineering behavior and lineage preservation. The
catalog structures do not establish a flat band or any other material property;
reviewed bridge assignment is an applicability mapping, not source evidence, and
a route executes only when its assigned bridge has search support. The run makes
no novelty, prior-art, patent, or validated-property claim. Multi-format body
fetch and TagGraph feedback remain P3.3 work.
