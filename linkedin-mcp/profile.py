"""Fill the logged-in LinkedIn profile from a local pack JSON."""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import re
from pathlib import Path
from typing import Any, Optional

from playwright.async_api import Locator, Page

from browser import BASE_URL, dismiss_noise, goto, is_logged_in, screenshot

HERE = Path(__file__).resolve().parent
MONTHS = (
    "",
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
)

TYPEAHEAD = (
    '[role="option"], .basic-typeahead__selectable, '
    ".search-typeahead-v2__hit, .artdeco-typeahead__result, "
    '[role="listbox"] [role="option"]'
)

# The account may still render Ukrainian labels, so every pattern accepts both.
L = {
    "save": r"^(Save|Зберегти)$",
    "add_section": r"(Add section|Add profile section|Додати розділ)",
    "edit_intro": r"(Edit intro|Edit profile|Редагувати вступ|Змінити вступ)",
    "first_name": r"(First name|Ім'я|Ім’я)",
    "last_name": r"(Last name|Прізвище)",
    "headline": r"(Headline|Заголовок)",
    "industry": r"(Industry|Галузь)",
    "location": r"(^Location$|City|Місцезнаходження|Місто)",
    "edit_about": r"(Edit about|Add about|Про себе)",
    "about_field": r"(About|Summary|Про себе)",
    "add_experience": r"(Add experience|Add position|Додати досвід|Додати посаду)",
    "job_title": r"(^Title$|Position|Посада)",
    "company": r"(Company or organization|^Company$|Компанія|Організація)",
    "employment_type": r"(Employment type|Тип зайнятості)",
    "location_type": r"(Location type|Тип місцезнаходження)",
    "description": r"(Description|Опис)",
    "currently_working": r"(I currently work here|I am currently working|зараз працюю)",
    "update_headline": r"(Update my headline|Оновити заголовок)",
    "add_education": r"(Add education|Додати освіту)",
    "school": r"(^School$|Навчальний заклад|Заклад освіти)",
    "degree": r"(^Degree$|Ступінь)",
    "field_of_study": r"(Field of study|Спеціальність|Напрям)",
    "add_skill": r"(Add skill|Add skills|Додати навичк)",
    "skill_field": r"(^Skill$|Skill name|Навичка)",
    "add_language": r"(Add language|Додати мову)",
    "language_field": r"(^Language$|^Мова$)",
    "proficiency": r"(Proficiency|Рівень володіння)",
    "notify": r"(Notify network|Share with network|Notify my network|Сповістити)",
}


async def ensure_english_ui(page: Page) -> dict[str, Any]:
    """Switch the interface language to English so form labels are predictable."""
    await goto(page, f"{BASE_URL}/mypreferences/d/language")
    await dismiss_noise(page)
    if not await is_logged_in(page):
        return _need_login(page)
    await page.wait_for_timeout(800)
    sel = page.locator("select").first
    if await sel.count() == 0:
        shot = await screenshot(page, "lang_no_select")
        return {"error": "Language select not found.", "screenshot": shot}
    current = ""
    with contextlib.suppress(Exception):
        current = await sel.input_value()
    if current.startswith("en"):
        return {"status": "already_english", "value": current}
    picked = ""
    for label in ("English (англійська)", "English"):
        with contextlib.suppress(Exception):
            await sel.select_option(label=label)
            picked = label
            break
    if not picked:
        with contextlib.suppress(Exception):
            await sel.select_option(value="en_US")
            picked = "en_US"
    await page.wait_for_timeout(2500)
    await _click_named(page, L["save"], r"^(Apply|Застосувати)$")
    await page.wait_for_timeout(2000)
    await goto(page, f"{BASE_URL}/in/me/")
    body = ""
    with contextlib.suppress(Exception):
        body = await page.inner_text("body")
    return {
        "status": "ok" if "Додати розділ" not in body else "still_ukrainian",
        "picked": picked,
    }


def pack_path() -> Path:
    raw = os.environ.get("LI_PACK", "").strip()
    if raw:
        p = Path(os.path.expanduser(raw))
        return p if p.is_absolute() else HERE / p
    local = HERE.parent / "local" / "linkedin.pack.json"
    if local.exists():
        return local
    return HERE / "pack.json"


def load_pack(path: Optional[str] = None) -> dict[str, Any]:
    p = Path(path) if path else pack_path()
    if not p.exists():
        raise FileNotFoundError(
            f"LinkedIn pack not found at {p}. "
            "Copy linkedin-mcp/pack.example.json to local/linkedin.pack.json"
        )
    return json.loads(p.read_text(encoding="utf-8"))


def _need_login(page: Page) -> dict[str, Any]:
    return {"error": "Not logged in. Run `login` first.", "url": page.url}


async def _dialog(page: Page) -> Locator:
    dlg = page.get_by_role("dialog")
    if await dlg.count() > 0:
        return dlg.last
    return page.locator("body")


