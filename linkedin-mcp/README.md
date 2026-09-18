# LinkedIn MCP

MCP-сервер, щоб AI-агент міг **читати інбокс LinkedIn і відповідати** від твого
імені через реальний браузер (Playwright).

Публічного messaging API немає — використовується **залогінена сесія**
(персистентний профіль Chromium). Логінишся один раз.

> ⚠️ Діє від твого імені. У межах [LinkedIn ToS](https://www.linkedin.com/legal/user-agreement).
> Не розсилай масовий спам.

## Інструменти

| Інструмент | Що робить |
|---|---|
| `login` | Видиме вікно — логін вручну (email, Google, 2FA, капча). Сесія зберігається. |
| `session_status` | Чи залогінені + snapshot профілю. |
| `list_inbox` | Список діалогів. `unread_only=true` — лише непрочитані. |
| `read_thread` | Прочитати конкретний діалог. |
| `send_message` | Відповісти. `confirm=False` — заповнити поле без надсилання. |
| `apply_profile_pack` | Заповнити профіль з `local/linkedin.pack.json` (intro, about, досвід, освіта, скіли, мови, Open to work, URL). |
| `upload_cv` | Залити PDF з `CANDIDATE_CV` у saved resumes / job application settings. |
| `update_intro` / `update_about` / `add_experience` / `add_education` / `add_skills` / `add_languages` / `set_open_to_work` / `set_custom_url` | Ті самі кроки по одному. |
| `pack_progress` | Які кроки пака вже пройшли, якщо ран обірвався. |
| `debug_screenshot` / `debug_dump_html` | Якщо LinkedIn змінив верстку. |

## Встановлення

```bash
cd linkedin-mcp
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
# браузери можна взяти з djinni-mcp:
export PLAYWRIGHT_BROWSERS_PATH="$PWD/../djinni-mcp/.pw-browsers"
```

Скопіюй `../.cursor/mcp.json.example` → `../.cursor/mcp.json` (сервер `linkedin`).
**Settings → MCP → Reload**.

## Перший запуск

1. «залогінься в LinkedIn» → відкриється Chromium, ти входиш.
2. Скопіюй `pack.example.json` → `local/linkedin.pack.json` і заповни з `experience.md`.
3. «заповни профіль з пака» → `apply_profile_pack`.
4. «що в інбоксі LinkedIn?» → `list_inbox`.

Факти для відповідей — `.cursor/skills/rewrite-cv/experience.md`. Сесію
(`browser_profile/`) не комітити.
