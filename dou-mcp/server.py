"""
DOU MCP server (jobs.dou.ua + dou.ua candidate profile).

Exposes DOU candidate actions as MCP tools driven by Playwright:
  - login            : open a real browser once, log in manually; session reused.
  - search_jobs      : search vacancies by category / keywords / remote / exp.
  - get_job          : full text of a vacancy + whether you already applied.
  - apply            : send an application (DOU form or external ATS).
  - get_profile      : read your DOU user/candidate profile (form + visible text).
  - update_profile   : fill and save profile fields (incl. «Шукаю роботу»).
  - list_inbox       : list recruiter conversations.
  - read_thread      : read one conversation.
  - send_message     : reply in a conversation.
  - debug_screenshot / debug_dump_html

DOU has no public candidate API, so this uses browser automation with YOUR
logged-in session (persistent Chromium profile). Selectors are best-effort —
use the debug_* tools if markup changes.

This tool acts on your behalf. Use it responsibly and within DOU's Terms of
Service — avoid mass/spam applications.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Optional
from urllib.parse import quote, urlencode, urljoin, urlparse

from mcp.server.fastmcp import FastMCP
from playwright.async_api import (
    BrowserContext,
    Page,
    TimeoutError as PWTimeout,
    async_playwright,
)

# Cover letters live next to the Djinni MCP (same facts, no invention).
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "djinni-mcp"))
from cover_letters import (  # noqa: E402
    build_letter,
    clean_title,
    detect_variant,
    extract_company_from_job_text,
)

import applied as applied_store  # noqa: E402
import ats  # noqa: E402
import profile_content as PC  # noqa: E402

# --------------------------------------------------------------------------- #
# Config
# --------------------------------------------------------------------------- #
JOBS_URL = "https://jobs.dou.ua"
SITE_URL = "https://dou.ua"

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


PROFILE_DIR = _resolve(_env("DOU_PROFILE_DIR", "./browser_profile"))
DEBUG_DIR = _resolve(_env("DOU_DEBUG_DIR", "./debug"))
HEADLESS = _env("DOU_HEADLESS", "1") not in ("0", "false", "False", "")
SLOWMO = int(_env("DOU_SLOWMO", "0") or "0")
TIMEOUT = int(_env("DOU_TIMEOUT", "30000") or "30000")
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

SELECTORS = {
    "login_link": "#login-link",
    "logged_in": (
        "a[href*='/logout'], a.logout, .min-profile a[href*='/users/'], "
        "a[href*='/users/'][href$='/']"
    ),
    "apply_open": "#reply-btn-id, .reply a:not(.replied-external):not(.rules-link)",
    "apply_external": "a.replied-external",
    "apply_textarea": (
        "#reply_descr, form#replied-id textarea, textarea[name='descr'], "
        ".reply textarea, textarea[name='body'], textarea[name='message']"
    ),
    "apply_cv": "#reply_file, input[name='user_cv']",
    "apply_submit": "form#replied-id button.replied-btn.send, .replied-btn.send",
    "inbox_contacts": ".contact[data-username]",
    "inbox_textarea": "#id_message, textarea[name='message']",
    "inbox_submit": "#add_message",
    "more_jobs": ".more-btn a, a.dui-button",
    "vacancy_card": "li.l-vacancy",
    "vacancy_title": "a.vt",
}

CATEGORIES = {
    "Node.js",
    "Front End",
    "Python",
    "Java",
    "PHP",
    "QA",
    "DevOps",
    "AI/ML",
    "React Native",
    "Design",
    "Other",
}
EXP_LEVELS = {"0-1", "1-3", "3-5", "5plus"}

ALREADY_APPLIED_RE = re.compile(
    r"ви вже відгукн\w* на цю|already applied to this|"
    r"відгук надіслано|відгук відправлено|дякуємо за відгук",
    re.I,
)

mcp = FastMCP("dou")
_LOCK = asyncio.Lock()


# --------------------------------------------------------------------------- #
# Browser plumbing
# --------------------------------------------------------------------------- #
@contextlib.asynccontextmanager
async def browser(headless: Optional[bool] = None):
    """Open the persistent-profile Chromium context, yield a ready page, clean up."""
    PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    use_headless = HEADLESS if headless is None else headless
    async with async_playwright() as pw:
        ctx: BrowserContext = await pw.chromium.launch_persistent_context(
            user_data_dir=str(PROFILE_DIR),
            headless=use_headless,
            slow_mo=SLOWMO,
            user_agent=USER_AGENT,
            viewport={"width": 1366, "height": 900},
            locale="uk-UA",
            args=["--no-sandbox", "--disable-blink-features=AutomationControlled"],
        )
        ctx.set_default_timeout(TIMEOUT)
        page = ctx.pages[0] if ctx.pages else await ctx.new_page()
        try:
            yield ctx, page
        finally:
            with contextlib.suppress(Exception):
                await ctx.close()


async def _goto(page: Page, url: str) -> None:
    await page.goto(url, wait_until="domcontentloaded")
    with contextlib.suppress(PWTimeout):
        await page.wait_for_load_state("networkidle", timeout=8000)


async def _is_logged_in(page: Page) -> bool:
    """Logged in iff the header 'Вхід і реєстрація' link is gone."""
    try:
        if await page.locator(SELECTORS["login_link"]).count() > 0:
            return False
        return await page.evaluate(
            """
            () => {
              const t = (document.body && document.body.innerText) || '';
              return /вийти|logout|редагувати профіль|особист/i.test(t)
                || !!document.querySelector("a[href*='/logout']");
            }
            """
        )
    except Exception:
        return False


def _abs_jobs(url: str) -> str:
    return url if url.startswith("http") else urljoin(JOBS_URL, url)


def _abs_site(url: str) -> str:
    return url if url.startswith("http") else urljoin(SITE_URL, url)


_JOB_PATH_RE = re.compile(
    r"/companies/([^/]+)/vacancies/(\d+)/?", re.I
)


def _job_url(job: str) -> str:
    """Accept a full URL, /companies/<slug>/vacancies/<id>/, or slug/id."""
    job = str(job).strip()
    if job.startswith("http"):
        return job.split("?")[0].rstrip("/") + "/"
    if job.startswith("/"):
        return urljoin(JOBS_URL, job)
    m = _JOB_PATH_RE.search(job)
    if m:
        return f"{JOBS_URL}/companies/{m.group(1)}/vacancies/{m.group(2)}/"
    # slug/id or slug:id
    m = re.match(r"^([a-z0-9._-]+)[/:](\d+)$", job, re.I)
    if m:
        return f"{JOBS_URL}/companies/{m.group(1)}/vacancies/{m.group(2)}/"
    raise ValueError(
        "DOU vacancies need a company slug + id. Pass a full URL "
        "(https://jobs.dou.ua/companies/<slug>/vacancies/<id>/) "
        "or '<slug>/<id>'. Numeric id alone 404s."
    )


_INBOX_PATH_RE = re.compile(r"/inbox/([^/?#]+)/?", re.I)


def _thread_slug(thread: str) -> str:
    raw = str(thread).strip()
    if raw.startswith("http") or raw.startswith("/"):
        m = _INBOX_PATH_RE.search(raw)
        if m:
            return m.group(1)
    return raw.strip("/").split("/")[-1]


def _thread_url(thread: str) -> str:
    slug = _thread_slug(thread)
    if not slug:
        raise ValueError("empty DOU inbox thread")
    return f"{SITE_URL}/inbox/{slug}/"


def _parse_job_ref(url: str) -> dict[str, str]:
    m = _JOB_PATH_RE.search(url or "")
    if not m:
        return {"company_slug": "", "id": ""}
    return {"company_slug": m.group(1), "id": m.group(2)}


async def _screenshot(page: Page, label: str) -> str:
    DEBUG_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = DEBUG_DIR / f"{ts}_{label}.png"
    with contextlib.suppress(Exception):
        await page.screenshot(path=str(path), full_page=True)
    return str(path)


EXTRACT_CARDS_JS = """
() => {
  const out = [];
  const seen = new Set();
  for (const li of document.querySelectorAll('li.l-vacancy')) {
    const a = li.querySelector('a.vt');
    if (!a) continue;
    const href = a.getAttribute('href') || a.href || '';
    const m = href.match(/\\/companies\\/([^/]+)\\/vacancies\\/(\\d+)/);
    if (!m) continue;
    const id = m[2];
    if (seen.has(id)) continue;
    seen.add(id);
    const companyA = li.querySelector('a.company');
    const salary = (li.querySelector('.salary') || {}).innerText || '';
    const cities = (li.querySelector('.cities') || {}).innerText || '';
    const date = (li.querySelector('.date') || {}).innerText || '';
    const snip = (li.querySelector('.sh-info') || {}).innerText || '';
    out.push({
      id,
      company_slug: m[1],
      title: (a.innerText || '').trim(),
      company: (companyA && companyA.innerText || '').trim(),
      salary: salary.trim(),
      location: cities.trim(),
      date: date.trim(),
      snippet: snip.trim().slice(0, 400),
      url: a.href,
    });
  }
  return out;
}
"""


EXTRACT_JOB_JS = """
() => {
  const q = s => document.querySelector(s);
  const h1 = q('h1');
  const companyA = q('.b-compinfo .l-n a, .b-compinfo a[href*="/companies/"]');
  const place = q('.sh-info .place, .l-vacancy .place');
  const body = q('.vacancy-section, .l-vacancy .b-typo, .l-vacancy');
  const text = ((body && body.innerText) || (q('.l-vacancy') || document.body).innerText || '')
    .trim();
  const t = (document.body.innerText || '');
                  const already = /ви вже відгукн\\w* на цю|already applied to this|відгук надіслано|відгук відправлено|дякуємо за відгук/i.test(t);
  const applyBtn = Array.from(document.querySelectorAll('a, button')).find(e =>
    /відгукнут/i.test(e.innerText || '') && !/правила/i.test(e.innerText || '')
  );
  const ext = document.querySelector('a.replied-external');
  return {
    title: (h1 && h1.innerText || '').trim(),
    company: (companyA && companyA.innerText || '').trim(),
    location: (place && place.innerText || '').trim(),
    already_applied: already,
    apply_available: (!!applyBtn || !!ext) && !already,
    external_apply: !!ext,
    external_url: ext ? (ext.href || '') : '',
    text: text.slice(0, 8000),
  };
}
"""


EXTRACT_FORM_JS = """
() => {
  const labelOf = (el) => {
    if (el.id) {
      const l = document.querySelector(`label[for="${CSS.escape(el.id)}"]`);
      if (l) return (l.innerText || '').trim();
    }
    const wrap = el.closest('label, .row, .field, .form-row, li, tr, .b-form div');
    if (wrap) {
      const l = wrap.querySelector('label');
      if (l) return (l.innerText || '').trim();
      const prev = wrap.querySelector('.input-label, .label, th, .name');
      if (prev) return (prev.innerText || '').trim();
    }
    return (el.getAttribute('placeholder') || el.getAttribute('aria-label') || el.name || '').trim();
  };
  const fields = [];
  const seen = new Set();
  for (const el of document.querySelectorAll('input, textarea, select')) {
    const name = el.name || el.id || '';
    if (!name) continue;
    if (el.type === 'hidden') continue;
    if (/csrf|token|password/i.test(name)) continue;
    const key = name + '|' + (el.type || el.tagName);
    if (seen.has(key)) continue;
    seen.add(key);
    let value = el.value;
    if (el.type === 'checkbox' || el.type === 'radio') {
      value = el.checked ? (el.value || 'on') : '';
    }
    let options = [];
    if (el.tagName === 'SELECT') {
      options = Array.from(el.options).slice(0, 30).map(o => ({
        value: o.value, text: (o.text || '').trim(), selected: o.selected,
      }));
      const o = el.options[el.selectedIndex];
      value = o ? o.value : el.value;
    }
    fields.push({
      name,
      id: el.id || '',
      tag: el.tagName.toLowerCase(),
      type: el.type || '',
      label: labelOf(el).slice(0, 120),
      value: typeof value === 'string' ? value.slice(0, 2000) : value,
      checked: !!(el.checked),
      options,
    });
  }
  const h1 = document.querySelector('h1');
  const looking = Array.from(document.querySelectorAll('input[type=checkbox]')).some(c => {
    const lab = labelOf(c) + ' ' + (c.name || '') + ' ' + (c.id || '');
    return /шукаю роботу|looking.?for.?job/i.test(lab);
  });
  return {
    url: location.href,
    heading: (h1 && h1.innerText || '').trim(),
    looking_for_job_checked: looking,
    visible_text: ((document.querySelector('.l-content, .page-profile, .b-user-info, article')
      || document.body).innerText || '').trim().slice(0, 4000),
    fields,
  };
}
"""


FILL_FIELDS_JS = """
(updates) => {
  const norm = s => (s || '').toLowerCase().replace(/\\s+/g, ' ').trim();
  const results = [];
  const all = Array.from(document.querySelectorAll('input, textarea, select'));
  const labelOf = (el) => {
    if (el.id) {
      const l = document.querySelector(`label[for="${CSS.escape(el.id)}"]`);
      if (l) return (l.innerText || '').trim();
    }
    const wrap = el.closest('label, .row, .field, .form-row, li, tr, .b-form div');
    if (wrap) {
      const l = wrap.querySelector('label, .input-label, .label');
      if (l) return (l.innerText || '').trim();
    }
    return (el.getAttribute('placeholder') || el.name || '').trim();
  };
  const setNative = (el, value) => {
    const proto = el.tagName === 'SELECT' ? HTMLSelectElement.prototype
      : (el.tagName === 'TEXTAREA' ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype);
    const desc = Object.getOwnPropertyDescriptor(proto, 'value');
    if (desc && desc.set) desc.set.call(el, value);
    else el.value = value;
    el.dispatchEvent(new Event('input', {bubbles: true}));
    el.dispatchEvent(new Event('change', {bubbles: true}));
  };
  for (const u of updates) {
    const match = norm(u.match);
    const candidates = all.filter(el => {
      if (el.type === 'hidden' || /csrf|password/i.test(el.name || '')) return false;
      const blob = norm([el.name, el.id, labelOf(el), el.getAttribute('placeholder')].join(' '));
      return blob.includes(match);
    });
    if (!candidates.length) {
      results.push({match: u.match, status: 'not_found'});
      continue;
    }
    const el = candidates[0];
    if (u.type === 'checkbox' || el.type === 'checkbox') {
      const want = !!u.value && String(u.value) !== '0' && String(u.value).toLowerCase() !== 'false';
      if (el.checked !== want) el.click();
      results.push({match: u.match, status: 'ok', via: 'checkbox', name: el.name});
      continue;
    }
    if (el.tagName === 'SELECT') {
      const want = String(u.value);
      const opts = Array.from(el.options);
      const hit = opts.find(o => o.value === want)
        || opts.find(o => norm(o.text) === norm(want))
        || opts.find(o => norm(o.text).includes(norm(want)) || norm(want).includes(norm(o.text)));
      if (!hit) {
        results.push({match: u.match, status: 'no_option', name: el.name,
                      options: opts.slice(0, 15).map(o => o.text.trim())});
        continue;
      }
      el.value = hit.value;
      el.dispatchEvent(new Event('change', {bubbles: true}));
      results.push({match: u.match, status: 'ok', via: 'select', name: el.name, value: hit.text});
      continue;
    }
    // CKEditor / contenteditable sibling
    const ed = document.querySelector('.ck-editor__editable, [contenteditable="true"]');
    if (ed && (el.tagName === 'TEXTAREA') && u.type === 'about') {
      if (ed.ckeditorInstance) {
        ed.ckeditorInstance.setData(String(u.value));
      } else {
        ed.innerHTML = String(u.value).replace(/\\n/g, '<br>');
        ed.dispatchEvent(new Event('input', {bubbles: true}));
      }
      setNative(el, String(u.value));
      results.push({match: u.match, status: 'ok', via: 'editor', name: el.name});
      continue;
    }
    setNative(el, String(u.value));
    results.push({match: u.match, status: 'ok', via: el.tagName.toLowerCase(), name: el.name});
  }
  return results;
}
"""


async def _own_profile_url(page: Page) -> str:
    """Best-effort URL of the logged-in user's public profile."""
    href = await page.evaluate(
        """
        () => {
          const header = document.querySelector('header, .b-head, .right-part');
          const scope = header || document;
          const as = Array.from(scope.querySelectorAll("a[href*='/users/']"));
          for (const a of as) {
            const href = a.getAttribute('href') || '';
            if (/\\/users\\/[^/]+\\/?$/.test(href) && !/\\/users\\/?$/.test(href)) {
              return a.href;
            }
          }
          const any = document.querySelector("a.min-profile, .min-profile a, .b-author a");
          return any ? any.href : '';
        }
        """
    )
    return href or ""