async def _safe_click(loc: Locator) -> bool:
    """The sticky top nav swallows real clicks, so fall back to a DOM click."""
    with contextlib.suppress(Exception):
        await loc.scroll_into_view_if_needed()
    with contextlib.suppress(Exception):
        await loc.click(timeout=6000)
        return True
    with contextlib.suppress(Exception):
        await loc.evaluate(
            "el => (el.closest('button, a, [role=button]') || el).click()"
        )
        return True
    return False


async def _click_named(scope: Locator | Page, *patterns: str) -> bool:
    for pat in patterns:
        rx = re.compile(pat, re.I)
        for role in ("button", "link", "menuitem"):
            loc = scope.get_by_role(role, name=rx)  # type: ignore[arg-type]
            if await loc.count() > 0 and await _safe_click(loc.first):
                return True
        loc = scope.get_by_text(rx)
        if await loc.count() > 0 and await _safe_click(loc.first):
            return True
    return False


async def _fill_labeled(scope: Locator | Page, labels: list[str], value: str) -> bool:
    if value is None:
        return False
    for label in labels:
        loc = scope.get_by_label(re.compile(label, re.I))
        if await loc.count() == 0:
            loc = scope.get_by_placeholder(re.compile(label, re.I))
        if await loc.count() == 0:
            continue
        el = loc.first
        await el.click()
        with contextlib.suppress(Exception):
            await el.fill("")
        await el.fill(str(value))
        return True
    return False


MODAL = ".artdeco-modal, [role='dialog']"

NAME_INDEX_JS = """
() => {
  const inputs = [...document.querySelectorAll('input')];
  const add = inputs.find(
    (i) => ((i.labels && i.labels[0] ? i.labels[0].innerText : '') || '').trim()
      === 'Additional name'
  );
  if (!add) return null;
  const i = inputs.indexOf(add);
  if (i < 2) return null;
  return {first: i - 2, last: i - 1,
          firstValue: inputs[i - 2].value, lastValue: inputs[i - 1].value};
}
"""


CONTROLS = 'input, textarea, [contenteditable="true"]'

FIELD_BY_LABEL_JS = """
(labelText) => {
  const controls = [...document.querySelectorAll(
    'input, textarea, [contenteditable="true"]'
  )];
  const norm = (s) => (s || '').replace(/\\*/g, '').trim().toLowerCase();
  const nodes = [...document.querySelectorAll('label, span, div, h3, p')];
  const target = nodes.find(
    (n) => norm(n.innerText) === norm(labelText) && n.children.length === 0
  );
  if (!target) return null;
  let el = target;
  for (let i = 0; i < 6 && el; i++) {
    const c = el.querySelector
      && [...el.querySelectorAll('input, textarea, [contenteditable="true"]')]
        .find((x) => !['checkbox', 'hidden', 'radio'].includes(x.type || ''));
    if (c) return controls.indexOf(c);
    el = el.parentElement;
  }
  return null;
}
"""


async def _field_by_placeholder(page: Page, placeholder: str) -> Optional[Locator]:
    loc = page.get_by_placeholder(re.compile(re.escape(placeholder), re.I))
    return loc.first if await loc.count() > 0 else None


async def _any_field(page: Page, label: str = "", placeholder: str = "") -> Optional[Locator]:
    el = await _field_by_label(page, label) if label else None
    if el is None and placeholder:
        el = await _field_by_placeholder(page, placeholder)
    return el


async def _field_by_label(page: Page, label: str) -> Optional[Locator]:
    """LinkedIn labels are often plain text, so match on the visible caption."""
    idx = await page.evaluate(FIELD_BY_LABEL_JS, label)
    if idx is None or idx < 0:
        return None
    return page.locator(CONTROLS).nth(idx)


async def _modal(page: Page) -> Locator:
    loc = page.locator(MODAL)
    if await loc.count() > 0:
        return loc.last
    return page.locator("body")


async def _modal_open(page: Page) -> bool:
    """LinkedIn's editors are not role=dialog, so look for the Save affordance."""
    if await page.locator(MODAL).count() > 0:
        return True
    btn = page.get_by_role("button", name=re.compile(L["save"], re.I))
    return await btn.count() > 0


async def _fill_names(page: Page, first: str, last: str) -> dict[str, Any]:
    """Name inputs carry no label — locate them relative to 'Additional name'."""
    idx = await page.evaluate(NAME_INDEX_JS)
    if not idx:
        return {"first_name": False, "last_name": False, "reason": "anchor not found"}
    inputs = page.locator("input")
    out = {}
    for key, pos, value in (
        ("first_name", idx["first"], first),
        ("last_name", idx["last"], last),
    ):
        el = inputs.nth(pos)
        try:
            await el.scroll_into_view_if_needed()
            await el.fill(value)
            out[key] = True
        except Exception as exc:
            out[key] = False
            out[f"{key}_error"] = str(exc)[:120]
    return out


