"""Scrape saved searches and property data from Redfin."""

from playwright.async_api import BrowserContext

from homepickle.models import Property, SavedSearch


async def get_saved_searches(context: BrowserContext) -> list[SavedSearch]:
    """Fetch the list of saved searches from the user's Redfin account.

    Args:
        context: An authenticated browser context.

    Returns:
        A list of SavedSearch objects with names populated.
    """
    page = await context.new_page()
    await page.goto("https://www.redfin.com/myredfin/favorites")

    # TODO: Parse saved search names and URLs from the page.
    searches: list[SavedSearch] = []

    await page.close()
    return searches


async def get_properties(
    context: BrowserContext, search: SavedSearch
) -> list[Property]:
    """Scrape all properties from a saved search.

    Args:
        context: An authenticated browser context.
        search: The saved search to scrape properties from.

    Returns:
        A list of Property objects.
    """
    # TODO: Navigate to the saved search and extract property data.
    return search.properties
