---
name: rewrite-cv
description: Rewrite and optimize a CV/resume for AI-driven screening (ATS + LLM ranking) while staying human-readable. Use when the user asks to rewrite, improve, tailor, or generate a CV/resume, a summary, project bullets, or wants to adapt the CV to a specific job description.
---

# Rewrite CV (2026, AI-optimized)

Мета: зробити CV, яке однаково добре читається **AI-моделлю** (ATS парсить → LLM аналізує й порівнює з вакансією → генерує summary рекрутеру) і **людиною** (20–60 секунд читання).

Актуальне джерело фактів — локальний `experience.md` (не в git). **Завжди читай його першим.** PDF резюме — шлях з `local/profile.env` (`CANDIDATE_CV`).

## Головне правило: без вигадок

AI дуже добре знаходить невідповідності, тому:

- **Ніколи не вигадуй цифри, метрики, назви компаній чи технології.** Бери лише те, що є в `experience.md` або що підтвердив користувач.
- Якщо для сильного bullet потрібна метрика, якої немає — **запитай користувача** або залиш плейсхолдер `[TODO: метрика]`, а не вигадуй.
- Не завищуй seniority. Рівень підкріплюй складністю задач, а не гучним титулом.
- Дати, стек і назви проєктів мають бути **консистентні** між усіма версіями CV.

## Робочий процес

```
- [ ] 1. Прочитати experience.md (+ за потреби обидва PDF)
- [ ] 2. Якщо є вакансія — витягти з неї ключові слова/стек/вимоги
- [ ] 3. Уточнити відсутні метрики в користувача (не вигадувати)
- [ ] 4. Скласти CV за структурою нижче (контент)
- [ ] 5. Прогнати по чеклісту якості
- [ ] 6. Згенерувати PDF (див. "Експорт у PDF")
```

**Крок 2 (якщо є вакансія):** зроби tailored-версію — під конкретну вакансію піднімай нагору релевантні проєкти й вживай саме її термінологію. Master-версію (`cv/master.md` або подібне) не ламай; tailored зберігай окремо (напр. `cv/tailored-<company>.md`).

## Структура CV (порядок важливий)

1. **Header** — ім'я, титул, локація, email, phone, LinkedIn, Telegram/GitHub.
2. **Summary** (3–4 рядки) — AI майже завжди читає першим. Хто ти, скільки років, головний стек, домени, AI-досвід. Приклад у секції нижче.
3. **Core Expertise** — компактний блок ключових слів (для ATS/LLM-матчингу). Список у `experience.md`.
4. **AI & LLM Experience** — окремий блок (у 2026 це майже must-have).
5. **Work Experience** — проєкти з досягненнями (кожен відповідає на 4 питання).
6. **Technical Skills** — згруповано, але коротко (деталі стека вже в контексті проєктів).
7. **Education**, **Languages**.

Максимум **2 сторінки**. Залишай найсильніші проєкти з `experience.md`, слабші — скорочуй або прибирай.

## 10 принципів формулювань

1. **Конкретні результати з цифрами.**
   - ❌ `Developed backend APIs using a popular framework.`
   - ✅ `Built REST APIs with a backend framework and SQL supporting high daily traffic.`
   - ✅ `Reduced report generation time by optimizing SQL queries and indexes.`
   (Цифри — тільки реальні/підтверджені.)
2. **Термінологія вакансії.** Пиши конкретні сервіси (`AWS Lambda, S3, ECS, CloudWatch`), а не `Cloud infrastructure`.
3. **Ніяких "Responsible for…".** Починай з дієслова дії: `Designed`, `Built`, `Implemented`, `Owned`, `Automated`, `Optimized`, `Scaled`.
4. **Проєкти важливіші за список технологій.** Опиши, яку бізнес-проблему вирішив і яким стеком, а не просто перелік `Node, React, AWS`.
5. **AI-досвід окремим блоком** (OpenAI, Anthropic, LangChain, LangGraph, prompt engineering, structured outputs, function calling, RAG, AI automation).
6. **Не завищуй seniority** (див. правило вище).
7. **Кожен проєкт відповідає на 4 питання:** Що це? Які задачі? Які технології? Який результат?
8. **Менше buzzwords** — прибери `hardworking`, `team player`, `fast learner`, `passionate`, `self-motivated`.
9. **Короткий Summary на початку** (див. приклад).
10. **Не більше 2 сторінок.**

