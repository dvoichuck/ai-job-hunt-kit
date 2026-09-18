---
name: interview-prep
description: Prepare for a technical interview against a specific job posting. Use when the user pastes a job link or job text and asks to prepare for the interview, likely questions, or how to present their experience for that role.
---

# Interview Prep (CV + vacancy → tailored prep doc)

Мета: за посиланням на вакансію (Djinni) + резюме кандидата згенерувати **персональну підготовку до тех-співбесіди**: fit-матриця вимог, ймовірні питання з готовими відповідями на РЕАЛЬНОМУ досвіді, чесні прогалини, і що спитати роботодавця.

## Незмінні правила

- **Джерело правди про досвід** — локальний `experience.md` (не в git). **Читай його першим.**
- **Без вигадок.** Відповіді лише на підтверджених фактах. Немає факту → прогалина, не вигадуй.
- **NDA** — формулювання з `local/profile.env` / experience.md.
- **Мова відповіді** — як у запиті користувача (типово українська для пояснень; приклади відповідей на співбесіді — англійською, бо співбесіди зазвичай EN).

## Робочий процес

```
- [ ] 1. Прочитати локальний experience.md
- [ ] 2. Витягти вакансію (див. «Отримання вакансії»)
- [ ] 3. Розібрати вакансію: роль, рівень, must-have стек, nice-to-have, домен, обов'язки
- [ ] 4. Побудувати fit-матрицю: кожна вимога → конкретний доказ з досвіду → сила (Strong/Partial/Gap)
- [ ] 5. Згенерувати prep-док за template.md у interview-prep/<company-or-role>-<YYYY-MM-DD>.md
- [ ] 6. Підсумувати: топ-сильні сторони, топ-ризики (прогалини) і план їх закрити
```

## Отримання вакансії

Пробуй у такому порядку:

1. **Djinni MCP** (найкраще): виклич тул `get_job` сервера `djinni` з посиланням/ID вакансії.
   - Якщо повертає помилку авторизації — виклич `login` того ж сервера, тоді повтори `get_job`.
2. **WebFetch** посилання на вакансію (якщо MCP недоступний).
3. Якщо обидва не вдались — **попроси користувача вставити текст вакансії**.

## Аналіз вакансії — що витягти

- Роль і рівень (Junior/Middle/Senior/Lead), тип (product/outsource), локація/формат.
- **Must-have** стек і роки досвіду; **nice-to-have**; домен (fintech, healthcare, logistics, AI…).
- Ключові обов'язки і «сигнали» (system design, scaling, leadership, AI/LLM, тести, CI/CD).
- Мова/термінологія вакансії — вживай її саме так у відповідях.

## Fit-матриця (обов'язково)

Для кожної значущої вимоги: `Вимога → Твій доказ (проєкт + факт/метрика) → Strong / Partial / Gap`.
Бери докази лише з локального experience.md (проєкт + факт/метрика). Не підставляй приклади з голови.

## Формат prep-доку

Використовуй `template.md` (у цій папці). Секції: TL;DR fit, Fit-матриця, Likely questions (behavioral / technical-stack / system design / domain / AI-LLM) з відповідями (STAR + реальні факти), Gaps & mitigation, Questions to ask them, 60-second pitch, Stack cheat-sheet.

## Вивід

Зберігай кожну підготовку окремим файлом у `interview-prep/` з назвою `<company-or-role>-<YYYY-MM-DD>.md`. Локальний experience.md не редагувати без підтвердження фактів.
