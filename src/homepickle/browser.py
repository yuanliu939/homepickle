"""Headless browser automation for Redfin using Playwright."""

import asyncio
import json
from pathlib import Path

from playwright.async_api import BrowserContext, Playwright, async_playwright

COOKIES_PATH = Path.home() / ".homepickle" / "cookies.json"


async def _start_playwright() -> Playwright:
    """Start and return a Playwright instance.

    Returns:
        A running Playwright instance.
    """
    return await async_playwright().start()


async def interactive_login() -> None:
    """Launch a visible browser for the user to log in to Redfin manually.

    Opens the Redfin login page in a headed (visible) browser window. The user
    logs in manually, handling any CAPTCHA or 2FA. Once the user reaches their
    account page, cookies are saved to disk for future headless sessions.
    """
    pw = await _start_playwright()
    # Use the actual installed Chrome rather than Playwright's Chromium
    # to avoid bot detection.
    browser = await pw.chromium.launch(
        headless=False,
        channel="chrome",
        args=["--disable-blink-features=AutomationControlled"],
    )
    context = await browser.new_context(
        viewport={"width": 1280, "height": 800},
    )
    await context.add_init_script(
        "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
    )
    page = await context.new_page()

    await page.goto("https://www.redfin.com/login")
    print("Please log in to Redfin in the browser window.")
    print("Cookies will be saved once you reach your account page.")
    print("(Or close the browser to cancel.)")

    # Poll the URL until the user navigates away from the login page.
    saved = False
    try:
        while not page.is_closed():
            url = page.url
            on_login = "/login" in url or "/signup" in url
            on_redfin = "redfin.com" in url
            if on_redfin and not on_login:
                # User has logged in and been redirected. Wait for cookies
                # to settle, then save.
                await asyncio.sleep(3)
                cookies = await context.cookies()
                _save_cookies(cookies)
                print(f"Cookies saved to {COOKIES_PATH}")
                saved = True
                break
            await asyncio.sleep(1)
    except Exception:
        pass

    if not saved:
        print("Browser closed before login completed. No cookies saved.")

    try:
        await browser.close()
    except Exception:
        pass
    await pw.stop()


async def create_context() -> tuple[Playwright, BrowserContext]:
    """Create a headless browser context loaded with saved cookies.

    Returns:
        A tuple of (Playwright, BrowserContext). Caller is responsible for
        closing both when done.

    Raises:
        FileNotFoundError: If no saved cookies exist. Run interactive_login first.
    """
    cookies = _load_cookies()
    pw = await _start_playwright()
    browser = await pw.chromium.launch(
        headless=True,
        channel="chrome",
        args=["--disable-blink-features=AutomationControlled"],
    )
    context = await browser.new_context(
        user_agent=(
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/125.0.0.0 Safari/537.36"
        ),
        viewport={"width": 1280, "height": 800},
    )
    await context.add_init_script(
        "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
    )
    await context.add_cookies(cookies)
    return pw, context


async def refresh_cookies(context: BrowserContext) -> None:
    """Save the current browser cookies back to disk.

    Calling this after a successful authenticated page load keeps
    the session alive between daemon cycles.

    Args:
        context: An authenticated browser context.
    """
    cookies = await context.cookies()
    if cookies:
        _save_cookies(cookies)


def _save_cookies(cookies: list[dict]) -> None:
    """Write cookies to disk.

    Args:
        cookies: List of cookie dicts from Playwright.
    """
    COOKIES_PATH.parent.mkdir(parents=True, exist_ok=True)
    COOKIES_PATH.write_text(json.dumps(cookies, indent=2))


def _load_cookies() -> list[dict]:
    """Read cookies from disk.

    Returns:
        List of cookie dicts for Playwright.

    Raises:
        FileNotFoundError: If the cookies file does not exist.
    """
    if not COOKIES_PATH.exists():
        raise FileNotFoundError(
            f"No cookies found at {COOKIES_PATH}. "
            "Run 'homepickle login' first."
        )
    return json.loads(COOKIES_PATH.read_text())
