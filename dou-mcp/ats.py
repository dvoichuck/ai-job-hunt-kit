"""Fill and (optionally) submit external ATS forms opened from DOU.

Facts only from profile_content / experience.md. Unknown screening questions
are never invented — the run stops with needs_review.
"""

from __future__ import annotations

import contextlib
import logging
import re
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from playwright.async_api import Page

import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import candidate as C
import profile_content as PC

log = logging.getLogger(__name__)

HERE = Path(__file__).resolve().parent
CV_PATH = C.CV_PATH if C.CV_PATH.exists() else HERE.parent / "local" / "cv.pdf"

HOST_FAMILY = (
    (re.compile(r"peopleforce\.io$", re.I), "peopleforce"),
    (re.compile(r"teamtailor\.com$", re.I), "teamtailor"),
    (re.compile(r"talentlyft\.com$", re.I), "talentlyft"),
    (re.compile(r"greenhouse\.io$|boards\.greenhouse", re.I), "greenhouse"),
    (re.compile(r"jobs\.lever\.co$|lever\.co$", re.I), "lever"),
    (re.compile(r"apply\.workable\.com$|workable\.com$", re.I), "workable"),
    (re.compile(r"recruitee\.com$", re.I), "recruitee"),
    (re.compile(r"smartrecruiters\.com$", re.I), "smartrecruiters"),
    (re.compile(r"ashbyhq\.com$", re.I), "ashby"),
)

APPLY_CTA = re.compile(
    r"apply for this job|apply now|apply|відгукнутися|подати заявку|"
    r"submit application|start application",
    re.I,
)
COOKIE_BTN = re.compile(
    r"^(accept all|accept|прийняти|принять|only required( cookies)?|got it)$",
    re.I,
)

# Label/placeholder → value. None means "do not fill / skip optional".
_SCREENING: list[tuple[re.Pattern[str], str | None]] = [
    (re.compile(r"salary|зарплат|компенсац|очікуван|compensation|rate\b|pay\b", re.I),
     f"${C.SALARY_NET} net/month" if C.SALARY_NET else None),
    (re.compile(r"when.*(start|available)|notice|коли.*(старт|можеш)|доступн", re.I),
     C.NOTICE),
    (re.compile(r"where.*(live|located)|локац|містц|country|країн|city|місто", re.I),
     C.LOCATION_LINE),
    (re.compile(r"english|англій", re.I), C.ENGLISH),
    (re.compile(r"\bfop\b|\bфоп\b|\bb2b\b|contractor|контракт|sole prop", re.I),
     f"Yes — {C.CONTRACT}."),
    (re.compile(r"telegram", re.I), PC.CONTACTS["telegram_handle"].lstrip("@")),
    (re.compile(r"linkedin", re.I), PC.CONTACTS["linkedin"]),
    (re.compile(r"phone|телефон", re.I), PC.CONTACTS["phone"]),
    (re.compile(
        r"(years?|досвід|experience).{0,48}(node\.?js|nestjs)|(node\.?js|nestjs).{0,48}(years?|досвід|experience)",
        re.I,
    ), C.years_for("node") or None),
    (re.compile(
        r"(years?|досвід|experience).{0,40}\breact\b|\breact\b.{0,40}(years?|досвід|experience)",
        re.I,
    ), C.years_for("react") or None),
    (re.compile(
        r"(years?|досвід|experience).{0,40}typescript|typescript.{0,40}(years?|досвід|experience)",
        re.I,
    ), C.years_for("typescript") or None),
    (re.compile(r"cover letter|супровід|motivation|why (are you|do you)|про себе|about you", re.I),
     "__COVER__"),
    (re.compile(
        r"github (url|profile|link|account)|portfolio url|personal (site|website)|website url",
        re.I,
    ), PC.CONTACTS["linkedin"]),
    (re.compile(r"python", re.I), "0 — scripting/LLM tooling only, not primary backend."),
]

_PRIVACY = re.compile(
    r"privacy|consent|згод|політик|terms|даних|data processing|confirm that i",
    re.I,
)
_MARKETING = re.compile(
    r"future|майбутн|newsletter|marketing|12 month|store my data|contact me about",
    re.I,
)
_CAPTCHA = (
    "iframe[src*='recaptcha'], iframe[src*='hcaptcha'], "
    ".g-recaptcha, [data-sitekey], .h-captcha"
)