async def _open_profile_edit(page: Page) -> dict[str, Any]:
    """Navigate to the user's profile and click Редагувати if present."""
    await _goto(page, f"{SITE_URL}/")
    if not await _is_logged_in(page):
        return {"error": "Not logged in. Run `login` first.", "url": page.url}

    profile_url = await _own_profile_url(page)
    if not profile_url:
        await _goto(page, f"{SITE_URL}/users/me/")
        profile_url = page.url
    else:
        await _goto(page, profile_url)

    # Own profile usually shows «Редагувати» / settings.
    clicked = False
    for name in (
        "Редагувати профіль",
        "Редагувати",
        "Edit profile",
        "Edit",
        "Налаштування",
        "Settings",
    ):
        loc = page.get_by_role("link", name=re.compile(rf"^{re.escape(name)}$", re.I))
        if await loc.count() == 0:
            loc = page.get_by_text(re.compile(name, re.I))
        if await loc.count() > 0:
            with contextlib.suppress(Exception):
                await loc.first.click()
                await page.wait_for_timeout(800)
                with contextlib.suppress(PWTimeout):
                    await page.wait_for_load_state("networkidle", timeout=8000)
                clicked = True
                break

    return {"profile_url": profile_url, "edit_clicked": clicked, "url": page.url}


