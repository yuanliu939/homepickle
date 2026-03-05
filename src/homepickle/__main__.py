"""CLI entry point for homepickle.

Usage:
    uv run homepickle login              # Interactive login, saves cookies
    uv run homepickle scrape             # Scrape favorites and print JSON
    uv run homepickle analyze            # Scrape and print analysis report
    uv run homepickle evaluate [url]     # LLM evaluation (one URL or all)
    uv run homepickle debug              # Dump favorites page HTML + screenshot
"""

import asyncio
import sys
from pathlib import Path

from homepickle.analyzer import format_report
from homepickle.browser import create_context, interactive_login
from homepickle.evaluator import evaluate_property, evaluate_property_summary
from homepickle.models import Property
from homepickle.scraper import (
    debug_dump,
    get_favorite_lists,
    properties_to_json,
    scrape_properties,
    scrape_property_page,
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


async def _evaluate() -> None:
    """LLM-evaluate properties using Claude.

    If a URL is provided as an argument, evaluate that single property.
    Otherwise, scrape all favorites and evaluate each one, followed by
    a comparative summary.
    """
    url_arg = sys.argv[2] if len(sys.argv) > 2 else None
    pw, context = await create_context()
    try:
        if url_arg:
            await _evaluate_single(context, url_arg)
        else:
            await _evaluate_all(context)
    finally:
        await context.browser.close()
        await pw.stop()


async def _evaluate_single(context, url: str) -> None:
    """Evaluate a single property by URL.

    Args:
        context: An authenticated browser context.
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

    # Build a minimal Property from the URL for the evaluator.
    prop = Property(address=url, city="", state="", zip_code="", url=url)

    print("\nAsking Claude for evaluation...\n")
    result = evaluate_property(prop, page_text)
    print(result)


async def _evaluate_all(context) -> None:
    """Evaluate all favorite properties and print a comparative summary.

    Args:
        context: An authenticated browser context.
    """
    fav_lists = await get_favorite_lists(context)
    all_properties: list[Property] = []
    for fav_list in fav_lists:
        properties = await scrape_properties(context, fav_list)
        all_properties.extend(properties)

    if not all_properties:
        print("No properties found.")
        return

    print(f"Found {len(all_properties)} properties. Scraping detail pages...")
    page_texts: dict[str, str] = {}
    for i, prop in enumerate(all_properties):
        if not prop.url:
            continue
        print(f"  [{i + 1}/{len(all_properties)}] {prop.address}, {prop.city}")
        page_texts[prop.url] = await scrape_property_page(context, prop.url)

    # Filter to properties with data for the summary.
    evaluated = [p for p in all_properties if p.url and p.url in page_texts]

    print(f"\nAsking Claude for comparative analysis of {len(evaluated)} "
          f"properties...\n")
    summary = evaluate_property_summary(evaluated, page_texts)
    print(summary)


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
        "evaluate": _evaluate,
        "debug": _debug,
    }

    if len(sys.argv) < 2 or sys.argv[1] not in commands:
        print(f"Usage: homepickle <{'|'.join(commands)}>")
        sys.exit(1)

    asyncio.run(commands[sys.argv[1]]())


if __name__ == "__main__":
    main()
