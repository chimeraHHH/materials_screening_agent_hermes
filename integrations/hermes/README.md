# Hermes integration

This directory contains the reproducible, narrow Hermes control-plane bundle for
the materials inspiration workflow. Hermes runs in a separate environment and
talks to the material engine only through the allowlisted Materials MCP server.

## Install the pinned runtime

From the repository root:

```bash
.venv/bin/python integrations/hermes/scripts/bootstrap_runtime.py
```

The script fetches only the commit recorded in `hermes.lock.json`, verifies the
resolved SHA, and runs Hermes's committed `uv.lock` into `.venv-hermes`. It does
not run a remote shell installer and does not install Hermes into the material
engine's `.venv`.

## Profile bundle

`profiles/materials-inspiration/` is the source-controlled profile distribution.
Its platform configuration names the raw `materials` server; Hermes v0.20.0 then
registers the dynamic `mcp-materials` toolset and `mcp__materials__*` tools. The
MCP server exposes four coarse tools and disables server resources and prompts.

The versioned `SKILL.md` is mirrored into `SOUL.md` so its policy is loaded on
every API-server run without enabling Hermes's inseparable `skill_manage` tool.
Run `verify_bundle.py` after changing either file; drift fails the release gate.

At runtime, set these non-secret variables in the profile environment:

```text
MATERIAL_AGENT_PYTHON=/absolute/path/to/materials_screening_agent/.venv-gateway/bin/python
MATERIAL_AGENT_WORKSPACE=/absolute/path/to/a/bounded/workspace
MATERIAL_AGENT_PROJECT_ID=materials-inspiration
```

Provider credentials and `API_SERVER_KEY` belong only in the ignored runtime
profile `.env`; never commit them. Bind the API server to loopback unless a
separate authenticated deployment boundary has been designed.

The profile is intentionally not a filesystem sandbox. Its practical boundary is
the absence of terminal/file/browser toolsets plus the Gateway's fixed schemas,
path guards, size limits, hash verification, and human-approval checks.
