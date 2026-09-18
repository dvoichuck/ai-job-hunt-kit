# Job-search toolkit

Generic kit for an AI coding agent: ATS-ready CV from a local facts file, interview prep, and Playwright MCP servers for Djinni, DOU, and LinkedIn.

Personal name, salary, stack, and employers stay out of git. Fill `local/profile.env` and `experience.md` on your machine.

## What’s in here

| Path | Role |
|---|---|
| `local/profile.env.example` | Candidate identity, salary, skip lists, cover-letter blocks |
| `.cursor/skills/rewrite-cv/` | Rewrite a CV from local `experience.md` → HTML → selectable PDF |
| `.cursor/skills/shortlist-jobs/` | Search boards and score vacancies into `applications/tracker.md` |
| `.cursor/skills/interview-prep/` | Prep doc for one vacancy (fit matrix, answers, gaps) |
| `djinni-mcp/` | Djinni: search, apply, inbox |
| `dou-mcp/` | DOU: search, apply (incl. external ATS), inbox |
| `linkedin-mcp/` | LinkedIn inbox + profile pack (`local/linkedin.pack.json`) |
| `boards/` / `applications/` / `NEXT.md` | Board registry, vacancy tracker, working-state queue |
| `scripts/setup.sh` | Copy example profile / MCP / pack files locally |
| `candidate.py` / `filters.py` | Load the local profile; skip wrong-stack / skip-company jobs |
| `.cursor/rules/` | Apply and messenger guardrails (facts only, no re-greeting) |

## First-time setup

```bash
bash scripts/setup.sh
```

Fill `local/profile.env` and `experience.md`. Put a CV PDF on `CANDIDATE_CV` (default `local/cv.pdf`).

Then install each MCP you need (venv + Playwright Chromium). Browsers can be shared:

```bash
cd djinni-mcp
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
PLAYWRIGHT_BROWSERS_PATH="$PWD/.pw-browsers" python -m playwright install chromium
```

Repeat in `dou-mcp/` and `linkedin-mcp/` (point `PLAYWRIGHT_BROWSERS_PATH` at `../djinni-mcp/.pw-browsers` if you already installed Chromium). Reload MCP in Cursor.

Details: [Djinni](djinni-mcp/README.md), [DOU](dou-mcp/README.md), [LinkedIn](linkedin-mcp/README.md), [local profile](local/README.md).

## Rules the agent must follow

- Facts only from local `experience.md`. Unknown → ask or `[TODO]`, never invent.
- Contract, notice, location, salary → `local/profile.env`.
- Don’t spam applications; don’t apply twice.
- In an already-open thread, don’t greet again.
- `apply` / `send_message` / profile updates: dry-run unless `confirm=True`.
- Browser sessions (`*/browser_profile/`) and `local/profile.env` are gitignored.

## License

Use at your own risk. The boards did not invite this automation; your account is what you put on the line.
