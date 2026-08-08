# P3.1 live Crossref Gateway run — 2026-08-08

## Purpose and boundary

This release Gate exercised real Crossref metadata I/O inside the same
approval-bound, persistent Materials Gateway lifecycle used by the Hermes
profile. It did not read a PDF or full-text page, call an internal LLM, compute
the target property, or evaluate novelty. Provider content was not frozen as a
scientific truth; the run records identity, cost, lineage, hashes, and bounded
proposal semantics.

## Environment and command

- Time: 2026-08-08, approximately 18:28–18:33 CST.
- Branch: `agent/inspiration-generalization`.
- Code checkpoint before the live-test commit: `630b701`.
- Python: repository `.venv`, Python 3.11.15.
- Crossref pool: public; `MATERIALS_CROSSREF_CONTACT_EMAIL` explicitly absent.
- Persisted local evidence root (ignored by Git):
  `workspace/p31-live-crossref-20260808/`.

```bash
.venv/bin/python -m pytest -q --run-live-crossref \
  --basetemp workspace/p31-live-crossref-20260808 \
  tests/live/test_live_crossref_inspiration.py::test_live_crossref_runs_inside_the_approval_bound_gateway_lifecycle
```

Result: `1 passed in 3.18s`. The complete live file, including the lower-level
metadata probe, also passed: `2 passed in 4.29s`.

The test used the real production factory, created a run, recomputed the frozen
execution-manifest hash, issued an exact one-time requirement-freeze grant, acted
on that interaction, and retrieved the hash-verified terminal result. It did not
simulate or deliberately provoke provider throttling.

## Gateway result

- Run ID: `inspiration-f29a802d816c008e6e5a27fd`.
- Request SHA-256:
  `95638304820884088f26d4b1973012acf997b9caa45b3bd5c47313f057fe4555`.
- Gateway terminal status: `PARTIAL`; warnings were retained rather than hidden.
- Bundle outcome: `SUCCEEDED`.
- Selected proposals: 1.
- Proposal structural status: `STRUCTURE_VALID`.
- Target-property status: `UNKNOWN`.
- Bundle `scientific_conclusion`: `false`.
- Logical queries: 3 (`DIRECT`, `BRIDGE`, `COUNTER`).
- HTTP attempts: 3 success, 0 error, 0 retry.
- Raw hits / unique documents: 3 / 3.
- Selected and vectorized passages: 2 / 2.
- Crossref response bytes: 7,944.
- Estimated embedding input tokens: 142.
- Body fetches / PDF reads / internal LLM calls: 0 / 0 / 0.
- Serialized Gateway walltime remains deterministic `0`; the runtime ceiling was
  enforced separately.

The three live metadata records resolved to DOIs
`10.1038/s42005-025-01936-2`, `10.1115/imece2019-10872`, and
`10.3390/s25216693`. Only two supplied a bounded abstract passage. The missing
body was reported as `BODY_NOT_FETCHED`; it was not silently fetched. Two
curated bridge rules lacked required SUPPORT tags and remained explicit skipped
warnings. One acoustic-local-resonance bridge closed and produced the single
deterministic TiS2-to-TiSe2 proposal.

## Authoritative hashes

| Artifact | SHA-256 |
|---|---|
| `report.md` | `e38336b5646bce295aadaa6ebbd979bbbbe3237b6cda8ae6429c690ec43b505e` |
| `stage_result.json` | `3901a0429805b17265455c3def871ca20978da5821f95a834aa979dcac1bb9d9` |
| `inspiration_bundle.json` | `f5612b2ecfae8df24465309209a0141054d100bb91c482a2bfcbdab97a9255e6` |
| `cost_ledger.json` | `5dc144f332c29ba416a68d22c286c50a64a7d28cd4cefc13059c248aeca6a323` |
| `search_attempts.jsonl` | `4e8f19050c9e508045531f40ddfa79e683d77e6b5186314065fdaf812e3ac750` |

Gateway canonical structured-result SHA-256:
`bc3dd21c3ca3ee5770da01c08d384d17458a9d1e80ce84a94b51edfb4dc37b65`.

## Interpretation

Supported engineering claim: real Crossref search, attempt accounting, raw
metadata persistence, passage selection, evidence closure, transformation,
projection, and result verification can complete in one approval-bound Gateway
run. The run does not show multi-candidate or multi-mechanism diversity; that is
the separate P3.2 Gate. It also does not show bounded HTML/JATS fetching or tag
feedback; those remain P3.3.