async def _set_text(page: Page, el: Locator, value: str) -> None:
    """fill() alone leaves React state stale here, so type it and blur to commit."""
    await el.scroll_into_view_if_needed()
    await el.click()
    await page.keyboard.press("Control+a")
    await page.keyboard.press("Delete")
    await el.type(value, delay=15)
    await page.keyboard.press("Tab")
    await page.wait_for_timeout(300)


async def _type_and_pick(page: Page, field: Locator, value: str, option: str = "") -> bool:
    """Type, then accept a suggestion only when it matches — never a random first hit.

    The first typeahead row is often a different org with a similar name, so an
    unmatched list is left as plain text instead of clicking blindly.
    """
    with contextlib.suppress(Exception):
        await field.scroll_into_view_if_needed()
    await field.click(timeout=8000)
    with contextlib.suppress(Exception):
        await field.fill("")
    await field.type(value, delay=60)
    wanted = option or value
    # Results are server-rendered and can take several seconds to stream in.
    for _ in range(8):
        await page.wait_for_timeout(700)
        if await page.locator(TYPEAHEAD).count() > 0:
            break
    opt = page.locator(TYPEAHEAD).filter(
        has_text=re.compile(rf"^\s*{re.escape(wanted)}\s*$", re.I)
    )
    if await opt.count() == 0:
        opt = page.locator(TYPEAHEAD).filter(
            has_text=re.compile(rf"^\s*{re.escape(wanted)}\b", re.I)
        )
    if await opt.count() > 0:
        with contextlib.suppress(Exception):
            await opt.first.click()
            await page.wait_for_timeout(600)
            return True
    with contextlib.suppress(Exception):
        await page.keyboard.press("Escape")
    return False


async def _confirm_typeahead(page: Page, query: str) -> None:
    await page.wait_for_timeout(700)
    opt = page.locator(TYPEAHEAD).filter(has_text=re.compile(re.escape(query), re.I))
    if await opt.count() > 0:
        with contextlib.suppress(Exception):
            await opt.first.click()
            return
    any_opt = page.locator(TYPEAHEAD)
    if await any_opt.count() > 0:
        with contextlib.suppress(Exception):
            await any_opt.first.click()
            return
    with contextlib.suppress(Exception):
        await page.keyboard.press("ArrowDown")
        await page.keyboard.press("Enter")


async def _uncheck_notify(scope: Locator | Page) -> None:
    box = scope.get_by_label(re.compile(L["notify"], re.I))
    if await box.count() > 0:
        with contextlib.suppress(Exception):
            if await box.first.is_checked():
                await box.first.uncheck()


async def _save(scope: Locator | Page, page: Page) -> bool:
    """A click is not a save: LinkedIn keeps the editor open on validation errors."""
    await _uncheck_notify(scope)
    if not await _click_named(scope, L["save"]):
        return False
    await page.wait_for_timeout(2000)
    with contextlib.suppress(Exception):
        return await page.locator(MODAL).count() == 0
    return True


async def _open_profile(page: Page) -> dict[str, Any] | None:
    await goto(page, f"{BASE_URL}/in/me/")
    await dismiss_noise(page)
    if not await is_logged_in(page):
        return _need_login(page)
    return None


async def profile_base(page: Page) -> str:
    """Canonical /in/<vanity>/ URL — edit routes hang off it, not off /in/me/."""
    if "/in/" not in (page.url or "") or "/in/me/" in (page.url or ""):
        await goto(page, f"{BASE_URL}/in/me/")
    url = (page.url or "").split("?")[0]
    m = re.search(r"(https://[^/]+/in/[^/]+/)", url)
    return m.group(1) if m else f"{BASE_URL}/in/me/"


async def _open_editor(page: Page, *patterns: str, fallback_url: str = "") -> bool:
    if await _click_named(page, *patterns):
        await page.wait_for_timeout(900)
        return True
    if fallback_url:
        await goto(page, fallback_url)
        await dismiss_noise(page)
        await page.wait_for_timeout(800)
        return True
    return False


async def _select_combo(scope: Locator | Page, labels: list[str], value: str) -> bool:
    for label in labels:
        combo = scope.get_by_role("combobox", name=re.compile(label, re.I))
        if await combo.count() == 0:
            combo = scope.get_by_label(re.compile(label, re.I))
        if await combo.count() == 0:
            continue
        el = combo.first
        tag = ""
        with contextlib.suppress(Exception):
            tag = (await el.evaluate("e => e.tagName") or "").lower()
        if tag == "select":
            for attempt in (value, value.lower()):
                with contextlib.suppress(Exception):
                    await el.select_option(label=attempt)
                    return True
                with contextlib.suppress(Exception):
                    await el.select_option(value=attempt)
                    return True
        else:
            await el.click()
            await el.fill("")
            await el.fill(value)
            opt = scope.get_by_role("option", name=re.compile(re.escape(value), re.I))
            if await opt.count() > 0:
                await opt.first.click()
                return True
            host = scope if isinstance(scope, Page) else scope.page
            await _confirm_typeahead(host, value)
            return True
    return False


