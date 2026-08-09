# Gate E Scientific-Quality Plan

## Scope and claim discipline

- Novelty, prior-art, patentability, and validated target-property claims remain
  out of scope.
- Existing Crossref and fixture observations are `parsed` engineering evidence.
- Existing structure proposals and deterministic selection results are
  `computed` only as software outputs; they are not computed material properties.
- Cross-domain usefulness remains a `hypothesis` until an external domain expert
  signs a versioned gold set. Runtime search yield must never self-promote a Tag.

## E1 — Bounded recall expansion

- Add a versioned query-candidate pool per target and bridge rule: curated term,
  exact synonym, mechanism phrase, and one counter-condition phrase.
- Allocate the frozen physical-request budget with deterministic round-robin
  coverage across direct/bridge/counter classes; no online free-form Tag creation.
- Rank source records using explicit metadata quality features (stable DOI,
  abstract availability, publication year, title completeness, provider rank)
  while preserving every raw hit and rejection reason.
- Acceptance: offline labeled corpus reports recall@budget, precision@selected,
  source-quality distribution, and per-bridge coverage. External outages remain
  distinct from scientific no-match.

## E2 — Expert Tag workflow

- Introduce immutable review proposals bound to base graph ID/version/SHA,
  candidate Tag/rule payload, rationale, supporting and counter evidence IDs,
  reviewer identity, decision, timestamp, and canonical signature/hash.
- Legal decisions: `ACCEPT`, `REJECT`, `REQUEST_CHANGES`; runtime-generated
  feedback remains `UNKNOWN` and cannot issue any decision.
- Promotion creates a new graph version and an append-only change set. Rollback
  creates another version referencing the reverted change; it never rewrites a
  historical graph or completed run.
- Acceptance: stale-base, replay, duplicate decision, missing evidence,
  unauthorized reviewer, and rollback-lineage tests fail closed.

## E3 — Versioned semantic vectors

- Define a provider-neutral, passage-only semantic embedding protocol with exact
  model ID, revision, tokenizer/config hashes, dimension, normalized input hash,
  and model-bundle SHA. No full document/PDF entry point is permitted.
- The current signed-hashing vector remains the deterministic lexical baseline;
  it must not be renamed “semantic”. Production semantic mode remains unavailable
  until a SHA-pinned local model bundle and dependency/license check pass.
- Add an explicit hybrid similarity policy used by document/passage near-dedup
  and candidate ranking, with ablation against lexical-only behavior.
- Acceptance: same bundle/input replay, model drift rejection, dimension and
  finite-value checks, input/token budgets, cache-key isolation, and ranking
  ablation metrics.

## E4 — Multi-candidate scientific evaluation

- Define a versioned gold-set schema with target constraints, relevant source
  IDs, accepted/rejected bridge routes, counterexamples, candidate-family labels,
  reviewer IDs, adjudication state, and source licenses.
- Ship only a clearly labeled synthetic engineering fixture for CI. It may verify
  metric code but cannot satisfy the expert Gate.
- Report recall@budget, evidence precision, relation macro-F1, bridge coverage,
  candidate family coverage, exact/strict duplicate rate, MMR diversity, and
  underfill reasons, each with numerator/denominator and confidence limits where
  meaningful.
- Production acceptance requires at least two independent expert reviews per
  item, adjudication of disagreements, frozen gold-set SHA, and a comparison of
  lexical baseline versus semantic/hybrid candidate systems.

### Implemented engineering contract (2026-08-09)

- `evaluation.py` now strict-round-trips every gold case, gold set, and
  prediction before use. This closes Pydantic `model_copy(update=...)`
  validator bypasses; semantic gold content remains bound to its SHA and ID.
- The judged document, evidence-passage, and accepted/rejected bridge universes
  are closed. Unjudged IDs fail evaluation instead of being silently counted as
  negatives. Candidate bridge routes must also be members of the judged bridge
  universe and a subset of the prediction's supported bridges.
- Bridge evaluation records TP/FP/FN/TN plus precision, recall, specificity,
  accuracy, and F1. Relation evaluation records per-class confusion counts and
  exact-rational macro-F1. Every reported score, including aggregate means,
  carries a numerator and denominator.
- Ranked candidate records carry family, exact structure hash, strict structure
  group, and bridge-route IDs. The report computes exact/strict duplicate rate,
  fill/underfill rate, family coverage, pairwise route Jaccard distance, and
  pairwise family disagreement. Underfill reasons are required exactly when the
  requested Top-K is not filled.
- The report explicitly uses
  `DESCRIPTIVE_BOUNDED_CORPUS_NO_CI`: Wilson/bootstrap intervals are intentionally
  omitted because this frozen corpus is not asserted to be a probability sample.
  Synthetic results remain `ENGINEERING_EVALUATION`, with
  `scientific_conclusion=false`.
- An `ADJUDICATED` set requires at least two unique reviewers and a distinct
  adjudicator. In v1 those set-level identities apply to every included case;
  partial-review sets cannot be represented as adjudicated.
- Targeted evidence: `tests/unit/test_inspiration_evaluation.py` — 6 passed.

Remaining external Gate: the repository still has no real adjudicated gold set
or expert identities. CI's synthetic set only validates code paths and cannot
establish cross-domain utility, scientific performance, or production acceptance.

## External completion dependency

The repository can implement and test the workflow, schemas, metrics, and
synthetic fixture autonomously. It cannot truthfully mark the expert scientific
Gate complete without reviewer identities, signed decisions, and an adjudicated
gold set supplied through the new workflow.
