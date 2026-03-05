"""Headless browser automation for Redfin using Playwright."""

from playwright.async_api import Browser, BrowserContext, async_playwright


async def launch_browser() -> tuple[Browser, BrowserContext]:
    """Launch a headless Chromium browser and create a context.

    Returns:
        A tuple of (Browser, BrowserContext).
    """
    pw = await async_playwright().start()
    browser = await pw.chromium.launch(headless=True)
    context = await browser.new_context()
    return browser, context


async def login(context: BrowserContext, email: str, password: str) -> None:
    """Log in to Redfin with the given credentials.

    Args:
        context: The browser context to use.
        email: Redfin account email.
        password: Redfin account password.
    """
    page = await context.new_page()
    await page.goto("https://www.redfin.com/login")
    await page.fill('input[name="emailInput"]', email)
    await page.fill('input[name="passwordInput"]', password)
    await page.click('button[type="submit"]')
    await page.wait_for_url("**/myredfin/**", timeout=15000)
    await page.close()