def _default_updates() -> list[dict[str, Any]]:
    """Label/name substrings → values from experience.md (via profile_content)."""
    return [
        {"match": "шукаю роботу", "value": True, "type": "checkbox"},
        {"match": "looking for job", "value": True, "type": "checkbox"},
        {"match": "ім'я", "value": PC.FULL_NAME},
        {"match": "прізвище", "value": PC.FULL_NAME.split()[-1] if PC.FULL_NAME else ""},
        {"match": "name", "value": PC.FULL_NAME},
        {"match": "посада", "value": PC.POSITION},
        {"match": "position", "value": PC.POSITION},
        {"match": "headline", "value": PC.POSITION},
        {"match": "спеціалізац", "value": (PC.SPECIALIZATIONS[0] if PC.SPECIALIZATIONS else "")},
        {"match": "про себе", "value": PC.ABOUT, "type": "about"},
        {"match": "опис", "value": PC.ABOUT, "type": "about"},
        {"match": "about", "value": PC.ABOUT, "type": "about"},
        {"match": "summary", "value": PC.ABOUT, "type": "about"},
        {"match": "досвід", "value": PC.EXPERIENCE_YEARS},
        {"match": "experience", "value": PC.EXPERIENCE_YEARS},
        {"match": "англійськ", "value": PC.ENGLISH_SHORT},
        {"match": "english", "value": PC.ENGLISH_SHORT},
        {"match": "місто", "value": PC.CITY},
        {"match": "city", "value": PC.CITY},
        {"match": "локац", "value": PC.LOCATION_LINE},
        {"match": "зарплат", "value": str(PC.SALARY_USD_NET)},
        {"match": "salary", "value": str(PC.SALARY_USD_NET)},
        {"match": "telegram", "value": PC.CONTACTS["telegram_handle"]},
        {"match": "linkedin", "value": PC.CONTACTS["linkedin"]},
        {"match": "email", "value": PC.CONTACTS["email"]},
        {"match": "пошта", "value": PC.CONTACTS["email"]},
        {"match": "телефон", "value": PC.CONTACTS["phone"]},
        {"match": "phone", "value": PC.CONTACTS["phone"]},
        {"match": "навички", "value": PC.SKILLS_TEXT},
        {"match": "skills", "value": PC.SKILLS_TEXT},
        {"match": "стек", "value": PC.SKILLS_TEXT},
        {"match": "технолог", "value": PC.SKILLS_TEXT},
    ]


