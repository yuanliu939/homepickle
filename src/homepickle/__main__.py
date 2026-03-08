"""CLI entry point for homepickle.

Usage:
    uv run homepickle login              # Interactive login, saves cookies
    uv run homepickle scrape             # Scrape favorites and print JSON
    uv run homepickle analyze            # Scrape and print analysis report
    uv run homepickle sync [--quiet] [--workers N]  # Scrape, diff, evaluate, cache
    uv run homepickle evaluate [url]     # LLM evaluation (one URL or all cached)
    uv run homepickle report             # Show all cached evaluations
    uv run homepickle daemon [--interval N] [--workers N]  # Continuous sync
    uv run homepickle web [--host ADDR] [--port N]  # Start the web UI
    uv run homepickle debug              # Dump favorites page HTML + screenshot
"""

import asyncio
import hashlib
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from homepickle.analyzer import format_report
from homepickle.browser import create_context, interactive_login
from homepickle.evaluator import (
    DEFAULT_MODEL,
    evaluate_property,
    personalize_evaluation,
)
from homepickle.models import Property
from homepickle.scraper import (
    debug_dump,
    get_favorite_lists,
    properties_to_json,
    scrape_properties,
    scrape_property_page,
)
from homepickle.storage import (
    get_all_evaluations,
    get_connection,
    get_latest_evaluation,
    get_profile,
    needs_evaluation,
    needs_personalized_evaluation,
    save_evaluation,
    save_personalized_evaluation,
    sync_favorites,
    upsert_property,
)


async def _cleanup(pw, context) -> None:
    """Safely close browser and Playwright, ignoring errors.

    Args:
        pw: Playwright instance.
        context: Browser context.
    """
    try:
        await asyncio.wait_for(context.browser.close(), timeout=5)
    except Exception:
        pass
    try:
        await asyncio.wait_for(pw.stop(), timeout=5)
    except Exception:
        pass


async def _login() -> None:
    """Run the interactive login flow."""
    await interactive_login()


async def _scrape() -> None:
    """Scrape all favorites and print property data as JSON."""
    pw, context = await create_context()
    try:
        fav_lists = await get_favorite_lists(context)
        print(f"Found {len(fav_lists)} favorite list(s).")

        for fav_list in fav_lists:
            print(f"\nScraping: {fav_list.name}")
            properties = await scrape_properties(context, fav_list)
            fav_list.properties = properties
            print(f"  {len(properties)} properties found.")

            if properties:
                print(properties_to_json(properties))
    finally:
        await _cleanup(pw, context)


async def _analyze() -> None:
    """Scrape all favorites and print an analysis report."""
    pw, context = await create_context()
    try:
        fav_lists = await get_favorite_lists(context)
        for fav_list in fav_lists:
            properties = await scrape_properties(context, fav_list)
            if not properties:
                continue
            print(f"\n--- {fav_list.name} ---\n")
            print(format_report(properties))
    finally:
        await _cleanup(pw, context)


def _parse_int_flag(flag: str, default: int) -> int:
    """Parse an integer CLI flag like --workers N.

    Args:
        flag: The flag name (e.g. "--workers").
        default: Default value if flag is absent.

    Returns:
        The parsed integer value.
    """
    if flag in sys.argv:
        idx = sys.argv.index(flag)
        if idx + 1 < len(sys.argv):
            return int(sys.argv[idx + 1])
    return default


