---
name: shortlist-jobs
description: Search job boards and score vacancies against the local facts file
  and profile. Use when the user asks to find jobs, scan a board, rank roles,
  or refresh the tracker. Not for writing the CV (rewrite-cv) or interview
  answers (interview-prep).
---

# Shortlist jobs

Мета: знайти вакансії й оцінити fit **без відгуку**. Відгук — окремий крок після
підтвердження користувача.

## Джерела

Читай у цьому порядку:

1. Локальний `experience.md` (не в git). Якщо там ще `<Your Name>` / `[TODO]` —
   ранжуй **треки**, не 20 живих вакансій.
2. `local/profile.env` — локація, зарплата, skip-компанії, стек, ключові слова.
3. `boards/registry.md`, `boards/searches.md`, `boards/accounts.md`.
   Шукай лише підключені або публічні борди. Якщо акаунт `missing` — скажи і
   все одно дай публічні URL.

Не вигадуй стек, роботодавця, CEFR, зарплату чи контакт.

## Пошук

- Запити — з `boards/searches.md` і `CANDIDATE_SEARCH_KEYWORDS`.
- Skip-правила коду — `filters.py` (компанії, стек, титул, підлога зарплати).
- Djinni / DOU: MCP `search_jobs` → `get_job`. LinkedIn — інбокс, або вставлений
  лінк / публічний пошук. Інші борди з registry — fetch / paste.
- Не подавайся повторно: звіряй `applications/tracker.md` і MCP `my_applications`.

## Fit

Факти лише з `experience.md`.

- **Strong** — must-have стек і рівень є в фактах; роль IC, не skip-титул.
- **Partial** — суміжний стек або частина must-have; треба tailored CV.
- **Stretch** — слабке покриття; сказати це явно.
- **Skip** — `filters.should_skip`, C-level / EM / чужий стек, зарплата нижче
  профілю, extra-мова якої немає в фактах.

Best = шанс не витрачати час рекрутера, не найгучніший титул.

## Вихід

Допиши рядки в `applications/tracker.md`. Не відгукуйся. Не вигадуй контакти.

У чаті: топ-5, блокери (акаунт / факти), який CV різати (master vs tailored).
