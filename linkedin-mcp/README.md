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
| `list_inbox` | Список діалогів. `unread_only=true` — лише непрочитані. |
| `read_thread` | Прочитати конкретний діалог. |
| `send_message` | Відповісти. `confirm=False` — заповнити поле без надсилання. |
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
2. «що в інбоксі LinkedIn?» → `list_inbox`.
3. «відповіси в треді …» → `read_thread` + `send_message`.

Факти для відповідей — `.cursor/skills/rewrite-cv/experience.md`. Сесію
(`browser_profile/`) не комітити.
