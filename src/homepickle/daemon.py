"""Continuously running sync daemon that periodically polls Redfin favorites."""

import asyncio
import hashlib
import logging
import signal
from concurrent.futures import ThreadPoolExecutor, as_completed

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

DEFAULT_WORKERS = 4


def _handle_signal() -> None:
    """Set the shutdown event when a termination signal is received."""
    log.info("Shutdown signal received, finishing current cycle...")
    _shutdown.set()


def _label(prop: Property) -> str:
    """Return a short display label for a property.

    Args:
        prop: The property.

    Returns:
        A string like "123 Main St, Seattle".
    """
    return f"{prop.address}, {prop.city}" if prop.city else prop.address


def _evaluate_one(
    prop: Property, page_text: str
) -> tuple[Property, str, str]:
    """Run base evaluation for a single property (thread-safe).

    Args:
        prop: The property to evaluate.
        page_text: Scraped detail page text.

    Returns:
        A tuple of (property, evaluation_text, page_text_hash).
    """
    text_hash = hashlib.sha256(page_text.encode()).hexdigest()[:16]
    evaluation = evaluate_property(prop, page_text)
    return prop, evaluation, text_hash


def _personalize_one(
    prop: Property, base_text: str, profile: str
) -> tuple[Property, str]:
    """Run personalized evaluation for a single property (thread-safe).

    Args:
        prop: The property to personalize.
        base_text: The base evaluation text.
        profile: The buyer profile text.

    Returns:
        A tuple of (property, personalized_text).
    """
    result = personalize_evaluation(prop, base_text, profile)
    return prop, result


def _process_regeneration_queue(
    conn, profile: str, workers: int
) -> int:
    """Process all pending regeneration requests.

    Args:
        conn: An open database connection.
        profile: The buyer profile text.
        workers: Max concurrent evaluations.

    Returns:
        Number of properties regenerated.
    """
    queue = get_regeneration_queue(conn)
    if not queue:
        return 0

    log.info("Processing %d regeneration request(s).", len(queue))

    # Collect tasks.
    tasks: list[tuple[Property, str, str, int]] = []
    for row in queue:
        url = row["property_url"]
        prop_row = get_property(conn, url)
        base_eval = get_latest_evaluation(conn, url)
        if not prop_row or not base_eval:
            clear_regeneration(conn, url)
            conn.commit()
            continue
        prop = row_to_property(prop_row)
        tasks.append((
            prop, base_eval["evaluation_text"], url, base_eval["id"]
        ))

    if not tasks:
        return 0

    count = 0
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {}
        for prop, base_text, url, base_id in tasks:
            if _shutdown.is_set():
                break
            future = executor.submit(
                _personalize_one, prop, base_text, profile
            )
            futures[future] = (url, base_id)

        for future in as_completed(futures):
            url, base_id = futures[future]
            try:
                prop, result = future.result()
                save_personalized_evaluation(
                    conn, url, base_id, DEFAULT_MODEL, result, profile
                )
                clear_regeneration(conn, url)
                conn.commit()
                count += 1
                log.info("  Regenerated: %s", _label(prop))
            except Exception:
                log.exception(
                    "  Failed to regenerate %s, skipping.", url
                )

    return count


