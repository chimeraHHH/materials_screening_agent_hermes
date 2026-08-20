# Materials research benchmark v1

This directory contains a synthetic engineering fixture for the PaperQA2-inspired
Hermes research benchmark. It exercises DOI recall, evidence-ID validity, located
full-text citations, direct constraint coverage, conclusion-term coverage, native
lead closure, executed skeptic counterqueries, candidate-triggered second retrieval,
and the property-verification boundary.

The fixture is deliberately labelled `SYNTHETIC_TEST_ONLY`. It may pass engineering
gates with:

```bash
.venv/bin/python scripts/run_research_benchmark.py \
  benchmarks/materials_research_v1/synthetic_gold.json \
  benchmarks/materials_research_v1/synthetic_predictions.json \
  --allow-synthetic
```

Without `--allow-synthetic`, it must fail `gold_not_adjudicated`. A production release
gold set requires at least two independent reviewers and a separate adjudicator.