async def _pick_dropdown(page: Page, label: str, value: str) -> bool:
    """Dates render as custom div dropdowns here, not as <select> elements."""
    loc = page.get_by_label(re.compile(label, re.I))
    if await loc.count() == 0:
        return False
    el = loc.first
    tag = ""
    with contextlib.suppress(Exception):
        tag = (await el.evaluate("e => e.tagName.toLowerCase()")) or ""
    if tag == "select":
        for kwargs in ({"label": value}, {"value": value}):
            with contextlib.suppress(Exception):
                await el.select_option(**kwargs)  # type: ignore[arg-type]
                return True
        return False
    inner = el.locator("select")
    if await inner.count() > 0:
        with contextlib.suppress(Exception):
            await inner.first.select_option(label=value)
            return True
    if not await _safe_click(el):
        return False
    await page.wait_for_timeout(700)
    opt = page.get_by_role("option", name=re.compile(rf"^\s*{re.escape(value)}\s*$", re.I))
    if await opt.count() == 0:
        opt = page.locator(TYPEAHEAD).filter(
            has_text=re.compile(rf"^\s*{re.escape(value)}\s*$", re.I)
        )
    if await opt.count() > 0 and await _safe_click(opt.first):
        await page.wait_for_timeout(400)
        return True
    with contextlib.suppress(Exception):
        await page.keyboard.press("Escape")
    return False


async def _set_month_year(
    scope: Locator | Page, page: Page, which: str, month: Optional[int], year: Optional[int]
) -> dict[str, bool]:
    done = {"month": False, "year": False}
    if month:
        done["month"] = await _pick_dropdown(
            page, rf"{which} date.*month", MONTHS[month]
        ) or await _pick_dropdown(page, rf"{which} month", MONTHS[month])
    if year:
        done["year"] = await _pick_dropdown(
            page, rf"{which} date.*year", str(year)
        ) or await _pick_dropdown(page, rf"{which} year", str(year))
    return done


async def snapshot(page: Page) -> dict[str, Any]:
    err = await _open_profile(page)
    if err:
        return err
    data = await page.evaluate(
        """
        () => {
          const h1 = document.querySelector('h1');
          const headline = document.querySelector(
            '.text-body-medium, .pv-text-details__left-panel .text-body-medium'
          );
          return {
            url: location.href,
            title: document.title,
            name: (h1 && h1.innerText || '').trim(),
            headline: (headline && headline.innerText || '').trim(),
            text: (document.body.innerText || '').slice(0, 18000),
          };
        }
        """
    )
    data["logged_in"] = True
    return data


async def update_intro(page: Page, pack: dict[str, Any]) -> dict[str, Any]:
    err = await _open_profile(page)
    if err:
        return err
    base = await profile_base(page)
    opened = await _open_editor(
        page,
        L["edit_intro"],
        fallback_url=f"{base}edit/intro/",
    )
    if not opened:
        shot = await screenshot(page, "intro_no_edit")
        return {"error": "Could not open intro editor.", "screenshot": shot, "url": page.url}
    await page.wait_for_timeout(1800)
    dlg = await _modal(page)
    filled: dict[str, Any] = {}

    async def put(key: str, label: str, value: str) -> None:
        loc = dlg.get_by_label(re.compile(rf"^{label}$", re.I))
        el = loc.first if await loc.count() > 0 else await _field_by_label(page, label)
        if el is None:
            filled[key] = False
            return
        try:
            await _set_text(page, el, value)
            current = ""
            with contextlib.suppress(Exception):
                current = await el.input_value()
            if not current:
                with contextlib.suppress(Exception):
                    current = await el.inner_text()
            filled[key] = current.strip() == value.strip()
        except Exception as exc:
            filled[key] = False
            filled[f"{key}_error"] = str(exc)[:140]

    await put("first_name", "First name", pack["first_name"])
    await put("last_name", "Last name", pack["last_name"])
    if not (filled.get("first_name") and filled.get("last_name")):
        filled.update(await _fill_names(page, pack["first_name"], pack["last_name"]))
    await put("headline", "Headline", pack["headline"])

    industry = await _field_by_label(page, "Industry")
    if industry is not None:
        filled["industry"] = await _type_and_pick(page, industry, pack["industry"])
        # An unconfirmed industry keeps a validation error that blocks Save.
        body = ""
        with contextlib.suppress(Exception):
            body = await dlg.inner_text()
        if "valid industry" in body.lower():
            with contextlib.suppress(Exception):
                await industry.fill("")
                await page.keyboard.press("Tab")
                await page.wait_for_timeout(500)
            filled["industry"] = False
    saved = await _save(dlg, page)
    if not saved:
        with contextlib.suppress(Exception):
            filled["blocked_by"] = (await dlg.inner_text())[:400]
    shot = await screenshot(page, "intro")
    return {"status": "ok" if saved else "maybe", "filled": filled, "screenshot": shot, "url": page.url}


