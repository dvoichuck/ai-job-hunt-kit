"""Cover-letter templates for Djinni applications.

Djinni job cards often embed salary markers ($, $$, $$$, $$$$) and company names
in scraped titles — always run titles through `clean_title()` before use.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Literal

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import candidate as C

Variant = Literal["backend", "fullstack", "ai", "generic"]

# Djinni salary-tier markers and common UI noise in scraped titles.
_NOISE = re.compile(
    r"\$\$?\$?\$?|🔥|ШВИДКО ВІДПОВІДАЄ|🇺🇦.*|Продуктова компанія|"
    r"Аутстафінгова компанія|Стартап",
    re.I,
)

_CORE = C.COVER_CORE

_HOOKS = {
    "ai": C.COVER_HOOK_AI or None,
    "healthcare": C.COVER_HOOK_HEALTHCARE or None,
    "energy": C.COVER_HOOK_ENERGY or None,
    "fintech": C.COVER_HOOK_FINTECH or None,
    "gambling": None,
}

_OPENING = C.COVER_OPENING

_BODIES = {
    "backend": _CORE,
    "fullstack": _CORE,
    "ai": (_CORE + " " + _HOOKS["ai"]).strip() if _HOOKS["ai"] else _CORE,
    "generic": _CORE,
}

_CLOSING = C.COVER_CLOSING


def clean_title(raw: str) -> str:
    """Strip Djinni noise from a job title string (may be multiline from search cards)."""
    if not raw:
        return "this role"
    # Search cards glue title + company + $$$$ — take first meaningful line.
    line = raw.strip().split("\n")[0].strip()
    line = _NOISE.sub("", line)
    line = re.sub(r"\s{2,}", " ", line).strip(" -–—|")
    return line or "this role"


_COMPANY_SKIP = re.compile(
    r"^(online|djinni|пропозиції|вакансії|зарплати|remote|fullstack|backend|"
    r"розробка|data and analytics|всі вакансії)$",
    re.I,
)


def extract_company(raw: str) -> str:
    """Best-effort company name from multiline search-card title."""
    parts = [p.strip() for p in raw.strip().split("\n") if p.strip()]
    for p in parts[1:]:
        if _NOISE.search(p) or p.startswith("$"):
            continue
        if _COMPANY_SKIP.match(p.strip()):
            continue
        if len(p) < 60 and not re.search(r"developer|engineer|full", p, re.I):
            cleaned = _NOISE.sub("", p).strip()
            if cleaned and not _COMPANY_SKIP.match(cleaned):
                return cleaned
    return ""


def extract_company_from_job_text(job_text: str) -> str:
    """Parse company name from a Djinni job page innerText blob."""
    if not job_text:
        return ""
    lines = [ln.strip() for ln in job_text.split("\n") if ln.strip()]
    # Pattern: h1 title, then company name on the next short line before actions.
    for i, ln in enumerate(lines):
        if _NOISE.search(ln) and ln.count("$") >= 2:
            continue
        if _COMPANY_SKIP.match(ln):
            continue
        if re.search(r"developer|engineer|full.?stack|backend|lead\b", ln, re.I):
            for nxt in lines[i + 1 : i + 5]:
                if _NOISE.search(nxt) and "$" in nxt:
                    continue
                if _COMPANY_SKIP.match(nxt):
                    continue
                if re.search(
                    r"підписатись|зберегти|сховати|about the|про компанію|"
                    r"опубліковано|віддалена|remote|product company|продуктова",
                    nxt,
                    re.I,
                ):
                    break
                if 2 <= len(nxt) <= 50 and not re.search(
                    r"developer|engineer|full.?stack|backend|відгук|apply",
                    nxt,
                    re.I,
                ):
                    cleaned = _NOISE.sub("", nxt).strip()
                    if cleaned and not _COMPANY_SKIP.match(cleaned):
                        return cleaned
            break
    return ""


def detect_variant(title: str, job_text: str = "") -> Variant:
    """Pick a letter variant from title + job description keywords."""
    blob = f"{title} {job_text}".lower()
    if re.search(r"\b(llm|langgraph|langchain|genai|rag|ai agent|openai)\b", blob):
        return "ai"
    if re.search(r"\bfull.?stack|fullstack\b", blob):
        return "fullstack"
    if re.search(r"\bbackend|back-end|back end\b", blob) and not re.search(
        r"full.?stack", blob
    ):
        return "backend"
    return "generic"


def detect_hooks(job_text: str) -> list[str]:
    """Return matching experience hooks for optional second paragraph."""
    blob = job_text.lower()
    found = []
    for key in ("ai", "healthcare", "energy", "fintech"):
        if key == "ai":
            if re.search(r"\b(llm|langgraph|genai|rag|ai)\b", blob):
                found.append(key)
        elif key in blob:
            found.append(key)
    return found


def build_letter(
    *,
    role: str,
    company: str = "",
    variant: Variant | None = None,
    job_text: str = "",
    extra_hook: str = "",
) -> str:
    """Build a cover letter from templates. All facts stay in reusable blocks — no invention."""
    role_clean = clean_title(role)
    var = variant or detect_variant(role_clean, job_text)
    body = _BODIES[var]

    hooks = detect_hooks(job_text)
    hook_lines = []
    for h in hooks[:2]:
        line = _HOOKS.get(h)
        if line and line not in body:
            hook_lines.append(line)
    if extra_hook:
        hook_lines.append(extra_hook.strip())

    parts = [_OPENING, "", body]
    if hook_lines:
        parts.extend(["", " ".join(hook_lines)])
    parts.extend(["", _CLOSING])
    return "\n".join(parts)


def build_from_search_card(title_field: str, job_text: str = "") -> str:
    """Convenience: parse a search-result title blob and build a letter."""
    return build_letter(
        role=title_field,
        company=extract_company(title_field),
        job_text=job_text,
    )
