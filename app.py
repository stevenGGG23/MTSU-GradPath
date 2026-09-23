import os
import threading
import time
from mtsugradpath.config import PROGRAM_PREFIX, SYNC_ADMIN_USER, SYNC_ADMIN_PASSWORD
from mtsugradpath.db import init_db, SessionLocal
from flask import (
    Flask,
    render_template,
    request,
    redirect,
    url_for,
    flash,
    jsonify,
    Response,
)
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

from mtsugradpath.degree import (
    SUPPORTING_COURSES,
    SUPPORTING_GENERIC,
    TBC_GENERIC,
    ELECTIVES_GENERIC_ID,
    DEGREE_REGISTRY,
    elective_required_hours,
    build_audit,
    get_degree_config,
)

from mtsugradpath.degree_configs import get_full_degree_config, DEGREE_CONFIGS

from mtsugradpath.models import Course, SyncStatus
from mtsugradpath.planner import (
    generate_plan,
    load_catalog_courses,
    default_start_season,
    SEASON_CYCLE,
    validate_plan,
    build_prereq_graph,
    render_prereq_mermaid,
    build_personal_prereq_graph,
    render_personal_prereq_mermaid,
)

from mtsugradpath.scraper import sync_courses
from datetime import date

# Create the Flask application and its databases
app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ["SECRET_KEY"]
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"

# Rate limiting, keyed per client IP. Storage is in-memory (the default) --
# with multiple gunicorn workers each worker counts independently, so the
# effective ceiling is roughly limit x worker count, not exact. That's fine
# here: the goal is a backstop against a traffic spike or scripted abuse, not
# precise per-client accounting, and adding Redis just for exact counts isn't
# worth it at this scale.
limiter = Limiter(
    get_remote_address,
    app=app,
    default_limits=["60 per minute", "1000 per hour"],
    storage_uri="memory://",
)

with app.app_context():
    init_db()


# A full resync of every major takes a few minutes at most (see the timed
# manual run in dev: ~40s for a single mid-size major). Anything still
# "running" past this is an orphaned lock, not a real in-progress sync -- most
# likely the worker thread running it was killed mid-sync (deploy, OOM
# restart) before it could set running=False, and since a daemon thread dies
# silently with its process, nothing else would ever clear the flag.
STALE_SYNC_SECONDS = 20 * 60


def _sync_is_stale(state):
    started_at = state.get("started_at")
    return started_at is None or (time.time() - started_at) > STALE_SYNC_SECONDS


# Sync progress lives in the DB (SyncStatus, single row id=1), not a process
# global -- so /sync/status is consistent across gunicorn workers.
def _get_sync_state():
    with SessionLocal() as db_session:
        row = db_session.get(SyncStatus, 1)
        if row is None:
            return {
                "running": False, "total": 0, "fresh": 0, "cached": 0,
                "errors": [], "done": False, "started_at": None,
            }
        return {
            "running": row.running,
            "total": row.total,
            "fresh": row.fresh,
            "cached": row.cached,
            "errors": row.errors.splitlines() if row.errors else [],
            "done": row.done,
            "started_at": row.started_at,
        }


def _set_sync_state(**fields):
    with SessionLocal() as db_session:
        row = db_session.get(SyncStatus, 1)
        if row is None:
            row = SyncStatus(id=1)
            db_session.add(row)
        for key, value in fields.items():
            if key == "errors":
                value = "\n".join(value)
            setattr(row, key, value)
        db_session.commit()


def _admin_auth_ok():
    """Checks HTTP Basic Auth against SYNC_ADMIN_USER/PASSWORD.

    If those env vars aren't set (local dev), force-resync stays open.
    """
    if not (SYNC_ADMIN_USER and SYNC_ADMIN_PASSWORD):
        return True
    auth = request.authorization
    return bool(
        auth and auth.username == SYNC_ADMIN_USER and auth.password == SYNC_ADMIN_PASSWORD
    )


def _admin_auth_challenge():
    return Response(
        "Admin credentials required for a full catalog resync.",
        401,
        {"WWW-Authenticate": 'Basic realm="GradPath Admin"'},
    )

