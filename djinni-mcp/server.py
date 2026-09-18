"""
Djinni MCP server.

Exposes Djinni.co candidate actions as MCP tools driven by Playwright:
  - login            : open a real browser once, log in manually; the session is
                       stored in a persistent profile and reused by every other tool.
  - session_status   : whether the saved session is still logged in.
  - apply_profile_pack / update_profile / upload_cv : write the local profile.
  - get_profile      : structured candidate profile fields.
  - audit_profile    : empty vs filled visible form fields + banners.
  - search_jobs      : search vacancies by filters (returns structured results).
  - get_job          : full text of a single vacancy + whether you already applied.
  - apply            : send an application (cover letter) to a vacancy.
  - list_inbox       : list recruiter conversations.
  - read_thread      : read one conversation.
  - send_message     : reply in a conversation.
  - my_applications  : list vacancies you already applied to (+ status).
  - debug_screenshot : screenshot any page (to fix selectors when the site changes).
  - debug_dump_html  : dump raw HTML of a page/selector.

Design notes
------------
Djinni has no public candidate API, so this uses browser automation with YOUR
logged-in session (persistent Chromium profile). Selectors are kept resilient
(role/text based + JS extraction) and centralised in SELECTORS below so they are
easy to tweak if the markup changes. Use the debug_* tools to inspect the live
page and adjust.

This tool acts on your behalf. Use it responsibly and within Djinni's Terms of
Service — avoid mass/spam applications.
"""

from __future__ import annotations

import asyncio
import os
import re
import sys
import contextlib
from datetime import datetime
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlencode, urljoin

from mcp.server.fastmcp import FastMCP
from playwright.async_api import (
    async_playwright,
    BrowserContext,
    Page,
    TimeoutError as PWTimeout,
)

# --------------------------------------------------------------------------- #
# Config
# --------------------------------------------------------------------------- #
BASE_URL = "https://djinni.co"
HERE = Path(__file__).resolve().parent

# Optionally load a local .env (no hard dependency).
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


PROFILE_DIR = _resolve(_env("DJINNI_PROFILE_DIR", "./browser_profile"))
DEBUG_DIR = _resolve(_env("DJINNI_DEBUG_DIR", "./debug"))
HEADLESS = _env("DJINNI_HEADLESS", "1") not in ("0", "false", "False", "")
SLOWMO = int(_env("DJINNI_SLOWMO", "0") or "0")
TIMEOUT = int(_env("DJINNI_TIMEOUT", "30000") or "30000")
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

# Centralised, best-effort selectors. Tweak here if Djinni changes markup.
SELECTORS = {
    # Marks that we are logged in (inbox / profile links present in navbar).
    "logged_in": "a[href='/inbox/'], a[href*='/my/'], a[href*='/logout']",
    # Apply form on a job page.
    "apply_textarea": (
        "textarea[name='message']:not([name='flag_msg']), "
        "textarea#message, "
        "form.js-inbox-apply textarea, "
        ".profile-page-apply textarea[name='message']"
    ),
    # Message compose box in a conversation.
    "message_textarea": "textarea[name='body'], textarea[name='message'], form textarea",
    # Candidate profile edit form + its save button.
    "profile_form": "#js-profile-form",
    "profile_submit": ".js-profile-form-submit, #js-profile-form button[type='submit']",
}

PROFILE_URL = f"{BASE_URL}/my/profile/"

# experience_years: Djinni stores an option index, not the literal number of
# years. This maps human years -> the <select> option value used on the profile.
EXPERIENCE_YEARS_OPTIONS = {
    "0": "0", "0.5": "1", "1": "2", "1.5": "3", "2": "4", "2.5": "5",
    "3": "6", "3.5": "7", "4": "8", "4.5": "9", "5": "10", "6": "11",
    "7": "12", "8": "13", "9": "14", "10": "15", "10+": "16",
}

# Job search filter -> query param mapping (matches Djinni URL params).
EXP_LEVELS = {"no_exp", "1y", "2y", "3y", "5y"}
ENGLISH_LEVELS = {"no_english", "basic", "pre", "intermediate", "upper", "fluent"}
EMPLOYMENT = {"remote", "office", "parttime", "freelance"}
COMPANY_TYPES = {"product", "outsource", "outstaff", "agency", "startup"}

from cover_letters import build_letter, clean_title, detect_variant, extract_company_from_job_text

mcp = FastMCP("djinni")

# Serialise browser access; one persistent profile can only be opened once.
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
            locale="en-US",
            # --no-sandbox is required when the server runs as root (Chromium's
            # setuid sandbox refuses to start otherwise and navigation hangs).
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
    # Give client-side rendering a moment.
    with contextlib.suppress(PWTimeout):
        await page.wait_for_load_state("networkidle", timeout=8000)


async def _is_logged_in(page: Page) -> bool:
    try:
        return await page.locator(SELECTORS["logged_in"]).count() > 0
    except Exception:
        return False


def _job_url(job: str) -> str:
    """Accept a job id, slug or full url and return an absolute job URL."""
    job = str(job).strip()
    if job.startswith("http"):
        return job
    if job.startswith("/"):
        return urljoin(BASE_URL, job)
    # A bare numeric id must NOT have a trailing slash: Djinni redirects
    # /jobs/<id> to the full slug URL, but /jobs/<id>/ returns 404.
    if job.isdigit():
        return f"{BASE_URL}/jobs/{job}"
    return f"{BASE_URL}/jobs/{job.strip('/')}/"


def _abs(url: str) -> str:
    return url if url.startswith("http") else urljoin(BASE_URL, url)


def _thread_url(thread: str) -> str:
    """Accept a thread id, path or URL and return an absolute inbox thread URL."""
    thread = str(thread).strip()
    if thread.startswith("http"):
        return thread
    if thread.startswith("/"):
        return _abs(thread)
    if thread.isdigit():
        return f"{BASE_URL}/my/inbox/{thread}/"
    return _abs(f"/my/inbox/{thread.strip('/')}/")


