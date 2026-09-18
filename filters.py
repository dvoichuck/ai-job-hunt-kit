"""Vacancy skip rules. Profile-driven via candidate.py."""

from __future__ import annotations

import re
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
import candidate

SALARY_FLOOR = candidate.SALARY_FLOOR or 0

_VACANCY_SALARY_RE = re.compile(
    r"(?:до|up\s+to)\s*\$?\s*(\d{3,5})"
    r"|\$\s*(\d{3,5})\s*[–\-—]\s*\$?\s*(\d{3,5})",
    re.I,
)

SKIP_COMPANIES: frozenset[str] = candidate.SKIP_COMPANIES
SKIP_STACK: tuple[str, ...] = candidate.SKIP_STACK or (
    "solidity",
    "unity",
    "unreal",
)
_RELEVANT_STACK = candidate.STACK_RE

_SKIP_TITLE_RE = re.compile(
    r"("
    r"\b(?:cpo|cto|ceo|coo|cfo)\b|"
    r"\bchief\s+(?:product|technology|technical|executive|operating|marketing)\b|"
    r"\bhead\s+of\s+(?:product|engineering|design)\b|"
    r"\bvp\s+(?:of\s+)?(?:product|engineering)\b|"
    r"\bproduct\s+(?:owner|manager|officer)\b|"
    r"\bengineering\s+manager\b"
    r")",
    re.I,
)

_YEARS_RE = re.compile(
    r"\b([7-9]|\d{2,})\+?\s*(?:\+?\s*)?(?:years?|yrs?|рок(?:ів|и)?)\b",
    re.I,
)

_LANG_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("Java", re.compile(r"\bjava(?!script)\b", re.I)),
    ("PHP", re.compile(r"\bphp\b", re.I)),
    (".NET", re.compile(r"(?<![A-Za-z0-9_])(?:asp\.net|\.net|dotnet)\b", re.I)),
    ("C#", re.compile(r"\bc\s*#\b", re.I)),
    ("Go", re.compile(r"\b(?:golang|go(?:lang)?\s*(?:developer|engineer|backend))\b", re.I)),
    ("Laravel", re.compile(r"\blaravel\b", re.I)),
    ("Ruby", re.compile(r"\bruby\b", re.I)),
    ("Rails", re.compile(r"\brails\b", re.I)),
    ("Scala", re.compile(r"\bscala\b", re.I)),
    ("Kotlin", re.compile(r"\bkotlin\b", re.I)),
]

_QA_TITLE_RE = re.compile(
    r"\b(sdet|automation\s+qa|qa\s+leader|qa\s+engineer|test\s+engineer)\b",
    re.I,
)
_DJANGO_RE = re.compile(r"\bdjango\b", re.I)
_ADJACENT_FE_BE_RE = re.compile(
    r"\b(react|next\.?js|vue|angular|node\.?js|nestjs|typescript|full[\s-]?stack)\b",
    re.I,
)


def _norm_company(name: str) -> str:
    return " ".join(name.lower().split())


def _is_qa_title(title: str) -> bool:
    return bool(_QA_TITLE_RE.search(title or ""))


def _is_django_only(title: str) -> bool:
    if not _DJANGO_RE.search(title or ""):
        return False
    return not _ADJACENT_FE_BE_RE.search(title or "")


def should_skip(title: str, company: str, description: str = "") -> str | None:
    """Return a short skip reason, or None if the job is worth applying to."""
    blob = f"{title}\n{company}\n{description}"
    header = f"{title}\n{company}"
    norm_co = _norm_company(company)
    norm_header = _norm_company(header)

    if _SKIP_TITLE_RE.search(title):
        return "role: not an IC engineering title"

    for skip in SKIP_COMPANIES:
        if skip in norm_co or skip in norm_header:
            return f"former employer: {skip.title()}"

    lower = blob.lower()
    for stack in SKIP_STACK:
        if re.search(r"(?<![a-z0-9])" + re.escape(stack) + r"(?![a-z0-9])", lower):
            return f"stack: {stack}"

    for label, pattern in _LANG_PATTERNS:
        if pattern.search(header):
            return f"primary language: {label}"
        if pattern.search(blob) and not _RELEVANT_STACK.search(header):
            return f"primary language: {label}"

    if _is_qa_title(title):
        return "role: QA / SDET"

    if _is_django_only(title):
        return "stack: Django-only (no Node/React/Vue/TS)"

    if _YEARS_RE.search(header):
        return "experience: 7+ years required"

    if salary_below_floor(blob):
        return f"salary below ${SALARY_FLOOR}"

    return None


def salary_below_floor(text: str, *, floor: int = SALARY_FLOOR) -> bool:
    if floor <= 0:
        return False
    amounts: list[int] = []
    for line in (text or "").splitlines():
        if re.search(r"очікуван|expectation", line, re.I):
            continue
        for match in _VACANCY_SALARY_RE.finditer(line):
            amounts.extend(int(g) for g in match.groups() if g)
    if not amounts:
        return False
    return max(amounts) < floor


def is_relevant_stack(text: str) -> bool:
    return bool(_RELEVANT_STACK.search(text))