# Function that formats credit hours with a display label
def credit_label(hours):
    if not hours:
        return ""

    hours_value = int(hours) if float(hours).is_integer() else hours
    unit = "credit hour" if hours_value == 1 else "credit hours"

    return f"{hours_value} {unit}"

# Function that formats credit hours into a shot hand display
def credit_short(hours):
    if not hours:
        return ""

    hours_value = int(hours) if float(hours).is_integer() else hours

    return f"{hours_value} cr"

# Function that reads the complete generic requirement hours from the submitted form
# Uses the degree_cfg's generic items when provided; falls back to CS defaults.
def read_generic_hours(form, degree_cfg=None):
    generic_hours = {}

    if degree_cfg is not None:
        all_generic = degree_cfg["supporting_generic"] + degree_cfg["tbc_generic"]
    else:
        all_generic = SUPPORTING_GENERIC + TBC_GENERIC

    for generic_id, _, _hours, _ in all_generic:
        generic_hours[generic_id] = form.get(f"hours_{generic_id}", 0)

    generic_hours[ELECTIVES_GENERIC_ID] = form.get(f"hours_{ELECTIVES_GENERIC_ID}", 0)

    return generic_hours


# Function that displays the planner form and processes the submitted degree plans
@app.route("/", methods=["GET", "POST"])
def index():
    # Selections are intentionally NOT persisted across visits -- every fresh
    # load of the planner starts blank. The only thing carried across a
    # reload is the major, and only via the ?major= query param itself (used
    # by the major-selector radio's same-visit reload), never stored server-side.
    saved_state = {"major": request.args.get("major", "cs")}

    # Determine which major to display courses for on GET
    degree_cfg_for_display = get_full_degree_config(saved_state["major"])
    display_prefix = degree_cfg_for_display["prefix"]

    with SessionLocal() as db_session:
        course_list = (
            db_session.query(Course)
            .filter(Course.prefix == display_prefix)
            .order_by(Course.prefix, Course.number)
            .all()
        )

        major_courses = [
            {
                "code": f"{course.prefix} {course.number}",
                "label": (
                    f"{course.prefix} {course.number}"
                    f" - {course.title}"
                ),
                "credits": course.credits or 0,
                "level": f"{course.number[0]}000-Level",
            }
            for course in course_list
            if course.prefix and course.number
        ]

        major_courses.sort(key=lambda c: int(c["code"].split()[1]))

    courses = list(major_courses)

    for code, hours, title in degree_cfg_for_display["supporting_courses"]:
        courses.append({"code": code, "label": f"{code} - {title}", "credits": hours})

    # Combines major courses with required supporting courses
    courses.sort(key=lambda c: c["code"])

    if request.method == "POST":
        completed_text = request.form.get("completed_courses", "")

        # Convert the submitted course list into course codes
        completed_courses = {
            code.strip().upper()
            for code in completed_text.splitlines()
            if code.strip()
        }

        # Read selected major and get the full degree config
        selected_major = request.form.get("major", "cs")
        degree_cfg = get_full_degree_config(selected_major)

        generic_hours = read_generic_hours(request.form, degree_cfg)

        try:
            target_semesters = int(
                request.form.get("target_semesters", 4)
            )
        except ValueError:
            target_semesters = 4

        include_summer = bool(request.form.get("include_summer"))

        start_season = (
            request.form.get("start_season") or default_start_season()
        )

        try:
            start_year = int(request.form.get("start_year") or date.today().year)
        except ValueError:
            start_year = date.today().year

        # Loaded once and reused below -- generate_plan() used to reload the
        # catalog itself internally, so every plan generation cost two DB
        # round trips instead of one.
        catalog = load_catalog_courses(degree_cfg["prefix"])

        # Generates the plan, audits, and prerequisite warnings
        plan = generate_plan(
            completed_courses,
            generic_hours,
            target_semesters,
            include_summer,
            start_season=start_season,
            start_year=start_year,
            degree_cfg=degree_cfg,
            catalog=catalog,
        )

        audit = build_audit(completed_courses, generic_hours, catalog, degree_cfg=degree_cfg)

        prereq_warnings = validate_plan(plan, completed_courses, catalog, degree_cfg=degree_cfg)

        # Build the student-specific prerequisite tree
        personal_nodes, personal_edges = build_personal_prereq_graph(catalog, completed_courses, plan, degree_cfg=degree_cfg)
        personal_mermaid = render_personal_prereq_mermaid(personal_nodes, personal_edges)

        # Maps the course codes for full display
        # Reload courses for the selected major to build the course_map
        with SessionLocal() as db_session2:
            post_course_list = (
                db_session2.query(Course)
                .filter(Course.prefix == degree_cfg["prefix"])
                .order_by(Course.prefix, Course.number)
                .all()
            )
        post_courses = [
            {
                "code": f"{c.prefix} {c.number}",
                "label": f"{c.prefix} {c.number} - {c.title}",
                "credits": c.credits or 0,
            }
            for c in post_course_list
            if c.prefix and c.number
        ]
        for code, hours, title in degree_cfg["supporting_courses"]:
            post_courses.append({"code": code, "label": f"{code} - {title}", "credits": hours})
        course_map = {c["code"]: c for c in post_courses}

        # Nested function that returns the full course label for warnings
        def warning_label(code):
            course = course_map.get(code)
            return course["label"] if course else code

        warnings_display = [
            {
                "course": warning_label(w["course"]),
                "prereq": warning_label(w["prereq"]) if w.get("prereq") else None,
                "term": w["term"],
                "type": w["type"],
            }
            for w in prereq_warnings
        ]

        # Nested function that formats a completed course on the results page
        def completed_label(code):
            course = course_map.get(code)
            if course:
                hours = credit_label(course["credits"])
                return f"{course['label']} — {hours}" if hours else course["label"]
            return code

        # Nested function that converts planner items into values the template accepts
        def display_item(item):
            kind = item["kind"]

            if kind == "course":
                title = item["label"].split(" - ", 1)[1] if " - " in item["label"] else ""
                return {
                    "kind": kind,
                    "code": item["code"],
                    "title": title,
                    "hours": credit_short(item["hours"]),
                }

            if kind == "requirement":
                return {
                    "kind": kind,
                    "code": None,
                    "title": item["label"],
                    "hours": credit_short(item["hours"]),
                    "suggestion": item.get("suggestion"),
                }

            return {"kind": kind, "code": None, "title": item["label"], "hours": ""}

        # Convert planner data into display structures
        completed_display = [completed_label(code) for code in sorted(completed_courses)]

        plan_display = {
            term: [display_item(item) for item in term_items]
            for term, term_items in plan.items()
        }

        # Calculates the total number of planned hours for each semester
        term_hours_display = {
            term: sum(item.get("hours", 0) for item in term_items)
            for term, term_items in plan.items()
            if not term.startswith("Remaining")
        }

        term_hours_display = {
            term: (int(hours) if float(hours).is_integer() else hours)
            for term, hours in term_hours_display.items()
        }

        max_term_hours = max(term_hours_display.values()) if term_hours_display else 0

        return render_template(
            "plan.html",
            completed=completed_display,
            plan=plan_display,
            semesters=target_semesters,
            include_summer=include_summer,
            audit=audit,
            term_hours=term_hours_display,
            max_term_hours=max_term_hours,
            warnings=warnings_display,
            personal_mermaid=personal_mermaid,
            degree_name=degree_cfg["name"],
        )

    # Build degree list from DEGREE_CONFIGS (all have available=True now)
    # Also include any non-STEM majors from DEGREE_REGISTRY that aren't in DEGREE_CONFIGS
    degree_list = list(DEGREE_CONFIGS.items())
    for key, reg_cfg in DEGREE_REGISTRY.items():
        if key not in DEGREE_CONFIGS:
            degree_list.append((key, reg_cfg))

    # Compute elective hours for the current major's config
    cfg_generic = degree_cfg_for_display["supporting_generic"] + degree_cfg_for_display["tbc_generic"]
    fixed_hours = (
        sum(h for _, h, _ in degree_cfg_for_display["core_courses"])
        + sum(h for _, h, _ in degree_cfg_for_display["concentration_courses"])
        + degree_cfg_for_display.get("concentration_elective_hours", 0)
        + sum(h for _, h, _ in degree_cfg_for_display["supporting_courses"])
        + sum(h for _, _, h, _ in cfg_generic)
    )
    display_elective_hours = max(degree_cfg_for_display["total_hours"] - fixed_hours, 0)

    return render_template(
        "index.html",
        courses=courses,
        major_courses=major_courses,
        degree_cfg=degree_cfg_for_display,
        supporting_generic=degree_cfg_for_display["supporting_generic"],
        tbc_generic=degree_cfg_for_display["tbc_generic"],
        electives_generic_id=ELECTIVES_GENERIC_ID,
        electives_hours=display_elective_hours,
        season_options=SEASON_CYCLE,
        default_season=default_start_season(),
        default_year=date.today().year,
        saved_state=saved_state,
        degree_list=degree_list,
    )

