# MTSU GradPath

A degree-progress and term-planning tool for Middle Tennessee State University's B.S. Computer Science, Professional Computer Science Concentration. The application scrapes MTSU's undergraduate course catalog, evaluates completed coursework against the degree's 120-hour requirement structure, and generates a term-by-term schedule that respects prerequisite ordering.

Originally proposed under the working title "MTSU Academic Path Planner." See Project Background for the relationship between the original proposal and the current implementation.

---

## Demo

| | |
|---|---|
| ![Course selection form](static/demo-course-selection.png) | ![Plan overview and completion progress](static/demo-plan-overview.png) |
| ![Term-by-term generated schedule](static/demo-plan-schedule.png) | ![Prerequisite dependency graph](static/demo-prerequisite-map.png) |

---

## Overview

| | |
|---|---|
| Degree program | B.S. Computer Science, Professional Computer Science Concentration |
| Data source | MTSU undergraduate catalog widget API (catalog ID 46) |
| Backend | Python 3.10+, Flask, SQLAlchemy |
| Storage | SQLite by default; PostgreSQL supported via `DATABASE_URL` |
| Frontend | Server-rendered Jinja templates, Bootstrap 5, custom CSS |
| Tests | `pytest`, 11 test cases covering scheduling, prerequisite validation, and course-offering-pattern constraints |
| Live deployment | [mtsu-gradpath.onrender.com](https://mtsu-gradpath.onrender.com) (Render, free tier) |

---

## Architecture

```
MTSU-GradPath/
├── app.py                     Flask application: routes, request handling, view assembly
├── scrape_courses.py          CLI entry point for mtsugradpath.scraper.sync_courses
├── mtsugradpath/
│   ├── config.py              Environment configuration (database URL, catalog IDs, program prefix)
│   ├── db.py                  SQLAlchemy engine and session factory
│   ├── models.py               ORM models: Course, CourseType, CourseCourseType, Prerequisite
│   ├── scraper.py             Catalog widget API client and database sync
│   ├── degree.py               Degree requirement definitions and audit computation
│   └── planner.py             Term scheduling algorithm and plan validation
├── templates/                 Jinja templates (base.html, index.html, plan.html, prereqs.html, _requirement_row.html)
├── static/                    CSS, logo, and favicon assets
├── tests/                     pytest test suite
└── data/                      Local SQLite database file (gitignored)
```

Module responsibilities:

- **`scraper.py`** issues HTTP requests to MTSU's catalog widget API, parses course titles and prerequisite text out of the returned HTML fragments, and upserts the results into the `courses`, `course_types`, and `prerequisites` tables. Each course is committed individually so a failure partway through a sync does not roll back prior progress.
- **`degree.py`** defines the B.S. CS Professional Concentration requirement structure as static data (specific required courses, flexible elective-hour buckets, and non-CSCI requirement buckets with example course suggestions) and computes a completion audit from a set of completed course codes and a mapping of partial hours entered for non-CSCI buckets.
- **`planner.py`** contains the term-scheduling algorithm (`generate_plan`) and an independent plan validator (`validate_plan`), described below.
- **`app.py`** wires the above into three routes: `/` (GET renders the selection form, POST generates and renders a plan), `/prerequisites` (renders the prerequisite dependency graph), and `/sync` (triggers a catalog re-sync and redirects with a flash message).

---

## Scheduling Algorithm

`generate_plan()` in `mtsugradpath/planner.py` builds a term-by-term schedule from three sources, in priority order:

1. Required CSCI courses (Computer Science Core and Professional Concentration) not yet completed, subject to prerequisite chains parsed from the synced catalog.
2. Specific supporting courses (COMM 2200, MATH 1910/1920/2050, PHIL 3170) not yet completed, subject to a small explicit prerequisite map (for example, MATH 1920 requires MATH 1910).
3. Non-CSCI requirement buckets (math elective, science sequences, True Blue Core categories, general electives) with remaining hours, in course-sized chunks.

Each term is filled toward a target credit-hour load (`TARGET_HOURS_PER_TERM`, currently 15) rather than a fixed course count, so a mix of one-hour colloquium courses and four-hour lab sciences produces a realistic term composition. Summer terms are included or excluded based on a user-supplied flag; when excluded, the corresponding term is emitted as a placeholder entry.

Prerequisite correctness constraint: a course scheduled in a given term must have all of its prerequisites satisfied by courses completed in strictly earlier terms. Courses added earlier in the same term-building pass are tracked separately (`newly_completed`) and are not visible to prerequisite checks until the term is finalized. This was previously a defect: an earlier revision updated the completed-course set as courses were added within a single term, which allowed a course to be scheduled in the same term as its own prerequisite whenever the term's credit-hour budget had room left (for example, MATH 1910 and MATH 1920 together). The fix separates "completed prior to this term" from "added during this term" and merges the latter into the former only after the term is complete.

`validate_plan()` re-derives prerequisite relationships independently of the scheduler and walks the generated plan term by term, flagging any course whose prerequisite is either scheduled in the same term or not present in any strictly earlier term. This function does not share code paths with `generate_plan()`'s scheduling logic; it exists specifically so that a defect in the scheduler is caught and surfaced to the user as a warning rather than silently producing an invalid plan. In normal operation, `validate_plan()` is expected to return an empty list; a non-empty result is displayed as a warning banner on the generated plan page, naming the affected course, term, and missing prerequisite.

Both functions are covered by `tests/test_planner.py`, including a regression test that reproduces the exact scenario described above.

---

## Course Offering Patterns

MTSU's Computer Science department does not offer every undergraduate CSCI course every term; some run fall-only, spring-only, or in alternating years. This is published by the department at `csc.mtsu.edu/course_sched_patterns/` and encoded in `mtsugradpath/degree.py` as `COURSE_OFFERING_SEASONS` (a mapping of course code to the set of seasons it runs in) and `ODD_YEAR_SPRING_ONLY_COURSES` (courses offered only in odd-numbered-year spring terms). `course_offered_in_term(code, season, year)` evaluates this data and is applied as an additional filter on scheduling candidates in `generate_plan()`, so a course is never placed in a term it is not actually offered in.

| Pattern | Example course | Behavior |
|---|---|---|
| Every Fall and Spring | CSCI 1170, 2170, 3080, 3110, 3130, 3240, 4700 | No restriction beyond prerequisite ordering |
| Fall only | CSCI 4160, 4250, 4300, 4350, 4560 | Only scheduled in a Fall term |
| Spring only | CSCI 3210, 4330, 4410 | Only scheduled in a Spring term |
| Odd-year Spring only | CSCI 4360 | Only scheduled in Spring of an odd-numbered year |
| Unpublished (on demand) | CSCI 3200, 3250, 3350, and other electives not listed by the department | Treated as available in any term |

This data covers undergraduate CSCI courses only, consistent with this tool's scope. Graduate course offering patterns published on the same page are not modeled.

---

## Prerequisite Map

The `/prerequisites` route renders a dependency graph of the required courses for the B.S. CS Professional Concentration (Computer Science Core, Professional Concentration, high-level language options, and Supporting Courses), generated by `build_prereq_graph()` and rendered client-side with Mermaid.js from Mermaid flowchart syntax produced by `render_prereq_mermaid()`. An edge from course A to course B indicates A is a prerequisite of B. Prerequisites outside the tracked course set (for example, a math placement course) are still rendered as nodes, styled distinctly, since they represent real requirements even though the planner does not schedule them. The graph is intentionally scoped to required courses rather than the full CSCI catalog, since including every upper-division elective would make the diagram unreadable.

---

## Degree Requirement Model

`mtsugradpath/degree.py` defines the requirement structure for the B.S. CS Professional Concentration, based on MTSU's published academic map:

| Category | Hours | Tracking method |
|---|---|---|
| Computer Science Core | 26 | Specific CSCI courses: 1010, 1170, 2170, 3080, 3110, 3130, 3240, 4700 |
| Professional Concentration | 18 | Two specific courses (3210, 4160) plus 12 flexible hours (high-level language requirement and upper-division electives), satisfied automatically by any additional completed CSCI 3000/4000-level course |
| Supporting Courses | 33 | Five specific courses (COMM 2200, MATH 1910/1920/2050, PHIL 3170) plus partial-hour math/science buckets |
| True Blue Core | 24 | Partial-hour general-education buckets (Written Communication, Information Literacy, History and Civic Learning, Human Society and Social Relationships, Creativity and Cultural Expression) |
| Electives | 19 | Computed as the remainder needed to reach 120 total hours |

Non-CSCI requirements are intentionally represented as partial-hour buckets with example course suggestions rather than matched against specific scraped course records, since general-education course selection is flexible by design and out of scope for exact catalog matching. Users enter completed hours directly (0 up to the bucket's total); a "Mark complete" control sets the remaining hours in one action.

---

## Installation

1. Create a virtual environment and install dependencies:

   ```bash
   python3 -m venv .venv
   source .venv/bin/activate
   pip install -r requirements.txt
   ```

2. Optionally configure PostgreSQL for persistence instead of the SQLite default:

   ```bash
   export DATABASE_URL=postgresql+psycopg2://user:password@localhost:5432/mtsu_gradpath
   ```

3. Run the application:

   ```bash
   python app.py
   ```

4. Open `http://127.0.0.1:5000` in a browser.

5. Optionally copy `.env.example` to `.env` to configure catalog and database settings via file instead of shell environment variables:

   ```bash
   cp .env.example .env
   ```

The catalog sync can also be run outside the web application:

```bash
python scrape_courses.py
```

---

## Configuration

Environment variables, read via `mtsugradpath/config.py` (values may be set directly or via `.env`):

| Variable | Default | Description |
|---|---|---|
| `DATABASE_URL` | `sqlite:///data/mtsu_courses.db` | SQLAlchemy connection string |
| `MTSU_CATALOG_URL` | `https://catalog.mtsu.edu` | Base URL for the catalog widget API |
| `MTSU_CATALOG_IDS` | `46` | Comma-separated catalog IDs to sync (46 is MTSU's undergraduate catalog) |
| `MTSU_PROGRAM_PREFIX` | `CSCI` | Course prefix the sync and planner are scoped to |

---

## Deployment

The application is hosted on [Render](https://render.com) as a Python 3 web service, live at **https://mtsu-gradpath.onrender.com**.

| | |
|---|---|
| Repository | `stevenGGG23/MTSU-GradPath`, `main` branch, auto-deploys on every commit |
| Build command | `pip install -r requirements.txt` |
| Start command | `gunicorn app:app --bind 0.0.0.0:$PORT` (also defined in `Procfile`) |
| Instance | Free tier — spins down after inactivity, so the first request after idle can take up to a minute |

Required environment variables (set under the service's Environment tab):

| Variable | Required | Notes |
|---|---|---|
| `SECRET_KEY` | Yes | Flask session signing key; the app fails to start without it (`app.py` reads it via `os.environ["SECRET_KEY"]`) |
| `DATABASE_URL` | No | Points at a Render PostgreSQL instance for persistent storage. Without it, the app falls back to a local SQLite file, which does not persist across deploys/restarts on Render's ephemeral filesystem |

To deploy your own instance, fork the repository, create a new Render Web Service pointed at it, set the build/start commands above, and add the `SECRET_KEY` environment variable before the first deploy.

---

## Usage

1. From the home page, select `Sync Catalog` to populate the local database from MTSU's live catalog. A flash message reports the number of courses synced or any error encountered.
2. Mark completed CSCI courses using the checklist, grouped by course level. Use the search field to add courses not present in the checklist (supporting courses such as MATH 1910, COMM 2200, PHIL 3170, or any other code).
3. Enter completed hours for non-CSCI requirement buckets (math elective, science sequences, True Blue Core categories, general electives). Partial values are accepted; the "Mark complete" control fills the remaining hours.
4. Select a starting term and year, the number of terms to plan, and whether to include summer terms.
5. Submit the form. The resulting page displays: an overall completion percentage and per-category breakdown; a credit-hours-per-term bar chart; a grid of term cards listing scheduled courses and requirement items; and, if applicable, a prerequisite warning banner.
6. Select `Prerequisite Map` from the navigation bar to view the dependency graph of required courses independently of any generated plan.

---

## Testing

```bash
python -m pytest tests
```

The test suite covers term generation, prerequisite gating in `next_courses`, inclusion and completion of generic requirement buckets, `validate_plan` correctness (including a regression test for the same-term prerequisite defect described above), and `course_offered_in_term` against the published department scheduling patterns.

---

## Known Limitations

- Course offering pattern data is limited to what MTSU's CS department publishes for undergraduate courses; graduate course patterns and any unpublished exceptions are not modeled.
- Only the undergraduate catalog (ID 46) is synced by default; this tool targets undergraduate degree planning, not graduate programs.
- Courses cross-listed under a prefix other than `CSCI` (for example, a COMS-listed course satisfying a CSCI requirement) are not resolved, since the catalog sync and planner are scoped to the `CSCI` prefix.
- The prerequisite map is scoped to required courses; it does not include the full CSCI elective catalog.
- There is no persistence of user input across requests; completed courses and requirement hours must be re-entered each time a plan is generated.
- The application targets a single degree program (B.S. CS, Professional Concentration); other majors are out of scope for the current implementation.

---

## Project Background

This project originated from a course project proposal (Statement of Scope, dated July 15, 2026) submitted under the group name "Future FANG" by Steven Gobran, Beshoy Azrak, Caleb Lykens, and Mina Youssef Eshak. The stated objective was to generate an optimal graduation path for a given degree program from a set of completed courses, scoped initially to the Computer Science major.

The core objective is unchanged; the implementation approach evolved during development:

| Proposed | Implemented | Rationale |
|---|---|---|
| JSON data files for the course catalog | SQLite via SQLAlchemy ORM (`mtsugradpath/models.py`) | Prerequisite relationships and repeated catalog syncs are better represented in a relational schema than in hand-maintained JSON files. |
| Manually collected catalog data | Automated scraping of MTSU's catalog widget API (`mtsugradpath/scraper.py`) | MTSU's public catalog site exposes a JSON API suitable for automated retrieval, removing the need for manual data entry. |
| Desktop GUI (tkinter / CustomTkinter) | Flask web application (`app.py`, `templates/`) | A browser-based interface removes the need for local GUI toolkit configuration. |
| Chart rendering via matplotlib | CSS-based progress ring and bar chart | Avoids an additional runtime dependency while providing equivalent visualizations. |
| Command-line interface | Web interface only | See Known Limitations; a CLI could be added as a thin wrapper over the existing `mtsugradpath` package. |
| Prerequisite resolver (dependency graph) | `mtsugradpath/planner.py` for scheduling, plus a rendered dependency graph at `/prerequisites` (see Prerequisite Map) | Scheduling logic operates on prerequisites parsed from synced catalog data rather than a hand-authored graph; the visual graph was added directly on top of the same data. |
| Requirements engine (core, electives, general education) | `mtsugradpath/degree.py` | Implemented as proposed, covering all 120 hours of the degree across five requirement categories. |

---

## Team

- Steven Gobran
- Beshoy Azrak
- Caleb Lykens
- Mina Youssef Eshak

---

## License

Developed for educational purposes as part of a university Computer Science course project.