async def _sync() -> None:
    """Scrape favorites, diff against cache, evaluate new/changed properties.

    This is the main incremental pipeline:
    1. Scrape all favorite lists.
    2. Diff against the database to find new and removed properties.
    3. Scrape detail pages for properties that need evaluation.
    4. Run LLM evaluations in parallel and cache results.
    5. Print a summary of changes.

    Supports --quiet flag for cron-friendly output (only prints the summary).
    Supports --workers N to set concurrent evaluations (default 4).
    """
    quiet = "--quiet" in sys.argv or "-q" in sys.argv
    workers = _parse_int_flag("--workers", 4)

    def _log(msg: str) -> None:
        if not quiet:
            print(msg)

    pw, context = await create_context()
    conn = get_connection()
    try:
        fav_lists = await get_favorite_lists(context)
        _log(f"Found {len(fav_lists)} favorite list(s).")

        total_new = 0
        total_removed = 0
        to_evaluate: list[Property] = []
        all_properties: list[Property] = []

        for fav_list in fav_lists:
            _log(f"\nScraping: {fav_list.name}")
            properties = await scrape_properties(context, fav_list)
            _log(f"  {len(properties)} properties found.")

            for prop in properties:
                upsert_property(conn, prop)
            conn.commit()

            new_props, removed_urls = sync_favorites(
                conn, fav_list.name, properties
            )
            conn.commit()

            if new_props:
                _log(f"  {len(new_props)} new")
            if removed_urls:
                _log(f"  {len(removed_urls)} removed")

            total_new += len(new_props)
            total_removed += len(removed_urls)
            all_properties.extend(properties)

            for prop in properties:
                if prop.url and needs_evaluation(conn, prop.url, prop.price):
                    to_evaluate.append(prop)

        # --- Tier 1: Base evaluations ---
        base_count = 0
        if to_evaluate:
            _log(f"\n{len(to_evaluate)} properties need base evaluation.")

            # Phase 1: Scrape detail pages sequentially.
            _log("Scraping detail pages...")
            scraped: list[tuple[Property, str]] = []
            for i, prop in enumerate(to_evaluate):
                if not prop.url:
                    continue
                label = (f"{prop.address}, {prop.city}"
                         if prop.city else prop.address)
                _log(f"  [{i + 1}/{len(to_evaluate)}] {label}")
                page_text = await scrape_property_page(context, prop.url)
                scraped.append((prop, page_text))

            # Phase 2: Evaluate in parallel.
            if scraped:
                _log(f"\nEvaluating {len(scraped)} properties "
                     f"with {workers} workers...")
                with ThreadPoolExecutor(max_workers=workers) as executor:
                    futures = {
                        executor.submit(
                            evaluate_property, prop, text
                        ): (prop, text)
                        for prop, text in scraped
                    }
                    for future in as_completed(futures):
                        prop, page_text = futures[future]
                        label = (f"{prop.address}, {prop.city}"
                                 if prop.city else prop.address)
                        try:
                            evaluation = future.result()
                            text_hash = hashlib.sha256(
                                page_text.encode()
                            ).hexdigest()[:16]
                            save_evaluation(
                                conn, prop.url, DEFAULT_MODEL,
                                evaluation, text_hash, prop.price,
                            )
                            conn.commit()
                            base_count += 1
                            _log(f"  Done: {label}")
                        except Exception as exc:
                            _log(f"  Failed: {label} ({exc})")

        # --- Tier 2: Personalized evaluations ---
        profile_row = get_profile(conn)
        profile = profile_row["preferences"] if profile_row else None
        personal_count = 0

        if profile:
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
                _log(f"\nPersonalizing {len(to_personalize)} properties "
                     f"with {workers} workers...")
                with ThreadPoolExecutor(max_workers=workers) as executor:
                    futures = {
                        executor.submit(
                            personalize_evaluation, prop, base_text,
                            profile
                        ): (prop, base_id)
                        for prop, base_text, base_id in to_personalize
                    }
                    for future in as_completed(futures):
                        prop, base_id = futures[future]
                        label = (f"{prop.address}, {prop.city}"
                                 if prop.city else prop.address)
                        try:
                            result = future.result()
                            save_personalized_evaluation(
                                conn, prop.url, base_id,
                                DEFAULT_MODEL, result, profile,
                            )
                            conn.commit()
                            personal_count += 1
                            _log(f"  Done: {label}")
                        except Exception as exc:
                            _log(f"  Failed: {label} ({exc})")

        parts = [f"Sync complete. {total_new} new, {total_removed} removed"]
        if base_count:
            parts.append(f"{base_count} evaluated")
        if personal_count:
            parts.append(f"{personal_count} personalized")
        if not base_count and not personal_count and not to_evaluate:
            parts.append("all up to date")
        summary = ", ".join(parts) + "."
        print(summary) if quiet else _log(f"\n{summary}")

    finally:
        conn.close()
        await context.browser.close()
        await pw.stop()


async def _evaluate() -> None:
    """LLM-evaluate properties using Claude.

    If a URL is provided as an argument, evaluate that single property.
    Otherwise, show cached evaluations (run `sync` first to populate).
    """
    url_arg = sys.argv[2] if len(sys.argv) > 2 else None

    if url_arg:
        pw, context = await create_context()
        conn = get_connection()
        try:
            await _evaluate_single(context, conn, url_arg)
        finally:
            conn.close()
            await _cleanup(pw, context)
    else:
        print("Use 'homepickle evaluate <url>' for a single property,")
        print("or 'homepickle sync' to evaluate all new/changed favorites.")
        print("\nCached evaluations:")
        _show_report()


