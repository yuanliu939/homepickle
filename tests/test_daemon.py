"""Tests for the sync daemon."""

import asyncio

from homepickle.daemon import _shutdown, run_daemon


def test_daemon_stops_on_shutdown_event() -> None:
    """Daemon exits promptly when the shutdown event is set."""

    async def _run() -> None:
        # Set shutdown before starting so it exits immediately.
        _shutdown.set()
        await run_daemon(interval_minutes=1)
        # Reset for other tests.
        _shutdown.clear()

    asyncio.run(_run())
