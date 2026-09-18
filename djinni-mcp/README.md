# Djinni MCP

MCP-сервер, що дає AI-агентові (Cursor тощо) працювати з [Djinni.co](https://djinni.co)
від імені **кандидата** через реальний браузер (Playwright).

У Djinni немає публічного API для кандидатів, тому сервер керує браузером з **твоєю
залогіненою сесією** (персистентний профіль Chromium). Логінишся один раз — сесія
зберігається й використовується всіма інструментами.

> ⚠️ Інструмент діє від твого імені. Використовуй відповідально й у межах
> [Правил Djinni](https://djinni.co/terms/). Не роби масових спам-відгуків.

## Інструменти

| Інструмент | Що робить |
|---|---|
| `login` | Відкриває видиме вікно браузера — ти логінишся вручну (email, Google, LinkedIn, капча). Сесія зберігається. |
| `session_status` | Чи сесія жива. |
| `apply_profile_pack` | Заповнити профіль з `local/profile.env`. |
| `get_profile` | Структуровані поля профілю. |
| `audit_profile` | Порожні vs заповнені поля форми + банери. |
| `upload_cv` | Залити PDF з `CANDIDATE_CV` у профіль. |
| `search_jobs` | Пошук вакансій за фільтрами (keywords, exp_level, english_level, employment, company_type, salary_min, page). |
| `get_job` | Повний опис вакансії + чи ти вже відгукувався. |
| `apply` | Надіслати відгук із супровідним листом. Є `confirm=False` — «сухий» прогін без надсилання. |
| `list_inbox` | Список діалогів з рекрутерами. |
| `read_thread` | Прочитати конкретний діалог. |
| `send_message` | Відповісти в діалозі. |
| `my_applications` | Вакансії, на які ти вже відгукнувся, + статуси. |
| `debug_screenshot` | Скріншот будь-якої сторінки (якщо сайт змінив верстку). |
| `debug_dump_html` | Дамп HTML сторінки/селектора для оновлення селекторів. |

### Значення фільтрів
- `exp_level`: `no_exp`, `1y`, `2y`, `3y`, `5y`
- `english_level`: `no_english`, `basic`, `pre`, `intermediate`, `upper`, `fluent`
- `employment`: `remote`, `office`, `parttime`, `freelance`
- `company_type`: `product`, `outsource`, `outstaff`, `agency`, `startup`
- `salary_min`: число (USD/міс)

## Встановлення

Уже зроблено в цьому каталозі, але для відтворення:

```bash
cd djinni-mcp
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
PLAYWRIGHT_BROWSERS_PATH="$PWD/.pw-browsers" python -m playwright install chromium
```

## Підключення до Cursor

Скопіюй `../.cursor/mcp.json.example` → `../.cursor/mcp.json` і перезапусти Cursor
(або Settings → MCP → Reload). Сервер `djinni` має зʼявитися в списку.

## Перший запуск

1. Попроси агента: **«залогінься в Djinni»** → викличе `login`, відкриється браузер,
   ти входиш вручну. Вікно закриється саме після успіху.
2. Далі все працює headless. Приклади запитів до агента:
   - «Знайди Python remote вакансії з досвідом 2 роки і зарплатою від $3000».
   - «Візьми вакансію 835139, адаптуй під неї мій супровідний лист на основі
     локального CV і зроби `confirm=False` (сухий прогін)».
   - «Покажи мою вхідну скриньку і статуси відгуків».

## Робочий процес «адаптувати CV/лист і відгукнутися»

Адаптацію під вакансію робить сам агент (він читає твоє CV та опис вакансії з
`get_job`), а сервер лише надсилає підготовлений лист:

1. `search_jobs` → обрати вакансію.
2. `get_job` → отримати повний опис.
3. Агент читає твоє резюме (PDF у батьківському каталозі) і генерує лист під вакансію.
4. `apply(job, cover_letter, confirm=False)` → перевірити (скріншот у `debug/`).
5. `apply(job, cover_letter, confirm=True)` → надіслати.

## Налаштування (env)

Дивись `.env.example`. Ключове:
- `DJINNI_HEADLESS=0` — показувати вікно браузера й для звичайних дій (корисно
  для дебагу або якщо Cloudflare блокує headless).
- `DJINNI_SLOWMO=200` — сповільнити дії (мс).
- `DJINNI_PROFILE_DIR` — де зберігається сесія (не комітити в git!).

## Якщо щось зламалось

Djinni періодично змінює верстку. Якщо `apply`/`send_message` не знаходять форму:
1. Виклич `debug_dump_html` на потрібній сторінці.
2. Онови словник `SELECTORS` угорі `server.py`.

## Безпека

- Каталог `browser_profile/` містить твою сесію Djinni — він у `.gitignore`, **не
  комітити й не ділитися**.
- Сервер локальний (stdio), нікуди дані не відправляє, крім самого Djinni.
