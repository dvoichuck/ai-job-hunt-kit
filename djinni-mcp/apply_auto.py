#!/usr/bin/env python3
"""Find new Djinni jobs and apply with templated cover letters."""
from __future__ import annotations

import asyncio
import json
import re
import sys
from pathlib import Path

import server as s
from cover_letters import build_letter, clean_title, extract_company_from_job_text

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))
import candidate as C  # noqa: E402
import filters  # noqa: E402

SKIP_TITLE = re.compile(
    r"Laravel|PHP\b|\.Net|\.NET|Java\b|Go/|Golang|"
    r"Blazor|QA\b|DevOps|React Native|Data Engineer|Product Manager|CMO|SEO|"
    r"Marketing|SDET|Test Engineer|Animator|Artist|Analyst\b|Mobile\b|"
    r"iOS\b|Android\b|Sales|Recruiter|Designer|Integration Engineer|"
    r"Pixi\.?js|WebGPU|Three\.?js|Unity|WebGL|3D Visualization",
    re.I,
)
MATCH = C.STACK_RE

GENERIC_ANSWERS = [
    (r"node\.?js|nestjs", C.YEARS or "0"),
    (r"react", C.YEARS or "0"),
    (r"typescript", C.YEARS or "0"),
    (r"python", "0"),
    (r"співвідношен|fe/be|frontend.*backend|бекенд|backend vs frontend|focus \(%",
     C.FE_BE_SPLIT),
    (r"english|англій", C.ENGLISH),
    (r"salary|зарплат|очікуван", f"${C.SALARY_NET} net/month." if C.SALARY_NET else ""),
    (r"start|коли|when can|when would", f"Available within {C.NOTICE}."),
    (r"fop|фоп|b2b|contractor|контракт|pe|sole prop", f"Yes — I work via {C.CONTRACT}."),
    (r"located|локац|where are you|країн|місцезнаход", C.LOCATION_LINE),
    (r"llm|genai|ai tool|cursor|claude|chatgpt|prompt", C.COVER_HOOK_AI),
    (r"комерційн.*node|commercial.*node|досвід.*node", f"{C.YEARS} years commercial {C.TITLE}."),
    (r"комерційн.*react|commercial.*react|досвід.*react", f"{C.YEARS} years commercial {C.TITLE}."),
    (r"terraform", None),  # radio: no
    (r"devops|infrastructure-heavy", None),  # radio: yes handled below
    (r"2 years|more than 2|більше 2", None),  # radio: yes
    (r"high-load|real-time", None),  # radio: yes
    (r"frontend architecture", None),  # radio: yes
    (r"b2 or higher|b2\+", None),  # radio: yes
    (r"c1 english", None),  # radio: no
    (r"5\+.*node|node.*5\+", None),  # radio: yes
]

QUERIES = [(kw, 1) for kw in C.SEARCH_KEYWORDS]


async def _fill_screening(pg) -> list[str]:
    filled = []
    fields = await pg.evaluate(
        """
        () => Array.from(document.querySelectorAll(
          'textarea[name^=answer_], input[name^=answer_][type=number], input[name^=answer_][type=radio]'
        )).map(e => {
          const grp = e.closest('.form-group,.mb-3,.question,fieldset') || e.parentElement;
          const q = grp ? (grp.innerText || '').trim().slice(0, 220) : '';
          return {tag: e.tagName, type: e.type, id: e.id, name: e.name, value: e.value, q};
        })
        """
    )
    for f in fields:
        q = (f.get("q") or "").lower()
        fid = f.get("id") or ""
        if f.get("type") == "radio":
            if re.search(r"c1 english", q) and fid.endswith("_no"):
                await pg.locator(f"#{fid}").check()
                filled.append("C1:no")
            elif re.search(r"terraform", q) and fid.endswith("_no"):
                await pg.locator(f"#{fid}").check()
                filled.append("tf:no")
            elif re.search(r"5\+.*node|node.*5\+|commercial node", q) and (
                fid.endswith("_yes") or f.get("value") == "1"
            ):
                await pg.locator(f"#{fid}").check()
                filled.append("node5+:yes")
            elif re.search(r"2 year|більше 2|more than 2", q) and fid.endswith("_yes"):
                await pg.locator(f"#{fid}").check()
                filled.append("2y:yes")
            elif re.search(r"devops|infrastructure-heavy", q) and fid.endswith("_yes"):
                await pg.locator(f"#{fid}").check()
                filled.append("devops:yes")
            elif re.search(r"high-load|real-time", q) and fid.endswith("_yes"):
                await pg.locator(f"#{fid}").check()
                filled.append("hl:yes")
            elif re.search(r"frontend architecture", q) and fid.endswith("_yes"):
                await pg.locator(f"#{fid}").check()
                filled.append("fe-arch:yes")
            elif re.search(r"b2 or higher|b2\+|рівень.*b2", q) and fid.endswith("_yes"):
                await pg.locator(f"#{fid}").check()
                filled.append("b2:yes")
            continue
        if f.get("type") == "number" and fid:
            val = None
            val = C.years_for(q) or None
            if val and not (await pg.locator(f"#{fid}").input_value()).strip():
                await pg.locator(f"#{fid}").fill(val)
                filled.append(f"num:{fid}={val}")
            continue
        if f.get("tag") != "TEXTAREA" or not fid.startswith("answer"):
            continue
        for pat, ans in GENERIC_ANSWERS:
            if ans is None:
                continue
            if re.search(pat, q, re.I):
                loc = pg.locator(f"#{fid}")
                if await loc.count() and not (await loc.input_value()).strip():
                    await loc.fill(ans if isinstance(ans, str) else str(ans))
                    filled.append(fid)
                break
    return filled


