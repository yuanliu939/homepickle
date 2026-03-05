"""CLI entry point for homepickle.

Usage:
    uv run homepickle login     # Interactive login, saves cookies
    uv run homepickle scrape    # Scrape favorites and print JSON
    uv run homepickle analyze   # Scrape and print analysis report
    uv run homepickle debug     # Dump favorites page HTML + screenshot
"""

import asyncio
import sys
from pathlib import Path

from homepickle.analyzer import format_report
from homepickle.browser import create_context, interactive_login
from homepickle.scraper import (
    debug_dump,
    get_favorite_lists,
    properties_to_json,
    scrape_properties,
)


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
        await context.browser.close()
        await pw.stop()


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
        await context.browser.close()
        await pw.stop()


async def _debug() -> None:
    """Dump the favorites page for debugging."""
    pw, context = await create_context()
    try:
        await debug_dump(context, Path("examples/debug"))
    finally:
        await context.browser.close()
        await pw.stop()


def main() -> None:
    """Parse CLI arguments and run the appropriate command."""
    commands = {
        "login": _login,
        "scrape": _scrape,
        "analyze": _analyze,
        "debug": _debug,
    }

    if len(sys.argv) < 2 or sys.argv[1] not in commands:
        print(f"Usage: homepickle <{'|'.join(commands)}>")
        sys.exit(1)

    asyncio.run(commands[sys.argv[1]]())


if __name__ == "__main__":
    main()
