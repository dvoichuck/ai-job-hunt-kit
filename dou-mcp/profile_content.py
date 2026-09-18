"""DOU profile copy — values come from gitignored local/profile.env."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import candidate as C

POSITION = C.TITLE
FULL_NAME = C.NAME
CITY = C.CITY
COUNTRY = C.COUNTRY
LOCATION_LINE = C.LOCATION_LINE
SALARY_USD_NET = C.SALARY_NET
EXPERIENCE_YEARS = C.YEARS
ENGLISH = C.ENGLISH
ENGLISH_SHORT = C.ENGLISH.split()[0] if C.ENGLISH else ""
SPECIALIZATIONS = C.SPECIALIZATIONS
SKILLS = C.SKILLS
SKILLS_TEXT = ", ".join(SKILLS)
CONTACTS = {
    "email": C.EMAIL,
    "phone": C.PHONE,
    "linkedin": C.LINKEDIN,
    "telegram": C.TELEGRAM,
    "telegram_handle": C.TELEGRAM_HANDLE,
}
LOOKING_FOR_JOB = True
CV_PATH = C.CV_PATH
ABOUT_UK = C.ABOUT_UK
ABOUT_EN = C.ABOUT_EN
ABOUT = (ABOUT_UK or ABOUT_EN).strip()