def detect_family(url: str) -> str:
    host = urlparse(url).hostname or ""
    host = host.lower().lstrip("www.")
    for pat, name in HOST_FAMILY:
        if pat.search(host):
            return name
    return "generic"


def _candidate() -> dict[str, str]:
    first, _, last = PC.FULL_NAME.partition(" ")
    return {
        "full_name": PC.FULL_NAME,
        "first_name": first or PC.FULL_NAME,
        "last_name": last or PC.FULL_NAME,
        "email": PC.CONTACTS["email"],
        "phone": PC.CONTACTS["phone"],
        "telegram": PC.CONTACTS["telegram_handle"].lstrip("@"),
        "linkedin": PC.CONTACTS["linkedin"],
        "city": PC.CITY,
        "country": PC.COUNTRY,
        "location": PC.LOCATION_LINE,
        "salary": str(PC.SALARY_USD_NET),
    }


def _answer_question(label: str, cover_letter: str) -> tuple[str | None, bool]:
    """Return (value, known). known=False → do not invent."""
    blob = label.strip()
    if not blob:
        return None, True
    for pat, val in _SCREENING:
        if pat.search(blob):
            if val is None:
                return None, True
            if val == "__COVER__":
                return cover_letter, True
            return val, True
    if _PRIVACY.search(blob):
        return None, True
    return None, False


async def _dismiss_noise(page: Page) -> None:
    for _ in range(3):
        loc = page.get_by_role("button", name=COOKIE_BTN)
        if await loc.count() == 0:
            break
        with contextlib.suppress(Exception):
            await loc.first.click(timeout=1500)
            await page.wait_for_timeout(400)


async def _click_apply_cta(page: Page) -> bool:
    for role in ("button", "link"):
        loc = page.get_by_role(role, name=APPLY_CTA)
        if await loc.count() == 0:
            continue
        n = await loc.count()
        for i in range(n - 1, -1, -1):
            with contextlib.suppress(Exception):
                el = loc.nth(i)
                if await el.is_visible():
                    await el.click(timeout=3000)
                    await page.wait_for_timeout(1200)
                    return True
    text = page.get_by_text(APPLY_CTA)
    if await text.count():
        with contextlib.suppress(Exception):
            await text.last.click(timeout=3000)
            await page.wait_for_timeout(1200)
            return True
    return False


async def _visible_fields(page: Page) -> list[dict[str, Any]]:
    return await page.evaluate(
        """
        () => {
          const vis = el => {
            const s = getComputedStyle(el);
            const r = el.getBoundingClientRect();
            return s.display !== 'none' && s.visibility !== 'hidden'
              && r.width + r.height > 0 && el.type !== 'hidden';
          };
          const labelOf = el => {
            if (el.getAttribute('aria-label')) return el.getAttribute('aria-label');
            if (el.id) {
              const l = document.querySelector('label[for="'+CSS.escape(el.id)+'"]');
              if (l) return (l.innerText || '').trim();
            }
            const wrap = el.closest('label, .form-group, .field, .form-item, li, p');
            let raw = wrap ? (wrap.innerText || '') : '';
            raw = raw.split('\\n').map(s => s.trim()).filter(Boolean)[0] || raw;
            return (raw || el.placeholder || el.name || '').trim().slice(0, 200);
          };
          return [...document.querySelectorAll('input, textarea, select')]
            .filter(vis)
            .map((e, i) => ({
              i,
              tag: e.tagName,
              type: e.type || '',
              name: e.name || '',
              id: e.id || '',
              ph: e.placeholder || '',
              req: !!e.required,
              label: labelOf(e),
            }));
        }
        """
    )