# --------------------------------------------------------------------------- #
# Tools
# --------------------------------------------------------------------------- #
@mcp.tool()
async def login(wait_seconds: int = 180) -> str:
    """Open a visible browser window so you can log in to DOU once.

    The session is saved in the persistent profile and reused by all other tools.
    Works with email/password, Google, LinkedIn, GitHub, Facebook and captcha,
    because YOU do it.

    Args:
        wait_seconds: how long to keep the window open waiting for login to finish.
    """
    async with _LOCK:
        async with browser(headless=False) as (_ctx, page):
            await _goto(page, JOBS_URL + "/")
            if await page.locator(SELECTORS["login_link"]).count() > 0:
                with contextlib.suppress(Exception):
                    await page.locator(SELECTORS["login_link"]).first.click()
            deadline = asyncio.get_event_loop().time() + wait_seconds
            while asyncio.get_event_loop().time() < deadline:
                if await _is_logged_in(page):
                    return (
                        "Logged in successfully. Session saved to the persistent "
                        "profile — you can now use the other tools headlessly."
                    )
                await asyncio.sleep(2)
            if await _is_logged_in(page):
                return "Logged in successfully. Session saved."
            return (
                "Timed out waiting for login. If you did log in, it still may be "
                "saved — try `get_profile` or re-run `login` with a bigger "
                "wait_seconds."
            )