async def update_about(page: Page, text: str) -> dict[str, Any]:
    err = await _open_profile(page)
    if err:
        return err
    base = await profile_base(page)
    opened = await _open_editor(page, r"(Edit about|Add about)")
    if not opened:
        if await _click_named(page, L["add_section"]):
            await page.wait_for_timeout(1200)
            opened = await _click_named(page, r"(Add about|^About$)")
    if not opened:
        await goto(page, f"{base}edit/forms/summary/new/")
        await page.wait_for_timeout(1500)
        opened = await _modal_open(page)
    if not opened:
        shot = await screenshot(page, "about_no_edit")
        return {"error": "Could not open About editor.", "screenshot": shot, "url": page.url}
    await page.wait_for_timeout(1500)
    dlg = await _modal(page)
    area = await _field_by_label(page, "About")
    if area is None:
        cand = dlg.locator('textarea, [contenteditable="true"]')
        area = cand.first if await cand.count() > 0 else None
    ok = False
    if area is not None:
        with contextlib.suppress(Exception):
            await _set_text(page, area, text)
            ok = True
    saved = await _save(dlg, page)
    shot = await screenshot(page, "about")
    return {
        "status": "ok" if saved and ok else "maybe",
        "filled": ok,
        "screenshot": shot,
        "url": page.url,
    }


async def _section_text(page: Page, section: str) -> str:
    """Headline and About mention the same companies, so read the section itself."""
    base = await profile_base(page)
    await goto(page, f"{base}details/{section}/")
    await dismiss_noise(page)
    await page.wait_for_timeout(1200)
    if "details" not in (page.url or ""):
        return ""
    with contextlib.suppress(Exception):
        return await page.inner_text("main")
    return ""


async def add_experience(page: Page, job: dict[str, Any], skip_if_present: bool = True) -> dict[str, Any]:
    err = await _open_profile(page)
    if err:
        return err
    if skip_if_present:
        existing = await _section_text(page, "experience")
        if job["company"] in existing and job["title"] in existing:
            return {"status": "skipped", "reason": f"already on profile: {job['company']}"}
    await _open_profile(page)
    base = await profile_base(page)
    opened = await _open_editor(page, L["add_experience"])
    if not opened:
        if await _click_named(page, L["add_section"]):
            await page.wait_for_timeout(1200)
            opened = await _click_named(page, L["add_experience"])
    if not opened:
        await goto(page, f"{base}add-edit/POSITION/")
        await page.wait_for_timeout(1500)
        opened = await _modal_open(page)
    if not opened:
        shot = await screenshot(page, "exp_no_edit")
        return {"error": "Could not open experience editor.", "screenshot": shot}
    await page.wait_for_timeout(1500)
    dlg = await _modal(page)
    filled: dict[str, Any] = {}

    title = await _any_field(page, "Job title", "Example: Senior Product Manager")
    filled["title"] = (
        await _type_and_pick(page, title, job["title"]) if title is not None else False
    )
    company = await _any_field(page, "Organization", "Example: Microsoft")
    filled["company"] = (
        await _type_and_pick(page, company, job["company"])
        if company is not None
        else False
    )
    filled["employment_type"] = await _select_combo(
        dlg, [L["employment_type"]], job.get("employment_type") or "Full-time"
    )
    # End dates stay hidden while this is ticked.
    current = page.get_by_label(re.compile(L["currently_working"], re.I))
    if await current.count() > 0:
        with contextlib.suppress(Exception):
            if await current.first.is_checked():
                await current.first.uncheck()
                await page.wait_for_timeout(800)
    filled["start"] = await _set_month_year(
        dlg, page, "start", job.get("start_month"), job.get("start_year")
    )
    filled["end"] = await _set_month_year(
        dlg, page, "end", job.get("end_month"), job.get("end_year")
    )
    if job.get("location_type"):
        filled["location_type"] = await _select_combo(
            dlg, [L["location_type"]], job["location_type"]
        )
    if job.get("location") and job["location"] != "Remote":
        loc_field = await _any_field(page, "Location", "City or region")
        if loc_field is not None:
            filled["location"] = await _type_and_pick(
                page, loc_field, job["location"].split(",")[0]
            )
    desc = await _any_field(page, "Highlights", "Projects, problems you solved")
    if desc is None:
        desc = await _any_field(page, "Description")
    if desc is not None and job.get("description"):
        with contextlib.suppress(Exception):
            await _set_text(page, desc, job["description"])
            filled["description"] = True
    filled.setdefault("description", False)
    # Left ticked, LinkedIn rewrites the headline to "<title> at <company>".
    for getter in (
        lambda: page.get_by_label(re.compile(L["update_headline"], re.I)),
        lambda: page.get_by_role("switch", name=re.compile(r"headline", re.I)),
        lambda: page.get_by_role("checkbox", name=re.compile(r"headline", re.I)),
    ):
        box = getter()
        if await box.count() == 0:
            continue
        with contextlib.suppress(Exception):
            await box.first.scroll_into_view_if_needed()
            if await box.first.is_checked():
                await box.first.uncheck()
                filled["headline_kept"] = True
        break
    saved = await _save(dlg, page)
    if not saved:
        with contextlib.suppress(Exception):
            filled["blocked_by"] = (await dlg.inner_text())[:400]
    shot = await screenshot(page, f"exp_{job['company'].replace(' ', '_')}")
    return {
        "status": "ok" if saved else "maybe",
        "company": job["company"],
        "filled": filled,
        "screenshot": shot,
        "url": page.url,
    }