async def _evaluate_single(context, conn, url: str) -> None:
    """Evaluate a single property by URL and cache the result.

    Args:
        context: An authenticated browser context.
        conn: An open database connection.
        url: Redfin property URL.
    """
    # Expand short URLs (redf.in).
    if "redf.in" in url:
        page = await context.new_page()
        await page.goto(url, wait_until="domcontentloaded", timeout=30_000)
        url = page.url
        await page.close()

    print(f"Evaluating: {url}")
    page_text = await scrape_property_page(context, url)
    text_hash = hashlib.sha256(page_text.encode()).hexdigest()[:16]

    # Build a minimal Property from the URL for the evaluator.
    prop = Property(address=url, city="", state="", zip_code="", url=url)
    upsert_property(conn, prop)

    # Tier 1: Base evaluation.
    print("\nAsking Claude for base evaluation...\n")
    base_result = evaluate_property(prop, page_text)
    print(base_result)

    save_evaluation(conn, url, DEFAULT_MODEL, base_result, text_hash, prop.price)
    conn.commit()
    print("\n(Base evaluation cached.)")

    # Tier 2: Personalized evaluation (if profile exists).
    profile_row = get_profile(conn)
    profile = profile_row["preferences"] if profile_row else None

    if profile:
        base_eval = get_latest_evaluation(conn, url)
        print("\nGenerating personalized assessment...\n")
        personal_result = personalize_evaluation(
            prop, base_result, profile
        )
        print(personal_result)

        save_personalized_evaluation(
            conn, url, base_eval["id"],
            DEFAULT_MODEL, personal_result, profile,
        )
        conn.commit()
        print("\n(Personalized evaluation cached.)")


def _show_report() -> None:
    """Print all cached evaluations."""
    conn = get_connection()
    try:
        rows = get_all_evaluations(conn)
        if not rows:
            print("  No evaluations cached yet. Run 'homepickle sync' first.")
            return
        for row in rows:
            print(f"\n{'='*60}")
            print(f"{row['address']}, {row['city']}, {row['state']}")
            if row["price"]:
                print(f"${row['price']:,} | {row['beds']}bd/{row['baths']}ba "
                      f"| {row['sqft']} sqft")
            print(f"Evaluated: {row['created_at'][:10]} (model: {row['model']})")
            print(f"{'='*60}")
            print(row["evaluation_text"])
    finally:
        conn.close()


def _web() -> None:
    """Start the web UI server.

    Supports --host ADDR to change the bind address (default 127.0.0.1)
    and --port N to change the port (default 8080).
    """
    from homepickle.web import run_server

    host = "127.0.0.1"
    if "--host" in sys.argv:
        idx = sys.argv.index("--host")
        if idx + 1 < len(sys.argv):
            host = sys.argv[idx + 1]
    port = _parse_int_flag("--port", 8080)

    run_server(host=host, port=port)


async def _daemon() -> None:
    """Run the continuously polling sync daemon.

    Supports --interval N to set minutes between cycles (default 60).
    Supports --workers N to set concurrent evaluations (default 4).
    """
    import logging

    from homepickle.daemon import DEFAULT_WORKERS, run_daemon

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    interval = _parse_int_flag("--interval", 60)
    workers = _parse_int_flag("--workers", DEFAULT_WORKERS)

    await run_daemon(interval_minutes=interval, workers=workers)


async def _debug() -> None:
    """Dump the favorites page for debugging."""
    pw, context = await create_context()
    try:
        await debug_dump(context, Path("examples/debug"))
    finally:
        await _cleanup(pw, context)


def main() -> None:
    """Parse CLI arguments and run the appropriate command."""
    async_commands = {
        "login": _login,
        "scrape": _scrape,
        "analyze": _analyze,
        "sync": _sync,
        "evaluate": _evaluate,
        "daemon": _daemon,
        "debug": _debug,
    }
    sync_commands = {
        "report": _show_report,
        "web": _web,
    }
    all_commands = list(async_commands) + list(sync_commands)

    if len(sys.argv) < 2 or sys.argv[1] not in all_commands:
        print(f"Usage: homepickle <{'|'.join(all_commands)}>")
        sys.exit(1)

    cmd = sys.argv[1]
    if cmd in sync_commands:
        sync_commands[cmd]()
    else:
        asyncio.run(async_commands[cmd]())


if __name__ == "__main__":
    main()