@mcp.tool()
async def session_status() -> dict[str, Any]:
    """Check whether the Playwright profile is logged in."""
    async with _LOCK:
        async with browser() as (_ctx, page):
            await _goto(page, SITE_URL + "/")
            logged = await _is_logged_in(page)
            return {
                "logged_in": logged,
                "url": page.url,
                "screenshot": await _screenshot(page, "session"),
                "hint": "" if logged else "Run `login` first.",
            }


@mcp.tool()
async def apply_profile_pack(confirm: bool = True) -> dict[str, Any]:
    """Fill the DOU profile from local/profile.env / experience.md."""
    return await update_profile(apply_defaults=True, confirm=confirm)


@mcp.tool()
async def search_jobs(
    category: str = "",
    keywords: str = "",
    search_in_description: bool = False,
    remote: bool = True,
    exp_level: str = "",
    city: str = "",
    relocation: bool = False,
    load_more: int = 1,
    limit: int = 30,
) -> dict[str, Any]:
    """Search DOU vacancies. Returns structured results.

    Args:
        category: DOU category, e.g. "Front End", "Back End". Empty = all.
        keywords: free-text (title, company, city). Example: "TypeScript".
        search_in_description: also search vacancy body (`descr=1`).
        remote: only remote jobs (adds `&remote`).
        exp_level: one of 0-1, 1-3, 3-5, 5plus.
        city: city name as on DOU (Київ, Львів, …). Ignored if remote=True.
        relocation: «за кордоном» filter (`&relocation`).
        load_more: how many times to click «Більше вакансій» (0 = first page only).
        limit: max results to return.
    """
    sys.path.insert(0, str(HERE.parent))
    import candidate as C

    category = category or C.dou_category()
    keywords = keywords or C.search_keyword()
    if exp_level and exp_level not in EXP_LEVELS:
        return {"error": f"exp_level must be one of {sorted(EXP_LEVELS)}"}

    params: dict[str, str] = {}
    extra_flags: list[str] = []
    if category:
        params["category"] = category
    if keywords:
        params["search"] = keywords
    if search_in_description:
        params["descr"] = "1"
    if exp_level:
        params["exp"] = exp_level
    if city and not remote:
        params["city"] = city
    if remote:
        extra_flags.append("remote")
    if relocation:
        extra_flags.append("relocation")

    qs = urlencode(params, quote_via=quote)
    flags = ("&" if qs else "") + "&".join(extra_flags) if extra_flags else ""
    url = f"{JOBS_URL}/vacancies/"
    if qs or flags:
        url += "?" + qs + (("&" + flags.lstrip("&")) if flags else "")
        url = url.replace("?&", "?").rstrip("&")

    async with _LOCK:
        async with browser() as (_ctx, pg):
            await _goto(pg, url)
            logged_in = await _is_logged_in(pg)
            for _ in range(max(0, load_more)):
                btn = pg.locator(SELECTORS["more_jobs"]).filter(
                    has_text=re.compile(r"більше вакансій", re.I)
                )
                if await btn.count() == 0:
                    break
                prev = await pg.locator(SELECTORS["vacancy_card"]).count()
                with contextlib.suppress(Exception):
                    await btn.first.click()
                    await pg.wait_for_timeout(1200)
                    with contextlib.suppress(PWTimeout):
                        await pg.wait_for_function(
                            f"document.querySelectorAll('li.l-vacancy').length > {prev}",
                            timeout=8000,
                        )
            jobs = await pg.evaluate(EXTRACT_CARDS_JS)
            heading = ""
            with contextlib.suppress(Exception):
                heading = (await pg.inner_text("h1")).strip()
            return {
                "search_url": url,
                "heading": heading,
                "logged_in": logged_in,
                "count": len(jobs[:limit]),
                "jobs": jobs[:limit],
                "hint": (
                    ""
                    if logged_in
                    else "Not logged in — public listings only. Run `login` to apply."
                ),
            }


@mcp.tool()
async def get_job(job: str) -> dict[str, Any]:
    """Fetch the full description of a single vacancy.

    Args:
        job: full URL or '<company-slug>/<id>' (e.g. fuelfinance/365651).
    """
    try:
        url = _job_url(job)
    except ValueError as e:
        return {"error": str(e)}
    async with _LOCK:
        async with browser() as (_ctx, pg):
            await _goto(pg, url)
            data = await pg.evaluate(EXTRACT_JOB_JS)
            data["url"] = pg.url
            data.update(_parse_job_ref(pg.url))
            return data


