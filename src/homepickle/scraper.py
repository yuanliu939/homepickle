"""Scrape favorites and property data from Redfin."""

import asyncio
import json
import re
from dataclasses import asdict
from pathlib import Path

from playwright.async_api import BrowserContext, Page

from homepickle.models import FavoriteList, Property

FAVORITES_URL = "https://www.redfin.com/myredfin/favorites"


async def get_favorite_lists(context: BrowserContext) -> list[FavoriteList]:
    """Fetch all favorite lists from the user's Redfin account.

    Navigates to the favorites page and extracts list names and home counts
    from the FavoriteListCard elements.

    Args:
        context: An authenticated browser context.

    Returns:
        A list of FavoriteList objects with name populated.
    """
    page = await context.new_page()
    await page.goto(FAVORITES_URL, wait_until="domcontentloaded", timeout=60_000)
    await asyncio.sleep(5)

    lists: list[FavoriteList] = []
    cards = await page.query_selector_all(
        "[data-rf-test-name='FavoriteListCard']"
    )

    for card in cards:
        name_el = await card.query_selector("[data-rf-test-name='ListName']")
        if not name_el:
            continue
        name = (await name_el.inner_text()).strip()
        # Skip the "All favorites" aggregate list.
        if name.lower() == "all favorites":
            continue
        if name:
            lists.append(FavoriteList(name=name))

    await page.close()
    return lists


async def scrape_properties(
    context: BrowserContext, fav_list: FavoriteList
) -> list[Property]:
    """Scrape all properties from a favorites list.

    Navigates to the favorites page, clicks into the named list, then
    extracts property data from the home cards.

    Args:
        context: An authenticated browser context.
        fav_list: The favorites list to scrape.

    Returns:
        A list of Property objects extracted from the page.
    """
    page = await context.new_page()
    await page.goto(FAVORITES_URL, wait_until="domcontentloaded", timeout=60_000)
    await asyncio.sleep(5)

    # Click into the named list.
    clicked = await _click_list_card(page, fav_list.name)
    if not clicked:
        await page.close()
        return []

    await asyncio.sleep(5)
    properties = await _extract_properties(page)
    await page.close()
    return properties