# Function that builds and displays the dependency graph
@app.route("/prerequisites")
def prerequisites():
    catalog = load_catalog_courses()

    nodes, edges = build_prereq_graph(catalog)
    
    mermaid_source = render_prereq_mermaid(nodes, edges)

    return render_template("prereqs.html", mermaid_source=mermaid_source)

# Background sync runner
def _run_sync_background(force=False):
    _set_sync_state(
        running=True, total=0, fresh=0, cached=0, errors=[], done=False,
        started_at=time.time(),
    )
    total = 0
    fresh = 0   # courses actually fetched from network
    cached = 0  # courses already in DB (no network call)
    errors = []
    try:
        for key, cfg in DEGREE_CONFIGS.items():
            prefix = cfg["prefix"]
            try:
                count = sync_courses(prefix=prefix, force=force)
                if count < 0:
                    # Negative = already loaded, no network call made
                    cached += abs(count)
                else:
                    fresh += count
                total += abs(count)
                _set_sync_state(total=total)
            except Exception as exc:
                errors.append(f"{prefix}: {str(exc)[:120]}")
                _set_sync_state(errors=errors[:])
    finally:
        # Always clears the running flag, even if something above raised
        # outside the per-major try/except -- otherwise the lock never
        # clears and every future sync request just reports "still running".
        _set_sync_state(
            running=False, total=total,
            fresh=fresh, cached=cached,
            errors=errors, done=True,
        )


