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
The profile pins the repository-owned
`material_agent.integration.hermes_service:create_hermes_fixture_service`
factory; neither the model nor a tool caller can replace it or supply paths.

The versioned `SKILL.md` is mirrored into `SOUL.md` so its policy is loaded on
every API-server run without enabling Hermes's inseparable `skill_manage` tool.
Run `verify_bundle.py` after changing either file; drift fails the release gate.

At runtime, set these non-secret variables in the profile environment:

```text
MATERIAL_AGENT_PYTHON=/absolute/path/to/materials_screening_agent/.venv-gateway/bin/python
MATERIAL_AGENT_WORKSPACE=/absolute/path/to/a/bounded/workspace
MATERIAL_AGENT_PROJECT_ID=materials-inspiration
```

`MATERIAL_AGENT_PYTHON` must name a separate Gateway environment containing the
material project dependencies plus the pinned MCP SDK. The trusted local pilot
accepts only its source-controlled flat-band fixture request, stores artifacts
beneath `<workspace>/<project>/`, and persists Gateway state in
`<workspace>/<project>/.gateway/materials-gateway.sqlite3`. A separate mode-0600
`operator-approval-grants.sqlite3` stores one-time action grants and is never
exposed through MCP. Restarting the MCP process therefore preserves submission
idempotency, pending approval, terminal state, and the bounded result. Scientific
completion still depends on the canonical structured-result hash plus immutable
`stage_result.json` and report artifacts passing URI/SHA/size verification.

## Record a real user approval out of band

The MCP caller cannot authorize itself: sending `confirmed_by_user=true` without
a matching operator grant fails while the run remains `INTERACTION_REQUIRED`.
After showing the exact interaction and frozen execution-manifest hash to the
user, a trusted local operator records the decision with the Gateway runtime:

```bash
.venv-gateway/bin/python -m material_agent.integration.operator_approval \
  --workspace /absolute/path/to/a/bounded/workspace \
  --project materials-inspiration \
  --run-id inspiration-... \
  --confirmation-reference user-confirmation:ticket-001
```

The grant binds the canonical request, complete interaction, execution manifest,
and exact action, and is consumed atomically before runner execution. If the MCP
process dies after consumption but before committing Gateway state, first verify
that the original process has stopped. The same command can then revalidate all
frozen inputs and explicitly re-arm the stranded grant with a fresh reference:

```bash
.venv-gateway/bin/python -m material_agent.integration.operator_approval \
  --workspace /absolute/path/to/a/bounded/workspace \
  --project materials-inspiration \
  --run-id inspiration-... \
  --confirmation-reference user-confirmation:recovery-002 \
  --recover-consumed-grant \
  --confirm-original-process-stopped
```

This recovery command is deliberately absent from Hermes's tool list. Replaying
the fixed local pilot is safe only because its runner inputs and outputs are
immutable and deterministic; any byte drift fails closed.

OAuth provider credentials belong only in Hermes's ignored runtime credential
store; environment-based provider secrets and `API_SERVER_KEY` belong only in
the ignored runtime profile `.env`. Never print or commit either form. Bind the
API server to loopback unless a separate authenticated deployment boundary has
been designed.

The profile is intentionally not a filesystem sandbox. Its practical boundary is
the absence of terminal/file/browser toolsets plus the Gateway's fixed schemas,
path guards, size limits, hash verification, and human-approval checks.
