"""Local candidate profile. Real values live in gitignored env files."""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path

log = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent

_DISABLE_DOTENV = os.environ.get("CANDIDATE_DISABLE_DOTENV", "").strip().lower() in {
    "1",
    "true",
    "yes",
}

if not _DISABLE_DOTENV:
    try:
        from dotenv import load_dotenv

        load_dotenv(ROOT / ".env")
        load_dotenv(ROOT / "local" / "profile.env", override=True)
    except Exception:
        pass


def _s(name: str, default: str = "") -> str:
    return (os.environ.get(name) or default).strip()


def _i(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    return int(raw)


def _csv(name: str) -> tuple[str, ...]:
    raw = _s(name)
    if not raw:
        return ()
    return tuple(part.strip() for part in raw.split(",") if part.strip())


def _pairs(name: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for part in _csv(name):
        if "=" not in part:
            continue
        key, value = part.split("=", 1)
        key, value = key.strip().lower(), value.strip()
        if key and value:
            out[key] = value
    return out


NAME = _s("CANDIDATE_NAME", "Candidate")
SHORT_NAME = _s("CANDIDATE_SHORT_NAME", "Alex")
LAST_NAME = _s("CANDIDATE_LAST_NAME", NAME.split()[-1] if NAME else "")
SELF_NAMES = _csv("CANDIDATE_SELF_NAMES") or (NAME, SHORT_NAME)
TITLE = _s("CANDIDATE_TITLE", "Full-Stack Developer")
EMAIL = _s("CANDIDATE_EMAIL", "")
PHONE = _s("CANDIDATE_PHONE", "")
LINKEDIN = _s("CANDIDATE_LINKEDIN", "")
TELEGRAM = _s("CANDIDATE_TELEGRAM", "")
TELEGRAM_HANDLE = _s("CANDIDATE_TELEGRAM_HANDLE", "")
CITY = _s("CANDIDATE_CITY", "City")
COUNTRY = _s("CANDIDATE_COUNTRY", "")
LOCATION_LINE = _s(
    "CANDIDATE_LOCATION",
    f"{CITY}, remote".strip(", "),
)
CONTRACT = _s("CANDIDATE_CONTRACT", "contractor")
NOTICE = _s("CANDIDATE_NOTICE", "1–2 weeks")
SALARY_NET = _i("CANDIDATE_SALARY_NET", 0)
SALARY_FLOOR = _i("CANDIDATE_SALARY_FLOOR", 0)
ENGLISH = _s("CANDIDATE_ENGLISH", "B2")
YEARS = _s("CANDIDATE_YEARS", "")
FE_BE_SPLIT = _s("CANDIDATE_FE_BE_SPLIT", "")
CV_PATH = Path(_s("CANDIDATE_CV", str(ROOT / "local" / "cv.pdf")))
EXPERIENCE_PATH = Path(
    _s(
        "CANDIDATE_EXPERIENCE",
        str(ROOT / ".cursor" / "skills" / "rewrite-cv" / "experience.md"),
    )
)

SKIP_COMPANIES = frozenset(x.lower() for x in _csv("CANDIDATE_SKIP_COMPANIES"))
SKIP_CONTACTS = frozenset(x.lower() for x in _csv("CANDIDATE_SKIP_CONTACTS"))
SKIP_STACK = tuple(x.lower() for x in _csv("CANDIDATE_SKIP_STACK"))
STACK_PATTERN = _s(
    "CANDIDATE_STACK_PATTERN",
    r"\b(full[\s-]?stack|backend|frontend|typescript|javascript)\b",
)
STACK_RE = re.compile(STACK_PATTERN, re.I)

COVER_OPENING = _s("CANDIDATE_COVER_OPENING", f"Hi, I'm a {TITLE}.")
COVER_CORE = _s(
    "CANDIDATE_COVER_CORE",
    "I have commercial experience building APIs and products.",
)
COVER_CLOSING = _s(
    "CANDIDATE_COVER_CLOSING",
    f"Happy to discuss how I can contribute.\n\nBest,\n{SHORT_NAME}",
).replace("\\n", "\n")
COVER_HOOK_AI = _s("CANDIDATE_COVER_HOOK_AI", "")
COVER_HOOK_HEALTHCARE = _s("CANDIDATE_COVER_HOOK_HEALTHCARE", "")
COVER_HOOK_ENERGY = _s("CANDIDATE_COVER_HOOK_ENERGY", "")
COVER_HOOK_FINTECH = _s("CANDIDATE_COVER_HOOK_FINTECH", "")

ABOUT_UK = _s("CANDIDATE_ABOUT_UK", "")
ABOUT_EN = _s("CANDIDATE_ABOUT_EN", "")
SPECIALIZATIONS = list(_csv("CANDIDATE_SPECIALIZATIONS"))
SKILLS = list(_csv("CANDIDATE_SKILLS"))
SEARCH_KEYWORDS = list(_csv("CANDIDATE_SEARCH_KEYWORDS"))
DOU_CATEGORIES = list(_csv("CANDIDATE_DOU_CATEGORIES"))
STACK_YEARS = _pairs("CANDIDATE_STACK_YEARS")
NDA_NOTE = _s("CANDIDATE_NDA_NOTE", "")


def years_for(question: str) -> str:
    q = (question or "").lower()
    for key, years in STACK_YEARS.items():
        if key and key in q:
            return years
    return YEARS


if NAME == "Candidate" and not _DISABLE_DOTENV:
    log.warning(
        "CANDIDATE_NAME is the placeholder; copy local/profile.env.example "
        "to local/profile.env before sending replies."
    )