@app.route("/sync", methods=["GET", "POST"])
@limiter.limit("15 per minute")
def sync():
    force = request.args.get("force") == "1" or (
        request.is_json and request.get_json(silent=True, force=True) or {}
    ).get("force")

    # Force-resync re-scrapes every major from MTSU's catalog even when the DB
    # is already populated -- that's the expensive/abusable path, so it's the
    # only one gated behind admin credentials. The plain "Sync Catalog" button
    # below (POST, no force) stays open: sync_courses() already no-ops per
    # major when courses are already loaded, so it's cheap to leave public.
    if request.method == "GET" and not request.args.get("force"):
        # Legacy GET — start a force sync and redirect (backwards compat for old bookmarks)
        if not _admin_auth_ok():
            return _admin_auth_challenge()
        legacy_state = _get_sync_state()
        if not legacy_state["running"] or _sync_is_stale(legacy_state):
            t = threading.Thread(target=_run_sync_background, kwargs={"force": True}, daemon=True)
            t.start()
        flash("Catalog sync started in the background. Check back in a moment.", "info")
        return redirect(url_for("index"))

    if force and not _admin_auth_ok():
        return _admin_auth_challenge()

    # POST or GET with ?force=1 — async API
    state = _get_sync_state()
    if state["running"] and not _sync_is_stale(state):
        return jsonify({"status": "running", "total": state["total"]})

    _set_sync_state(running=False, total=0, fresh=0, cached=0, errors=[], done=False)
    t = threading.Thread(target=_run_sync_background, kwargs={"force": bool(force)}, daemon=True)
    t.start()
    return jsonify({"status": "started"})


@app.route("/sync/status")
@limiter.limit("120 per minute")  # polled every ~1.5s by the JS while a sync is running
def sync_status():
    return jsonify(_get_sync_state())

if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=5000)
