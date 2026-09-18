"""Upload the local CV PDF to LinkedIn saved resumes / application settings."""

from __future__ import annotations

from typing import Any

from playwright.async_api import Page

from browser import BASE_URL, dismiss_noise, goto, screenshot


async def dump_file_ui(page: Page) -> dict[str, Any]:
    return await page.evaluate(
        """
        () => {
          const files = [...document.querySelectorAll('input[type=file]')].map(el => ({
            name: el.name || '', id: el.id || '', accept: el.accept || '',
          }));
          const texts = [...document.querySelectorAll('button, a, label, input')]
            .map(el => (el.innerText || el.getAttribute('aria-label') || '').trim().slice(0, 80))
            .filter(t => /resume|cv|pdf|upload|featured|media|document/i.test(t));
          return {url: location.href, files, texts: [...new Set(texts)].slice(0, 40)};
        }
        """
    )


async def try_upload(page: Page, pdf: str) -> bool:
    inp = page.locator("input[type='file']")
    n = await inp.count()
    for i in range(n):
        el = inp.nth(i)
        accept = ((await el.get_attribute("accept")) or "").lower()
        name = ((await el.get_attribute("name")) or "").lower()
        if accept and not any(x in accept for x in ("pdf", "application", "*", "document")):
            if "image" in accept:
                continue
            continue
        if name and any(x in name for x in ("picture", "avatar", "photo", "image")):
            continue
        try:
            await el.set_input_files(pdf)
            await page.wait_for_timeout(1500)
            return True
        except Exception:
            continue
    return False


async def click_add(page: Page) -> bool:
    for name in (
        "Upload resume",
        "Add resume",
        "Upload file",
        "Upload",
        "Add a resume",
        "Replace",
        "Add featured",
        "Add media",
    ):
        loc = page.get_by_text(name, exact=False)
        if await loc.count() == 0:
            continue
        try:
            await loc.first.click()
            await page.wait_for_timeout(800)
            return True
        except Exception:
            continue
    return False


async def save(page: Page) -> bool:
    for name in ("Save", "Done", "Upload", "Apply"):
        btn = page.get_by_role("button", name=name)
        if await btn.count() == 0:
            continue
        try:
            await btn.first.click()
            await page.wait_for_timeout(1200)
            return True
        except Exception:
            continue
    return False


async def upload_cv(page: Page, pdf: str) -> dict[str, Any]:
    urls = [
        f"{BASE_URL}/mypreferences/d/manage-saved-resumes",
        f"{BASE_URL}/mypreferences/d/settings/job-application-settings",
    ]
    results = []
    for url in urls:
        await goto(page, url)
        await dismiss_noise(page)
        await page.wait_for_timeout(800)
        before = await dump_file_ui(page)
        uploaded = await try_upload(page, pdf)
        if not uploaded:
            await click_add(page)
            uploaded = await try_upload(page, pdf)
        saved = await save(page) if uploaded else False
        results.append(
            {
                "tried": url,
                "landed": page.url,
                "uploaded": uploaded,
                "saved": saved,
                "before": before,
                "after": await dump_file_ui(page),
                "screenshot": await screenshot(page, "cv_upload"),
            }
        )
    return {"pdf": pdf, "pages": results}
