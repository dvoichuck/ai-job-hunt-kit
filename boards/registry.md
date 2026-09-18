# Board registry

Status of each account is in `accounts.md`, not here.

| id | Platform | How to apply | Agent |
|---|---|---|---|
| djinni | [Djinni](https://djinni.co/) | MCP `apply` (`confirm=False` first) | `djinni` |
| dou | [DOU Jobs](https://jobs.dou.ua/) | MCP `apply` (native or external ATS) | `dou` |
| linkedin | [LinkedIn](https://www.linkedin.com/) | Inbox + Easy Apply by hand unless a tool exists | `linkedin` |
| workua | [Work.ua](https://www.work.ua/) | Account required; paste or fetch | web |
| robotaua | [Robota.ua](https://robota.ua/) | Account required | web |
| indeed | [Indeed](https://www.indeed.com/) | Account required | web |

Add rows for boards you actually use. Skip aggregators that only bounce to the above.

## Company ATS

DOU (and some LinkedIn Easy Apply pages) bounce to a vendor form. Automation for
those hosts lives in `dou-mcp/ats.py`: Teamtailor, Ashby, Workable, Greenhouse,
Lever, PeopleForce, TalentLyft, Recruitee, plus a generic fallback. Unknown
screening questions stay `needs_review` — do not invent answers.