async def add_education(page: Page, edu: dict[str, Any], skip_if_present: bool = True) -> dict[str, Any]:
    err = await _open_profile(page)
    if err:
        return err
    mark = edu.get("degree") or ""
    if skip_if_present and mark:
        existing = await _section_text(page, "education")
        if mark.split("'")[0] in existing:
            return {"status": "skipped", "reason": f"already on profile: {mark}"}
    await _open_profile(page)
    opened = await _open_editor(page, L["add_education"])
    if not opened:
        if await _click_named(page, L["add_section"]):
            await page.wait_for_timeout(1200)
            opened = await _click_named(page, L["add_education"])
    if not opened:
        shot = await screenshot(page, "edu_no_edit")
        return {"error": "Could not open education editor.", "screenshot": shot}
    await page.wait_for_timeout(1500)
    dlg = await _modal(page)
    filled: dict[str, Any] = {}

    school = await _any_field(page, "School", "Example: Boston University")
    filled["school"] = (
        await _type_and_pick(
            page, school, edu["school"], str(edu.get("school_option") or "")
        )
        if school is not None
        else False
    )
    if edu.get("degree"):
        degree = await _any_field(page, "Degree", "Example: Bachelor's")
        filled["degree"] = (
            await _type_and_pick(page, degree, edu["degree"])
            if degree is not None
            else False
        )
    if edu.get("field"):
        field = await _any_field(page, "Field of study", "Example: Business")
        filled["field"] = (
            await _type_and_pick(
                page, field, edu["field"], str(edu.get("field_option") or "")
            )
            if field is not None
            else False
        )
    # Start year unknown — do not invent. Only set end if the form allows it.
    if edu.get("end_year"):
        filled["end"] = await _set_month_year(
            dlg, page, "end", edu.get("end_month"), edu.get("end_year")
        )
    if edu.get("description"):
        desc = await _any_field(page, "Description", "Projects, problems you solved")
        if desc is not None:
            with contextlib.suppress(Exception):
                await _set_text(page, desc, edu["description"])
                filled["description"] = True
        filled.setdefault("description", False)
    saved = await _save(dlg, page)
    if not saved:
        with contextlib.suppress(Exception):
            filled["blocked_by"] = (await dlg.inner_text())[:300]
    shot = await screenshot(page, f"edu_{edu.get('degree', 'x').split()[0]}")
    return {
        "status": "ok" if saved else "maybe",
        "degree": edu.get("degree"),
        "filled": filled,
        "screenshot": shot,
        "url": page.url,
    }


async def _open_skill_editor(page: Page) -> bool:
    if "add-another-skill" in (page.url or "") or "skill" in (page.url or "").lower():
        if await _any_field(page, "Skill", "Skill (ex: Project Management)") is not None:
            return True
    await _open_profile(page)
    await page.wait_for_timeout(800)
    if await _click_named(page, L["add_skill"]):
        await page.wait_for_timeout(1800)
        return True
    if await _click_named(page, L["add_section"]):
        await page.wait_for_timeout(1200)
        if await _click_named(page, L["add_skill"]):
            await page.wait_for_timeout(1800)
            return True
    return False


async def add_skills(page: Page, skills: list[str]) -> dict[str, Any]:
    err = await _open_profile(page)
    if err:
        return err
    existing = (await _section_text(page, "skills")).lower()
    results = []
    opened = False
    for skill in skills:
        if skill.lower() in existing:
            results.append({"skill": skill, "status": "skipped"})
            continue
        if not opened:
            opened = await _open_skill_editor(page)
            if not opened:
                results.append({"skill": skill, "status": "error", "reason": "no editor"})
                break
        field = await _any_field(page, "Skill", "Skill (ex: Project Management)")
        if field is None:
            opened = await _open_skill_editor(page)
            field = await _any_field(page, "Skill", "Skill (ex: Project Management)")
        ok = await _type_and_pick(page, field, skill) if field is not None else False
        dlg = await _modal(page)
        saved = await _save(dlg, page)
        if not saved:
            saved = await _click_named(page, r"^(Add skill|Add|Done|Save)$")
        await page.wait_for_timeout(1500)
        # LinkedIn often stays on "add another skill" — keep filling there.
        opened = await _any_field(page, "Skill", "Skill (ex: Project Management)") is not None
        results.append({"skill": skill, "status": "ok" if ok and saved else "maybe"})
    shot = await screenshot(page, "skills")
    return {"results": results, "screenshot": shot, "url": page.url}