async def _job_company(pg, job_text: str) -> str:
    company = await pg.evaluate(
        """
        () => {
          const h1 = document.querySelector('h1');
          if (!h1) return '';
          const block = h1.closest('div') || h1.parentElement;
          if (!block) return '';
          const links = [...block.querySelectorAll('a[href*="/jobs/company/"], a[href*="/companies/"]')];
          if (links.length) return (links[0].innerText || '').trim();
          const lines = (block.innerText || '').split('\\n').map(s => s.trim()).filter(Boolean);
          const hi = lines.indexOf((h1.innerText || '').trim());
          for (let i = hi + 1; i < Math.min(hi + 4, lines.length); i++) {
            const ln = lines[i];
            if (/^\\$+$/.test(ln) || /підписатись|зберегти|сховати/i.test(ln)) continue;
            if (ln.length >= 2 && ln.length <= 50 && !/developer|engineer/i.test(ln)) return ln;
          }
          return '';
        }
        """
    )
    return (company or extract_company_from_job_text(job_text)).strip()


async def apply_job(jid: str, title: str, job_text: str) -> dict:
    async with s.browser() as (_ctx, pg):
        await s._goto(pg, s._job_url(jid))
        company = await _job_company(pg, job_text)
        letter = build_letter(role=title, company=company, job_text=job_text)
        if re.search(r"already applied|ви вже відгук", (await pg.inner_text("body")).lower()):
            return {"jid": jid, "status": "skipped", "reason": "applied", "company": company}

        for name in ("Відгукнутися", "Відгукнутись", "Apply", "Respond"):
            btn = pg.get_by_role("button", name=re.compile(name, re.I))
            if await btn.count():
                with __import__("contextlib").suppress(Exception):
                    await btn.first.click()
                    await pg.wait_for_timeout(900)
                break

        screening = await _fill_screening(pg)
        ta = pg.locator("#message").first
        if await ta.count() == 0:
            return {"jid": jid, "status": "error", "reason": "no form"}

        await ta.fill(letter)
        for name in ("Надіслати відгук", "Надіслати", "Відправити", "Send"):
            btn = pg.get_by_role("button", name=re.compile(name, re.I))
            if await btn.count():
                await btn.first.click()
                break
        await pg.wait_for_timeout(2500)

        await s._goto(pg, s._job_url(jid))
        ok = await pg.evaluate(
            "() => /already applied|ви вже відгук/i.test(document.body.innerText)"
        )
        return {
            "jid": jid,
            "title": clean_title(title),
            "company": company,
            "status": "submitted" if ok else "unknown",
            "verified": ok,
            "screening": screening,
            "letter_preview": letter[:120] + "...",
        }


async def collect_candidates(limit: int = 40) -> list[dict]:
    seen: set[str] = set()
    out: list[dict] = []
    for kw, page in QUERIES:
        r = await s.search_jobs(
            keywords=kw, page=page,
            exp_level=f"{C.YEARS}y" if C.YEARS.isdigit() else (C.YEARS or ""),
            employment="remote",
            salary_min=C.SALARY_FLOOR or C.SALARY_NET or 0,
            limit=25,
        )
        for j in r.get("jobs", []):
            jid = j["id"]
            if jid in seen:
                continue
            seen.add(jid)
            if SKIP_TITLE.search(j["title"]):
                continue
            if filters.should_skip(j["title"], j.get("company") or ""):
                continue
            if not MATCH.search(j["title"]):
                continue
            d = await s.get_job(jid)
            if d.get("already_applied") or not d.get("apply_available"):
                continue
            out.append({
                "id": jid,
                "title": d.get("title") or j["title"],
                "text": d.get("text") or "",
            })
            if len(out) >= limit:
                return out
    return out


async def main() -> None:
    print("Collecting candidates...")
    candidates = await collect_candidates(25)
    print(f"Found {len(candidates)} new applicable jobs")

    results = []
    ok_count = 0
    for c in candidates:
        if ok_count >= 10:
            break
        print(f"Applying {c['id']} {clean_title(c['title'])}...")
        r = await apply_job(c["id"], c["title"], c["text"])
        results.append(r)
        print(r.get("status"), r.get("title"))
        if r.get("verified"):
            ok_count += 1

    open(str(_ROOT / "djinni-mcp" / "debug" / "apply_auto.json"), "w").write(
        json.dumps(results, ensure_ascii=False, indent=2)
    )
    print(f"DONE: {ok_count} submitted")


if __name__ == "__main__":
    asyncio.run(main())
