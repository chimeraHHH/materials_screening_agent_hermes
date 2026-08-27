#!/usr/bin/env python3
"""Launch the pinned Hermes dashboard with bounded detached-PTY retention.

Hermes intentionally keeps dashboard PTYs alive for 30 minutes so a browser can
reattach.  The single-user materials profile needs a much shorter grace period:
otherwise every fresh browser token leaves a TUI and its Python gateway alive.
This integration-owned launcher changes only the dashboard process; the pinned
Hermes checkout remains clean and reproducible.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Sequence

PTY_IDLE_TTL_SECONDS = 10.0
PTY_REAPER_INTERVAL_SECONDS = 2.0


def configure_managed_pty_lifecycle() -> None:
    from hermes_cli import pty_session

    if getattr(pty_session, "_materials_managed_lifecycle", False):
        return
    original_init = pty_session.PtySessionRegistry.__init__
    original_reaper = pty_session.run_reaper

    def managed_init(
        self,
        *,
        ttl: float,
        max_sessions: int,
        buffer_cap: int,
        read_timeout: float,
    ) -> None:
        original_init(
            self,
            ttl=min(float(ttl), PTY_IDLE_TTL_SECONDS),
            max_sessions=max_sessions,
            buffer_cap=buffer_cap,
            read_timeout=read_timeout,
        )

    async def managed_reaper(registry, *, interval: float = PTY_REAPER_INTERVAL_SECONDS):
        return await original_reaper(registry, interval=interval)

    pty_session.PtySessionRegistry.__init__ = managed_init
    pty_session.run_reaper = managed_reaper
    pty_session._materials_managed_lifecycle = True


def main(argv: Sequence[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if arguments != ["--self-test"]:
        sys.argv = ["hermes", *arguments]
    configure_managed_pty_lifecycle()
    if arguments == ["--self-test"]:
        from hermes_cli.pty_session import PtySessionRegistry

        registry = PtySessionRegistry(
            ttl=30 * 60,
            max_sessions=16,
            buffer_cap=1024,
            read_timeout=0.2,
        )
        print(
            json.dumps(
                {
                    "pty_idle_ttl_seconds": registry._ttl,
                    "reaper_interval_seconds": PTY_REAPER_INTERVAL_SECONDS,
                    "schema_version": "materials-hermes-managed-dashboard-v1",
                },
                sort_keys=True,
            )
        )
        return 0

    # Hermes resolves ``-p/--profile`` while importing its CLI module.  Install
    # the forwarded argv first so profile-scoped HERMES_HOME is selected before
    # any dashboard modules or configuration are imported.
    from hermes_cli.main import main as hermes_main

    return int(hermes_main() or 0)


if __name__ == "__main__":
    raise SystemExit(main())
