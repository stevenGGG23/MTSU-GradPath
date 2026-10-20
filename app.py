import gzip
import json
import os
import threading
import time
from datetime import date

from flask import (
    Flask,
    Response,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    url_for,
)
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

from mtsugradpath import catalog_store
from mtsugradpath.catalog_programs import auto_generic_hours, tbc_area_options
from mtsugradpath.config import SYNC_ADMIN_PASSWORD, SYNC_ADMIN_USER
from mtsugradpath.db import SessionLocal, init_db
from mtsugradpath.degree import (
    ELECTIVES_GENERIC_ID,
    SUPPORTING_GENERIC,
    TBC_GENERIC,
    build_audit,
)
from mtsugradpath.degree_configs import (
    DEGREE_CONFIGS,
    display_name,
    find_degree_config,
    get_full_degree_config,
    major_prefixes,
    picker_majors,
)
from mtsugradpath.double_major import (
    SECOND_MAJOR_ELECTIVE_ID_PREFIX,
    apply_second_major_to_config,
    build_second_major_audit,
    config_course_codes,
    second_major_allowed,
)
from mtsugradpath.minors import (
    MINOR_ELECTIVE_ID_PREFIX,
    apply_minor_to_config,
    build_minor_audit,
    get_minor_config,
    minor_allowed_for_major,
    picker_minors,
)
from mtsugradpath.models import CourseEquivalency, SyncStatus
from mtsugradpath.planner import (
    SEASON_CYCLE,
    build_personal_prereq_graph,
    build_program_prereq_graph,
    clear_catalog_cache,
    config_course_codes_all,
    default_start_season,
    generate_plan,
    load_catalog_for,
    meets_minimum_grade,
    render_personal_prereq_mermaid,
    render_prereq_mermaid,
    validate_plan,
)

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


# Gzip text responses. The planner page carries every major/minor name and
# /courses.json every course in the catalog; compressed they're a fraction of
# the size, which matters most on phones.
@app.after_request
def _gzip(response):
    if (
        response.status_code != 200
        or response.direct_passthrough
        or "gzip" not in request.headers.get("Accept-Encoding", "")
        or "Content-Encoding" in response.headers
        or not (response.mimetype or "").startswith(("text/", "application/json"))
    ):
        return response
    data = response.get_data()
    if len(data) < 1024:
        return response
    response.set_data(gzip.compress(data, compresslevel=6))
    response.headers["Content-Encoding"] = "gzip"
    response.headers["Content-Length"] = str(len(response.get_data()))
    response.vary.add("Accept-Encoding")
    return response

# Load the catalog export (and convert every program) at startup, so the
# first visitor doesn't pay for it
catalog_store.data()
picker_majors()
picker_minors()


# A full catalog re-export takes a while (the catalog's firewall limits how
# fast it can be downloaded). Anything still "running" past this is an
# orphaned lock, not a real in-progress export -- most likely the worker
# thread running it was killed (deploy, OOM restart) before it could set
# running=False, and since a daemon thread dies silently with its process,
# nothing else would ever clear the flag.
STALE_SYNC_SECONDS = 90 * 60


def _sync_is_stale(state):
    started_at = state.get("started_at")
    return started_at is None or (time.time() - started_at) > STALE_SYNC_SECONDS