@mcp.tool()
async def build_cover_letter(job: str, variant: str = "") -> dict[str, Any]:
    """Generate a templated cover letter for a vacancy (no submission).

    Facts stay in reusable blocks from the Djinni cover-letter module.

    Args:
        job: full URL or '<company-slug>/<id>'.
        variant: optional override — backend, fullstack, ai, or generic.
    """
    try:
        url = _job_url(job)
    except ValueError as e:
        return {"error": str(e)}
    async with _LOCK:
        async with browser() as (_ctx, pg):
            await _goto(pg, url)
            data = await pg.evaluate(EXTRACT_JOB_JS)
    company = (data.get("company") or "").strip() or extract_company_from_job_text(
        data.get("text") or ""
    )
    var = variant if variant in ("backend", "fullstack", "ai", "generic") else None
    letter = build_letter(
        role=data.get("title") or "",
        company=company,
        variant=var,  # type: ignore[arg-type]
        job_text=data.get("text") or "",
    )
    return {
        "url": url,
        "title": clean_title(data.get("title") or ""),
        "company": company,
        "variant": var or detect_variant(data.get("title") or "", data.get("text") or ""),
        "cover_letter": letter,
    }


@mcp.tool()
async def apply(
    job: str,
    cover_letter: str = "",
    use_template: bool = False,
    confirm: bool = True,
) -> dict[str, Any]:
    """Apply to a vacancy with a cover letter.

    Safety: refuses to submit if it detects you already applied. Set confirm=False
    to only open the form and report what it sees WITHOUT submitting (dry run).

    Args:
        job: full URL or '<company-slug>/<id>'.
        cover_letter: the application message. Leave empty when use_template=True.
        use_template: auto-generate a cover letter from the job page.
        confirm: True actually submits; False = dry run (no submit).
    """
    if use_template and not cover_letter.strip():
        built = await build_cover_letter(job)
        if built.get("error"):
            return built
        cover_letter = built["cover_letter"]
    if not cover_letter or not cover_letter.strip():
        return {"error": "cover_letter is empty. Pass text or set use_template=True."}
    try:
        url = _job_url(job)
    except ValueError as e:
        return {"error": str(e)}

    async with _LOCK:
        async with browser() as (_ctx, pg):
            await _goto(pg, url)
            if not await _is_logged_in(pg):
                return {"error": "Not logged in. Run `login` first.", "url": pg.url}

            body_text = await pg.inner_text("body")
            if ALREADY_APPLIED_RE.search(body_text):
                return {"status": "skipped", "reason": "already applied", "url": pg.url}

            ref = _parse_job_ref(pg.url)
            job_key = f"{ref['company_slug']}/{ref['id']}" if ref.get("id") else pg.url
            if applied_store.is_applied(job_key):
                prev = applied_store.get(job_key) or {}
                return {
                    "status": "skipped",
                    "reason": "already applied (local ATS log)",
                    "previous": prev,
                    "url": pg.url,
                }

            # External ATS — follow the DOU goto link and fill the foreign form.
            if await pg.locator(SELECTORS["apply_external"]).count() > 0 \
                    and await pg.locator("#reply-btn-id").count() == 0:
                href = await pg.locator(SELECTORS["apply_external"]).first.get_attribute("href")
                if not href:
                    return {"error": "external apply link has no href", "url": pg.url}
                await _goto(pg, href)
                result = await ats.apply_external(
                    pg,
                    cover_letter=cover_letter,
                    confirm=confirm,
                    job_key=job_key,
                )
                shot = await _screenshot(pg, f"ats_{result.get('ats', 'ext')}")
                result["screenshot"] = shot
                result["dou_url"] = url
                if result.get("status") in (
                    "submitted", "needs_captcha", "needs_review"
                ):
                    applied_store.mark(
                        job_key,
                        ats=str(result.get("ats") or ""),
                        url=str(result.get("url") or href),
                        status=str(result.get("status")),
                        note=str(result.get("reason") or "")[:300],
                    )
                return result

            # Reveal the on-site form (#reply-btn-id → form#replied-id).
            opener = pg.locator("#reply-btn-id")
            if await opener.count() > 0:
                with contextlib.suppress(Exception):
                    await opener.first.click()
                    await pg.wait_for_timeout(800)
            else:
                with contextlib.suppress(Exception):
                    await pg.locator(SELECTORS["apply_open"]).first.click()
                    await pg.wait_for_timeout(800)

            textarea = pg.locator(SELECTORS["apply_textarea"]).first
            if await textarea.count() == 0:
                shot = await _screenshot(pg, "apply_no_form")
                return {
                    "error": "Could not find the apply form textarea.",
                    "url": pg.url,
                    "screenshot": shot,
                    "hint": "Use debug_dump_html on this URL and update SELECTORS.",
                }
            await textarea.fill(cover_letter)
            cv = PC.CV_PATH
            if cv.exists() and await pg.locator(SELECTORS["apply_cv"]).count() > 0:
                with contextlib.suppress(Exception):
                    await pg.locator(SELECTORS["apply_cv"]).first.set_input_files(str(cv))

            if not confirm:
                shot = await _screenshot(pg, "apply_dryrun")
                return {
                    "status": "dry_run",
                    "message": "Form filled but NOT submitted (confirm=False).",
                    "url": pg.url,
                    "screenshot": shot,
                }

            submitted = False
            for name in ("Надіслати", "Відправити", "Send", "Submit"):
                btn = pg.get_by_role("button", name=re.compile(name, re.I))
                if await btn.count() > 0:
                    with contextlib.suppress(Exception):
                        await btn.first.click()
                        submitted = True
                        break
            if not submitted:
                loc = pg.locator(SELECTORS["apply_submit"])
                if await loc.count() > 0:
                    with contextlib.suppress(Exception):
                        await loc.first.click()
                        submitted = True
            await pg.wait_for_timeout(1500)
            shot = await _screenshot(pg, "apply_result")
            body = (await pg.inner_text("body")).lower()
            ok = bool(ALREADY_APPLIED_RE.search(body) or "дякуємо" in body)
            return {
                "status": "submitted" if ok or submitted else "unknown",
                "clicked_submit": submitted,
                "url": pg.url,
                "screenshot": shot,
            }


