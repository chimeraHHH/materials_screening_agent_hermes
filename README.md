# Material Screening Agent

This repository implements the material-retrieval stage described in
`material-screening-agent01-plan.md`.

## Development environment

The project targets Python 3.11 and uses a repository-local virtual
environment:

```bash
/opt/anaconda3/envs/py311/bin/python -m venv .venv
.venv/bin/pip install -r requirements.lock
.venv/bin/pip install --no-deps -e .
```

The Materials Project API key is read from `MP_API_KEY`. It must not be placed
in project configuration or artifacts.

## Run the offline demo

```bash
material-agent retrieval \
  --requirement tests/fixtures/requirement.si-o.json \
  --output workspace/demo \
  --fixture tests/fixtures/mp-summary.si-o.json
```

Run without `--fixture` to use the live Materials Project API.