# --------------------------------------------------------------------------- #
# Tools
# --------------------------------------------------------------------------- #
@mcp.tool()
async def login(wait_seconds: int = 180) -> str:
    """Open a visible browser window so you can log in to Djinni once.

    The session is saved in the persistent profile and reused by all other tools,
    so you normally only need to call this a single time (and again if it expires).
    Works with email/password, Google, LinkedIn and any captcha, because YOU do it.

    Args:
        wait_seconds: how long to keep the window open waiting for login to finish.
    """
    async with _LOCK:
        async with browser(headless=False) as (_ctx, page):
            await _goto(page, f"{BASE_URL}/login")
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
                "saved — try `list_inbox` or re-run `login` with a bigger "
                "wait_seconds."
            )


@mcp.tool()
async def session_status() -> dict[str, Any]:
    """Check whether the Playwright profile is logged in."""
    async with _LOCK:
        async with browser() as (_ctx, page):
            await _goto(page, PROFILE_URL)
            logged = await _is_logged_in(page)
            return {
                "logged_in": logged,
                "url": page.url,
                "screenshot": await _screenshot(page, "session"),
                "hint": "" if logged else "Run `login` first.",
            }


@mcp.tool()
async def apply_profile_pack(confirm: bool = True) -> dict[str, Any]:
    """Fill the Djinni profile from local/profile.env (candidate.py)."""
    sys.path.insert(0, str(HERE.parent))
    import candidate as C

    return await update_profile(
        position=C.TITLE,
        moreinfo=C.ABOUT_EN or C.ABOUT_UK,
        salary_min=C.SALARY_NET,
        experience_years=C.YEARS,
        city=C.CITY,
        add_skills=C.SKILLS or None,
        confirm=confirm,
    )


@mcp.tool()
async def upload_cv() -> dict[str, Any]:
    """Upload the PDF from CANDIDATE_CV to Djinni account / resume pages."""
    sys.path.insert(0, str(HERE.parent))
    import candidate as C
    from upload_cv import upload_cv as _upload

    pdf = C.CV_PATH
    if not pdf.is_file():
        return {"error": f"CV not found: {pdf}. Set CANDIDATE_CV."}
    async with _LOCK:
        async with browser() as (_ctx, page):
            await _goto(page, PROFILE_URL)
            if not await _is_logged_in(page):
                return {"error": "Not logged in. Run `login` first.", "url": page.url}
            result = await _upload(page, str(pdf), base_url=BASE_URL)
            result["screenshot"] = await _screenshot(page, "cv_upload")
            return result


@mcp.tool()
async def search_jobs(
    keywords: str = "",
    exp_level: str = "",
    english_level: str = "",
    employment: str = "",
    company_type: str = "",
    salary_min: int = 0,
    page: int = 1,
    limit: int = 20,
) -> dict[str, Any]:
    """Search Djinni vacancies. Returns structured results.

    Args:
        keywords: job title / technology, e.g. "Python", "React", "QA".
        exp_level: one of no_exp, 1y, 2y, 3y, 5y.
        english_level: one of no_english, basic, pre, intermediate, upper, fluent.
        employment: one of remote, office, parttime, freelance.
        company_type: one of product, outsource, outstaff, agency, startup.
        salary_min: minimum expected salary in USD/month.
        page: results page (1-based).
        limit: max results to return from this page.
    """
    sys.path.insert(0, str(HERE.parent))
    import candidate as C

    keywords = keywords or C.search_keyword()
    english_level = english_level or C.djinni_english_level()
    if not salary_min:
        salary_min = C.SALARY_FLOOR
    params: dict[str, str] = {}
    if keywords:
        params["primary_keyword"] = keywords
    if exp_level:
        if exp_level not in EXP_LEVELS:
            return {"error": f"exp_level must be one of {sorted(EXP_LEVELS)}"}
        params["exp_level"] = exp_level
    if english_level:
        if english_level not in ENGLISH_LEVELS:
            return {"error": f"english_level must be one of {sorted(ENGLISH_LEVELS)}"}
        params["english_level"] = english_level
    if employment:
        if employment not in EMPLOYMENT:
            return {"error": f"employment must be one of {sorted(EMPLOYMENT)}"}
        params["employment"] = employment
    if company_type:
        if company_type not in COMPANY_TYPES:
            return {"error": f"company_type must be one of {sorted(COMPANY_TYPES)}"}
        params["company_type"] = company_type
    if salary_min:
        params["salary"] = str(salary_min)
    if page and page > 1:
        params["page"] = str(page)

    url = f"{BASE_URL}/jobs/"
    if params:
        url += "?" + urlencode(params)

    async with _LOCK:
        async with browser() as (_ctx, pg):
            await _goto(pg, url)
            logged_in = await _is_logged_in(pg)
            # Extract job cards resiliently: every vacancy links to /jobs/<id>-...
            jobs = await pg.evaluate(
                """
                () => {
                  const seen = new Set();
                  const out = [];
                  const anchors = Array.from(
                    document.querySelectorAll("a[href*='/jobs/']")
                  );
                  for (const a of anchors) {
                    const m = a.getAttribute('href').match(/\\/jobs\\/(\\d+)/);
                    if (!m) continue;
                    const id = m[1];
                    if (seen.has(id)) continue;
                    // climb to the card container
                    let card = a.closest('li, article, .job-list__item, .list-jobs__item');
                    if (!card) card = a.parentElement;
                    const title = (a.innerText || a.textContent || '').trim();
                    if (!title) continue;
                    seen.add(id);
                    const text = (card.innerText || '').replace(/\\s+\\n/g, '\\n').trim();
                    out.push({
                      id,
                      title,
                      url: a.href,
                      details: text.slice(0, 1200),
                    });
                  }
                  return out;
                }
                """
            )
            return {
                "search_url": url,
                "logged_in": logged_in,
                "count": len(jobs[:limit]),
                "jobs": jobs[:limit],
                "hint": (
                    "" if logged_in else
                    "Not logged in — results are public listings only. Run `login`."
                ),
            }


