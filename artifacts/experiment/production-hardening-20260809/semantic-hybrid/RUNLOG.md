# Gate E3 Semantic/Hybrid Run Log

## 2026-08-09 — contract lock

- Repository baseline remains `signed-hashing-v1`; it is lexical and must not
  be renamed semantic.
- No real local SHA-pinned semantic bundle or heavyweight model runtime exists.
- Implementation is restricted to standard-library contracts and explicit
  synthetic-provider unit tests.
- Production semantic mode must remain explicitly unavailable until bundle,
  dependency, license, and provider checks pass.

## 2026-08-09 — implementation and focused validation

- Added a provider-neutral local adapter with frozen identity and per-use
  observed-bundle verification.  Synthetic providers require explicit opt-in;
  the production factory returns `SEMANTIC_PRODUCTION_UNAVAILABLE` when no real
  provider is supplied.
- Added the versioned ranked near-dedup engine.  Missing semantic input fails
  closed unless lexical fallback is explicitly requested and recorded; hybrid
  decisions always include lexical-only kept IDs and changed-decision IDs.
- Reused the decision core in the existing passage selector in lexical-only
  mode with weights `1/0`; existing ordering, Jaccard threshold, and stop-at-K
  semantics are unchanged.
- Validation: focused semantic/hybrid/extractor/vectorizer/runner suites:
  `42 passed in 0.44s`; dependency check: `No broken requirements found`;
  `git diff --check`: clean.
- Claim verdict: supported only for the engineering contract.  Scientific
  usefulness remains inconclusive because no real pinned provider or expert
  gold set was used.