## Правила під AI-скринінг (додатково)

- **ATS-friendly формат:** одна колонка, стандартні заголовки секцій (`Summary`, `Work Experience`, `Skills`, `Education`), звичайний текст. Не ховай текст у зображення/іконки/складні таблиці — парсер їх втрачає.
- Фінальний PDF має містити **виділюваний текст** (не картинка). Canva-експорт це вміє.
- Ключові слова вживай природно і в Summary, і в Core Expertise, і в контексті проєктів (LLM цінує повторення в контексті, а не keyword-stuffing).
- Формат bullet: **Action verb + що зробив + технології + результат/метрика.**

## Шаблон проєкту (Work Experience)

```markdown
### <Company> (<Client/Product>) — <Role>
<Month Year> – <Month Year>

<Одне речення: що це за продукт і яку бізнес-проблему вирішує.>

- <Action verb> <що зробив> using <tech>, <результат/метрика>.
- <Action verb> ...
- <Action verb> ...

**Tech:** <релевантний стек цього проєкту>
```

## Приклад Summary

```
Senior Full-Stack Engineer with N+ years building production SaaS.
Backend focus, cloud, and the stack from experience.md — no invented metrics.
```

## Приклад "до / після"

**До:** `Responsible for backend development using a popular framework.`

**Після:** `Built REST APIs with a backend framework and SQL supporting high daily traffic.`
(Цифри — лише з experience.md або `[TODO]`.)

## Експорт у PDF

Фінальний PDF генеруємо з HTML через headless Google Chrome — текст лишається
**виділюваним** (ATS/LLM його читають), верстка контрольована через CSS, результат
відтворюваний із коду.

1. Взяти `template.example.html` (у цій папці) як основу — там уже є стилі (A4, одна колонка,
   ATS-заголовки) і структура. Скопіювати в корінь репо (напр. `cv.html` або
   `cv-<company>.html`) і замінити контент на актуальний з `experience.md`.
2. Зібрати PDF:

```bash
bash .cursor/skills/rewrite-cv/scripts/build-pdf.sh cv.html cv.pdf
```

Скрипт викликає Chrome із `--headless --print-to-pdf --no-pdf-header-footer`.
Поля/розмір сторінки задаються через `@page` у CSS шаблону (A4, поля 14–15 мм).

**Правила для PDF:** не вставляти текст як зображення; тримати одну колонку і стандартні
заголовки секцій; перевіряти, що все вміщується у ≤ 2 сторінки (за потреби скорочувати
контент, а не зменшувати шрифт нижче ~10pt).

**Дизайн-база шаблону** (узгоджено рев'ю кількох моделей): шрифт `Noto Sans` (встановлений
у системі, fallback Arial); акцент — navy `#1F4E79`, вживати ощадливо (посилання, підзаголовок);
роздільники секцій — тонкі світло-сірі (`#CBD5E1`), а не важкі сині; дати робіт вирівняні
праворуч; ім'я — головний якір (22pt/700, `#0F172A`).

Перевірка кількості сторінок після збірки:

```bash
python3 -c "import re;d=open('cv.pdf','rb').read();print('pages:',len(re.findall(rb'/Type\s*/Page(?![s])',d)))"
```

Альтернатива: Markdown + ручна верстка. HTML→Chrome — дефолт, бо відтворюваний.

## Чекліст якості (перед видачею)

- [ ] Summary на 3–4 рядки, вгорі, з ключовим стеком і AI-досвідом
- [ ] Є блок AI & LLM Experience
- [ ] Кожен проєкт відповідає на 4 питання і має ≥2 bullet з результатом
- [ ] Bullets починаються з дієслів дії, без "Responsible for"
- [ ] Немає вигаданих цифр; невідомі метрики позначені `[TODO]`
- [ ] Дати/стек/назви консистентні з `experience.md`
- [ ] Стек винесено в контекст проєктів + компактний Core Expertise
- [ ] Прибрані buzzwords
- [ ] ≤ 2 сторінки, одна колонка, ATS-friendly заголовки
- [ ] Якщо під вакансію — релевантні проєкти вгорі, вжита її термінологія
- [ ] PDF згенеровано з HTML, текст виділюваний (не картинка), ≤ 2 сторінки
