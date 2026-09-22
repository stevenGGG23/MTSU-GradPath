# MTSU GradPath

A degree-progress and term-planning tool for MTSU undergraduates. It scrapes MTSU's undergraduate course catalog, audits completed coursework against a degree's requirement structure, and generates a term-by-term schedule that respects prerequisite ordering.

Currently supports all 5 STEM majors: **Computer Science, Biology, Mathematics, Chemistry, Physics**. Goal for the semester is to cover every undergraduate major in the catalog.

Live demo: **https://mtsu-gradpath.onrender.com**
Video walkthrough: **https://youtu.be/ePZ4cazj3Gw**

| | |
|---|---|
| ![Course selection form](static/demo-course-selection.png) | ![Plan overview and completion progress](static/demo-plan-overview.png) |
| ![Term-by-term generated schedule](static/demo-plan-schedule.png) | ![Prerequisite dependency graph](static/demo-prerequisite-map.png) |

---

## Installation

1. Create a virtual environment and install dependencies:

   **Linux / macOS**
   ```bash
   python3 -m venv .venv && source .venv/bin/activate
   pip install -r requirements.txt
   ```
   **Windows (PowerShell)**
   ```powershell
   python -m venv .venv; .venv\Scripts\Activate.ps1
   pip install -r requirements.txt
   ```

2. Set a `SECRET_KEY` — **required**, `app.py` reads it via `os.environ["SECRET_KEY"]` and will not start without it:

   ```bash
   cp .env.example .env
   # edit .env and set SECRET_KEY to the output of:
   python -c "import secrets; print(secrets.token_hex(32))"
   ```

3. Run it and open `http://127.0.0.1:5000`:

   ```bash
   python app.py
   ```

### Running on JupyterHub

1. Upload/clone this folder into your JupyterHub home directory.
2. Open a **Terminal** (not a notebook cell) and follow steps 1–3 above exactly as on Linux.
3. JupyterHub doesn't expose `127.0.0.1:5000` directly — use the hub's proxy path instead:
   ```
   https://<your-jupyterhub-domain>/user/<your-username>/proxy/5000/
   ```
   If `jupyter-server-proxy` isn't installed on the hub, ask the instructor for the proxy URL pattern used there.

---

## Configuration

Read via `mtsugradpath/config.py`, settable in `.env` or as shell env vars:

| Variable | Default | Description |
|---|---|---|
| `SECRET_KEY` | *(required)* | Flask session signing key |
| `DATABASE_URL` | `sqlite:///data/mtsu_courses.db` | SQLAlchemy connection string. Production uses Supabase Postgres — see `.env.example` for the connection string format |
| `MTSU_CATALOG_URL` | `https://catalog.mtsu.edu` | Base URL for the catalog widget API |
| `MTSU_CATALOG_IDS` | `36` | Catalog ID(s) to sync (36 = last publicly accessible undergrad catalog; 44-49 require an MTSU IT API token) |
| `MTSU_PROGRAM_PREFIX` | `CSCI` | Default course prefix when none is selected |
| `DB_POOL_SIZE` / `DB_MAX_OVERFLOW` | `3` / `2` | SQLAlchemy connection pool size per process. `workers x (pool + overflow)` must stay under Supabase's connection limit |
| `SYNC_ADMIN_USER` / `SYNC_ADMIN_PASSWORD` | *(unset = open)* | HTTP Basic Auth for force-resync (`/sync?force=1`). Set both in production; the normal Sync Catalog button is unaffected |
| `WEB_CONCURRENCY` | `3` | Gunicorn worker count (set via Render env vars — see `Procfile`) |

`/sync` syncs all 5 configured majors (CSCI, BIOL, MATH, CHEM, PHYS) in one pass, regardless of `MTSU_PROGRAM_PREFIX`.

---

## Usage

