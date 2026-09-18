# Local profile (not in git)

```bash
cp local/profile.env.example local/profile.env
```

Fill name, contacts, salary, stack, skip lists, cover-letter blocks.
Copy facts into `.cursor/skills/rewrite-cv/experience.md` (also gitignored).
Put the CV PDF on the path in `CANDIDATE_CV`.

`candidate.py` loads repo-root `.env`, then `local/profile.env` with `override=True`.
So `CANDIDATE_*` in `local/profile.env` always win.
