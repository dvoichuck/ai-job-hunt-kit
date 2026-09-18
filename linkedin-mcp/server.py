"""
LinkedIn MCP server (inbox + profile pack).

Exposes LinkedIn as MCP tools driven by Playwright:
  - login / session_status
  - list_inbox / read_thread / send_message
  - apply_profile_pack and per-section profile updates from local/linkedin.pack.json
  - upload_cv           : upload CANDIDATE_CV to saved resumes / application settings
  - debug_screenshot / debug_dump_html

LinkedIn has no public messaging API for candidates, so this uses YOUR
logged-in session (persistent Chromium profile). Prefer the DOM (acts as you
in the messaging UI). Voyager calls from the same session are a fallback for
listing/reading if the markup shifts.

Use it responsibly and within LinkedIn's Terms of Service — no mass spam.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urljoin, unquote

from mcp.server.fastmcp import FastMCP
from playwright.async_api import (
    BrowserContext,
    Page,
    TimeoutError as PWTimeout,
    async_playwright,
)

HERE = Path(__file__).resolve().parent
BASE_URL = "https://www.linkedin.com"
MESSAGING_URL = f"{BASE_URL}/messaging/"

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
    "conversation_list": (
        "ul.msg-conversations-container__conversations-list, "
        ".msg-conversations-container__conversations-list"
    ),
    "conversation_item": (
        "li.msg-conversation-listitem, .msg-conversation-listitem, "
        ".msg-conversation-card"
    ),
    "composer": (
        "div.msg-form__contenteditable[contenteditable='true'], "
        ".msg-form__contenteditable, "
        "div[contenteditable='true'][role='textbox']"
    ),
    "send_button": (
        "button.msg-form__send-button, "
        "button[type='submit'].msg-form__send-btn, "
        "button[aria-label*='Send' i], "
        "button[aria-label*='Надіслати' i], "
        "button[aria-label*='Отправить' i]"
    ),
}

from browser import LOCK as _LOCK
from browser import browser as _pack_browser
import profile as li_profile
import upload_cv as li_upload

mcp = FastMCP("linkedin")


async def _guarded(coro, timeout: int = 300) -> Any:
    try:
        return await asyncio.wait_for(coro, timeout=timeout)
    except asyncio.TimeoutError:
        return {"error": f"timed out after {timeout}s", "hint": "check debug/ screenshots"}


# --------------------------------------------------------------------------- #
# Browser
# --------------------------------------------------------------------------- #
@contextlib.asynccontextmanager
async def browser(headless: Optional[bool] = None):
    PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    for name in ("SingletonLock", "SingletonCookie", "SingletonSocket"):
        with contextlib.suppress(Exception):
            (PROFILE_DIR / name).unlink()
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
        # System Chrome is less often challenged by LinkedIn than bundled Chromium.
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


async def _goto(page: Page, url: str) -> None:
    await page.goto(url, wait_until="domcontentloaded")
    with contextlib.suppress(PWTimeout):
        await page.wait_for_load_state("networkidle", timeout=10000)


async def _is_logged_in(page: Page) -> bool:
    url = (page.url or "").lower()
    if any(x in url for x in ("/login", "/checkpoint", "/uas/login", "/authwall")):
        return False
    try:
        if await page.locator(SELECTORS["login_form"]).count() > 0:
            return False
        return await page.locator(SELECTORS["logged_in"]).count() > 0
    except Exception:
        return False


def _abs(url: str) -> str:
    return url if url.startswith("http") else urljoin(BASE_URL, url)


def _thread_id(thread: str) -> str:
    """Accept a thread id, /messaging/thread/<id>/ path, or full URL."""
    thread = unquote(str(thread).strip())
    m = re.search(r"/messaging/thread/([^/?#]+)", thread)
    if m:
        return m.group(1)
    return thread.strip("/")


def _thread_url(thread: str) -> str:
    tid = _thread_id(thread)
    if tid.startswith("http"):
        return tid
    return f"{BASE_URL}/messaging/thread/{tid}/"


async def _screenshot(page: Page, label: str) -> str:
    DEBUG_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = DEBUG_DIR / f"{ts}_{label}.png"
    with contextlib.suppress(Exception):
        await page.screenshot(path=str(path), full_page=True)
    return str(path)


async def _dismiss_noise(page: Page) -> None:
    """Close cookie / premium / messaging-overlay prompts if they block the UI."""
    for name in (
        "Accept",
        "Allow",
        "Got it",
        "Not now",
        "Skip",
        "Dismiss",
        "No thanks",
        "Accept cookies",
    ):
        loc = page.get_by_role("button", name=re.compile(rf"^{re.escape(name)}$", re.I))
        if await loc.count() > 0:
            with contextlib.suppress(Exception):
                await loc.first.click(timeout=1500)
                await page.wait_for_timeout(300)


EXTRACT_CONVERSATIONS_JS = """
() => {
  const out = [];
  const seen = new Set();
  const cards = document.querySelectorAll(
    'li.msg-conversation-listitem, .msg-conversation-listitem'
  );
  for (const c of cards) {
    const a = c.querySelector('a[href*="/messaging/thread/"]');
    const href = a ? (a.href || a.getAttribute('href') || '') : '';
    const m = href.match(/thread\\/([^/?#]+)/);
    const nameEl = c.querySelector(
      '.msg-conversation-listitem__participant-names, ' +
      '.msg-conversation-card__participant-names, h3'
    );
    const name = ((nameEl && nameEl.innerText) || '').trim();
    if (!name || seen.has(name)) continue;
    seen.add(name);
    const snipEl = c.querySelector('.msg-conversation-card__message-snippet');
    const timeEl = c.querySelector('time, .msg-conversation-listitem__time-stamp');
    const unread = c.classList.contains('msg-conversation-listitem--unread')
      || !!c.querySelector('.notification-badge, .msg-conversation-card__unread-count');
    const id = m ? decodeURIComponent(m[1]) : '';
    out.push({
      id: id || name,
      name,
      preview: ((snipEl && snipEl.innerText) || '').trim().slice(0, 280),
      time: ((timeEl && timeEl.innerText) || '').trim(),
      unread,
      url: href.startsWith('http') ? href : '',
    });
  }
  return out;
}
"""

EXTRACT_MESSAGES_JS = """
() => {
  const out = [];
  const events = document.querySelectorAll(
    '.msg-s-event-listitem, li.msg-s-message-list__event, .msg-s-message-group'
  );
  for (const ev of events) {
    const nameEl = ev.querySelector(
      '.msg-s-message-group__name, .msg-s-event-listitem__name, .msg-s-message-group__profile-link'
    );
    const timeEl = ev.querySelector('time, .msg-s-message-group__timestamp');
    const bodyEl = ev.querySelector(
      '.msg-s-event-listitem__body, .msg-s-event__content, p, .msg-s-message-group__message'
    );
    const mine = ev.classList.contains('msg-s-event-listitem--other') === false
      && (ev.classList.contains('msg-s-event-listitem--self')
          || !!ev.querySelector('.msg-s-event-listitem--other') === false
             && !!ev.closest('.msg-s-message-group--self'));
    const text = ((bodyEl && bodyEl.innerText) || ev.innerText || '').trim();
    if (!text) continue;
    out.push({
      from: ((nameEl && nameEl.innerText) || (mine ? 'You' : '')).trim(),
      time: ((timeEl && timeEl.innerText) || '').trim(),
      text: text.slice(0, 4000),
    });
  }
  // Fallback: whole transcript pane.
  if (!out.length) {
    const pane = document.querySelector(
      '.msg-s-message-list-container, .msg-s-message-list, .msg-thread'
    );
    if (pane) {
      out.push({from: '', time: '', text: (pane.innerText || '').trim().slice(0, 8000)});
    }
  }
  return out;
}
"""


def _attr_text(obj: Any) -> str:
    if obj is None:
        return ""
    if isinstance(obj, str):
        return obj
    if isinstance(obj, dict):
        t = obj.get("text")
        if isinstance(t, str):
            return t
        body = obj.get("body")
        if isinstance(body, dict):
            return _attr_text(body)
    return ""


def _looks_like_thread_id(thread: str) -> bool:
    t = unquote(str(thread).strip())
    if t.startswith("http") or "/messaging/thread/" in t:
        return True
    return bool(re.match(r"^2-[A-Za-z0-9=+\-_%]+$", t))


def _listen_json(page: Page, url_substr: str) -> list[dict[str, Any]]:
    bucket: list[dict[str, Any]] = []

    async def on_resp(resp: Any) -> None:
        if url_substr in (resp.url or "") and resp.status == 200:
            with contextlib.suppress(Exception):
                bucket.append(await resp.json())

    page.on("response", on_resp)
    return bucket


async def _wait_bucket(bucket: list, timeout_ms: int = 8000) -> None:
    steps = max(1, timeout_ms // 250)
    for _ in range(steps):
        if bucket:
            return
        await asyncio.sleep(0.25)


def _parse_gql_conversations(payload: dict[str, Any]) -> list[dict[str, Any]]:
    data = payload.get("data") or payload
    token = data.get("messengerConversationsBySyncToken") or {}
    elements = token.get("elements") or []
    out: list[dict[str, Any]] = []
    for el in elements:
        if not isinstance(el, dict):
            continue
        backend = str(el.get("backendUrn") or "")
        tid = ""
        if "messagingThread:" in backend:
            tid = backend.split("messagingThread:", 1)[-1]
        if not tid:
            m = re.search(r",(2-[^)]+)\)", str(el.get("entityUrn") or ""))
            tid = m.group(1) if m else ""
        if not tid:
            continue
        self_urn = ""
        mself = re.search(r"fsd_profile:([^,)]+)", str(el.get("entityUrn") or ""))
        if mself:
            self_urn = mself.group(1)
        names: list[str] = []
        for p in el.get("conversationParticipants") or []:
            if not isinstance(p, dict):
                continue
            hid = str(p.get("hostIdentityUrn") or "")
            if self_urn and self_urn in hid:
                continue
            member = ((p.get("participantType") or {}).get("member") or {})
            names.append(
                f"{_attr_text(member.get('firstName'))} {_attr_text(member.get('lastName'))}".strip()
            )
        msgs = (el.get("messages") or {}).get("elements") or []
        preview = ""
        if isinstance(msgs, list) and msgs:
            preview = _attr_text(msgs[-1]).strip()
        url = el.get("conversationUrl") or f"{BASE_URL}/messaging/thread/{tid}/"
        out.append({
            "id": tid,
            "name": ", ".join(n for n in names if n) or tid,
            "preview": preview[:280],
            "unread": (not el.get("read")) or int(el.get("unreadCount") or 0) > 0,
            "url": url.split("#")[0],
        })
    seen: set[str] = set()
    uniq = []
    for c in out:
        if c["id"] in seen:
            continue
        seen.add(c["id"])
        uniq.append(c)
    return uniq


def _parse_gql_messages(payload: dict[str, Any]) -> list[dict[str, Any]]:
    data = payload.get("data") or payload
    token = data.get("messengerMessagesBySyncToken") or {}
    elements = token.get("elements") or []
    out = []
    for el in elements:
        if not isinstance(el, dict):
            continue
        text = _attr_text(el.get("body")).strip()
        if not text:
            continue
        sender = el.get("sender") or {}
        member = ((sender.get("participantType") or {}).get("member") or {})
        name = (
            f"{_attr_text(member.get('firstName'))} {_attr_text(member.get('lastName'))}".strip()
        )
        out.append({
            "from": name,
            "time": el.get("deliveredAt") or "",
            "text": text[:4000],
        })
    return out


async def _open_thread(page: Page, thread: str) -> None:
    """Open a conversation by id/URL, or click the inbox card by participant name."""
    if _looks_like_thread_id(thread):
        await _goto(page, _thread_url(thread))
        return
    await _goto(page, MESSAGING_URL)
    await _dismiss_noise(page)
    with contextlib.suppress(PWTimeout):
        await page.wait_for_selector(SELECTORS["conversation_item"], timeout=12000)
    name = thread.strip()
    cards = page.locator(SELECTORS["conversation_item"])
    n = await cards.count()
    for i in range(n):
        card = cards.nth(i)
        label = (await card.inner_text()).strip()
        if name.lower() in label.lower():
            clickable = card.locator(".msg-conversation-listitem__link").first
            target = clickable if await clickable.count() else card
            await target.click()
            await page.wait_for_timeout(800)
            return
    raise RuntimeError(f"Conversation not found: {thread}")


async def _voyager(page: Page, path: str, method: str = "GET", body: Any = None) -> dict[str, Any]:
    """Call LinkedIn Voyager from the logged-in page (same cookies + CSRF)."""
    return await page.evaluate(
        """
        async ({path, method, body}) => {
          const m = document.cookie.match(/JSESSIONID="?([^";]+)/);
          if (!m) return {error: 'no_csrf'};
          const headers = {
            'csrf-token': m[1],
            'x-restli-protocol-version': '2.0.0',
            'accept': 'application/vnd.linkedin.normalized+json+2.1',
          };
          const opts = {method, credentials: 'include', headers};
          if (body != null) {
            headers['content-type'] = 'application/json; charset=UTF-8';
            opts.body = JSON.stringify(body);
          }
          const r = await fetch('https://www.linkedin.com' + path, opts);
          let data = null;
          try { data = await r.json(); } catch (e) { data = {text: await r.text()}; }
          return {status: r.status, data};
        }
        """,
        {"path": path, "method": method, "body": body},
    )


def _parse_voyager_conversations(payload: dict[str, Any]) -> list[dict[str, Any]]:
    data = payload.get("data") or payload
    included = data.get("included") or []
    elements = []
    if isinstance(data.get("data"), dict):
        elements = data["data"].get("elements") or data["data"].get("*elements") or []
    if isinstance(data.get("elements"), list):
        elements = data["elements"]
    # Map mini profiles
    names: dict[str, str] = {}
    for item in included:
        if not isinstance(item, dict):
            continue
        urn = item.get("entityUrn") or item.get("dashEntityUrn") or ""
        fn = item.get("firstName") or ""
        ln = item.get("lastName") or ""
        if fn or item.get("name"):
            names[urn] = (item.get("name") or f"{fn} {ln}").strip()
    out = []
    src = elements if elements else included
    for conv in src:
        if not isinstance(conv, dict):
            continue
        if "events" not in conv and "unreadCount" not in conv and "*participants" not in conv:
            if conv.get("$type") and "Conversation" not in str(conv.get("$type")):
                continue
        urn = str(conv.get("entityUrn") or conv.get("backendUrn") or "")
        tid = ""
        m = re.search(r"messagingThread[:/:]([^,]+)$", urn)
        if m:
            tid = m.group(1)
        if not tid:
            tid = str(conv.get("conversationId") or conv.get("id") or "")
        if not tid:
            continue
        participants = conv.get("participants") or conv.get("*participants") or []
        pname = ""
        if isinstance(participants, list):
            for p in participants:
                if isinstance(p, str) and p in names:
                    pname = names[p]
                    break
                if isinstance(p, dict):
                    pname = (
                        p.get("name")
                        or f"{p.get('firstName','')} {p.get('lastName','')}".strip()
                    )
                    if pname:
                        break
        last = conv.get("events") or []
        preview = ""
        if isinstance(last, list) and last:
            ev = last[-1] if isinstance(last[-1], dict) else {}
            preview = (
                (ev.get("eventContent") or {})
                .get("com.linkedin.voyager.messaging.event.MessageEvent", {})
                .get("attributedBody", {})
                .get("text")
                or ev.get("body")
                or ""
            )
        out.append({
            "id": tid,
            "name": pname or tid,
            "preview": str(preview)[:280],
            "unread": int(conv.get("unreadCount") or 0) > 0,
            "url": f"{BASE_URL}/messaging/thread/{tid}/",
        })
    # unique
    seen = set()
    uniq = []
    for c in out:
        if c["id"] in seen:
            continue
        seen.add(c["id"])
        uniq.append(c)
    return uniq


# --------------------------------------------------------------------------- #
# Tools
# --------------------------------------------------------------------------- #
@mcp.tool()
async def login(wait_seconds: int = 240) -> str:
    """Open a visible browser so you can log in to LinkedIn once.

    Session is saved in the persistent profile and reused by inbox/reply tools.
    Works with email/password, Google, Apple, and any captcha/2FA, because YOU do it.

    Args:
        wait_seconds: how long to keep the window open waiting for login.
    """
    async with _LOCK:
        async with browser(headless=False) as (_ctx, page):
            await _goto(page, f"{BASE_URL}/login")
            deadline = asyncio.get_event_loop().time() + wait_seconds
            while asyncio.get_event_loop().time() < deadline:
                if await _is_logged_in(page):
                    return (
                        "Logged in successfully. Session saved — inbox and "
                        "`apply_profile_pack` can run headlessly."
                    )
                await asyncio.sleep(2)
            if await _is_logged_in(page):
                return "Logged in successfully. Session saved."
            return (
                "Timed out waiting for login. If you did log in, try `list_inbox` "
                "or re-run `login` with a bigger wait_seconds."
            )


@mcp.tool()
async def list_inbox(limit: int = 20, unread_only: bool = False) -> dict[str, Any]:
    """List LinkedIn messaging conversations.

    Args:
        limit: max conversations to return.
        unread_only: if True, only conversations marked unread.
    """
    async with _LOCK:
        async with browser() as (_ctx, pg):
            gql = _listen_json(pg, "messengerConversations")
            await _goto(pg, MESSAGING_URL)
            await _dismiss_noise(pg)
            if not await _is_logged_in(pg):
                return {
                    "error": "Not logged in. Run `login` first.",
                    "url": pg.url,
                }
            await _wait_bucket(gql, 8000)
            convos: list[dict[str, Any]] = []
            for payload in gql:
                convos.extend(_parse_gql_conversations(payload))
            if not convos:
                with contextlib.suppress(PWTimeout):
                    await pg.wait_for_selector(
                        SELECTORS["conversation_item"], timeout=12000
                    )
                convos = await pg.evaluate(EXTRACT_CONVERSATIONS_JS)
            if unread_only:
                convos = [c for c in convos if c.get("unread")]
            seen: set[str] = set()
            uniq = []
            for c in convos:
                key = str(c.get("id") or c.get("name") or "")
                if not key or key in seen:
                    continue
                seen.add(key)
                uniq.append(c)
            return {
                "url": pg.url,
                "count": len(uniq[:limit]),
                "conversations": uniq[:limit],
            }


@mcp.tool()
async def read_thread(thread: str, limit: int = 40) -> dict[str, Any]:
    """Read one LinkedIn conversation.

    Args:
        thread: thread id, /messaging/thread/<id>/ path, full URL, or participant name.
        limit: max messages to return (most recent kept if truncated).
    """
    async with _LOCK:
        async with browser() as (_ctx, pg):
            gql = _listen_json(pg, "messengerMessages")
            try:
                await _open_thread(pg, thread)
            except Exception as exc:
                return {"error": str(exc), "url": pg.url}
            await _dismiss_noise(pg)
            if not await _is_logged_in(pg):
                return {"error": "Not logged in. Run `login` first.", "url": pg.url}
            await _wait_bucket(gql, 8000)
            messages: list[dict[str, Any]] = []
            for payload in gql:
                messages.extend(_parse_gql_messages(payload))
            if not messages:
                await pg.wait_for_timeout(800)
                messages = await pg.evaluate(EXTRACT_MESSAGES_JS)
            title = ""
            with contextlib.suppress(Exception):
                title = (
                    await pg.locator(
                        ".msg-overlay-bubble-header__title, "
                        ".msg-thread__link-to-profile, h2"
                    ).first.inner_text(timeout=3000)
                ).strip()
            if len(messages) > limit:
                messages = messages[-limit:]
            return {
                "url": pg.url,
                "thread": _thread_id(pg.url) if _looks_like_thread_id(pg.url) else thread,
                "title": title,
                "count": len(messages),
                "messages": messages,
            }


@mcp.tool()
async def send_message(thread: str, text: str, confirm: bool = True) -> dict[str, Any]:
    """Send a reply in a LinkedIn conversation.

    Args:
        thread: thread id, path, full URL, or participant name.
        text: message body.
        confirm: True sends; False fills the composer only (dry run).
    """
    if not text or not text.strip():
        return {"error": "text is empty."}
    async with _LOCK:
        async with browser() as (_ctx, pg):
            try:
                await _open_thread(pg, thread)
            except Exception as exc:
                return {"error": str(exc), "url": pg.url}
            await _dismiss_noise(pg)
            if not await _is_logged_in(pg):
                return {"error": "Not logged in. Run `login` first.", "url": pg.url}
            await pg.wait_for_timeout(800)
            box = pg.locator(SELECTORS["composer"]).first
            if await box.count() == 0:
                shot = await _screenshot(pg, "msg_no_box")
                return {
                    "error": "Message composer not found.",
                    "url": pg.url,
                    "screenshot": shot,
                    "hint": "Use debug_dump_html on the thread URL.",
                }
            await box.click()
            filled = await pg.evaluate(
                """
                (text) => {
                  const el = document.querySelector(
                    'div.msg-form__contenteditable[contenteditable="true"], ' +
                    '.msg-form__contenteditable, ' +
                    'div[contenteditable="true"][role="textbox"]'
                  );
                  if (!el) return false;
                  el.focus();
                  const esc = (s) => s
                    .replace(/&/g, '&amp;')
                    .replace(/</g, '&lt;')
                    .replace(/>/g, '&gt;');
                  // LinkedIn collapses \\n inside a single <p>; one <p> per line keeps breaks.
                  el.innerHTML = text.split('\\n').map((line) => (
                    '<p>' + (esc(line) || '<br>') + '</p>'
                  )).join('');
                  el.dispatchEvent(new InputEvent('input', {
                    bubbles: true, inputType: 'insertText', data: text,
                  }));
                  el.dispatchEvent(new Event('input', {bubbles: true}));
                  return true;
                }
                """,
                text,
            )
            if not filled:
                await box.fill(text)
            # LinkedIn keeps Send disabled until a real keystroke.
            await box.type(" ", delay=20)
            await box.press("Backspace")

            if not confirm:
                shot = await _screenshot(pg, "msg_dryrun")
                return {
                    "status": "dry_run",
                    "message": "Composer filled but NOT sent (confirm=False).",
                    "url": pg.url,
                    "screenshot": shot,
                }

            sent = False
            send = pg.locator(SELECTORS["send_button"]).first
            if await send.count() == 0:
                send = pg.get_by_role(
                    "button",
                    name=re.compile(r"^(Send|Надіслати|Отправить)$", re.I),
                ).first
            if await send.count() > 0:
                with contextlib.suppress(Exception):
                    await send.click()
                    sent = True
            if not sent:
                with contextlib.suppress(Exception):
                    await box.press("Control+Enter")
                    sent = True
            await pg.wait_for_timeout(1500)
            leftover = ""
            with contextlib.suppress(Exception):
                leftover = (await box.inner_text()).strip()
            if leftover:
                sent = False
            shot = await _screenshot(pg, "msg_result")
            return {
                "status": "sent" if sent and not leftover else "unknown",
                "error": None if sent and not leftover else "Send did not leave the composer",
                "url": pg.url,
                "screenshot": shot,
            }


@mcp.tool()
async def session_status() -> dict[str, Any]:
    """Check whether the Playwright profile is logged in and return a profile snapshot."""
    async with _LOCK:
        async with _pack_browser() as (_ctx, page):
            return await _guarded(li_profile.snapshot(page), 90)


@mcp.tool()
async def apply_profile_pack() -> dict[str, Any]:
    """Fill the LinkedIn profile from local/linkedin.pack.json (or LI_PACK).

    Long. If it times out, read `pack_progress` — finished steps are already saved.
    """
    async with _LOCK:
        async with _pack_browser() as (_ctx, page):
            return await _guarded(li_profile.apply_pack(page), 900)


@mcp.tool()
async def upload_cv() -> dict[str, Any]:
    """Upload the PDF from CANDIDATE_CV to LinkedIn saved resumes / job settings."""
    sys.path.insert(0, str(HERE.parent))
    import candidate as C

    pdf = C.CV_PATH
    if not pdf.is_file():
        return {"error": f"CV not found: {pdf}. Set CANDIDATE_CV."}
    async with _LOCK:
        async with _pack_browser() as (_ctx, page):
            from browser import goto, is_logged_in, dismiss_noise

            await goto(page, f"{BASE_URL}/feed/")
            await dismiss_noise(page)
            if not await is_logged_in(page):
                return {"error": "Not logged in. Run `login` first.", "url": page.url}
            return await _guarded(li_upload.upload_cv(page, str(pdf)), 180)


@mcp.tool()
async def pack_progress() -> dict[str, Any]:
    """Read debug/progress.json — which pack steps already ran, even mid-run."""
    path = HERE / "debug" / "progress.json"
    if not path.exists():
        return {"steps": [], "note": "no run yet"}
    return {"steps": json.loads(path.read_text(encoding="utf-8"))}


@mcp.tool()
async def update_intro() -> dict[str, Any]:
    """Write name, headline, industry, location from the local pack."""
    async with _LOCK:
        async with _pack_browser() as (_ctx, page):
            return await _guarded(li_profile.update_intro(page, li_profile.load_pack()), 180)


@mcp.tool()
async def update_about() -> dict[str, Any]:
    """Write the About section from the local pack."""
    pack = li_profile.load_pack()
    async with _LOCK:
        async with _pack_browser() as (_ctx, page):
            return await _guarded(li_profile.update_about(page, pack.get("about") or ""), 180)


@mcp.tool()
async def add_experience(company: Optional[str] = None) -> dict[str, Any]:
    """Add experience entries from the local pack. Pass company to add only that job."""
    pack = li_profile.load_pack()
    jobs = pack.get("experience") or []
    if company:
        jobs = [j for j in jobs if str(j.get("company", "")).lower() == company.lower()]
        if not jobs:
            return {"error": f"No pack experience named {company}"}
    async with _LOCK:
        async with _pack_browser() as (_ctx, page):
            out = []
            for job in jobs:
                out.append(await _guarded(li_profile.add_experience(page, job), 180))
            return {"results": out}


@mcp.tool()
async def add_education() -> dict[str, Any]:
    """Add education rows from the local pack."""
    pack = li_profile.load_pack()
    async with _LOCK:
        async with _pack_browser() as (_ctx, page):
            out = []
            for edu in pack.get("education") or []:
                out.append(await _guarded(li_profile.add_education(page, edu), 180))
            return {"results": out}


@mcp.tool()
async def add_skills() -> dict[str, Any]:
    """Add skills from the local pack."""
    pack = li_profile.load_pack()
    async with _LOCK:
        async with _pack_browser() as (_ctx, page):
            return await _guarded(li_profile.add_skills(page, pack.get("skills") or []), 600)


@mcp.tool()
async def add_languages() -> dict[str, Any]:
    """Add languages from the local pack."""
    pack = li_profile.load_pack()
    async with _LOCK:
        async with _pack_browser() as (_ctx, page):
            out = []
            for lang in pack.get("languages") or []:
                out.append(
                    await _guarded(
                        li_profile.add_language(page, lang["name"], lang["proficiency"]),
                        180,
                    )
                )
            return {"results": out}


@mcp.tool()
async def set_open_to_work() -> dict[str, Any]:
    """Turn on Open to work from pack.json titles / remote."""
    pack = li_profile.load_pack()
    otw = pack.get("open_to_work") or {}
    async with _LOCK:
        async with _pack_browser() as (_ctx, page):
            return await _guarded(
                li_profile.set_open_to_work(
                    page, otw.get("titles") or [], bool(otw.get("remote", True))
                ),
                180,
            )


@mcp.tool()
async def set_custom_url() -> dict[str, Any]:
    """Set the public profile URL from pack custom_url_candidates."""
    pack = li_profile.load_pack()
    async with _LOCK:
        async with _pack_browser() as (_ctx, page):
            return await _guarded(
                li_profile.set_custom_url(page, pack.get("custom_url_candidates") or []),
                180,
            )


@mcp.tool()
async def debug_screenshot(url: str = "") -> dict[str, Any]:
    """Open a page (or LinkedIn messaging) and save a full-page screenshot."""
    target = _abs(url) if url else MESSAGING_URL
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
    """Dump raw HTML of a page (or a selector) to help update SELECTORS."""
    target = _abs(url) if url else MESSAGING_URL
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