async def add_language(page: Page, name: str, proficiency: str) -> dict[str, Any]:
    err = await _open_profile(page)
    if err:
        return err
    existing = await _section_text(page, "languages")
    # Footer language picker contains "English (English)" — only skip a real card.
    if re.search(rf"(?m)^{re.escape(name)}\s*$", existing) and "nothing to see" not in existing.lower():
        return {"status": "skipped", "language": name}
    await _open_profile(page)
    await page.wait_for_timeout(800)
    opened = await _click_named(page, L["add_language"])
    if not opened and await _click_named(page, L["add_section"]):
        await page.wait_for_timeout(1200)
        # Languages sit under Additional in the new Add section menu.
        await _click_named(page, r"(Additional|More)")
        await page.wait_for_timeout(600)
        opened = await _click_named(page, L["add_language"])
    if not opened:
        shot = await screenshot(page, f"lang_{name}_no_edit")
        return {"error": "Could not open language editor.", "language": name, "screenshot": shot}
    await page.wait_for_timeout(1800)
    dlg = await _modal(page)
    field = await _any_field(page, "Language", "Ex: English")
    filled = {
        "name": await _type_and_pick(page, field, name) if field is not None else False,
        "proficiency": await _pick_dropdown(page, r"Proficiency", proficiency)
        or await _select_combo(dlg, [L["proficiency"]], proficiency),
    }
    saved = await _save(dlg, page)
    shot = await screenshot(page, f"lang_{name}")
    return {
        "status": "ok" if saved else "maybe",
        "language": name,
        "filled": filled,
        "screenshot": shot,
        "url": page.url,
    }


async def set_custom_url(page: Page, candidates: list[str]) -> dict[str, Any]:
    err = await _open_profile(page)
    if err:
        return err
    await goto(page, f"{BASE_URL}/public-profile/settings/")
    await dismiss_noise(page)
    if not await is_logged_in(page):
        return _need_login(page)
    await page.wait_for_timeout(800)
    await _click_named(
        page, r"(Edit public profile|Edit URL|Редагувати загальнодоступний)", r"^(Edit|Редагувати)$"
    )
    await page.wait_for_timeout(500)
    box = page.locator(
        'input[id*="custom"], input[name*="vanity"], input[id*="vanity"], '
        'input[aria-label*="URL" i]'
    )
    if await box.count() == 0:
        box = page.get_by_label(re.compile(r"URL|vanity|public profile", re.I))
    if await box.count() == 0:
        shot = await screenshot(page, "url_no_input")
        return {
            "error": "Custom URL field not found — set it by hand.",
            "screenshot": shot,
            "url": page.url,
        }
    last_err = ""
    for slug in candidates:
        await box.first.click()
        await box.first.fill("")
        await box.first.fill(slug)
        saved = await _click_named(page, L["save"])
        await page.wait_for_timeout(1200)
        body = (await page.inner_text("body")).lower()
        if "unavailable" in body or "недоступн" in body:
            last_err = f"{slug} taken"
            continue
        if saved or slug in page.url:
            return {"status": "ok", "slug": slug, "url": page.url}
        last_err = slug
    shot = await screenshot(page, "url")
    return {"status": "maybe", "tried": candidates, "note": last_err, "screenshot": shot}


async def _otw_form_open(page: Page) -> bool:
    if await page.get_by_text(re.compile(r"Select your job preferences", re.I)).count() > 0:
        return True
    if await page.get_by_role("button", name=re.compile(r"Save & continue", re.I)).count() > 0:
        return True
    return await _modal_open(page)


