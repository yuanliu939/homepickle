"""Continuously running sync daemon that periodically polls Redfin favorites."""

import asyncio
import hashlib
import logging
import signal

from homepickle.browser import create_context, refresh_cookies
from homepickle.evaluator import (
    DEFAULT_MODEL,
    evaluate_property,
    personalize_evaluation,
)
from homepickle.models import Property
from homepickle.scraper import (
    SessionExpiredError,
    get_favorite_lists,
    scrape_properties,
    scrape_property_page,
)
from homepickle.storage import (
    clear_regeneration,
    get_connection,
    get_latest_evaluation,
    get_profile,
    get_property,
    get_regeneration_queue,
    needs_evaluation,
    needs_personalized_evaluation,
    row_to_property,
    save_evaluation,
    save_personalized_evaluation,
    sync_favorites,
    upsert_property,
)

log = logging.getLogger("homepickle.daemon")

_shutdown = asyncio.Event()


def _handle_signal() -> None:
    """Set the shutdown event when a termination signal is received."""
    log.info("Shutdown signal received, finishing current cycle...")
    _shutdown.set()


def _run_personalization(
    conn, prop: Property, profile: str
) -> None:
    """Run tier-2 personalization for a single property if needed.

    Args:
        conn: An open database connection.
        prop: The property to personalize.
        profile: The buyer profile text.
    """
    if not prop.url:
        return
    base_eval = get_latest_evaluation(conn, prop.url)
    if base_eval is None:
        return
    if not needs_personalized_evaluation(
        conn, prop.url, base_eval["id"], profile
    ):
        return

    label = f"{prop.address}, {prop.city}" if prop.city else prop.address
    log.info("  Personalizing: %s", label)
    result = personalize_evaluation(
        prop, base_eval["evaluation_text"], profile
    )
    save_personalized_evaluation(
        conn, prop.url, base_eval["id"], DEFAULT_MODEL, result, profile
    )
    conn.commit()


def _process_regeneration_queue(conn, profile: str) -> int:
    """Process all pending regeneration requests.

    Args:
        conn: An open database connection.
        profile: The buyer profile text.

    Returns:
        Number of properties regenerated.
    """
    queue = get_regeneration_queue(conn)
    if not queue:
        return 0

    log.info("Processing %d regeneration request(s).", len(queue))
    count = 0
    for row in queue:
        if _shutdown.is_set():
            break
        url = row["property_url"]
        prop_row = get_property(conn, url)
        base_eval = get_latest_evaluation(conn, url)
        if not prop_row or not base_eval:
            clear_regeneration(conn, url)
            conn.commit()
            continue

        prop = row_to_property(prop_row)
        label = f"{prop.address}, {prop.city}" if prop.city else prop.address
        log.info("  Regenerating: %s", label)

        result = personalize_evaluation(
            prop, base_eval["evaluation_text"], profile
        )
        save_personalized_evaluation(
            conn, url, base_eval["id"], DEFAULT_MODEL, result, profile
        )
        clear_regeneration(conn, url)
        conn.commit()
        count += 1

    return count


async def _run_sync_cycle() -> str:
    """Run one full sync cycle: scrape, diff, evaluate, personalize.

    Returns:
        A summary string describing what happened.
    """
    pw, context = await create_context()
    conn = get_connection()
    try:
        fav_lists = await get_favorite_lists(context)
        log.info("Found %d favorite list(s).", len(fav_lists))

        if not fav_lists:
            log.warning(
                "No favorite lists found. This may indicate a session "
                "issue. Check that your Redfin login is still valid."
            )

        # Refresh cookies after successful auth to extend session life.
        await refresh_cookies(context)

        total_new = 0
        total_removed = 0
        to_evaluate: list[Property] = []
        all_properties: list[Property] = []

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
            all_properties.extend(properties)

            for prop in properties:
                if prop.url and needs_evaluation(conn, prop.url, prop.price):
                    to_evaluate.append(prop)

        # --- Tier 1: Base evaluations ---
        base_count = 0
        if to_evaluate:
            log.info("%d properties need base evaluation.", len(to_evaluate))

            for i, prop in enumerate(to_evaluate):
                if _shutdown.is_set():
                    break
                if not prop.url:
                    continue
                label = (
                    f"{prop.address}, {prop.city}"
                    if prop.city else prop.address
                )
                log.info("[%d/%d] %s", i + 1, len(to_evaluate), label)

                page_text = await scrape_property_page(context, prop.url)
                text_hash = hashlib.sha256(
                    page_text.encode()
                ).hexdigest()[:16]

                log.info("  Evaluating with Claude...")
                evaluation = evaluate_property(prop, page_text)

                save_evaluation(
                    conn, prop.url, DEFAULT_MODEL, evaluation,
                    text_hash, prop.price,
                )
                conn.commit()
                base_count += 1

        # --- Tier 2: Personalized evaluations ---
        profile_row = get_profile(conn)
        profile = profile_row["preferences"] if profile_row else None
        personal_count = 0

        if profile and not _shutdown.is_set():
            log.info("Running personalized evaluations...")
            for prop in all_properties:
                if _shutdown.is_set():
                    break
                if not prop.url:
                    continue
                base_eval = get_latest_evaluation(conn, prop.url)
                if base_eval is None:
                    continue
                if not needs_personalized_evaluation(
                    conn, prop.url, base_eval["id"], profile
                ):
                    continue
                _run_personalization(conn, prop, profile)
                personal_count += 1

        # --- Process regeneration queue ---
        regen_count = 0
        if profile and not _shutdown.is_set():
            regen_count = _process_regeneration_queue(conn, profile)

        parts = [f"Sync complete. {total_new} new, {total_removed} removed"]
        if base_count:
            parts.append(f"{base_count} evaluated")
        if personal_count:
            parts.append(f"{personal_count} personalized")
        if regen_count:
            parts.append(f"{regen_count} regenerated")
        all_idle = not (base_count or personal_count or regen_count or to_evaluate)
        if all_idle:
            parts.append("all up to date")
        return ", ".join(parts) + "."
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
        except SessionExpiredError:
            log.error(
                "Redfin session expired. Run 'homepickle login' to "
                "re-authenticate, then restart the daemon."
            )
            break
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
