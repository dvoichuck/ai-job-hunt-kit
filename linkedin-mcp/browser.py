"""Persistent Chromium context for the LinkedIn session (shared by inbox and profile pack)."""

from __future__ import annotations

import asyncio
import contextlib
import os
from datetime import datetime
from pathlib import Path
from typing import Optional
from urllib.parse import urljoin

from playwright.async_api import (
    BrowserContext,
    Page,
    TimeoutError as PWTimeout,
    async_playwright,
)

HERE = Path(__file__).resolve().parent
BASE_URL = "https://www.linkedin.com"

try:  # pragma: no cover
    from dotenv import load_dotenv

    load_dotenv(HERE / ".env")
except Exception:
    pass


def _env(name: str, default: str) -> str:
    return os.environ.get(name, default)


def _resolve(p: str) -> Path:
    path = Path(os.path.expanduser(p))
    return path if path.is_absolute() else (HERE / path)


PROFILE_DIR = _resolve(_env("LI_PROFILE_DIR", "./browser_profile"))
DEBUG_DIR = _resolve(_env("LI_DEBUG_DIR", "./debug"))
HEADLESS = _env("LI_HEADLESS", "1") not in ("0", "false", "False", "")
SLOWMO = int(_env("LI_SLOWMO", "0") or "0")
TIMEOUT = int(_env("LI_TIMEOUT", "45000") or "45000")
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

SELECTORS = {
    "logged_in": (
        ".global-nav__me, img.global-nav__me-photo, "
        "a.global-nav__primary-link[href*='/feed/'], "
        "a[href*='/messaging/']"
    ),
    "login_form": "input#username, input[name='session_key']",
}

LOCK = asyncio.Lock()


def clear_stale_lock() -> None:
    """A killed Chrome leaves SingletonLock behind and the next launch hangs."""
    for name in ("SingletonLock", "SingletonCookie", "SingletonSocket"):
        with contextlib.suppress(Exception):
            (PROFILE_DIR / name).unlink()


@contextlib.asynccontextmanager
async def browser(headless: Optional[bool] = None):
    PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    clear_stale_lock()
    use_headless = HEADLESS if headless is None else headless
    kwargs = dict(
        user_data_dir=str(PROFILE_DIR),
        headless=use_headless,
        slow_mo=SLOWMO,
        user_agent=USER_AGENT,
        viewport={"width": 1366, "height": 900},
        locale="en-US",
        args=["--no-sandbox", "--disable-blink-features=AutomationControlled"],
    )
    async with async_playwright() as pw:
        ctx: Optional[BrowserContext] = None
        with contextlib.suppress(Exception):
            ctx = await pw.chromium.launch_persistent_context(
                **kwargs, channel="chrome"  # type: ignore[arg-type]
            )
        if ctx is None:
            ctx = await pw.chromium.launch_persistent_context(**kwargs)
        ctx.set_default_timeout(TIMEOUT)
        await ctx.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
        )
        page = ctx.pages[0] if ctx.pages else await ctx.new_page()
        try:
            yield ctx, page
        finally:
            with contextlib.suppress(Exception):
                await ctx.close()


async def goto(page: Page, url: str) -> None:
    await page.goto(url, wait_until="domcontentloaded")
    with contextlib.suppress(PWTimeout):
        await page.wait_for_load_state("networkidle", timeout=10000)


async def is_logged_in(page: Page) -> bool:
    url = (page.url or "").lower()
    if any(x in url for x in ("/login", "/checkpoint", "/uas/login", "/authwall")):
        return False
    try:
        if await page.locator(SELECTORS["login_form"]).count() > 0:
            return False
        if await page.locator(SELECTORS["logged_in"]).count() > 0:
            return True
        # Settings pages render their own nav, so absence of a login form is enough.
        return "linkedin.com" in url
    except Exception:
        return False


def abs_url(url: str) -> str:
    return url if url.startswith("http") else urljoin(BASE_URL, url)


async def screenshot(page: Page, label: str) -> str:
    DEBUG_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = DEBUG_DIR / f"{ts}_{label}.png"
    with contextlib.suppress(Exception):
        await page.screenshot(path=str(path), full_page=True)
    return str(path)


async def dismiss_noise(page: Page) -> None:
    import re

    for name in (
        "Accept",
        "Allow",
        "Got it",
        "Not now",
        "Skip",
        "Dismiss",
        "No thanks",
        "Accept cookies",
        "Agree",
        "Прийняти",
        "Зрозуміло",
        "Не зараз",
        "Пропустити",
        "Закрити",
    ):
        loc = page.get_by_role("button", name=re.compile(rf"^{re.escape(name)}$", re.I))
        if await loc.count() > 0:
            with contextlib.suppress(Exception):
                await loc.first.click(timeout=1500)
                await page.wait_for_timeout(300)