1. On the home page, click `Sync Catalog` to populate the database from MTSU's live catalog.
2. Pick a major (CS, Biology, Math, Chemistry, or Physics).
3. Check off completed courses (grouped by level); use search to add supporting courses.
4. Enter completed hours for non-major buckets (science sequences, True Blue Core, general electives).
5. Pick a starting term/year, number of terms to plan, and whether to include summers, then submit.
6. Review the result: completion percentage, per-category breakdown, credit-hours-per-term chart, term-by-term schedule, and any prerequisite warnings.
7. Use `Prerequisite Map` in the nav bar to view the course dependency graph independently of a generated plan.

Run the catalog sync standalone: `python scrape_courses.py`

---

## Testing

```bash
python -m pytest tests
```

`tests/test_planner.py` covers term generation, prerequisite gating, requirement-bucket handling, `validate_plan`, and `course_offered_in_term` against published department scheduling patterns.

`tests/test_plan_integrity.py` independently re-derives correctness (without importing the scheduler's own helper functions, so a bug in them can't also hide from its own test) across all 5 majors: a full from-scratch plan for each major, 25 randomized partial-completion trials per major, and named regression tests for two real bugs that were found and fixed — a major-required course silently dropped a prerequisite from a different department's course list (e.g. PHYS 2120 needing MATH 1910), and the same gap on the supporting-course path (e.g. a Chemistry plan treating PHYS 2120 as if its only prerequisite were PHYS 2110). Both are now checked for every major, not just the two where they were originally found.

---

## Architecture

```
app.py                 Flask routes: / (form + plan), /prerequisites, /sync, /sync/status
scrape_courses.py      CLI entry point for the catalog sync
mtsugradpath/
├── config.py          Env configuration
├── db.py              SQLAlchemy engine/session
├── models.py          ORM: Course, CourseType, Prerequisite
├── scraper.py         Catalog widget API client + DB sync (sync_courses(prefix))
├── degree.py          Requirement registry (DEGREE_REGISTRY) + audit logic
├── degree_configs.py  Per-major requirement configs (DEGREE_CONFIGS: cs, biology, math, chemistry, physics)
└── planner.py         Term scheduling (generate_plan) + validation (validate_plan)
templates/, static/    Jinja templates, CSS/assets
tests/                 pytest suite
data/                  Local SQLite file (gitignored)
```

**Scheduling:** `generate_plan()` fills each term toward a target credit-hour load, in priority order: required major/concentration courses, then supporting courses (e.g. MATH/COMM/PHIL for CS), then general-requirement buckets — all gated by prerequisites and by published course-offering seasons (`_offered_in_term`, e.g. fall-only or odd-year-spring-only courses). Prerequisite and offering-season checks always resolve against a course's *own* major's config, not necessarily the major currently being planned — so a supporting course pulled in from another department (e.g. MATH 1910 on a Physics plan) is checked against MATH's own requirement and offering data, not silently skipped. `validate_plan()` independently re-derives the same checks (not by calling generate_plan's own logic) and surfaces a warning banner if any course precedes an unmet prerequisite or lands in a term it isn't offered.

**Degree model:** `mtsugradpath/degree_configs.py` encodes each major's requirement structure (core courses, concentration, supporting courses, True Blue Core, and electives as the remainder) as a mix of specific required courses and partial-hour buckets for flexible/general-education requirements. Each config also carries a `prereq_override_map` for catalog quirks (e.g. lab-section prerequisites listed instead of the lecture course).

**Prerequisite Map:** `/prerequisites` renders required-course dependencies as a Mermaid.js graph (`build_prereq_graph`), scoped to required courses rather than the full CSCI catalog.

---

## Deployment