def _identity_value(field: dict[str, Any], cand: dict[str, str], cover: str) -> str | None:
    name = f"{field.get('name','')} {field.get('id','')} {field.get('ph','')} {field.get('label','')}"
    typ = (field.get("type") or "").lower()
    if typ in ("email",) or re.search(r"e-?mail|пошт", name, re.I):
        return cand["email"]
    if typ in ("tel",) or re.search(r"phone|телефон", name, re.I):
        return cand["phone"]
    if re.search(r"first.?name|ім.?я(?! користувача)|ім'я(?! користувача)", name, re.I) and not re.search(r"last|прізв|full", name, re.I):
        return cand["first_name"]
    if re.search(r"last.?name|прізв|surname|family name", name, re.I):
        return cand["last_name"]
    if re.search(r"full.?name|повне ім|candidate\[name\]|your name", name, re.I):
        return cand["full_name"]
    if re.search(r"telegram", name, re.I):
        return cand["telegram"]
    if re.search(r"linkedin", name, re.I):
        return cand["linkedin"]
    if re.search(r"city|місто", name, re.I) and field.get("tag") != "SELECT":
        return cand["city"]
    if re.search(r"country|країн", name, re.I) and field.get("tag") != "SELECT":
        return cand["country"]
    if field.get("tag") == "TEXTAREA" and re.search(
        r"cover|супровід|motivation|message|comment|про себе", name, re.I
    ):
        return cover
    val, known = _answer_question(name, cover)
    if known:
        return val
    return None


async def _fill_text(page: Page, field: dict[str, Any], value: str) -> bool:
    try:
        loc = _locator_for(page, field)
        if await loc.count() == 0:
            return False
    except Exception:
        return False
    el = loc.first
    typ = (field.get("type") or "").lower()
    tag = field.get("tag") or ""
    try:
        if tag == "SELECT":
            with contextlib.suppress(Exception):
                await el.select_option(label=value)
                return True
            with contextlib.suppress(Exception):
                await el.select_option(value=value)
                return True
            return False
        if typ in ("checkbox", "radio"):
            return False
        await el.click()
        await el.fill(value)
        return True
    except Exception as exc:
        log.info("fill failed %s: %s", field.get("name"), exc)
        return False


def _css_attr(name: str, value: str) -> str:
    esc = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'[{name}="{esc}"]'


def _locator_for(page: Page, field: dict[str, Any]):
    eid = field.get("id") or ""
    if eid:
        if re.search(r"[\[\]\.#: ]", eid):
            return page.locator(_css_attr("id", eid))
        return page.locator(f"#{eid}")
    name = field.get("name") or ""
    if name:
        return page.locator(_css_attr("name", name))
    typ = field.get("type") or ""
    if typ:
        return page.locator(f"input[type='{typ}']")
    return page.locator("textarea").nth(field.get("i") or 0)


async def _upload_cv(page: Page) -> bool:
    if not CV_PATH.exists():
        log.warning("CV missing: %s", CV_PATH)
        return False
    files = page.locator("input[type=file]")
    n = await files.count()
    if n == 0:
        # Some ATS hide the input behind "Choose files" / "Upload CV".
        for name in ("Choose files", "Upload CV", "Attach", "Резюме", "CV"):
            btn = page.get_by_text(name, exact=False)
            if await btn.count():
                with contextlib.suppress(Exception):
                    await btn.first.click()
                    await page.wait_for_timeout(400)
        files = page.locator("input[type=file]")
        n = await files.count()
    uploaded = False
    for i in range(n):
        el = files.nth(i)
        accept = (await el.get_attribute("accept")) or ""
        name = ((await el.get_attribute("name")) or "").lower()
        # Skip "additional files" / photo.
        if re.search(r"photo|avatar|additional|other", name + accept, re.I):
            continue
        with contextlib.suppress(Exception):
            await el.set_input_files(str(CV_PATH))
            uploaded = True
            break
    return uploaded


async def _check_required_privacy(page: Page, field: dict[str, Any]) -> None:
    label = field.get("label") or ""
    if not _PRIVACY.search(label):
        return
    if _MARKETING.search(label) and not field.get("req"):
        return
    loc = _locator_for(page, field)
    if await loc.count():
        with contextlib.suppress(Exception):
            await loc.first.check()


async def _has_captcha(page: Page) -> bool:
    return await page.locator(_CAPTCHA).count() > 0


async def _submit(page: Page) -> bool:
    for name in (
        "Submit application",
        "Submit your application",
        "Submit",
        "Send application",
        "Apply",
        "Застосувати",
        "Надіслати",
        "Відправити",
        "Подати заявку",
    ):
        btn = page.get_by_role("button", name=re.compile(rf"^{re.escape(name)}$", re.I))
        if await btn.count() == 0:
            btn = page.locator(f"input[type=submit][value='{name}']")
        if await btn.count() == 0:
            continue
        with contextlib.suppress(Exception):
            await btn.first.click(timeout=4000)
            await page.wait_for_timeout(2000)
            return True
    return False


