"""Compatibility entrypoint pinned by the Hermes materials profile."""

from material_agent.gateway.mcp_server import main

if __name__ == "__main__":
    raise SystemExit(main())