# Export progress lives in the DB (SyncStatus, single row id=1), not a process
# global -- so /sync/status is consistent across gunicorn workers.
def _get_sync_state():
    try:
        with SessionLocal() as db_session:
            row = db_session.get(SyncStatus, 1)
    except Exception:
        row = None
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

    If those env vars aren't set (local dev), the re-export stays open.
    """
    if not (SYNC_ADMIN_USER and SYNC_ADMIN_PASSWORD):
        return True
    auth = request.authorization
    return bool(
        auth and auth.username == SYNC_ADMIN_USER and auth.password == SYNC_ADMIN_PASSWORD
    )


def _admin_auth_challenge():
    return Response(
        "Admin credentials required to re-export the catalog.",
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


# Function that formats credit hours into a short hand display
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


def _as_float(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


# Function that returns the second major's config, or None when none is chosen
# or it can't be paired with the primary (same program or same subject)
def _second_major_cfg(primary_cfg, key):
    second_cfg = find_degree_config(key or "")
    return second_cfg if second_major_allowed(primary_cfg, second_cfg) else None


# Courses shown in a major's "check off what you've taken" list: everything a
# catalog-generated program names (required courses and each choice's
# options), plus the rest of the subject when the program has upper-division
# electives in it. Hand-curated majors list their whole subject, as before.
def _checklist_codes(cfg):
    codes = []
    if cfg.get("catalog_driven"):
        codes = sorted(config_course_codes_all(cfg))
        if cfg.get("concentration_elective_hours"):
            codes += catalog_store.codes_for_prefix(cfg["prefix"])
    else:
        for prefix in major_prefixes(cfg):
            codes += catalog_store.codes_for_prefix(prefix)
    return [code for code in dict.fromkeys(codes) if catalog_store.course(code)]


def _required_codes(cfg):
    return {
        code
        for key in ("core_courses", "concentration_courses", "supporting_courses")
        for code, _, _ in cfg.get(key, [])
    }


def _program_option(key, cfg):
    name = display_name(cfg)
    return {
        "key": key,
        "name": name,
        "concentration": cfg.get("department") or "",
        "prefix": cfg["prefix"],
        "available": cfg.get("available", True),
        "verified": not cfg.get("catalog_driven"),
    }


# JSON list of every catalog course for the course search box. Served
# separately (and cached by the browser) so the planner page itself stays small.
@app.route("/courses.json")
@limiter.exempt
def courses_json():
    version = catalog_store.meta().get("fetched_at", "")
    etag = f'"courses-{version}"'
    if request.headers.get("If-None-Match") == etag:
        return Response(status=304)
    resp = Response(json.dumps(catalog_store.search_index()), mimetype="application/json")
    resp.headers["ETag"] = etag
    resp.headers["Cache-Control"] = "public, max-age=86400"
    return resp


# Function that displays the planner form and processes the submitted degree plans
@app.route("/", methods=["GET", "POST"])
def index():
    if request.method == "POST":
        return _plan()

    # Selections are intentionally NOT persisted across visits -- every fresh
    # load of the planner starts blank. The only thing carried across a
    # reload is the major, and only via the ?major= query param itself (used
    # by the major-selector radio's same-visit reload), never stored server-side.
    saved_state = {
        "major": request.args.get("major", "cs"),
        "second_major": request.args.get("second_major", ""),
    }

    degree_cfg = get_full_degree_config(saved_state["major"])
    saved_state["major"] = degree_cfg["key"]
    second_cfg = _second_major_cfg(degree_cfg, saved_state["second_major"])
    saved_state["second_major"] = second_cfg["key"] if second_cfg else ""

    primary_codes = _checklist_codes(degree_cfg)
    second_codes = [c for c in _checklist_codes(second_cfg) if c not in set(primary_codes)] if second_cfg else []
    display_prefixes = list(dict.fromkeys(
        major_prefixes(degree_cfg) + (major_prefixes(second_cfg) if second_cfg else [])
    ))

    # The second major's courses and electives folded in, so the requirement
    # rows and free-elective hours on the form match what will be planned
    form_cfg = degree_cfg
    if second_cfg:
        form_cfg, _ = apply_second_major_to_config(degree_cfg, second_cfg, set())

    # Minimum grades that a course's prerequisites call for, shown as a tooltip
    prerequisite_grade_requirements = {}
    for code in primary_codes + second_codes:
        prerequisite_grade_requirements.update((catalog_store.course(code) or {}).get("min_grades", {}))

    required = _required_codes(degree_cfg) | (_required_codes(second_cfg) if second_cfg else set())
    major_courses = []
    for group, codes in ((0, primary_codes), (1, second_codes)):
        for code in codes:
            info = catalog_store.course(code) or {}
            number = code.split()[1]
            major_courses.append({
                "code": code,
                "label": f"{code} - {info.get('title', '')}",
                "credits": info.get("credits") or 0,
                "minimum_grade": prerequisite_grade_requirements.get(code),
                "level": f"{number[0]}000-Level",
                "required": code in required,
                "desc": info.get("desc", ""),
                # 0 = primary major, 1 = second major (checklist sections)
                "group": group,
            })
    major_courses.sort(key=lambda c: (c["group"], c["level"], c["code"]))

    majors = [_program_option(key, cfg) for key, cfg in picker_majors()]
    # Old links to the generic Biology/Math/Physics configs still work; keep
    # the current pick in the list so the picker can show its name.
    for cfg in (degree_cfg, second_cfg):
        if cfg and not any(m["key"] == cfg["key"] for m in majors):
            majors.append(_program_option(cfg["key"], cfg))

    # Free-elective hours for the current major's config (with any second
    # major's requirements taking their share of the free electives)
    cfg_generic = form_cfg["supporting_generic"] + form_cfg["tbc_generic"]
    fixed_hours = (
        sum(h for _, h, _ in form_cfg["core_courses"])
        + sum(h for _, h, _ in form_cfg["concentration_courses"])
        + form_cfg.get("concentration_elective_hours", 0)
        + sum(h for _, h, _ in form_cfg["supporting_courses"])
        + sum(h for _, _, h, _ in cfg_generic)
    )
    display_elective_hours = max(form_cfg["total_hours"] - fixed_hours, 0)

    # Second-major electives are tracked from completed courses, not typed in
    manual_supporting_generic = [
        row for row in form_cfg["supporting_generic"]
        if not row[0].startswith(SECOND_MAJOR_ELECTIVE_ID_PREFIX)
    ]
    auto_ids = sorted((form_cfg.get("generic_options") or {}).keys()) + [
        gid for gid, _, _, _ in degree_cfg["tbc_generic"] if gid in tbc_area_options()
    ]

    return render_template(
        "index.html",
        major_courses=major_courses,
        degree_cfg=degree_cfg,
        degree_display_name=display_name(degree_cfg),
        second_cfg=second_cfg,
        second_display_name=display_name(second_cfg) if second_cfg else "",
        checklist_prefixes=display_prefixes,
        supporting_generic=manual_supporting_generic,
        auto_generic_ids=auto_ids,
        generic_options={gid: opts for gid, opts in (form_cfg.get("generic_options") or {}).items()},
        tbc_generic=degree_cfg["tbc_generic"],
        electives_generic_id=ELECTIVES_GENERIC_ID,
        electives_hours=display_elective_hours,
        season_options=SEASON_CYCLE,
        default_season=default_start_season(),
        default_year=date.today().year,
        saved_state=saved_state,
        majors=majors,
        minors=[
            {
                "key": key,
                "name": m["name"],
                "hours": m["total_hours"],
                "description": m["description"],
                "prefix": m["prefix"],
            }
            for key, m in picker_minors()
            if minor_allowed_for_major(m, display_prefixes)
        ],
        catalog_meta=catalog_store.meta(),
        courses_version=catalog_store.meta().get("fetched_at", ""),
    )


def _plan():
    completed_text = request.form.get("completed_courses", "")

    # Convert the submitted course list into course codes
    completed_courses = {
        " ".join(code.split()).upper()
        for code in completed_text.splitlines()
        if code.strip()
    }

    # Read grades for completed prerequisite courses that require a minimum grade
    try:
        course_grades = json.loads(request.form.get("course_grades", "{}"))
        if not isinstance(course_grades, dict):
            course_grades = {}
    except (json.JSONDecodeError, TypeError):
        course_grades = {}

    # Read selected major(s) and get the full degree configs
    degree_cfg = get_full_degree_config(request.form.get("major", "cs"))
    second_cfg = _second_major_cfg(degree_cfg, request.form.get("second_major"))
    all_major_prefixes = major_prefixes(degree_cfg) + (major_prefixes(second_cfg) if second_cfg else [])

    # Optional minors -- unknown ones, repeats, and ones in either major's
    # own subject are dropped
    minor_cfgs = []
    for key in request.form.getlist("minor"):
        minor_cfg = get_minor_config(key)
        if (
            minor_cfg
            and minor_cfg not in minor_cfgs
            and minor_allowed_for_major(minor_cfg, all_major_prefixes)
        ):
            minor_cfgs.append(minor_cfg)

    try:
        target_semesters = min(max(int(request.form.get("target_semesters", 4)), 1), 16)
    except ValueError:
        target_semesters = 4

    include_summer = bool(request.form.get("include_summer"))

    start_season = request.form.get("start_season") or default_start_season()

    try:
        start_year = int(request.form.get("start_year") or date.today().year)
    except ValueError:
        start_year = date.today().year

    # One catalog covering every course any of the chosen programs can plan
    catalog = load_catalog_for(degree_cfg, second_cfg, *minor_cfgs, completed=completed_courses)

    # Build minimum-grade requirements for prerequisite courses
    minimum_grades = {}
    for course_info in catalog.values():
        minimum_grades.update(course_info.get("prereq_grades", {}))

    # Only exclude a completed course when the student explicitly provided
    # a grade AND that grade falls below the required minimum. If no grade
    # was supplied (the dropdown was removed from the UI), assume the course
    # was completed satisfactorily and include it.
    validated_completed_courses = set()
    for code in completed_courses:
        minimum_grade = minimum_grades.get(code)
        provided_grade = course_grades.get(code)

        if minimum_grade and provided_grade:
            if meets_minimum_grade(provided_grade, minimum_grade):
                validated_completed_courses.add(code)
        else:
            validated_completed_courses.add(code)

    # With a second major or minors, the plan is built from a copy of the
    # major's config with their courses and electives folded in; the
    # major's own audit still uses the unmodified config.
    planning_cfg = degree_cfg
    planning_generic_hours = {}
    second_audit = None
    if second_cfg:
        planning_cfg, second_generic_hours = apply_second_major_to_config(
            degree_cfg, second_cfg, completed_courses, catalog
        )
        planning_generic_hours.update(second_generic_hours)

    generic_hours = read_generic_hours(request.form, planning_cfg)

    # Requirements that list specific courses ("Choose one: MATH 1530 or
    # BIA 2610") fill in from the courses the student checked off
    auto_hours, auto_applied = auto_generic_hours(planning_cfg, validated_completed_courses)
    for gid, hours in auto_hours.items():
        generic_hours[gid] = max(_as_float(generic_hours.get(gid)), hours)

    if second_cfg:
        second_audit = build_second_major_audit(second_cfg, completed_courses, generic_hours, catalog)

    minor_audits = []
    for minor_cfg in minor_cfgs:
        planning_cfg, minor_generic_hours = apply_minor_to_config(
            planning_cfg, minor_cfg, completed_courses, catalog
        )
        planning_generic_hours.update(minor_generic_hours)
        minor_audits.append(build_minor_audit(minor_cfg, completed_courses, catalog))

    # A major that requires "a minor (15-18 hours)" is satisfied by the
    # minor(s) the student picked: the minor's own courses get planned
    # instead of a generic placeholder, and the audit credits the minor's
    # progress toward that row.
    audit_generic_hours = dict(generic_hours)
    minor_bucket = degree_cfg.get("minor_bucket_id")
    if minor_cfgs and minor_bucket:
        planning_cfg = dict(planning_cfg)
        planning_cfg["supporting_generic"] = [
            row for row in planning_cfg["supporting_generic"] if row[0] != minor_bucket
        ]
        audit_generic_hours[minor_bucket] = sum(_as_float(m["completed_hours"]) for m in minor_audits)

    # Auto-computed elective progress wins over the (absent) form fields
    planning_generic_hours = {**generic_hours, **planning_generic_hours}

    # Courses that are on the plan only because of the second major or a
    # minor; ones the major already requires (e.g. MATH 1920 for CS) keep
    # their normal tag.
    major_codes = config_course_codes(degree_cfg)
    second_only_codes = (second_audit["course_codes"] - major_codes) if second_audit else set()
    minor_only_codes = set().union(*(m["course_codes"] for m in minor_audits)) - major_codes - second_only_codes

    # Generates the plan, audits, and prerequisite warnings
    plan = generate_plan(
        completed_courses,
        planning_generic_hours,
        target_semesters,
        include_summer,
        start_season=start_season,
        start_year=start_year,
        degree_cfg=planning_cfg,
        catalog=catalog,
        course_grades=course_grades,
    )

    try:
        with SessionLocal() as db_session:
            equivalencies = db_session.query(CourseEquivalency).all()
    except Exception:
        equivalencies = []

    audit = build_audit(
        validated_completed_courses,
        audit_generic_hours,
        catalog,
        degree_cfg={**degree_cfg, "_applied": auto_applied},
        equivalencies=equivalencies,
    )

    prereq_warnings = validate_plan(
        plan,
        completed_courses,
        catalog,
        degree_cfg=planning_cfg,
        course_grades=course_grades,
    )

    # Co-requisite check: warn when a student reports a lecture complete
    # without its companion lab.  The planner never schedules lab sections
    # itself, so this check lives here (on completed_courses) rather than
    # inside validate_plan (which only sees the generated schedule).
    coreq_map = planning_cfg.get("coreq_map", {})
    for coreq_lecture, coreq_labs in coreq_map.items():
        if coreq_lecture in completed_courses:
            for lab in sorted(coreq_labs):
                if lab not in completed_courses:
                    prereq_warnings.append({
                        "course": coreq_lecture,
                        "term": None,
                        "prereq": lab,
                        "type": "missing_coreq",
                    })

    # Detect completed courses that don't map to any known requirement.
    # These are courses the student entered that fall outside every degree
    # bucket — we surface them so the student can count them toward free
    # electives.
    all_applied_codes = set()
    for group in audit["groups"]:
        for entry in group["entries"]:
            all_applied_codes.update(entry.get("applied", []))
    all_known_codes = set()
    for cfg_item in [degree_cfg, second_cfg, *minor_cfgs]:
        if cfg_item:
            all_known_codes |= config_course_codes_all(cfg_item)
    for minor_audit in minor_audits:
        for entry in minor_audit["entries"]:
            all_applied_codes.update(entry.get("applied", []))
    # Courses that filled a choice or gen-ed row on their own
    for codes in auto_applied.values():
        all_applied_codes.update(codes)
    # Labs taken with a lecture count with it
    for labs in coreq_map.values():
        all_known_codes |= set(labs)
    unrecognized_courses = sorted(
        code for code in completed_courses
        if code not in all_applied_codes and code not in all_known_codes
    )

    # Build the student-specific prerequisite tree
    personal_nodes, personal_edges = build_personal_prereq_graph(
        catalog, validated_completed_courses, plan, degree_cfg=planning_cfg
    )
    personal_mermaid = render_personal_prereq_mermaid(personal_nodes, personal_edges)

    config_hours = {}
    for key in ("core_courses", "concentration_courses", "supporting_courses"):
        for code, hours, title in planning_cfg.get(key, []):
            config_hours[code] = (hours, title)

    # Nested function that returns the full course label for warnings
    def warning_label(code):
        if catalog_store.course(code):
            return catalog_store.label(code)
        title = config_hours.get(code, (None, ""))[1]
        return f"{code} - {title}" if title else code

    warnings_display = [
        {
            "course": warning_label(w["course"]),
            "prereq": warning_label(w["prereq"]) if w.get("prereq") else None,
            "term": w.get("term"),
            "type": w["type"],
            "minimum_grade": w.get("minimum_grade"),
        }
        for w in prereq_warnings
    ]

    # Nested function that formats a completed course on the results page
    def completed_label(code):
        info = catalog_store.course(code)
        if info:
            hours = credit_label(info.get("credits"))
            label = catalog_store.label(code)
            return f"{label} — {hours}" if hours else label
        if code in config_hours:
            hours, title = config_hours[code]
            return f"{code} - {title} — {credit_label(hours)}"
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
                "minor": item["code"] in minor_only_codes,
                "second": item["code"] in second_only_codes,
            }

        if kind == "requirement":
            return {
                "kind": kind,
                "code": None,
                "title": item["label"],
                "hours": credit_short(item["hours"]),
                "suggestion": item.get("suggestion"),
                "minor": item.get("id", "").startswith(MINOR_ELECTIVE_ID_PREFIX),
                "second": item.get("id", "").startswith(SECOND_MAJOR_ELECTIVE_ID_PREFIX),
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
        catalog_url=degree_cfg.get("catalog_url"),
        second_audit=second_audit,
        minor_audits=minor_audits,
        unrecognized_courses=unrecognized_courses,
        hide_sync=True,
        catalog_meta=catalog_store.meta(),
        generated_on=f"{date.today():%B} {date.today().day}, {date.today().year}",
    )


# Function that builds and displays the dependency graph for any major
@app.route("/prerequisites")
def prerequisites():
    cfg = get_full_degree_config(request.args.get("major", "cs"))
    catalog = load_catalog_for(cfg)
    nodes, edges = build_program_prereq_graph(cfg, catalog)
    # Courses with no prerequisite links inside the program would just stack
    # down the side of the graph -- list them separately
    linked = {code for edge in edges for code in edge}
    standalone = [
        {"code": code, "title": info.get("title", "")}
        for code, info in nodes.items() if code not in linked
    ]
    nodes = {code: info for code, info in nodes.items() if code in linked}
    mermaid_source = render_prereq_mermaid(nodes, edges)
    return render_template(
        "prereqs.html",
        mermaid_source=mermaid_source,
        degree_cfg=cfg,
        degree_display_name=display_name(cfg),
        majors=[_program_option(key, c) for key, c in picker_majors()],
        node_count=len(nodes),
        standalone=standalone,
        catalog_meta=catalog_store.meta(),
    )


# Background catalog re-export (admin only). The normal way to refresh the
# data is `python scrape_courses.py` + commit; this is for a quick refresh on
# a running server, written to the data directory and picked up by every worker.
def _run_sync_background():
    _set_sync_state(
        running=True, total=0, fresh=0, cached=0, errors=[], done=False,
        started_at=time.time(),
    )
    errors = []
    total = 0
    try:
        from scrape_courses import RAW_DIR, export

        data = export(out_file=catalog_store.RUNTIME_FILE, raw_dir=RAW_DIR, refresh=True)
        total = len(data["courses"])
        catalog_store.reload()
        clear_catalog_cache()
    except Exception as exc:
        errors.append(str(exc)[:200])
    finally:
        # Always clears the running flag, even if something above raised --
        # otherwise the lock never clears.
        _set_sync_state(
            running=False, total=total, fresh=total, cached=0,
            errors=errors, done=True,
        )


@app.route("/sync", methods=["GET", "POST"])
@limiter.limit("15 per minute")
def sync():
    # Re-downloading the catalog is slow and hits MTSU's site, so it's admin-only.
    if not _admin_auth_ok():
        return _admin_auth_challenge()

    state = _get_sync_state()
    if state["running"] and not _sync_is_stale(state):
        if request.method == "GET":
            flash("A catalog export is already running.", "info")
            return redirect(url_for("index"))
        return jsonify({"status": "running", "total": state["total"]})

    t = threading.Thread(target=_run_sync_background, daemon=True)
    t.start()
    if request.method == "GET":
        flash("Catalog export started in the background.", "info")
        return redirect(url_for("index"))
    return jsonify({"status": "started"})


@app.route("/sync/status")
@limiter.limit("120 per minute")
def sync_status():
    state = _get_sync_state()
    state["catalog"] = catalog_store.meta()
    return jsonify(state)


@app.route("/healthz")
@limiter.exempt
def healthz():
    meta = catalog_store.meta()
    return jsonify({
        "ok": catalog_store.has_data(),
        "catalog": meta.get("name"),
        "fetched_at": meta.get("fetched_at"),
        "courses": len(catalog_store.courses()),
        "majors": len(picker_majors()),
        "minors": len(picker_minors()),
    })


if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=5000)
