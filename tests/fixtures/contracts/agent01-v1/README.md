# Agent 01 frozen contract fixture

This directory is the minimal offline reference output for
`agent01-contract-v1`. It contains one candidate, its lossless source JSON and
CIF artifacts, the run-scoped candidate manifest, the stage result envelope,
and the frozen JSON Schemas.

It is generated only from `tests/fixtures/mp-summary.si-o.json`; it contains no
live Materials Project response and no API key.

Regenerate it from the repository root:

```bash
PYTHONDONTWRITEBYTECODE=1 MPLCONFIGDIR=/tmp/material-agent-mpl \
  .venv/bin/python scripts/generate_agent01_contract_fixture.py
```

`tests/contract/test_frozen_agent01_fixture.py` validates all referenced
artifact hashes and verifies that regeneration is byte-for-byte deterministic.
