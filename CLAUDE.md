# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Running the agent

```bash
# Activate venv first
source venv/Scripts/activate        # Windows/bash
# or: venv\Scripts\Activate.ps1    # PowerShell

# One-shot search cycle (run once, no scheduler)
python linkedin_agent.py

# Scheduled mode (runs every SCHEDULE_HOURS, blocking)
python linkedin_agent.py            # stays alive after first run

# Utility flags
python linkedin_agent.py --list-jobs
python linkedin_agent.py --reset-db
python linkedin_agent.py --send-report
python linkedin_agent.py --set-status <job_id> <new|applied|interviewing|rejected|offer>
```

## Environment variables (`.env`)

```
LINKEDIN_EMAIL=
LINKEDIN_PASSWORD=
NOTIFY_EMAIL_FROM=
NOTIFY_EMAIL_TO=
GMAIL_APP_PASSWORD=      # Gmail App Password, not account password
ANTHROPIC_API_KEY=       # optional — enables AI job summaries via claude-haiku-4-5
```

## Architecture

All logic lives in two files:

- **`linkedin_agent.py`** — everything: DB layer, LinkedIn API session, filtering pipeline, scoring, AI summaries, email rendering, scheduler.
- **`config.py`** — all tuneable parameters (keywords, locations, filters, score weights, `WISHLIST_COMPANIES`, `SCHEDULE_HOURS`).

### Data flow per cycle (`run()`)

1. `fetch_jobs()` fires 3 hardcoded broad keyword queries (no location) via the unofficial `linkedin_api` library.
2. Each result goes through two filter gates: `passes_filter()` (title) → `passes_description_filter()` (description). Description fetching is a separate API call per job — it's slow by design (`time.sleep(3)` between queries).
3. `compute_score()` ranks passing jobs 0–100 based on seniority keywords, title match against `keywords_list`, description keyword density, and remote mentions.
4. Jobs not yet in `seen_jobs` (SQLite) are considered new. On first run, they are indexed silently (no email). On subsequent runs, AI summaries are generated and an HTML email is sent.
5. If any new job's company is in `WISHLIST_COMPANIES`, a separate priority alert email is sent first.

### SQLite schema

Two tables in `jobs.db` (created/migrated automatically):
- `seen_jobs` — `job_id PK, title, company, location, url, found_at, score, status`
- `run_meta` — `key/value`, currently only stores `first_run_done` timestamp

### LinkedIn API session

`get_api()` returns a module-level singleton `Linkedin` instance. Session cookies are written to `./` (same directory as the script). On auth errors the session is reset and retried once.

### Scoring

Score is capped at 100. Breakdown:
- Seniority in title: senior +20, lead +15, manager +10
- Title in `keywords_list`: +10
- Description keywords matched × 3 (max 30)
- Remote mention: +5

To tune scoring or add new criteria, edit `compute_score()` in `linkedin_agent.py`.

## Key constraints

- The `linkedin_api` library is unofficial — LinkedIn rate-limits aggressively. The 3-second sleep between search queries is intentional.
- AI summaries use `claude-haiku-4-5` and are silently skipped if `ANTHROPIC_API_KEY` is missing or if the description is under 100 chars.
- Email uses Gmail SMTP SSL (port 465). `GMAIL_APP_PASSWORD` must be a Gmail App Password.
