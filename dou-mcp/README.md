# DOU MCP

MCP-сервер, що дає AI-агентові (Cursor тощо) працювати з [jobs.dou.ua](https://jobs.dou.ua)
від імені **кандидата** через реальний браузер (Playwright).

У DOU немає публічного API для кандидатів, тому сервер керує браузером з **твоєю
залогіненою сесією** (персистентний профіль Chromium). Логінишся один раз — сесія
зберігається й використовується всіма інструментами.

Профіль кандидата живе на [dou.ua/users/…](https://dou.ua/users/) (чекбокс
«Шукаю роботу» + поля досвіду/стеку). Вакансії — на jobs.dou.ua.

> ⚠️ Інструмент діє від твого імені. Використовуй відповідально й у межах
> [правил відгуків DOU](https://dou.ua/applications-rules/). Не роби масових
> спам-відгуків.

## Інструменти

| Інструмент | Що робить |
|---|---|
| `login` | Відкриває видиме вікно — ти логінишся вручну (email, Google, LinkedIn, GitHub, Facebook, капча). Сесія зберігається. |
| `session_status` | Чи сесія жива. |
| `apply_profile_pack` | Заповнити профіль з `local/profile.env` (`update_profile(apply_defaults=True)`). |
| `search_jobs` | Пошук вакансій (category, keywords, remote, exp_level, city). |
| `get_job` | Повний опис вакансії + чи ти вже відгукувався. |
| `apply` | Надіслати відгук: нативна форма DOU **або зовнішній ATS** (PeopleForce, Teamtailor, TalentLyft, Greenhouse, Lever, Workable, generic). `confirm=False` — заповнює без сабміту. Невідомі screening-питання → `needs_review` (без вигадок). Капча → `needs_captcha`. |
| `get_profile` | Зчитати свій профіль (поля форми + видимий текст). |
| `update_profile` | Оновити профіль. `apply_defaults=True` заповнює з локального профілю. |
| `list_inbox` | Список діалогів у [dou.ua/inbox](https://dou.ua/inbox/). |
| `read_thread` | Один тред (`oleh-panfilov` або повний URL). |
| `send_message` | Відповідь у треді. |
| `debug_screenshot` | Скріншот будь-якої сторінки. |
| `debug_dump_html` | Дамп HTML для оновлення селекторів. |

Вакансію ідентифікуй **повним URL** або `slug/id` (напр. `fuelfinance/365651`).
Сам числовий id на DOU дає 404.

### Фільтри `search_jobs`
- `category`: `Node.js`, `Front End`, …
- `exp_level`: `0-1`, `1-3`, `3-5`, `5plus`
- `remote`: `true` → `&remote`
- `keywords`: вільний текст (`search=`)

## Встановлення

```bash
cd dou-mcp
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
# Браузери можна перевикористати з djinni-mcp:
export PLAYWRIGHT_BROWSERS_PATH="$PWD/../djinni-mcp/.pw-browsers"
# або поставити свої:
# PLAYWRIGHT_BROWSERS_PATH="$PWD/.pw-browsers" python -m playwright install chromium
```

## Підключення до Cursor

Скопіюй `../.cursor/mcp.json.example` → `../.cursor/mcp.json` (сервер `dou`).
Перезапусти Cursor або Settings → MCP → Reload.

## Перший запуск

1. Попроси агента: **«залогінься в DOU»** → викличе `login`, відкриється браузер.
2. Далі: **«піджени профіль на DOU»** → `update_profile(apply_defaults=True)`.
3. Пошук/відгуки: `search_jobs` → `get_job` → `apply(..., confirm=False)` → `confirm=True`.

Тексти профілю — `profile_content.py` (значення з `local/profile.env`).

### Зовнішні ATS

Якщо на вакансії лише лінк «Відгукнутися на сайті компанії», `apply` відкриває його і заповнює форму:

- **PeopleForce / Teamtailor / TalentLyft** — окремі селектори + спільний generic fallback
- **Greenhouse / Lever / Workable / Recruitee / Ashby** — за host + generic labels
- Поля: ім’я, email, телефон, Telegram, LinkedIn, CV, cover letter, зарплата, notice, локація — з `local/profile.env`
- Кастомні питання без відповіді в `experience.md` (KYC, pgvector, …) — **не вигадуємо**, статус `needs_review`
- reCAPTCHA — заповнює форму, сабміт не форсить (`needs_captcha`)
- Дублікати пишуться в `applied.db` (не комітити)

## Налаштування (env)

Дивись `.env.example`. Ключове:
- `DOU_HEADLESS=0` — показувати вікно й для звичайних дій.
- `DOU_PROFILE_DIR` — де зберігається сесія (**не комітити**).

## Безпека

- `browser_profile/` містить твою сесію DOU — у `.gitignore`.
- Сервер локальний (stdio), нікуди дані не відправляє, крім самого DOU.
