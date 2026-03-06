"""CLI entry point for homepickle.

Usage:
    uv run homepickle login              # Interactive login, saves cookies
    uv run homepickle scrape             # Scrape favorites and print JSON
    uv run homepickle analyze            # Scrape and print analysis report
    uv run homepickle sync [--quiet]     # Scrape, diff, evaluate new/changed, cache
    uv run homepickle evaluate [url]     # LLM evaluation (one URL or all cached)
    uv run homepickle report             # Show all cached evaluations
    uv run homepickle web [--port N]     # Start the web UI
    uv run homepickle debug              # Dump favorites page HTML + screenshot
"""

import asyncio
import hashlib
import os
import signal
import sys
from pathlib import Path

from homepickle.analyzer import format_report
from homepickle.browser import create_context, interactive_login
from homepickle.evaluator import evaluate_property
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
    needs_evaluation,
    save_evaluation,
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


async def _sync() -> None:
    """Scrape favorites, diff against cache, evaluate new/changed properties.

    This is the main incremental pipeline:
    1. Scrape all favorite lists.
    2. Diff against the database to find new and removed properties.
    3. Scrape detail pages for properties that need evaluation.
    4. Run LLM evaluation and cache results.
    5. Print a summary of changes.

    Supports --quiet flag for cron-friendly output (only prints the summary).
    """
    quiet = "--quiet" in sys.argv or "-q" in sys.argv

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

        for fav_list in fav_lists:
            _log(f"\nScraping: {fav_list.name}")
            properties = await scrape_properties(context, fav_list)
            _log(f"  {len(properties)} properties found.")

            # Upsert all properties into DB.
            for prop in properties:
                upsert_property(conn, prop)
            conn.commit()

            # Diff against previous sync.
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

            # Queue properties that need evaluation.
            for prop in properties:
                if prop.url and needs_evaluation(conn, prop.url, prop.price):
                    to_evaluate.append(prop)

        if not to_evaluate:
            summary = (f"Sync complete. {total_new} new, "
                       f"{total_removed} removed. "
                       "All evaluations up to date.")
            print(summary) if quiet else _log(f"\n{summary}")
            return

        _log(f"\n{len(to_evaluate)} properties need evaluation. "
             "Scraping detail pages...")

        for i, prop in enumerate(to_evaluate):
            if not prop.url:
                continue
            label = f"{prop.address}, {prop.city}" if prop.city else prop.address
            _log(f"\n  [{i + 1}/{len(to_evaluate)}] {label}")

            _log("    Scraping detail page...")
            page_text = await scrape_property_page(context, prop.url)
            text_hash = hashlib.sha256(page_text.encode()).hexdigest()[:16]

            _log("    Evaluating with Claude...")
            evaluation = evaluate_property(prop, page_text)

            save_evaluation(
                conn, prop.url, "sonnet", evaluation, text_hash, prop.price
            )
            conn.commit()
            _log("    Done.")

        summary = (f"Sync complete. {total_new} new, {total_removed} removed, "
                   f"{len(to_evaluate)} evaluated.")
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

    print("\nAsking Claude for evaluation...\n")
    result = evaluate_property(prop, page_text)
    print(result)

    save_evaluation(conn, url, "sonnet", result, text_hash, prop.price)
    conn.commit()
    print("\n(Evaluation cached.)")


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

    Supports --port N to change the port (default 8080).
    """
    from homepickle.web import run_server

    port = 8080
    if "--port" in sys.argv:
        idx = sys.argv.index("--port")
        if idx + 1 < len(sys.argv):
            port = int(sys.argv[idx + 1])

    run_server(port=port)


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

    # Install a signal handler that exits immediately on Ctrl+C.
    # Playwright's async operations swallow KeyboardInterrupt inside the
    # event loop, so the default Python handler never fires. Using os._exit
    # bypasses all that and terminates the process immediately.
    _interrupted = False

    def _handle_sigint(signum: int, frame: object) -> None:
        nonlocal _interrupted
        if _interrupted:
            # Second Ctrl+C: force exit immediately.
            os._exit(130)
        _interrupted = True
        print("\nInterrupted. Press Ctrl+C again to force quit.")
        # Restore default handler so a second SIGINT kills the process
        # even if this handler doesn't run again.
        signal.signal(signal.SIGINT, signal.SIG_DFL)

    signal.signal(signal.SIGINT, _handle_sigint)

    cmd = sys.argv[1]
    try:
        if cmd in sync_commands:
            sync_commands[cmd]()
        else:
            asyncio.run(async_commands[cmd]())
    except KeyboardInterrupt:
        print("\nInterrupted.")
        sys.exit(130)


if __name__ == "__main__":
    main()