@mcp.tool()
async def get_job(job: str) -> dict[str, Any]:
    """Fetch the full description of a single vacancy.

    Args:
        job: job id (e.g. "751234"), slug, path (/jobs/...) or full URL.
    """
    url = _job_url(job)
    async with _LOCK:
        async with browser() as (_ctx, pg):
            await _goto(pg, url)
            data = await pg.evaluate(
                """
                () => {
                  const q = s => document.querySelector(s);
                  const main = q('main') || document.body;
                  const title = (q('h1') && q('h1').innerText || '').trim();
                  const applied = /already applied|ви вже відгук|ви відгукн/i
                      .test(document.body.innerText);
                  const existingThread = /маєте діалог з цим рекрутером|already have a (dialogue|conversation|thread) with this recruiter/i
                      .test(document.body.innerText);
                  const canApply = !!Array.from(document.querySelectorAll('button, a'))
                      .find(e => /apply|відгук/i.test(e.innerText || ''));
                  return {
                    title,
                    already_applied: applied || existingThread,
                    existing_recruiter_thread: existingThread,
                    apply_available: canApply,
                    text: (main.innerText || '').trim().slice(0, 8000),
                  };
                }
                """
            )
            data["url"] = pg.url
            return data


@mcp.tool()
async def build_cover_letter(job: str, variant: str = "") -> dict[str, Any]:
    """Generate a templated cover letter for a vacancy (no submission).

    Strips Djinni salary markers ($$$$) and builds from experience blocks.
    Use the returned text with `apply`, or edit before submitting.

    Args:
        job: job id, slug, path or full URL.
        variant: optional override — backend, fullstack, ai, or generic.
    """
    url = _job_url(job)
    async with _LOCK:
        async with browser() as (_ctx, pg):
            await _goto(pg, url)
            data = await pg.evaluate(
                """
                () => {
                  const h1 = document.querySelector('h1');
                  let company = '';
                  if (h1) {
                    const block = h1.closest('div') || h1.parentElement;
                    if (block) {
                      const link = block.querySelector('a[href*="/jobs/company/"], a[href*="/companies/"]');
                      if (link) company = (link.innerText || '').trim();
                      if (!company) {
                        const lines = (block.innerText || '').split('\\n').map(s => s.trim()).filter(Boolean);
                        const hi = lines.indexOf((h1.innerText || '').trim());
                        for (let i = hi + 1; i < Math.min(hi + 4, lines.length); i++) {
                          const ln = lines[i];
                          if (/^\\$+$/.test(ln) || /підписатись|зберегти|сховати/i.test(ln)) continue;
                          if (ln.length >= 2 && ln.length <= 50 && !/developer|engineer/i.test(ln)) {
                            company = ln; break;
                          }
                        }
                      }
                    }
                  }
                  return {
                    title: ((h1||{}).innerText || '').trim(),
                    company,
                    text: ((document.querySelector('main')||document.body).innerText||'')
                      .slice(0, 6000),
                  };
                }
                """
            )
    if not (data.get("company") or "").strip():
        data["company"] = extract_company_from_job_text(data.get("text") or "")
    var = variant if variant in ("backend", "fullstack", "ai", "generic") else None
    letter = build_letter(
        role=data.get("title") or "",
        company=data.get("company") or "",
        variant=var,  # type: ignore[arg-type]
        job_text=data.get("text") or "",
    )
    return {
        "url": url,
        "title": clean_title(data.get("title") or ""),
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
        job: job id, slug, path or full URL.
        cover_letter: the application message. Leave empty when use_template=True.
        use_template: auto-generate a cover letter from job page (strips $$$$ etc.).
        confirm: True actually submits; False = dry run (no submit).
    """
    if use_template and not cover_letter.strip():
        built = await build_cover_letter(job)
        if built.get("error"):
            return built
        cover_letter = built["cover_letter"]
    if not cover_letter or not cover_letter.strip():
        return {"error": "cover_letter is empty. Pass text or set use_template=True."}
    url = _job_url(job)
    async with _LOCK:
        async with browser() as (_ctx, pg):
            await _goto(pg, url)
            if not await _is_logged_in(pg):
                return {"error": "Not logged in. Run `login` first.", "url": pg.url}

            body_text = (await pg.inner_text("body")).lower()
            if re.search(r"already applied|ви вже відгук|ви відгукн", body_text):
                return {"status": "skipped", "reason": "already applied", "url": pg.url}

            # Reveal the apply form if it's behind a button or link.
            opened = False
            for name in ("Apply", "Відгукнутися", "Відгукнутись", "Respond"):
                for role in ("button", "link"):
                    loc = pg.get_by_role(role, name=re.compile(name, re.I))
                    if await loc.count() == 0:
                        continue
                    with contextlib.suppress(Exception):
                        await loc.first.click()
                        await pg.wait_for_timeout(1000)
                        opened = True
                        break
                if opened:
                    break
            if not opened:
                extra = pg.locator(
                    "a:has-text('Відгукнутися'), button:has-text('Відгукнутися'), "
                    "a:has-text('Відгукнутись'), button:has-text('Відгукнутись')"
                )
                if await extra.count() > 0:
                    with contextlib.suppress(Exception):
                        await extra.first.click()
                        await pg.wait_for_timeout(1000)

            with contextlib.suppress(Exception):
                await pg.locator("textarea[name='message']:visible").wait_for(timeout=5000)

            textarea = pg.locator(SELECTORS["apply_textarea"]).first
            visible = pg.locator(
                "textarea[name='message']:visible, textarea#message:visible"
            ).first
            if await visible.count() > 0:
                textarea = visible
            if await textarea.count() == 0:
                shot = await _screenshot(pg, "apply_no_form")
                return {
                    "error": "Could not find the apply form textarea.",
                    "url": pg.url,
                    "screenshot": shot,
                    "hint": "Use debug_dump_html on this URL and update SELECTORS.",
                }
            await textarea.fill(cover_letter)

            if not confirm:
                shot = await _screenshot(pg, "apply_dryrun")
                return {
                    "status": "dry_run",
                    "message": "Form filled but NOT submitted (confirm=False).",
                    "url": pg.url,
                    "screenshot": shot,
                }

            submitted = False
            for name in ("Send", "Надіслати", "Відправити", "Apply", "Submit"):
                btn = pg.get_by_role("button", name=re.compile(name, re.I))
                if await btn.count() > 0:
                    with contextlib.suppress(Exception):
                        await btn.first.click()
                        submitted = True
                        break
            if not submitted:
                with contextlib.suppress(Exception):
                    await textarea.press("Control+Enter")
                    submitted = True

            await pg.wait_for_timeout(1500)
            shot = await _screenshot(pg, "apply_result")
            after = (await pg.inner_text("body")).lower()
            ok = bool(re.search(r"applied|відгук|надіслано|sent", after))
            return {
                "status": "submitted" if ok else "unknown",
                "clicked_submit": submitted,
                "url": pg.url,
                "screenshot": shot,
            }


@mcp.tool()
async def list_inbox(limit: int = 30, bucket: str = "") -> dict[str, Any]:
    """List conversations in your Djinni inbox (recruiter chats / applications).

    Args:
        limit: max conversations to return.
        bucket: optional filter — "unread", "apply" (your applications) or
                "archive". Empty = all.
    """
    url = f"{BASE_URL}/my/inbox/"
    if bucket:
        url += f"?bucket={bucket}"
    async with _LOCK:
        async with browser() as (_ctx, pg):
            await _goto(pg, url)
            if not await _is_logged_in(pg):
                return {"error": "Not logged in. Run `login` first.", "url": pg.url}
            threads = await pg.evaluate(
                """
                () => {
                  const seen = new Set();
                  const out = [];
                  for (const a of document.querySelectorAll("a[href*='/my/inbox/']")) {
                    const href = a.getAttribute('href') || '';
                    const m = href.match(/\\/my\\/inbox\\/(\\d+)/);
                    if (!m) continue;
                    if (seen.has(m[1])) continue;
                    const row = a.closest('.proposal, .thread-item-wrapper, li, .msg-thread, tr, .list-group-item') || a;
                    const text = (row.innerText || a.innerText || '').replace(/\\s*\\n\\s*/g, ' | ').trim();
                    if (!text) continue;
                    seen.add(m[1]);
                    out.push({ thread: m[1], url: a.href, preview: text.slice(0, 400) });
                  }
                  return out;
                }
                """
            )
            return {"count": len(threads[:limit]), "threads": threads[:limit]}


@mcp.tool()
async def read_thread(thread: str) -> dict[str, Any]:
    """Read a single inbox conversation.

    Args:
        thread: conversation id, /my/inbox/... path, or full URL.
    """
    url = _thread_url(thread)
    async with _LOCK:
        async with browser() as (_ctx, pg):
            await _goto(pg, url)
            if not await _is_logged_in(pg):
                return {"error": "Not logged in. Run `login` first.", "url": pg.url}
            text = (await pg.inner_text("main")) if await pg.locator("main").count() else (
                await pg.inner_text("body")
            )
            return {"url": pg.url, "text": text.strip()[:8000]}


@mcp.tool()
async def send_message(thread: str, text: str) -> dict[str, Any]:
    """Send a reply in an inbox conversation.

    Args:
        thread: conversation id, /my/inbox/... path, or full URL.
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
            box = pg.locator(SELECTORS["message_textarea"]).first
            if await box.count() == 0:
                shot = await _screenshot(pg, "msg_no_box")
                return {"error": "Message box not found.", "screenshot": shot,
                        "url": pg.url}
            await box.fill(text)
            sent = False
            for name in ("Send", "Надіслати", "Відправити"):
                btn = pg.get_by_role("button", name=re.compile(name, re.I))
                if await btn.count() > 0:
                    with contextlib.suppress(Exception):
                        await btn.first.click()
                        sent = True
                        break
            if not sent:
                with contextlib.suppress(Exception):
                    await box.press("Control+Enter")
                    sent = True
            await pg.wait_for_timeout(1200)
            shot = await _screenshot(pg, "msg_result")
            return {"status": "sent" if sent else "unknown", "url": pg.url,
                    "screenshot": shot}


@mcp.tool()
async def my_applications(limit: int = 50) -> dict[str, Any]:
    """List vacancies you already applied to, with any visible status.

    On Djinni, applications live in the inbox under the "apply" bucket as
    conversations you started.
    """
    async with _LOCK:
        async with browser() as (_ctx, pg):
            await _goto(pg, f"{BASE_URL}/my/inbox/?bucket=apply")
            if not await _is_logged_in(pg):
                return {"error": "Not logged in. Run `login` first.", "url": pg.url}
            apps = await pg.evaluate(
                """
                () => {
                  const seen = new Set();
                  const out = [];
                  for (const a of document.querySelectorAll("a[href*='/my/inbox/']")) {
                    const href = a.getAttribute('href') || '';
                    const m = href.match(/\\/my\\/inbox\\/(\\d+)/);
                    if (!m || seen.has(m[1])) continue;
                    const row = a.closest('.proposal, .thread-item-wrapper, li, .msg-thread, tr, .list-group-item') || a;
                    const text = (row.innerText || '').replace(/\\s*\\n\\s*/g, ' | ').trim();
                    if (!text) continue;
                    seen.add(m[1]);
                    out.push({ thread: m[1], url: a.href, status: text.slice(0, 400) });
                  }
                  return out;
                }
                """
            )
            return {"url": pg.url, "count": len(apps[:limit]),
                    "applications": apps[:limit]}


_PROFILE_STATE_JS = """
() => {
  const val = n => { const e = document.querySelector(`[name='${n}']`); return e ? e.value : null; };
  const selText = n => {
    const e = document.querySelector(`select[name='${n}']`);
    if (!e) return null;
    const o = e.options[e.selectedIndex];
    return o ? {value: e.value, text: o.text.trim()} : {value: e.value};
  };
  const radio = n => {
    const e = document.querySelector(`[name='${n}']:checked`);
    return e ? e.value : null;
  };
  const collect = prefix => {
    const out = [];
    for (let i = 0; i < 40; i++) {
      const a = document.querySelector(
        `[name='${prefix}[${i}][skill]'],[name='${prefix}[${i}][domain]'],[name='${prefix}[${i}][code]']`
      );
      if (!a) continue;
      const yrs = document.querySelector(`[name='${prefix}[${i}][experience_years]']`);
      const lvl = document.querySelector(`[name='${prefix}[${i}][level]']`);
      const row = {key: a.value};
      if (yrs) { const o = yrs.options ? yrs.options[yrs.selectedIndex] : null; row.years = o ? o.text.trim() : yrs.value; }
      if (lvl) { const o = lvl.options ? lvl.options[lvl.selectedIndex] : null; row.level = o ? o.text.trim() : lvl.value; }
      out.push(row);
    }
    return out;
  };
  return {
    position: val('position'),
    primary_keyword: selText('primary_keyword_cache'),
    salary_min: val('salary_min'),
    salary_type: radio('salary_type'),
    experience_years: selText('experience_years'),
    moreinfo: val('moreinfo'),
    skills: collect('skills_experience'),
    languages: collect('language_knowledge'),
    domains: collect('domain_experience'),
  };
}
"""


async def _set_moreinfo(page: Page, text: str) -> str:
    """Set the 'About' summary. The #moreinfo textarea is enhanced by CKEditor,
    so the raw textarea is hidden — write through the editor instance (and sync
    the textarea as a fallback)."""
    return await page.evaluate(
        """
        (text) => {
          const ta = document.querySelector('#moreinfo');
          const ed = document.querySelector('.ck-editor__editable');
          if (ed && ed.ckeditorInstance) {
            ed.ckeditorInstance.setData(text);
            return 'ckeditor';
          }
          if (ta) {
            ta.value = text;
            ta.dispatchEvent(new Event('input', {bubbles: true}));
            ta.dispatchEvent(new Event('change', {bubbles: true}));
            return 'textarea';
          }
          return 'not_found';
        }
        """,
        text,
    )


async def _add_domains(page: Page, items: list[dict[str, str]]) -> dict[str, Any]:
    """Add domain rows. items: [{"value": "logistics", "years": "2"}, ...].

    Skips domains that are already present. Plain <select> widgets, so we set
    the value and dispatch a change event.
    """
    added, skipped, missing = [], [], []
    for it in items:
        dom = it.get("value", "").strip()
        if not dom:
            continue
        existing = await page.evaluate(
            "() => Array.from(document.querySelectorAll(\"select[name$='[domain]']\"))"
            ".map(e => e.value)"
        )
        if dom in existing:
            skipped.append(dom)
            continue
        btn = page.locator(".btn-add-entry", has_text=re.compile("домен", re.I)).first
        if await btn.count() == 0:
            missing.append(dom)
            continue
        await btn.click()
        await page.wait_for_timeout(350)
        ok = await page.evaluate(
            """
            ([dom, years]) => {
              const sels = Array.from(document.querySelectorAll("select[name$='[domain]']"));
              const sel = sels[sels.length - 1];
              if (!sel) return false;
              const opt = Array.from(sel.options).find(o => o.value === dom);
              if (!opt) return 'no_option';
              sel.value = dom;
              sel.dispatchEvent(new Event('change', {bubbles: true}));
              const m = sel.name.match(/\\[(\\d+)\\]/);
              if (m && years) {
                const y = document.querySelector(`select[name='domain_experience[${m[1]}][experience_years]']`);
                if (y) { y.value = years; y.dispatchEvent(new Event('change', {bubbles: true})); }
              }
              return true;
            }
            """,
            [dom, it.get("years", "")],
        )
        if ok is True:
            added.append(dom)
        else:
            missing.append(dom)
    return {"added": added, "skipped_existing": skipped, "no_option": missing}


async def _add_agentic_skills(page: Page, labels: list[str]) -> dict[str, Any]:
    """Click Djinni "agentic skills" chips by label (e.g. Cursor, Claude Code)."""
    added, missing = [], []
    for label in labels:
        chip = page.locator(
            ".agentic-skill-chip", has_text=re.compile(re.escape(label), re.I)
        ).first
        if await chip.count() == 0:
            missing.append(label)
            continue
        with contextlib.suppress(Exception):
            await chip.click()
            await page.wait_for_timeout(250)
        added.append(label)
    return {"clicked": added, "not_found": missing}


async def _remove_skills(page: Page, names: list[str]) -> dict[str, Any]:
    """Remove skill rows by (case-insensitive) skill name. Frees slots because
    Djinni caps the number of skills and hides the add button when full."""
    removed, not_found = [], []
    for name in names:
        ok = await page.evaluate(
            """
            (name) => {
              const inps = Array.from(document.querySelectorAll("input[name$='[skill]']"));
              const t = inps.find(i => (i.value || '').toLowerCase() === name.toLowerCase());
              if (!t) return false;
              const row = t.closest('.csc-group, .csc-entry, .input-group, tr, li');
              const btn = row && row.querySelector('.csc__remove, .btn-remove-entry');
              if (!btn) return false;
              btn.click();
              return true;
            }
            """,
            name,
        )
        (removed if ok else not_found).append(name)
        await page.wait_for_timeout(200)
    return {"removed": removed, "not_found": not_found}


async def _add_skills(page: Page, names: list[str]) -> dict[str, Any]:
    """Add skills through the TomSelect remote-search control.

    Djinni loads skill options from the server as you type, so a skill is only
    added if it exists in Djinni's taxonomy. Returns which names matched.
    """
    added, not_found = [], []
    for name in names:
        existing = await page.evaluate(
            "() => Array.from(document.querySelectorAll(\"input[name$='[skill]']\"))"
            ".map(e => (e.value || '').toLowerCase())"
        )
        if name.lower() in existing:
            added.append(name)
            continue
        btn = page.locator(
            ".btn-add-entry", has_text=re.compile("навичк", re.I)
        ).first
        if await btn.count() == 0:
            not_found.append(name)
            continue
        if not await btn.is_visible():
            # Djinni caps skills (≈20) and hides the add button when full.
            skill_count = await page.evaluate(
                "() => document.querySelectorAll(\"input[name$='[skill]']\").length"
            )
            return {"added": added, "not_found": not_found + names[len(added):],
                    "error": "add-skill button hidden (skill limit reached?)",
                    "skill_count": skill_count}
        # Add a fresh row via the button's JS handler; a lingering TomSelect
        # dropdown otherwise intercepts a real click on the button.
        await page.evaluate("() => window.addSkillEntry && window.addSkillEntry()")
        await page.wait_for_timeout(300)
        control = page.locator("input[id$='-ts-control']").last
        try:
            await control.click()
            await control.fill(name)
            # remote search loads options as you type
            await page.wait_for_timeout(1100)
        except Exception:
            not_found.append(name)
            continue
        # Select via the TomSelect instance (clicking the dropdown option is
        # flaky and free-text 'create' would store a bogus lowercase value).
        picked = await page.evaluate(
            """
            (q) => {
              const inps = document.querySelectorAll("input[name$='[skill]']");
              const inp = inps[inps.length - 1];
              const ts = inp && inp.tomselect;
              if (!ts) return {ok: false, reason: 'no_instance'};
              const vf = ts.settings.valueField, lf = ts.settings.labelField;
              const opts = Object.values(ts.options);
              const ql = q.toLowerCase();
              const exact = opts.find(o => String(o[lf] || '').toLowerCase() === ql);
              const part = opts.find(o => String(o[lf] || '').toLowerCase().includes(ql));
              const t = exact || part;
              if (!t) { return {ok: false, reason: 'no_option'}; }
              ts.clear(true);
              ts.addItem(t[vf], true);
              ts.close();
              return {ok: true, label: t[lf], value: t[vf]};
            }
            """,
            name,
        )
        if picked and picked.get("ok"):
            added.append(picked.get("label") or name)
        else:
            not_found.append(name)
        with contextlib.suppress(Exception):
            await control.press("Escape")
        await page.wait_for_timeout(150)

    # Remove any leftover empty skill rows so they don't submit as blanks.
    with contextlib.suppress(Exception):
        await page.evaluate(
            """
            () => {
              document.querySelectorAll("input[name$='[skill]']").forEach(inp => {
                if (!(inp.value || '').trim()) {
                  const row = inp.closest('.csc-group, .csc-entry, .input-group, tr, li');
                  const btn = row && row.querySelector('.csc__remove, .btn-remove-entry');
                  if (btn) btn.click();
                }
              });
            }
            """
        )
    return {"added": added, "not_found": not_found}


async def _strip_incomplete_profile_rows(page: Page) -> list[str]:
    """Remove incomplete candidate-widget rows that fail HTML5 required.

    Djinni marks language/skill/domain fields `required`. A leftover empty
    row (or a language with a code but no level) makes `form.checkValidity()`
    false, so the browser never fires submit and salary never POSTs.
    """
    return await page.evaluate(
        """
        () => {
          const removed = [];
          const pairs = [
            {kind: 'language', key: "select[name$='[code]']", extra: "select[name$='[level]']"},
            {kind: 'skill', key: "input[name$='[skill]']", extra: null},
            {kind: 'domain', key: "select[name$='[domain]'], input[name$='[domain]']", extra: null},
          ];
          const rows = document.querySelectorAll(
            '.csc-group, .csc-entry, .input-group.csc-group'
          );
          rows.forEach(row => {
            for (const p of pairs) {
              const keyEl = row.querySelector(p.key);
              if (!keyEl) continue;
              const extraEl = p.extra ? row.querySelector(p.extra) : null;
              const keyEmpty = !(keyEl.value || '').trim();
              const extraEmpty = extraEl && extraEl.required && !(extraEl.value || '').trim();
              if (!keyEmpty && !extraEmpty) continue;
              const btn = row.querySelector('.csc__remove, .btn-remove-entry');
              if (!btn) continue;
              btn.click();
              removed.push(
                extraEmpty && !keyEmpty
                  ? p.kind + ':' + keyEl.value + '(no level)'
                  : p.kind + ':empty'
              );
              break;
            }
          });
          return removed;
        }
        """
    )


@mcp.tool()
async def get_profile() -> dict[str, Any]:
    """Read your Djinni candidate profile (position, salary, experience, summary,
    skills, languages, domains) as structured data.

    Useful before `update_profile` to see the current values.
    """
    async with _LOCK:
        async with browser() as (_ctx, pg):
            await _goto(pg, PROFILE_URL)
            if not await _is_logged_in(pg):
                return {"error": "Not logged in. Run `login` first.", "url": pg.url}
            state = await pg.evaluate(_PROFILE_STATE_JS)
            state["url"] = pg.url
            return state


_AUDIT_FIELDS_JS = """
() => {
  const fields = [];
  for (const el of document.querySelectorAll('input, textarea, select')) {
    const type = (el.getAttribute('type') || el.tagName.toLowerCase()).toLowerCase();
    if (['hidden', 'submit', 'button', 'file'].includes(type)) continue;
    const name = el.name || el.id || '';
    if (!name) continue;
    let value = '';
    if (type === 'checkbox' || type === 'radio') {
      if (!el.checked) continue;
      value = el.value || 'on';
    } else if (el.tagName === 'SELECT') {
      const o = el.options[el.selectedIndex];
      value = o ? o.text.trim() : el.value;
    } else {
      value = (el.value || '').trim();
    }
    const label = el.labels && el.labels[0]
      ? el.labels[0].innerText.trim().slice(0, 80)
      : (el.getAttribute('placeholder') || '').slice(0, 80);
    fields.push({name, type, label, value: value.slice(0, 200), empty: !value});
  }
  const more = document.querySelector('#moreinfo');
  const banners = [...document.querySelectorAll('.alert, .notice, .banner, [role=alert]')]
    .map(e => (e.innerText || '').trim()).filter(Boolean).slice(0, 8);
  return {
    title: document.title,
    h1: (document.querySelector('h1') || {}).innerText || '',
    moreinfo_len: more ? (more.value || '').length : 0,
    banners,
    empty: fields.filter(f => f.empty && !['checkbox','radio'].includes(f.type)).map(f => ({
      name: f.name, label: f.label
    })),
    filled: fields.filter(f => !f.empty).map(f => ({
      name: f.name, label: f.label, value: f.value
    })),
  };
}
"""


@mcp.tool()
async def audit_profile() -> dict[str, Any]:
    """List empty vs filled visible fields on the Djinni profile form.

    Complements `get_profile`: catches blank required rows, leftover alerts,
    and fields the structured reader does not map.
    """
    async with _LOCK:
        async with browser() as (_ctx, pg):
            await _goto(pg, PROFILE_URL)
            if not await _is_logged_in(pg):
                return {"error": "Not logged in. Run `login` first.", "url": pg.url}
            state = await pg.evaluate(_PROFILE_STATE_JS)
            dump = await pg.evaluate(_AUDIT_FIELDS_JS)
            dump["structured"] = {
                "position": state.get("position"),
                "primary_keyword": state.get("primary_keyword"),
                "salary_min": state.get("salary_min"),
                "experience_years": state.get("experience_years"),
                "skills": state.get("skills"),
                "languages": state.get("languages"),
                "moreinfo_preview": (state.get("moreinfo") or "")[:180],
            }
            dump["screenshot"] = await _screenshot(pg, "audit")
            dump["url"] = pg.url
            return dump


@mcp.tool()
async def update_profile(
    position: str = "",
    moreinfo: str = "",
    salary_min: int = 0,
    salary_type: str = "",
    experience_years: str = "",
    primary_keyword: str = "",
    country_code: str = "",
    city: str = "",
    add_domains: Optional[list[str]] = None,
    domain_years: str = "1",
    agentic_skills: Optional[list[str]] = None,
    remove_skills: Optional[list[str]] = None,
    add_skills: Optional[list[str]] = None,
    confirm: bool = True,
) -> dict[str, Any]:
    """Update your Djinni candidate profile and save.

    Only the arguments you pass (non-empty / non-zero) are changed; everything
    else is left as-is. Set confirm=False for a dry run: it fills the form and
    reports before/after WITHOUT clicking "Оновити профіль".

    Args:
        position: job title, e.g. "Full-Stack (Node / React) Developer".
        moreinfo: the "About / experience" summary text.
        salary_min: minimum expected salary (USD/month). 0 = leave unchanged.
        salary_type: "net" or "gross".
        experience_years: human years as a string key — one of
            0, 0.5, 1, 1.5, 2, 2.5, 3, 3.5, 4, 4.5, 5, 6, 7, 8, 9, 10, 10+.
        primary_keyword: the specialization <select> value (e.g. "Fullstack").
        country_code: ISO-3 country of residence, e.g. "BGR", "ITA", "UKR".
        city: city of residence (free text).
        add_domains: domain option values to add, e.g. ["logistics", "energy"].
        domain_years: experience (in years, as a string) applied to added
            domains, e.g. "1", "2" (Djinni requires a value per domain).
        agentic_skills: agentic-tool chips to add, e.g. ["Cursor", "Codex"].
        remove_skills: skill names to remove first, e.g. ["HTML", "CSS"] — Djinni
            caps skills, so removing frees slots for add_skills.
        add_skills: skills to add via search, e.g. ["OpenAI", "LangChain"];
            only added if they exist in Djinni's skill taxonomy.
        confirm: True saves; False = dry run (fill only, no submit).
    """
    if salary_type and salary_type not in ("net", "gross"):
        return {"error": "salary_type must be 'net' or 'gross'."}
    exp_value = ""
    if experience_years:
        exp_value = EXPERIENCE_YEARS_OPTIONS.get(str(experience_years).strip())
        if not exp_value:
            return {
                "error": "experience_years must be one of "
                + ", ".join(EXPERIENCE_YEARS_OPTIONS)
            }

    async with _LOCK:
        async with browser() as (_ctx, pg):
            await _goto(pg, PROFILE_URL)
            if not await _is_logged_in(pg):
                return {"error": "Not logged in. Run `login` first.", "url": pg.url}

            before = await pg.evaluate(_PROFILE_STATE_JS)
            changed: list[str] = []
            widget_results: dict[str, Any] = {}

            if position:
                await pg.fill("#position", position)
                changed.append("position")
            if moreinfo:
                widget_results["moreinfo_via"] = await _set_moreinfo(pg, moreinfo)
                changed.append("moreinfo")
            if salary_min:
                box = pg.locator("#salary_min")
                await box.fill(str(salary_min))
                await box.dispatch_event("input")
                await box.dispatch_event("change")
                changed.append("salary_min")
            if salary_type:
                with contextlib.suppress(Exception):
                    await pg.check(f"#salary_type_{salary_type}")
                changed.append("salary_type")
            if exp_value:
                await pg.select_option("#experience_years", exp_value)
                changed.append("experience_years")
            if primary_keyword:
                with contextlib.suppress(Exception):
                    await pg.select_option(
                        "select[name='primary_keyword_cache']", primary_keyword
                    )
                changed.append("primary_keyword")
            if country_code:
                with contextlib.suppress(Exception):
                    await pg.select_option("#country_code", country_code)
                changed.append("country_code")
            if city:
                await pg.fill("#location", city)
                changed.append("city")
            if add_domains:
                widget_results["domains"] = await _add_domains(
                    pg, [{"value": d, "years": domain_years} for d in add_domains]
                )
                changed.append("domains")
            if agentic_skills:
                widget_results["agentic_skills"] = await _add_agentic_skills(
                    pg, agentic_skills
                )
                changed.append("agentic_skills")
            if remove_skills:
                widget_results["removed_skills"] = await _remove_skills(pg, remove_skills)
                changed.append("removed_skills")
            if add_skills:
                widget_results["skills"] = await _add_skills(pg, add_skills)
                changed.append("skills")

            if not changed:
                return {"status": "noop", "reason": "no fields provided",
                        "current": before, "url": pg.url}

            if not confirm:
                filled = await pg.evaluate(_PROFILE_STATE_JS)
                shot = await _screenshot(pg, "profile_dryrun")
                return {"status": "dry_run", "changed": changed,
                        "widgets": widget_results,
                        "before": before, "after_fill": filled,
                        "screenshot": shot, "url": pg.url}

            # Empty/incomplete language (or skill/domain) rows are required
            # selects — HTML5 then blocks submit with no POST. Djinni sometimes
            # adds a blank language row without a level; that silently breaks
            # salary updates until the row is removed.
            stripped = await _strip_incomplete_profile_rows(pg)
            if stripped:
                widget_results["stripped_incomplete_rows"] = stripped

            submit = pg.locator(SELECTORS["profile_submit"]).first
            if await submit.count() == 0:
                submit = pg.get_by_role(
                    "button",
                    name=re.compile(r"оновити профіль|зберегти|update profile", re.I),
                ).first
            if await submit.count() == 0:
                return {"error": "Save button not found.",
                        "hint": "Update SELECTORS['profile_submit'].", "url": pg.url}

            invalid = await pg.evaluate(
                """
                () => {
                  const form = document.querySelector('#js-profile-form');
                  if (!form || form.checkValidity()) return null;
                  const el = form.querySelector(':invalid');
                  return el
                    ? {name: el.name || el.id, message: el.validationMessage}
                    : {name: null, message: 'form invalid'};
                }
                """
            )
            if invalid:
                shot = await _screenshot(pg, "profile_invalid")
                return {
                    "error": "Profile form blocked by HTML5 validation; not saved.",
                    "invalid": invalid,
                    "widgets": widget_results,
                    "url": pg.url,
                    "screenshot": shot,
                }

            posted = False
            try:
                async with pg.expect_response(
                    lambda r: "/my/profile" in r.url
                    and r.request.method == "POST"
                    and r.status < 400,
                    timeout=15000,
                ):
                    await submit.click()
                posted = True
            except PWTimeout:
                await submit.click()
                with contextlib.suppress(PWTimeout):
                    await pg.wait_for_load_state("networkidle", timeout=10000)
            await pg.wait_for_timeout(1000)

            after = await pg.evaluate(_PROFILE_STATE_JS)
            shot = await _screenshot(pg, "profile_saved")
            body = (await pg.inner_text("body")).lower()
            ok = bool(
                re.search(
                    r"профіль оновл|профіль збереж|profile (saved|updated)",
                    body,
                    re.I,
                )
            )
            if salary_min and str((after or {}).get("salary_min") or "") != str(salary_min):
                ok = False
            return {"status": "submitted" if ok else "unknown",
                    "changed": changed, "widgets": widget_results,
                    "posted": posted,
                    "before": before, "after": after,
                    "screenshot": shot, "url": pg.url}


# --------------------------------------------------------------------------- #
# Debug helpers (to fix selectors when Djinni changes its markup)
# --------------------------------------------------------------------------- #
async def _screenshot(page: Page, label: str) -> str:
    DEBUG_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = DEBUG_DIR / f"{ts}_{label}.png"
    with contextlib.suppress(Exception):
        await page.screenshot(path=str(path), full_page=True)
    return str(path)


@mcp.tool()
async def debug_screenshot(url: str = "") -> dict[str, Any]:
    """Open a page (or the Djinni home) and save a full-page screenshot.

    Args:
        url: page to open; empty = Djinni home.
    """
    target = _abs(url) if url else BASE_URL
    async with _LOCK:
        async with browser() as (_ctx, pg):
            await _goto(pg, target)
            shot = await _screenshot(pg, "debug")
            return {"url": pg.url, "logged_in": await _is_logged_in(pg),
                    "screenshot": shot}


@mcp.tool()
async def debug_dump_html(url: str = "", selector: str = "") -> dict[str, Any]:
    """Dump raw HTML of a page (or a selector) to help update SELECTORS.

    Args:
        url: page to open; empty = Djinni home.
        selector: optional CSS selector; empty = whole document.
    """
    target = _abs(url) if url else BASE_URL
    async with _LOCK:
        async with browser() as (_ctx, pg):
            await _goto(pg, target)
            if selector:
                loc = pg.locator(selector)
                if await loc.count() == 0:
                    return {"url": pg.url, "error": f"selector matched nothing: {selector}"}
                html = await loc.first.inner_html()
            else:
                html = await pg.content()
            return {"url": pg.url, "length": len(html), "html": html[:20000]}


if __name__ == "__main__":
    mcp.run()