async def debug_dump(context: BrowserContext, output_dir: Path) -> None:
    """Save the favorites page HTML/screenshot, then click into the first list.

    Args:
        context: An authenticated browser context.
        output_dir: Directory to write debug files into.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    page = await context.new_page()
    await page.goto(FAVORITES_URL, wait_until="domcontentloaded", timeout=60_000)
    await asyncio.sleep(5)

    # Dump the list-of-lists page.
    (output_dir / "favorites.html").write_text(await page.content())
    await page.screenshot(
        path=str(output_dir / "favorites.png"), full_page=True
    )

    # Click the first real list card (skip "All favorites").
    cards = await page.query_selector_all(
        "[data-rf-test-name='FavoriteListCard']"
    )
    for card in cards:
        name_el = await card.query_selector("[data-rf-test-name='ListName']")
        if not name_el:
            continue
        name = (await name_el.inner_text()).strip()
        if name.lower() != "all favorites":
            await card.click()
            await asyncio.sleep(5)
            (output_dir / "list.html").write_text(await page.content())
            await page.screenshot(
                path=str(output_dir / "list.png"), full_page=True
            )
            print(f"Dumped list: {name}")
            break

    print(f"Debug dump saved to {output_dir}")
    await page.close()


async def _click_list_card(page: Page, list_name: str) -> bool:
    """Click a FavoriteListCard by its list name.

    Args:
        page: The favorites page.
        list_name: The name of the list to click.

    Returns:
        True if the card was found and clicked, False otherwise.
    """
    cards = await page.query_selector_all(
        "[data-rf-test-name='FavoriteListCard']"
    )
    for card in cards:
        name_el = await card.query_selector("[data-rf-test-name='ListName']")
        if not name_el:
            continue
        name = (await name_el.inner_text()).strip()
        if name == list_name:
            await card.click()
            return True
    return False


async def _extract_properties(page: Page) -> list[Property]:
    """Extract property data from home cards, scrolling to load all results.

    Redfin lazy-loads home cards as the user scrolls. This function scrolls
    to the bottom of the page repeatedly until no new cards appear.

    Args:
        page: A page showing a list of home cards.

    Returns:
        A list of Property objects.
    """
    # Scroll until all cards are loaded.
    prev_count = 0
    stable_rounds = 0
    while stable_rounds < 3:
        await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        await asyncio.sleep(2)
        current_count = await page.evaluate(
            "document.querySelectorAll('.bp-Homecard').length"
        )
        if current_count == prev_count:
            stable_rounds += 1
        else:
            stable_rounds = 0
            prev_count = current_count

    cards = await page.query_selector_all(".bp-Homecard")
    properties: list[Property] = []
    for card in cards:
        prop = await _parse_property_card(card)
        if prop:
            properties.append(prop)

    return properties


async def _parse_property_card(card) -> Property | None:
    """Extract property data from a single Redfin bp-Homecard element.

    Args:
        card: A Playwright ElementHandle for a home card.

    Returns:
        A Property if parsing succeeded, or None.
    """
    try:
        link_el = await card.query_selector("a[href]")
        if not link_el:
            return None

        # Address is the link text inside bp-Homecard__Content.
        raw_address = (await link_el.inner_text()).strip()
        # The link text contains everything (price, stats, address).
        # The actual address line is the last meaningful line.
        # Instead, use the dedicated address element or parse from href.
        address_el = await card.query_selector(
            ".bp-Homecard__Address, .bp-Homecard__Content a"
        )
        if address_el:
            raw_address = (await address_el.inner_text()).strip()
            # inner_text includes all child text. Extract just the address
            # which appears after the stats. Split by newlines and take the
            # line matching an address pattern.
            for line in raw_address.split("\n"):
                line = line.strip()
                if re.search(r"[A-Z]{2}\s+\d{5}", line):
                    raw_address = line
                    break

        address, city, state, zip_code = _parse_address(raw_address)
        if not address or address == raw_address == "":
            return None

        price_el = await card.query_selector(".bp-Homecard__Price--value")
        price = _parse_price(await price_el.inner_text()) if price_el else None

        beds_el = await card.query_selector(".bp-Homecard__Stats--beds")
        baths_el = await card.query_selector(".bp-Homecard__Stats--baths")
        sqft_el = await card.query_selector(".bp-Homecard__Stats--sqft")

        beds = _parse_int(await beds_el.inner_text()) if beds_el else None
        baths = _parse_float(await baths_el.inner_text()) if baths_el else None
        sqft = _parse_int(await sqft_el.inner_text()) if sqft_el else None

        href = await link_el.get_attribute("href")
        url = (
            f"https://www.redfin.com{href}"
            if href and href.startswith("/")
            else href
        )

        return Property(
            address=address,
            city=city,
            state=state,
            zip_code=zip_code,
            price=price,
            beds=beds,
            baths=baths,
            sqft=sqft,
            url=url,
        )
    except Exception:
        return None


def _parse_address(raw: str) -> tuple[str, str, str, str]:
    """Parse a Redfin address string into components.

    Redfin typically formats as "123 Main St, Seattle, WA 98101" or
    multi-line with the street on one line and city/state/zip on another.

    Args:
        raw: Raw address text from the page.

    Returns:
        A tuple of (address, city, state, zip_code). Missing parts default
        to empty string.
    """
    # Normalize newlines to commas.
    normalized = raw.replace("\n", ", ").strip()
    # Match: street, city, state zip
    match = re.match(
        r"^(.+?),\s*(.+?),\s*([A-Z]{2})\s+(\d{5}(?:-\d{4})?)$", normalized
    )
    if match:
        return match.group(1), match.group(2), match.group(3), match.group(4)
    # Fallback: put everything in address.
    return normalized, "", "", ""


def _parse_price(raw: str) -> int | None:
    """Parse a price string like '$500,000' into an integer.

    Args:
        raw: Raw price text from the page.

    Returns:
        Price as an integer, or None if parsing fails.
    """
    digits = re.sub(r"[^\d]", "", raw)
    return int(digits) if digits else None


def _parse_int(raw: str) -> int | None:
    """Extract the first integer from a string like '3 beds' or '1,500 sq ft'.

    Args:
        raw: Raw text that may contain a number.

    Returns:
        The parsed integer, or None.
    """
    digits = re.sub(r"[^\d]", "", raw)
    return int(digits) if digits else None


def _parse_float(raw: str) -> float | None:
    """Extract the first float from a string like '2.5 baths'.

    Args:
        raw: Raw text that may contain a number.

    Returns:
        The parsed float, or None.
    """
    match = re.search(r"[\d.]+", raw)
    return float(match.group()) if match else None


def _parse_stats(raw: str) -> tuple[int | None, float | None, int | None]:
    """Parse a stats string like '3 Beds 2 Baths 1,500 Sq Ft'.

    Args:
        raw: Raw stats text from the page.

    Returns:
        A tuple of (beds, baths, sqft). Missing values are None.
    """
    beds = baths = sqft = None

    beds_match = re.search(r"(\d+)\s*(?:Bed|bed|BD|bd)", raw)
    if beds_match:
        beds = int(beds_match.group(1))

    baths_match = re.search(r"([\d.]+)\s*(?:Bath|bath|BA|ba)", raw)
    if baths_match:
        baths = float(baths_match.group(1))

    sqft_match = re.search(r"([\d,]+)\s*(?:Sq\.?\s*Ft|sqft|SF|sf)", raw)
    if sqft_match:
        sqft = int(sqft_match.group(1).replace(",", ""))

    return beds, baths, sqft


def properties_to_json(properties: list[Property]) -> str:
    """Serialize a list of properties to a JSON string.

    Args:
        properties: Properties to serialize.

    Returns:
        A JSON string.
    """
    return json.dumps([asdict(p) for p in properties], indent=2)