Hosted on [Render](https://render.com) free tier (`stevenGGG23/MTSU-GradPath`, `main`, auto-deploy). Build: `pip install -r requirements.txt`. Start: `gunicorn app:app --bind 0.0.0.0:$PORT --timeout 600 --workers ${WEB_CONCURRENCY:-3}` (see `Procfile`). Requires `SECRET_KEY` and `DATABASE_URL` (Supabase Postgres) as env vars on Render — without `DATABASE_URL` it falls back to local SQLite, which does not persist on Render's ephemeral filesystem. Set `SYNC_ADMIN_USER`/`SYNC_ADMIN_PASSWORD` on Render too, so force-resync isn't public in production.

Migrating off the Render free tier to a host that can handle registration-period traffic (a few hundred concurrent users) is still a semester goal — free-tier CPU/RAM limits how many workers are actually usable regardless of the `--workers` flag. See Known Limitations below.

---

## Known Limitations

- **Worker count is untested under load.** `Procfile` now runs 3 gunicorn workers by default (tunable via `WEB_CONCURRENCY`), but that number hasn't been validated against Render free-tier resource limits or Supabase's pooler connection cap — needs real load testing before registration week.
- **Force-resync is admin-gated; the normal sync isn't.** `/sync?force=1` (and the legacy bare `GET /sync`) require `SYNC_ADMIN_USER`/`SYNC_ADMIN_PASSWORD` when those are set. The plain "Sync Catalog" button stays open since it no-ops when a major is already loaded — full per-user accounts (per the Statement of Scope's "saved plans" section) are still a future item.
- **Rate limiting counts per gunicorn worker, not globally.** Flask-Limiter uses in-memory storage (no Redis), so with `WEB_CONCURRENCY` workers each process enforces its own counter — the real ceiling is roughly `limit x workers`, not exact. Good enough as a backstop against a spike or scripted abuse; would need Redis for precise per-client accounting.
- **No responsive design pass verified in-browser.** `static/styles.css` now has a `@media (max-width: 576px)` block tightening headings, cards, and the progress ring for phone widths, but it hasn't been checked against a real device/browser — worth a look before demoing.
- **Each major re-fetches the full catalog listing.** `sync_courses(prefix)` calls `fetch_all_courses()` separately per major, so a full 5-major sync re-downloads the same catalog page list 5 times before filtering by prefix, instead of fetching it once and splitting locally. Per-course detail fetches are threaded (`DETAIL_FETCH_WORKERS` in `scraper.py`, default 5) and that's the main win so far; deduping the listing fetch across majors is the next speedup, not yet done.
- Course-offering data covers only published undergraduate patterns for the 5 supported majors; graduate courses and unpublished exceptions aren't modeled.
- Only catalog ID 36 syncs by default (last publicly accessible undergrad catalog widget); catalog IDs 44-49 require an MTSU IT API token.
- No persistence of user input beyond the session cookie — completed courses/hours don't survive across devices or browsers.
- Scoped to 5 STEM majors (CS, Biology, Math, Chemistry, Physics); the rest of the undergraduate catalog isn't modeled yet.

---

## Project Background

Originated from a course proposal (Statement of Scope, July 15, 2026) under group name "Future FANG." The objective — generate an optimal graduation path from completed coursework — is unchanged; the implementation diverged from the proposal in several ways: SQLite/SQLAlchemy replaced flat JSON files, catalog data is scraped automatically instead of hand-entered, the UI is a Flask web app instead of a desktop GUI, and charts are CSS-based instead of matplotlib. See git history for the full evolution.

## Acknowledgements

- Middle Tennessee State University Undergraduate Catalog
- Flask
- SQLAlchemy
- Bootstrap
- Mermaid.js
- Render

---

## Roadmap (Fall 2026)

Per the CSCI 4700/5700 Statement of Scope (due 10/01/2026):

- Cover every undergraduate major in the catalog (5 of ~80+ done: CS, Biology, Math, Chemistry, Physics).
- Migrate off Render's free tier to a host that handles registration-period concurrent load (a few hundred users) with good security and GitHub auto-deploy.
- Get an official MTSU Acalog API key (catalog IDs 44-49) to replace the public-widget scraper, which is unreliable under load.
- Admin-only catalog sync, scheduled/automatic re-sync after each catalog update.
- Daily PostgreSQL backups (source is always recoverable from the repo; course data is always recoverable via re-sync).
- User guide for students/advisors.
- Pursue MTSU adoption and hosting sponsorship.

## Team

**Data/Scraper:** Abigaid Ortiz, Will Reilly, Zackary Butler
**Infrastructure/Features:** Kevin Yassa, Mina Eshak, Brett Wilt
**Hosting:** Steven Gobran

## License

Developed for educational purposes as part of a university Computer Science course project.
