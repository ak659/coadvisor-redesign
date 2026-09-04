# CoAdvisor.AI

An agentic course-planning app for a virtual college, built for the **OpenAI WebMCP Challenge**.

CoAdvisor.AI lets a student and an AI agent work on the **same live plan together** — both can browse courses, add/remove them, check prerequisites, compare degree paths, and flag conflicts. Every action, whether taken by a human or an agent, is persisted to the same database and visibly attributed, so either party can pick up exactly where the other left off.

**Live app:** https://coadvisor-ai.onrender.com
**Demo video:** https://youtu.be/dBIR9_6RSYY

---

## Why this exists

Most course-planning tools are either a static catalog browser (no intelligence) or a chatbot bolted on top (no real access to app state). CoAdvisor.AI instead exposes its core actions as **WebMCP tools** — real functions, backed by a real database, that an agent visiting the page can call directly. The agent isn't guessing its way through the UI; it's using the same typed, documented tools a developer would.

Two real decision points this is designed around:
- **Prospective students** comparing whole degree programs before applying (`compare_paths`)
- **Upperclassmen** choosing between specializations/tracks, and checking whether their transcript actually supports a given path (`check_feasibility`)

## How human + agent collaboration works here

Every tool is a plain JavaScript function. The human-facing UI (buttons, dropdowns) and the WebMCP `registerTool()` `execute()` callbacks both call the **exact same functions** — there's no separate "agent path" and "human path." Whoever acts, the result is the same real write to MongoDB, visible to the other party on their next look at the page (or reload).

Agent actions are visually and structurally distinct from human ones:
- Advisor notes and conflict flags render as agent-authored callouts, separate from plan entries
- The Session Log tags every entry `human` or `agent`, in order, so the full back-and-forth is auditable

## Architecture

```
Browser (human clicks, or agent via WebMCP tools)
        │
        ├── registerTool() six-plus-one WebMCP tools
        │   (each execute() calls a regular JS function)
        │
        ▼
FastAPI backend (app.py)
        │
        ├── MongoDB Atlas — app state (plans, session log, advisor notes, synthetic students)
        └── catalog.js (generated, static) — course/degree/track/prerequisite data
```

- **Catalog data** (`degrees.csv`, `tracks.csv`, `courses.csv`, `prerequisites.csv`) is the relational source of truth — 5 degree programs, 10 tracks, 80 courses, real prerequisite chains, validated for referential integrity. `catalog.js` is a generated build artifact (see `scripts/` in the source history) served statically and loaded client-side.
- **App state** (what a student has planned, what an agent has said, what's happened in a session) lives in MongoDB — document-shaped data, no relational joins needed, separate from the catalog on purpose.
- **Synthetic students** (`generate_students.py`) are seeded once via a Faker-based, persona-weighted generator (fast-track / at-risk / track-switcher / average), with every transcript validated to respect real prerequisite chains — no student has a course without having completed its prerequisites first. This is what `check_feasibility` checks against.

## The WebMCP tools

| Tool | What it does |
|---|---|
| `add_to_plan` | Add a course to the current plan |
| `remove_from_plan` | Remove a course from the current plan |
| `check_prerequisites` | Check whether a course's prerequisites are satisfied by the current plan |
| `compare_paths` | Compare two degree/track paths by course composition and total credits |
| `check_feasibility` | Check whether a given synthetic student's transcript supports completing a target degree/track, listing reachable vs. blocked courses |
| `flag_conflict` | Flag a conflict between two courses (e.g. mutually exclusive capstones), left as a visible, agent-authored note |
| `ask_advisor_note` | Leave a general advisor-style note on a specific course |

## Tech stack

- **Frontend:** plain HTML/CSS/JS, WebMCP via `document.modelContext` (with the `@mcp-b/webmcp-polyfill` as a fallback for browsers without native support yet)
- **Backend:** FastAPI (Python)
- **Database:** MongoDB Atlas
- **Catalog tooling:** Python/pandas (`build_catalog_tables.py`) generating CSVs + SQLite reference tables; a Node.js generator turns those into the browser-facing `catalog.js`
- **Synthetic data:** Faker (Python)
- **Deployment:** Render

## Running it locally

```bash
pip install -r requirements.txt

# one-time: seed the course catalog reference data (if not already present)
# and the synthetic student dataset
python generate_students.py

# set your MongoDB connection string
$env:MONGODB_URI = "mongodb+srv://..."   # PowerShell
# export MONGODB_URI="mongodb+srv://..."  # bash

uvicorn app:app --reload --port 8000
```

Then open `http://localhost:8000/`.

## Testing with a real agent

Open the live URL in **ChatGPT's desktop app in-app browser** (WebMCP-enabled out of the box), or in **Chrome with `chrome://flags/#enable-webmcp-testing` enabled**, and ask it to plan or compare courses on your behalf. Its actions will show up in the Session Log tagged `agent`, alongside anything you do yourself.

## License

MIT — see `LICENSE`.
