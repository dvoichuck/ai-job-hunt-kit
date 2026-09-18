"""Upload the local CV PDF to Djinni account / resume pages."""

from __future__ import annotations

from typing import Any

from playwright.async_api import Page


async def dump_file_ui(page: Page) -> dict[str, Any]:
    return await page.evaluate(
        """
        () => {
          const files = [...document.querySelectorAll('input[type=file]')].map(el => ({
            name: el.name || '', id: el.id || '',
            accept: el.accept || '', hidden: el.hidden || el.offsetParent === null,
          }));
          const links = [...document.querySelectorAll('a, button, label, [role=button]')]
            .map(el => ({t: (el.innerText || '').trim().slice(0, 80), h: el.href || ''}))
            .filter(x => /резюме|resume|cv|pdf|завантаж|додат|замінити|видалити|upload|add/i.test(x.t + ' ' + x.h));
          return {url: location.href, files, links: links.slice(0, 30)};
        }
        """
    )


async def try_upload(page: Page, pdf: str) -> bool:
    inp = page.locator("input[type='file']")
    n = await inp.count()
    for i in range(n):
        el = inp.nth(i)
        accept = (await el.get_attribute("accept")) or ""
        name = (await el.get_attribute("name")) or ""
        if accept and not any(x in accept.lower() for x in ("pdf", "application", "*")):
            continue
        if name and any(x in name.lower() for x in ("picture", "avatar", "photo", "image")):
            continue
        try:
            await el.set_input_files(pdf)
            await page.wait_for_timeout(800)
            return True
        except Exception:
            continue
    return False


async def click_add(page: Page) -> bool:
    for name in (
        "Додати резюме",
        "Замінити",
        "Завантажити резюме",
        "Оновити резюме",
        "Add resume",
        "Upload",
    ):
        loc = page.get_by_text(name, exact=False)
        if await loc.count() == 0:
            continue
        try:
            await loc.first.click()
            await page.wait_for_timeout(600)
            return True
        except Exception:
            continue
    return False


async def save(page: Page) -> bool:
    for name in ("Зберегти зміни", "Зберегти", "Save", "Оновити"):
        btn = page.get_by_role("button", name=name)
        if await btn.count() == 0:
            continue
        try:
            await btn.first.click()
            await page.wait_for_timeout(1500)
            return True
        except Exception:
            continue
    return False


async def upload_cv(page: Page, pdf: str, *, base_url: str) -> dict[str, Any]:
    pages = [
        f"{base_url}/my/account/",
        f"{base_url}/my/resume/",
        f"{base_url}/my/fast_contacts/?action=on",
    ]
    results = []
    for url in pages:
        await page.goto(url, wait_until="domcontentloaded")
        uploaded = await try_upload(page, pdf)
        if not uploaded:
            await click_add(page)
            uploaded = await try_upload(page, pdf)
        saved = await save(page) if uploaded else False
        results.append(
            {
                "tried": url,
                "uploaded": uploaded,
                "saved": saved,
                "ui": await dump_file_ui(page),
            }
        )
    return {"pdf": pdf, "pages": results}