async def _looks_success(page: Page) -> bool:
    t = (await page.inner_text("body")).lower()
    return bool(
        re.search(
            r"thank you|thanks for (your )?appl|application (has been )?submitted|"
            r"дякуємо|відгук надіслано|заявк[уи] (надіслано|отримано)|successfully",
            t,
        )
    )


async def apply_external(
    page: Page,
    *,
    cover_letter: str,
    confirm: bool,
    job_key: str = "",
) -> dict[str, Any]:
    """Fill the current ATS page. Submit only when confirm=True and safe."""
    await _dismiss_noise(page)
    family = detect_family(page.url)
    form_url = re.search(
        r"/a/new|/apply\b|/application|/jobs/.+/new|candidate|job_applications",
        page.url,
        re.I,
    )
    fields = await _visible_fields(page)
    text_fields = [f for f in fields if (f.get("type") or "") not in ("file", "hidden", "checkbox")]
    if family == "talentlyft" and "/new" not in page.url:
        loc = page.get_by_text("Apply for this job", exact=False)
        if await loc.count():
            with contextlib.suppress(Exception):
                await loc.last.click(timeout=4000)
                await page.wait_for_timeout(1500)
    elif not form_url or len(text_fields) < 2:
        await _click_apply_cta(page)
        await _dismiss_noise(page)
    fields = await _visible_fields(page)

    family = detect_family(page.url) or family
    cand = _candidate()
    filled: list[str] = []
    unknown: list[str] = []
    uploaded = await _upload_cv(page)
    if uploaded:
        filled.append("cv")

    # Teamtailor location: prefer Ukraine (remote-friendly office tag), never Cyprus-only.
    if family == "teamtailor":
        ua = page.locator("text=Ukraine Development Center")
        if await ua.count():
            with contextlib.suppress(Exception):
                box = page.locator("input[name='candidate[location_ids][]']").first
                await box.check()
                filled.append("location:ukraine")

    for f in fields:
        typ = (f.get("type") or "").lower()
        if typ in ("hidden", "submit", "button"):
            continue
        if typ == "file":
            continue
        if typ in ("checkbox", "radio"):
            if _PRIVACY.search(f.get("label") or ""):
                await _check_required_privacy(page, f)
                filled.append(f"consent:{f.get('name')}")
            continue
        value = _identity_value(f, cand, cover_letter)
        label = (f.get("label") or f.get("name") or "")[:120]
        if value:
            ok = await _fill_text(page, f, value)
            if ok:
                filled.append(label[:60])
            continue
        _, known = _answer_question(label, cover_letter)
        looks_question = bool(
            re.search(
                r"\?|years|досвід|experience|how (many|long|would)|describe|"
                r"answers\[\d+\]",
                f"{label} {f.get('name')}",
                re.I,
            )
        )
        if not known and looks_question:
            unknown.append(label)

    captcha = await _has_captcha(page)
    result: dict[str, Any] = {
        "channel": "external_ats",
        "ats": family,
        "url": page.url,
        "filled": filled,
        "unknown_questions": unknown,
        "cv_uploaded": uploaded,
        "captcha": captcha,
        "job_key": job_key,
    }

    if unknown:
        result["status"] = "needs_review"
        result["reason"] = "unknown screening questions — not inventing answers"
        return result
    if not uploaded and any(f.get("type") == "file" and f.get("req") for f in fields):
        result["status"] = "error"
        result["reason"] = "required CV upload failed"
        return result
    if captcha:
        result["status"] = "needs_captcha"
        result["reason"] = "ATS shows captcha — fill is done, submit needs a human"
        if not confirm:
            result["status"] = "dry_run"
        return result
    if not confirm:
        result["status"] = "dry_run"
        result["message"] = "ATS form filled but NOT submitted (confirm=False)."
        return result

    clicked = await _submit(page)
    result["clicked_submit"] = clicked
    if await _looks_success(page) or clicked:
        result["status"] = "submitted"
    else:
        result["status"] = "unknown"
        result["reason"] = "submit clicked but success text not found"
    return result
