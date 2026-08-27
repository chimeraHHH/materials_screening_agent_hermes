# Gate E3 Semantic/Hybrid Engineering Plan

- Tier: `auxiliary/dev`; this is an engineering-contract validation, not a
  scientific effectiveness claim.
- Baseline: frozen `signed-hashing-v1` lexical passage vector and token-Jaccard
  local passage deduplication.
- Selected route: add a provider-neutral adapter that accepts only bounded
  title/section/selected-passage/tags inputs and fails closed unless an exact
  local model bundle identity is verified.  Use a versioned lexical/semantic
  similarity policy at a ranked near-dedup decision point while preserving a
  lexical-only ablation.
- Null hypothesis: the new contracts fail to distinguish unavailable/drifted
  semantic providers or change default lexical selection behavior.
- Alternative hypothesis: the contracts reject drift and invalid vectors/cache
  entries, replay deterministically, and preserve the lexical baseline while a
  synthetic provider can exercise a different hybrid decision.
- Code map: `semantic_embedding.py`, `hybrid_similarity.py`, the lexical dedup
  seam in `passages.py`, focused unit tests, and Inspiration plan notes only.
- Stop condition: focused tests cover local-only identity, bundle drift,
  dimension/finite/L2/token/cache validation, deterministic replay, lexical
  equivalence, and hybrid-versus-lexical ablation.
- Abandonment condition: the implementation would require a new heavyweight
  dependency, an unpinned download, or public runner/manifest schema churn.
- Known external dependency: a real SHA-pinned locally licensed model bundle
  and expert-adjudicated gold set are not present in this repository.