async def _run_sync_cycle(workers: int = DEFAULT_WORKERS) -> str:
    """Run one full sync cycle: scrape, diff, evaluate, personalize.

    Args:
        workers: Max concurrent evaluation threads.

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
        if to_evaluate and not _shutdown.is_set():
            log.info(
                "%d properties need base evaluation. "
                "Scraping detail pages...",
                len(to_evaluate),
            )

            # Phase 1: Scrape detail pages sequentially (browser not
            # thread-safe).
            scraped: list[tuple[Property, str]] = []
            for i, prop in enumerate(to_evaluate):
                if _shutdown.is_set():
                    break
                if not prop.url:
                    continue
                log.info(
                    "[%d/%d] Scraping: %s",
                    i + 1, len(to_evaluate), _label(prop),
                )
                try:
                    page_text = await scrape_property_page(
                        context, prop.url
                    )
                    scraped.append((prop, page_text))
                except Exception:
                    log.exception(
                        "  Failed to scrape %s, skipping.", _label(prop)
                    )

            # Phase 2: Evaluate in parallel.
            if scraped and not _shutdown.is_set():
                log.info(
                    "Evaluating %d properties with %d workers...",
                    len(scraped), workers,
                )
                with ThreadPoolExecutor(max_workers=workers) as executor:
                    futures = {
                        executor.submit(_evaluate_one, prop, text): prop
                        for prop, text in scraped
                    }
                    for future in as_completed(futures):
                        prop = futures[future]
                        try:
                            _, evaluation, text_hash = future.result()
                            save_evaluation(
                                conn, prop.url, DEFAULT_MODEL,
                                evaluation, text_hash, prop.price,
                            )
                            conn.commit()
                            base_count += 1
                            log.info("  Evaluated: %s", _label(prop))
                        except Exception:
                            log.exception(
                                "  Failed to evaluate %s, skipping.",
                                _label(prop),
                            )

        # --- Tier 2: Personalized evaluations ---
        profile_row = get_profile(conn)
        profile = profile_row["preferences"] if profile_row else None
        personal_count = 0

        if profile and not _shutdown.is_set():
            # Collect properties that need personalization.
            to_personalize: list[tuple[Property, str, int]] = []
            for prop in all_properties:
                if not prop.url:
                    continue
                base_eval = get_latest_evaluation(conn, prop.url)
                if base_eval is None:
                    continue
                if not needs_personalized_evaluation(
                    conn, prop.url, base_eval["id"], profile
                ):
                    continue
                to_personalize.append((
                    prop, base_eval["evaluation_text"], base_eval["id"]
                ))

            if to_personalize:
                log.info(
                    "Personalizing %d properties with %d workers...",
                    len(to_personalize), workers,
                )
                with ThreadPoolExecutor(max_workers=workers) as executor:
                    futures = {}
                    for prop, base_text, base_id in to_personalize:
                        if _shutdown.is_set():
                            break
                        future = executor.submit(
                            _personalize_one, prop, base_text, profile
                        )
                        futures[future] = (prop, base_id)

                    for future in as_completed(futures):
                        prop, base_id = futures[future]
                        try:
                            _, result = future.result()
                            save_personalized_evaluation(
                                conn, prop.url, base_id,
                                DEFAULT_MODEL, result, profile,
                            )
                            conn.commit()
                            personal_count += 1
                            log.info(
                                "  Personalized: %s", _label(prop)
                            )
                        except Exception:
                            log.exception(
                                "  Failed to personalize %s, skipping.",
                                _label(prop),
                            )

        # --- Process regeneration queue ---
        regen_count = 0
        if profile and not _shutdown.is_set():
            regen_count = _process_regeneration_queue(
                conn, profile, workers
            )

        parts = [f"Sync complete. {total_new} new, {total_removed} removed"]
        if base_count:
            parts.append(f"{base_count} evaluated")
        if personal_count:
            parts.append(f"{personal_count} personalized")
        if regen_count:
            parts.append(f"{regen_count} regenerated")
        all_idle = not (
            base_count or personal_count or regen_count or to_evaluate
        )
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


async def run_daemon(
    interval_minutes: int = 60, workers: int = DEFAULT_WORKERS
) -> None:
    """Run the sync loop, executing a cycle every `interval_minutes`.

    Handles SIGINT/SIGTERM gracefully — finishes the current cycle then exits.

    Args:
        interval_minutes: Minutes between sync cycles.
        workers: Max concurrent evaluation threads.
    """
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, _handle_signal)

    log.info(
        "Daemon started. Syncing every %d minute(s), %d workers. "
        "Ctrl+C to stop.",
        interval_minutes, workers,
    )

    while not _shutdown.is_set():
        try:
            summary = await _run_sync_cycle(workers)
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
