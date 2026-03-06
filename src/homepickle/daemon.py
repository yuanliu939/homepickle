"""Continuously running sync daemon that periodically polls Redfin favorites."""

import asyncio
import hashlib
import logging
import signal

from homepickle.browser import create_context
from homepickle.evaluator import evaluate_property
from homepickle.models import Property
from homepickle.scraper import (
    get_favorite_lists,
    scrape_properties,
    scrape_property_page,
)
from homepickle.storage import (
    get_connection,
    get_profile,
    needs_evaluation,
    save_evaluation,
    sync_favorites,
    upsert_property,
)

log = logging.getLogger("homepickle.daemon")

_shutdown = asyncio.Event()


def _handle_signal() -> None:
    """Set the shutdown event when a termination signal is received."""
    log.info("Shutdown signal received, finishing current cycle...")
    _shutdown.set()


async def _run_sync_cycle() -> str:
    """Run one full sync cycle: scrape, diff, evaluate.

    Returns:
        A summary string describing what happened.
    """
    pw, context = await create_context()
    conn = get_connection()
    try:
        fav_lists = await get_favorite_lists(context)
        log.info("Found %d favorite list(s).", len(fav_lists))

        total_new = 0
        total_removed = 0
        to_evaluate: list[Property] = []

        for fav_list in fav_lists:
            log.info("Scraping: %s", fav_list.name)
            properties = await scrape_properties(context, fav_list)
            log.info("  %d properties found.", len(properties))

            for prop in properties:
                upsert_property(conn, prop)
            conn.commit()

            new_props, removed_urls = sync_favorites(
                conn, fav_list.name, properties
            )
            conn.commit()

            total_new += len(new_props)
            total_removed += len(removed_urls)

            for prop in properties:
                if prop.url and needs_evaluation(conn, prop.url, prop.price):
                    to_evaluate.append(prop)

        if not to_evaluate:
            return (
                f"Sync complete. {total_new} new, {total_removed} removed. "
                "All evaluations up to date."
            )

        log.info("%d properties need evaluation.", len(to_evaluate))

        # Load buyer profile for personalized evaluation.
        profile_row = get_profile(conn)
        profile = profile_row["preferences"] if profile_row else None

        for i, prop in enumerate(to_evaluate):
            if _shutdown.is_set():
                return (
                    f"Sync interrupted. {total_new} new, {total_removed} removed, "
                    f"{i}/{len(to_evaluate)} evaluated before shutdown."
                )
            if not prop.url:
                continue
            label = f"{prop.address}, {prop.city}" if prop.city else prop.address
            log.info("[%d/%d] %s", i + 1, len(to_evaluate), label)

            page_text = await scrape_property_page(context, prop.url)
            text_hash = hashlib.sha256(page_text.encode()).hexdigest()[:16]

            log.info("  Evaluating with Claude...")
            evaluation = evaluate_property(prop, page_text, profile=profile)

            save_evaluation(
                conn, prop.url, "sonnet", evaluation, text_hash, prop.price
            )
            conn.commit()

        return (
            f"Sync complete. {total_new} new, {total_removed} removed, "
            f"{len(to_evaluate)} evaluated."
        )
    finally:
        conn.close()
        try:
            await context.browser.close()
        except Exception:
            pass
        try:
            await pw.stop()
        except Exception:
            pass


async def run_daemon(interval_minutes: int = 60) -> None:
    """Run the sync loop, executing a cycle every `interval_minutes`.

    Handles SIGINT/SIGTERM gracefully — finishes the current cycle then exits.

    Args:
        interval_minutes: Minutes between sync cycles.
    """
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, _handle_signal)

    log.info(
        "Daemon started. Syncing every %d minute(s). Ctrl+C to stop.",
        interval_minutes,
    )

    while not _shutdown.is_set():
        try:
            summary = await _run_sync_cycle()
            log.info(summary)
        except Exception:
            log.exception("Sync cycle failed.")

        if _shutdown.is_set():
            break

        log.info("Next sync in %d minute(s).", interval_minutes)
        try:
            await asyncio.wait_for(
                _shutdown.wait(), timeout=interval_minutes * 60
            )
        except TimeoutError:
            pass

    log.info("Daemon stopped.")