@mcp.tool()
async def get_profile() -> dict[str, Any]:
    """Read your DOU candidate/user profile as structured form fields + visible text.

    Opens the own profile (and the edit form if an Edit link exists).
    """
    async with _LOCK:
        async with browser() as (_ctx, pg):
            nav = await _open_profile_edit(pg)
            if nav.get("error"):
                return nav
            state = await pg.evaluate(EXTRACT_FORM_JS)
            state["nav"] = nav
            state["logged_in"] = True
            shot = await _screenshot(pg, "profile")
            state["screenshot"] = shot
            return state


@mcp.tool()
async def update_profile(
    apply_defaults: bool = False,
    looking_for_job: Optional[bool] = None,
    position: str = "",
    about: str = "",
    salary: int = 0,
    city: str = "",
    extra_fields: Optional[dict[str, str]] = None,
    confirm: bool = True,
) -> dict[str, Any]:
    """Update your DOU profile and save.

    Matches fields by label/name substring (Ukrainian or English). Unknown
    widgets are reported as not_found — inspect `get_profile` first.

    Args:
        apply_defaults: fill from local profile / experience.md. Does not invent facts.
        looking_for_job: set the recruiter-visible «Шукаю роботу» checkbox.
        position: job title from the local profile.
        about: summary / «про себе» text.
        salary: expected USD/month. 0 = leave unchanged (unless apply_defaults).
        city: city of residence.
        extra_fields: extra {label_substring: value} pairs.
        confirm: True clicks Save; False = dry run.
    """
    updates: list[dict[str, Any]] = []
    if apply_defaults:
        updates.extend(_default_updates())
    if looking_for_job is not None:
        updates.append(
            {"match": "шукаю роботу", "value": looking_for_job, "type": "checkbox"}
        )
        updates.append(
            {"match": "looking for job", "value": looking_for_job, "type": "checkbox"}
        )
    if position:
        updates.append({"match": "посада", "value": position})
        updates.append({"match": "position", "value": position})
    if about:
        updates.append({"match": "про себе", "value": about, "type": "about"})
        updates.append({"match": "about", "value": about, "type": "about"})
    if salary:
        updates.append({"match": "зарплат", "value": str(salary)})
        updates.append({"match": "salary", "value": str(salary)})
    if city:
        updates.append({"match": "місто", "value": city})
        updates.append({"match": "city", "value": city})
    if extra_fields:
        for k, v in extra_fields.items():
            updates.append({"match": k, "value": v})

    if not updates:
        return {
            "error": "No fields to change. Pass apply_defaults=True or some fields."
        }

    async with _LOCK:
        async with browser() as (_ctx, pg):
            nav = await _open_profile_edit(pg)
            if nav.get("error"):
                return nav
            before = await pg.evaluate(EXTRACT_FORM_JS)
            fill_result = await pg.evaluate(FILL_FIELDS_JS, updates)

            if not confirm:
                shot = await _screenshot(pg, "profile_dryrun")
                return {
                    "status": "dry_run",
                    "nav": nav,
                    "fill": fill_result,
                    "before": before,
                    "screenshot": shot,
                    "url": pg.url,
                }

            saved = False
            for name in ("Зберегти", "Оновити", "Save", "Update"):
                btn = pg.get_by_role("button", name=re.compile(name, re.I))
                if await btn.count() > 0:
                    with contextlib.suppress(Exception):
                        await btn.first.click()
                        saved = True
                        break
            if not saved:
                loc = pg.locator("button[type='submit'], input[type='submit']")
                if await loc.count() > 0:
                    with contextlib.suppress(Exception):
                        await loc.first.click()
                        saved = True
            await pg.wait_for_timeout(1200)
            with contextlib.suppress(PWTimeout):
                await pg.wait_for_load_state("networkidle", timeout=8000)
            after = await pg.evaluate(EXTRACT_FORM_JS)
            shot = await _screenshot(pg, "profile_saved")
            body = (await pg.inner_text("body")).lower()
            ok = any(w in body for w in ("збереж", "оновл", "saved", "updated"))
            return {
                "status": "submitted" if saved else "unknown",
                "saved_ok_text": ok,
                "nav": nav,
                "fill": fill_result,
                "before_field_count": len(before.get("fields") or []),
                "after": {
                    "heading": after.get("heading"),
                    "looking_for_job_checked": after.get("looking_for_job_checked"),
                    "url": after.get("url"),
                    "fields": after.get("fields"),
                },
                "screenshot": shot,
                "url": pg.url,
            }