async def set_open_to_work(page: Page, titles: list[str], remote: bool = True) -> dict[str, Any]:
    err = await _open_profile(page)
    if err:
        return err
    await page.wait_for_timeout(800)
    if not await _otw_form_open(page):
        await _click_named(page, r"(^Open to$|Open to work|Відкрити для)")
        await page.wait_for_timeout(1200)
        await _click_named(
            page, r"(Finding a new job|Add Open to work|Looking for a new job|Job preferences)"
        )
        await page.wait_for_timeout(1800)
    if not await _otw_form_open(page):
        shot = await screenshot(page, "otw_no_edit")
        return {
            "error": "Open to work control not found — turn it on by hand.",
            "screenshot": shot,
        }
    added = []
    for title in titles:
        if await page.get_by_text(re.compile(rf"^{re.escape(title)}$", re.I)).count() > 0:
            added.append(title)
            continue
        await _click_named(page, r"(\+ Add title|Add title)")
        await page.wait_for_timeout(500)
        field = await _any_field(page, "", "Add title")
        if field is None:
            field = await _any_field(page, "Job title")
        if field is None:
            cand = page.locator(
                'input[placeholder*="title" i], input[aria-label*="title" i], '
                'input[role="combobox"]'
            )
            field = None
            for i in range(await cand.count()):
                el = cand.nth(i)
                with contextlib.suppress(Exception):
                    if await el.is_visible():
                        field = el
                        break
        if field is None:
            continue
        if await _type_and_pick(page, field, title):
            added.append(title)
            await page.wait_for_timeout(400)
    if remote:
        remote_chip = page.get_by_text(re.compile(r"^Remote$", re.I))
        if await remote_chip.count() > 0:
            await _safe_click(remote_chip.first)
    # Drop leftover title chips that are not in the requested list.
    wanted = {t.strip().lower() for t in titles if t.strip()}
    remove_btns = page.get_by_role("button", name=re.compile(r"^Remove ", re.I))
    to_drop: list[Locator] = []
    for i in range(await remove_btns.count()):
        btn = remove_btns.nth(i)
        label = ""
        with contextlib.suppress(Exception):
            label = (await btn.get_attribute("aria-label")) or (await btn.inner_text())
        leftover = re.sub(r"^Remove\s+", "", label or "", flags=re.I).strip()
        if leftover and leftover.lower() not in wanted:
            to_drop.append(btn)
    for btn in to_drop:
        with contextlib.suppress(Exception):
            await btn.click()
            await page.wait_for_timeout(200)
    saved = await _click_named(
        page, r"^(Save & continue|Save|Done|Add)$"
    )
    await page.wait_for_timeout(2000)
    # Second pane: who can see this.
    await _click_named(page, r"(Recruiters only|All LinkedIn members|Тільки рекрутер)")
    await _click_named(page, r"^(Save & continue|Save|Done|Add)$")
    await page.wait_for_timeout(1500)
    shot = await screenshot(page, "open_to_work")
    return {
        "status": "ok" if saved else "maybe",
        "titles": added,
        "screenshot": shot,
        "url": page.url,
    }


async def apply_pack(
    page: Page,
    pack: Optional[dict[str, Any]] = None,
    step_timeout: int = 120,
    progress_path: Optional[Path] = None,
) -> dict[str, Any]:
    pack = pack or load_pack()
    steps: list[dict[str, Any]] = []
    progress = progress_path or (HERE / "debug" / "progress.json")

    def flush() -> None:
        with contextlib.suppress(Exception):
            progress.parent.mkdir(parents=True, exist_ok=True)
            progress.write_text(
                json.dumps(steps, ensure_ascii=False, indent=2), encoding="utf-8"
            )

    async def run(name: str, coro) -> None:
        try:
            result = await asyncio.wait_for(coro, timeout=step_timeout)
            steps.append({"step": name, **result})
        except asyncio.TimeoutError:
            steps.append({"step": name, "error": f"timeout after {step_timeout}s"})
        except Exception as exc:
            steps.append({"step": name, "error": str(exc)})
        flush()

    await run("language", ensure_english_ui(page))
    await run("intro", update_intro(page, pack))
    if pack.get("about"):
        await run("about", update_about(page, pack["about"]))
    for job in pack.get("experience") or []:
        await run(f"experience:{job.get('company') or job.get('title')}", add_experience(page, job))
    for edu in pack.get("education") or []:
        await run(f"education:{edu.get('degree')}", add_education(page, edu))
    if pack.get("skills"):
        await run("skills", add_skills(page, pack.get("skills") or []))
    for lang in pack.get("languages") or []:
        await run(
            f"language:{lang['name']}",
            add_language(page, lang["name"], lang["proficiency"]),
        )
    otw = pack.get("open_to_work") or {}
    if otw.get("titles"):
        await run(
            "open_to_work",
            set_open_to_work(page, otw.get("titles") or [], bool(otw.get("remote", True))),
        )
    if pack.get("custom_url_candidates"):
        await run(
            "custom_url",
            set_custom_url(page, pack.get("custom_url_candidates") or []),
        )
    final: dict[str, Any] = {}
    with contextlib.suppress(Exception):
        final = await asyncio.wait_for(snapshot(page), timeout=step_timeout)
    summary = {
        "ok": [s["step"] for s in steps if s.get("status") in ("ok", "already_english")],
        "check": [s["step"] for s in steps if s.get("status") == "maybe"],
        "failed": [s["step"] for s in steps if s.get("error")],
    }
    return {
        "summary": summary,
        "steps": steps,
        "profile_url": final.get("url"),
        "headline": final.get("headline"),
    }