@mcp.tool()
async def list_inbox(limit: int = 30) -> dict[str, Any]:
    """List recruiter conversations in the DOU inbox.

    Args:
        limit: max conversations to return.
    """
    async with _LOCK:
        async with browser() as (_ctx, pg):
            await _goto(pg, f"{SITE_URL}/inbox/")
            if not await _is_logged_in(pg):
                return {"error": "Not logged in. Run `login` first.", "url": pg.url}
            threads = await pg.evaluate(
                """
                () => [...document.querySelectorAll('.contact[data-username]')].map(el => {
                  const slug = el.getAttribute('data-username') || '';
                  const textOf = sel => {
                    const n = el.querySelector(sel);
                    return n ? (n.innerText || '').trim() : '';
                  };
                  return {
                    thread: slug,
                    url: slug ? `https://dou.ua/inbox/${slug}/` : '',
                    contact: textOf('.contact-name'),
                    time: textOf('.contact-date'),
                    last_message: textOf('.contact-excerpt'),
                    unread: el.classList.contains('unread')
                      || el.classList.contains('new'),
                  };
                })
                """
            )
            return {"count": len(threads[:limit]), "threads": threads[:limit], "url": pg.url}


@mcp.tool()
async def read_thread(thread: str) -> dict[str, Any]:
    """Read a single DOU inbox conversation.

    Args:
        thread: username slug, /inbox/<slug>/ path, or full URL.
    """
    url = _thread_url(thread)
    async with _LOCK:
        async with browser() as (_ctx, pg):
            await _goto(pg, url)
            if not await _is_logged_in(pg):
                return {"error": "Not logged in. Run `login` first.", "url": pg.url}
            data = await pg.evaluate(
                """
                () => {
                  const name = (document.querySelector('.top-contact')
                    || document.querySelector('.contact-name'));
                  const work = document.querySelector('.top-contact-workplace');
                  const messages = [...document.querySelectorAll(
                    '.b-inbox-messages .item'
                  )].map(el => {
                    const raw = (el.innerText || '').trim();
                    return {
                      from_us: el.classList.contains('i_sent'),
                      text: raw,
                    };
                  });
                  return {
                    contact: name ? name.innerText.trim().split('\\n')[0] : '',
                    workplace: work ? work.innerText.trim() : '',
                    messages,
                  };
                }
                """
            )
            parts = []
            for msg in data.get("messages") or []:
                who = "Ви" if msg.get("from_us") else (data.get("contact") or "?")
                parts.append(f"{who}: {msg.get('text') or ''}")
            return {
                "url": pg.url,
                "contact": data.get("contact") or "",
                "workplace": data.get("workplace") or "",
                "messages": data.get("messages") or [],
                "text": "\n\n".join(parts)[:8000],
            }


@mcp.tool()
async def send_message(thread: str, text: str) -> dict[str, Any]:
    """Send a reply in a DOU inbox conversation.

    Args:
        thread: username slug, /inbox/<slug>/ path, or full URL.
        text: message body to send.
    """
    if not text or not text.strip():
        return {"error": "text is empty."}
    url = _thread_url(thread)
    async with _LOCK:
        async with browser() as (_ctx, pg):
            await _goto(pg, url)
            if not await _is_logged_in(pg):
                return {"error": "Not logged in. Run `login` first.", "url": pg.url}
            box = pg.locator(SELECTORS["inbox_textarea"]).first
            if await box.count() == 0:
                shot = await _screenshot(pg, "dou_msg_no_box")
                return {"error": "Message box not found.", "screenshot": shot, "url": pg.url}
            await box.fill(text)
            btn = pg.locator(SELECTORS["inbox_submit"]).first
            sent = False
            if await btn.count() > 0:
                with contextlib.suppress(Exception):
                    await btn.click()
                    sent = True
            if not sent:
                with contextlib.suppress(Exception):
                    await box.press("Control+Enter")
                    sent = True
            await pg.wait_for_timeout(1500)
            shot = await _screenshot(pg, "dou_msg_result")
            return {"status": "sent" if sent else "unknown", "url": pg.url, "screenshot": shot}


@mcp.tool()
async def debug_screenshot(url: str = "") -> dict[str, Any]:
    """Open a page (or DOU jobs home) and save a full-page screenshot.

    Args:
        url: page to open; empty = https://jobs.dou.ua/.
    """
    target = url if url.startswith("http") else (_abs_jobs(url) if url else JOBS_URL + "/")
    async with _LOCK:
        async with browser() as (_ctx, pg):
            await _goto(pg, target)
            shot = await _screenshot(pg, "debug")
            return {
                "url": pg.url,
                "logged_in": await _is_logged_in(pg),
                "screenshot": shot,
            }


@mcp.tool()
async def debug_dump_html(url: str = "", selector: str = "") -> dict[str, Any]:
    """Dump raw HTML of a page (or a selector) to help update SELECTORS.

    Args:
        url: page to open; empty = jobs home.
        selector: optional CSS selector; empty = whole document.
    """
    target = url if url.startswith("http") else (_abs_jobs(url) if url else JOBS_URL + "/")
    async with _LOCK:
        async with browser() as (_ctx, pg):
            await _goto(pg, target)
            if selector:
                loc = pg.locator(selector)
                if await loc.count() == 0:
                    return {
                        "url": pg.url,
                        "error": f"selector matched nothing: {selector}",
                    }
                html = await loc.first.inner_html()
            else:
                html = await pg.content()
            return {"url": pg.url, "length": len(html), "html": html[:20000]}


if __name__ == "__main__":
    mcp.run()
