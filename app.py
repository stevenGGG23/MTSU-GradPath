import gzip
import json
import math
import os
import re
import secrets
import threading
import time
from datetime import date

from flask import (
    Flask,
    Response,
    g,
    jsonify,
    render_template,
    request,
    url_for,
)
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from sqlalchemy import or_, update
from werkzeug.middleware.proxy_fix import ProxyFix

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
    GRADE_RANK,
    meets_minimum_grade,
    render_personal_prereq_mermaid,
    render_prereq_mermaid,
    validate_plan,
)

# Create the Flask application
app = Flask(__name__)

_secret = os.environ.get("SECRET_KEY", "")
if not _secret or _secret == "replace_with_a_long_random_string":
    raise RuntimeError(
        "SECRET_KEY is not set. Generate one with "
        "`python -c \"import secrets; print(secrets.token_hex(32))\"` and set it in .env "
        "or the host's environment variables."
    )
app.config["SECRET_KEY"] = _secret
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["SESSION_COOKIE_HTTPONLY"] = True
# Hosted deployments are HTTPS-only; local dev runs over plain http
ON_HOST = bool(os.getenv("RENDER") or os.getenv("VERCEL"))
app.config["SESSION_COOKIE_SECURE"] = os.getenv("SESSION_COOKIE_SECURE", "1" if ON_HOST else "0") == "1"
# A planner form is a few KB; anything far bigger is abuse
app.config["MAX_CONTENT_LENGTH"] = 64 * 1024
app.config["MAX_FORM_MEMORY_SIZE"] = 64 * 1024
# Static assets are versioned in their URLs (see static_url), so browsers can keep them
app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 7 * 24 * 3600

# Render and Vercel sit behind one proxy hop: trust its X-Forwarded-For /
# -Proto so the rate limiter sees each visitor's address, not the proxy's
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1)

# Rate limiting, keyed per client IP. Storage is in-memory (the default) --
# with multiple gunicorn workers each worker counts independently, so the
# effective ceiling is roughly limit x worker count, not exact. That's fine
# here: the goal is a backstop against a traffic spike or scripted abuse, not
# precise per-client accounting, and adding Redis just for exact counts isn't
# worth it at this scale.
#
# The limit is per visitor address. A campus network or computer lab can put
# a whole class behind one public address, so it's set well above what one
# student needs (a page load is 2 requests; images/CSS aren't counted):
# 300/min covers ~40 students on one network all generating plans at once.
# Override with RATE_LIMIT, e.g. RATE_LIMIT="600 per minute;10000 per hour".
RATE_LIMIT = os.getenv("RATE_LIMIT", "300 per minute;5000 per hour")


def _client_ip():
    # Render serves through Cloudflare, which passes the visitor's own
    # address in CF-Connecting-IP (it overwrites any value a client sends)
    if os.getenv("RENDER"):
        return request.headers.get("CF-Connecting-IP") or get_remote_address()
    return get_remote_address()


limiter = Limiter(
    _client_ip,
    app=app,
    default_limits=[RATE_LIMIT],
    storage_uri="memory://",
    headers_enabled=True,
)

# The database only holds small shared state (export status, equivalencies),
# so the site still serves plans when it's unreachable
with app.app_context():
    try:
        init_db()
    except Exception:
        app.logger.exception("Database unavailable at startup; continuing without it")


# ── Response hardening ────────────────────────────────────────────────────────

def _csp_nonce():
    """This request's script nonce, created on first use -- so responses the
    rate limiter cuts short (429) still get one."""
    if "csp_nonce" not in g:
        g.csp_nonce = secrets.token_urlsafe(16)
    return g.csp_nonce


@app.context_processor
def _template_globals():
    return {"csp_nonce": _csp_nonce(), "static_url": static_url}


_STATIC_VERSIONS: dict = {}


def static_url(filename):
    """/static/<file>?v=<mtime>, so a deploy busts browser caches."""
    version = _STATIC_VERSIONS.get(filename)
    if version is None:
        try:
            version = str(int(os.path.getmtime(os.path.join(app.static_folder, filename))))
        except OSError:
            version = "0"
        _STATIC_VERSIONS[filename] = version
    return url_for("static", filename=filename, v=version)


_CDN = "https://cdn.jsdelivr.net"


@app.after_request
def _security_headers(response):
    headers = response.headers
    headers.setdefault("X-Content-Type-Options", "nosniff")
    headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    headers.setdefault("X-Frame-Options", "DENY")
    headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
    if request.is_secure:
        headers.setdefault("Strict-Transport-Security", "max-age=31536000")
    if (response.mimetype or "") == "text/html":
        nonce = _csp_nonce()
        headers.setdefault("Content-Security-Policy", "; ".join([
            "default-src 'self'",
            f"script-src 'self' 'nonce-{nonce}' {_CDN}",
            f"style-src 'self' 'unsafe-inline' {_CDN}",
            f"font-src 'self' {_CDN}",
            "img-src 'self' data:",
            "connect-src 'self'",
            "object-src 'none'",
            "base-uri 'self'",
            "form-action 'self'",
            "frame-ancestors 'none'",
        ]))
    return response


# Gzip text responses. The planner page carries every major/minor name and
# /courses.json every course in the catalog; compressed they're a fraction of
# the size, which matters most on phones.
@app.after_request
def _gzip(response):
    if not (response.mimetype or "").startswith(("text/", "application/json")):
        return response
    response.vary.add("Accept-Encoding")
    if (
        response.status_code != 200
        or response.direct_passthrough
        or response.is_streamed
        or request.accept_encodings.quality("gzip") <= 0
        or "Content-Encoding" in response.headers
    ):
        return response
    data = response.get_data()
    if len(data) < 1024:
        return response
    response.set_data(gzip.compress(data, compresslevel=6))
    response.headers["Content-Encoding"] = "gzip"
    response.headers["Content-Length"] = str(len(response.get_data()))
    return response


# Load the catalog export (and convert every program) at startup, so the
# first visitor doesn't pay for it
catalog_store.data()
picker_majors()
picker_minors()
if not catalog_store.has_data():
    app.logger.error("No catalog data loaded -- run `python scrape_courses.py` to create catalog_data/catalog.json")


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


def _claim_sync_lock():
    """Atomically mark an export as running; False if one already is.

    A single UPDATE ... WHERE, so two workers can't both start an export
    (both would wipe and rewrite the same download directory).
    """
    now = time.time()
    with SessionLocal() as db_session:
        if db_session.get(SyncStatus, 1) is None:
            db_session.add(SyncStatus(id=1, running=False))
            try:
                db_session.commit()
            except Exception:
                db_session.rollback()
        result = db_session.execute(
            update(SyncStatus)
            .where(SyncStatus.id == 1)
            .where(or_(
                SyncStatus.running.is_(False),
                SyncStatus.running.is_(None),
                SyncStatus.started_at.is_(None),
                SyncStatus.started_at < now - STALE_SYNC_SECONDS,
            ))
            .values(running=True, started_at=now, done=False, total=0, fresh=0, cached=0, errors="")
        )
        db_session.commit()
        return result.rowcount == 1


def _admin_auth_ok():
    """HTTP Basic Auth against SYNC_ADMIN_USER/PASSWORD.

    Fails closed: with no credentials configured, re-export is refused unless
    ALLOW_OPEN_SYNC=1 is set (local development only).
    """
    if not (SYNC_ADMIN_USER and SYNC_ADMIN_PASSWORD):
        return os.getenv("ALLOW_OPEN_SYNC") == "1"
    auth = request.authorization
    if not auth or auth.username is None or auth.password is None:
        return False
    user_ok = secrets.compare_digest(auth.username.encode(), SYNC_ADMIN_USER.encode())
    pass_ok = secrets.compare_digest(auth.password.encode(), SYNC_ADMIN_PASSWORD.encode())
    return user_ok and pass_ok


def _admin_auth_challenge():
    return Response(
        "Admin credentials required to re-export the catalog.",
        401,
        {"WWW-Authenticate": 'Basic realm="GradPath Admin"'},
    )


_ERRORS = {
    404: ("Page not found", "That page doesn't exist. Your planner selections are still saved in this tab."),
    413: ("That's too much at once", "The form you sent was much bigger than a degree plan needs. "
                                    "Try again with fewer courses."),
    429: ("Too many requests", "You're going a little fast. Wait a moment and try again."),
    500: ("Something went wrong", "GradPath hit an unexpected error building that page. Please try again; "
                                  "if it keeps happening, let the GradPath team know which major you picked."),
}


def _error_page(code):
    heading, message = _ERRORS[code]
    try:
        body = render_template("error.html", code=code, heading=heading, message=message,
                               catalog_meta=catalog_store.meta(), hide_sync=True)
    except Exception:
        body = f"<h1>{heading}</h1><p>{message}</p>"
    return body, code


@app.errorhandler(404)
def _not_found(_error):
    return _error_page(404)


@app.errorhandler(413)
def _too_large(_error):
    return _error_page(413)


@app.errorhandler(429)
def _too_many(_error):
    return _error_page(429)


@app.errorhandler(500)
def _server_error(_error):
    # Flask has already logged the traceback
    return _error_page(500)


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


# The "check off what you've taken" list for one major, as titled sections.
#
# Catalog majors follow the catalog's own sections (required courses first,
# then each section's options), then the rest of the subject in a collapsed
# "More BIOL courses" group when the program has upper-division electives.
# 0-credit labs that ride along with a lecture are left out -- checking the
# lecture counts both. Hand-curated majors list their subject by level.
def _checklist_sections(cfg, exclude=()):
    listed = set(exclude)
    required = _required_codes(cfg)
    sections = []

    def take(codes):
        out = [c for c in dict.fromkeys(codes) if c not in listed and catalog_store.course(c)]
        listed.update(out)
        return out

    if cfg.get("catalog_driven"):
        labs = {
            lab for lab_set in cfg.get("coreq_map", {}).values() for lab in lab_set
            if not catalog_store.credits(lab, default=0)
        }
        listed |= labs
        options = cfg.get("generic_options") or {}
        for section in cfg.get("display_sections", []):
            codes = []
            for row in section["rows"]:
                if row["type"] == "course":
                    codes.append(row["code"])
                elif row["type"] == "generic" and len(options.get(row["id"], [])) <= 60:
                    # (Broad "any ACTG/BIA/... course" rows are left to search)
                    codes += options.get(row["id"], [])
            codes = sorted(take(codes), key=lambda c: c not in required)
            if codes:
                sections.append({"title": section["label"], "codes": codes, "collapsed": False})
        if cfg.get("concentration_elective_hours"):
            more = take(catalog_store.codes_for_prefix(cfg["prefix"]))
            if more:
                sections.append({
                    "title": f"More {cfg['prefix']} courses",
                    "note": "count toward upper-division electives",
                    "codes": more,
                    "collapsed": True,
                })
    else:
        codes = take(c for prefix in major_prefixes(cfg) for c in catalog_store.codes_for_prefix(prefix))
        by_level = {}
        for code in codes:
            by_level.setdefault(f"{code.split()[1][0]}000-Level", []).append(code)
        sections = [{"title": level, "codes": by_level[level], "collapsed": False} for level in sorted(by_level)]
    return sections


def _checklist_prereqs(code, on_page):
    """Prerequisites of *code* that are also on this checklist -- the ones
    checking *code* also checks. An "A or B" prerequisite counts only when
    exactly one option is on the checklist (otherwise there's no telling
    which one the student took); prerequisites from outside the program
    (often skippable by placement score) are never assumed."""
    required, groups = catalog_store.prerequisite_options(code)
    picks = {p for p in required if p in on_page}
    for group in groups:
        listed = [p for p in group if p in on_page]
        if len(listed) == 1:
            picks.add(listed[0])
    return sorted(picks - {code})


def _course_card(code, required, min_grades, on_page=()):
    info = catalog_store.course(code) or {}
    title = info.get("title", "")
    offered = catalog_store.offering_summary(code)
    seasons = catalog_store.offered_seasons(code)
    # Hover details: everything about the course in one tooltip
    note = [f"{code} - {title}"]
    if info.get("credits") is not None:
        credits = info["credits"]
        note.append(f"{int(credits) if float(credits).is_integer() else credits} credit hours")
    note.append("Offered: " + (offered or " & ".join(s.lower() for s in seasons)))
    if code in required:
        note.append("Required by name for this program")
    if min_grades.get(code):
        note.append(f"Requires a {min_grades[code]} or better (a later course needs it)")
    if info.get("desc"):
        note.append(info["desc"])
    return {
        "code": code,
        "title": title,
        "label": f"{code} - {title}",
        "required": code in required,
        "minimum_grade": min_grades.get(code),
        "offered": offered,
        "seasons": " ".join(s.lower() for s in seasons),
        "note": "\n".join(note),
        "prereqs": _checklist_prereqs(code, on_page),
    }


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
    resp = Response(json.dumps(catalog_store.search_index()), mimetype="application/json")
    resp.set_etag(f"courses-{version}")
    resp.cache_control.public = True
    resp.cache_control.max_age = 86400
    return resp.make_conditional(request)


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

    display_prefixes = list(dict.fromkeys(
        major_prefixes(degree_cfg) + (major_prefixes(second_cfg) if second_cfg else [])
    ))

    # The second major's courses and electives folded in, so the requirement
    # rows and free-elective hours on the form match what will be planned
    form_cfg = degree_cfg
    if second_cfg:
        form_cfg, _ = apply_second_major_to_config(degree_cfg, second_cfg, set())

    # One checklist block per major; the second major skips courses the
    # first already lists
    primary_sections = _checklist_sections(degree_cfg)
    shown = {c for sec in primary_sections for c in sec["codes"]}
    second_sections = _checklist_sections(second_cfg, exclude=shown) if second_cfg else []

    # Minimum grades that a course's prerequisites call for, shown as a tooltip
    all_codes = shown | {c for sec in second_sections for c in sec["codes"]}
    min_grades = {}
    for code in all_codes:
        min_grades.update((catalog_store.course(code) or {}).get("min_grades", {}))

    checklist = []
    for cfg, sections in ((degree_cfg, primary_sections), (second_cfg, second_sections)):
        if cfg is None:
            continue
        required = _required_codes(cfg)
        checklist.append({
            "name": cfg["name"],
            "sections": [
                {**sec, "courses": [_course_card(c, required, min_grades, all_codes) for c in sec["codes"]]}
                for sec in sections
            ],
        })
    course_count = len(all_codes)

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
        checklist=checklist,
        course_count=course_count,
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


# MTSU's own Schedule Planner: pick sections by time, days and open seats
SCHEDULE_PLANNER_URL = "https://sched.mtsu.edu/#/form"

# Course equivalencies (old code -> current code) rarely change; read them at
# most every few minutes. A failure is remembered too, so a database that's
# gone (or a DATABASE_URL left pointing at a deleted project) costs one
# timeout per interval instead of one per plan.
_EQUIV_TTL = 600
_equiv_cache = {"at": -_EQUIV_TTL, "rows": []}


class _Equivalency:
    def __init__(self, old, new):
        self.old_course_code, self.new_course_code = old, new


def _course_equivalencies():
    now = time.monotonic()
    if now - _equiv_cache["at"] < _EQUIV_TTL:
        return _equiv_cache["rows"]
    rows = []
    try:
        with SessionLocal() as db_session:
            rows = [_Equivalency(r.old_course_code, r.new_course_code)
                    for r in db_session.query(CourseEquivalency).all()]
    except Exception:
        app.logger.warning("Course equivalencies unavailable (database unreachable?)")
    _equiv_cache.update(at=now, rows=rows)
    return rows


_COURSE_CODE_RE = re.compile(r"[A-Z]{2,5} \d{4}[A-Z]?")
MAX_COMPLETED_COURSES = 200
MAX_MINORS = 4


def _read_course_grades(raw):
    """{code: grade} from the hidden course_grades field, keeping only
    well-formed entries (anything else is ignored, never a 500)."""
    if not raw or len(raw) > 8000:
        return {}
    try:
        data = json.loads(raw)
    except (ValueError, RecursionError):
        return {}
    if not isinstance(data, dict):
        return {}
    grades = {}
    for code, grade in list(data.items())[:MAX_COMPLETED_COURSES]:
        if isinstance(code, str) and isinstance(grade, str):
            code = " ".join(code.split()).upper()
            grade = grade.strip().upper()
            if _COURSE_CODE_RE.fullmatch(code) and grade in GRADE_RANK:
                grades[code] = grade
    return grades


def _plan():
    completed_text = request.form.get("completed_courses", "")

    # Convert the submitted course list into course codes. Only things shaped
    # like a course code ("CSCI 1170") count, and a whole degree is well under
    # MAX_COMPLETED_COURSES -- this keeps junk and oversized input out.
    completed_courses = set()
    for line in completed_text.splitlines():
        code = " ".join(line.split()).upper()
        if _COURSE_CODE_RE.fullmatch(code):
            completed_courses.add(code)
            if len(completed_courses) >= MAX_COMPLETED_COURSES:
                break

    # Read grades for completed prerequisite courses that require a minimum grade
    course_grades = _read_course_grades(request.form.get("course_grades", ""))

    # Read selected major(s) and get the full degree configs
    degree_cfg = get_full_degree_config(request.form.get("major", "cs"))
    second_cfg = _second_major_cfg(degree_cfg, request.form.get("second_major"))
    all_major_prefixes = major_prefixes(degree_cfg) + (major_prefixes(second_cfg) if second_cfg else [])

    # Optional minors -- unknown ones, repeats, and ones in either major's
    # own subject are dropped
    minor_cfgs = []
    for key in request.form.getlist("minor")[:MAX_MINORS * 3]:
        if len(minor_cfgs) >= MAX_MINORS:
            break
        minor_cfg = get_minor_config(key)
        if (
            minor_cfg
            and minor_cfg["name"] not in {m["name"] for m in minor_cfgs}
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

    # A checked lecture counts for its 0-credit lab (catalog majors don't
    # even list those labs); labs that carry credit still get a warning
    # below if they're missing
    for cfg_item in (degree_cfg, second_cfg, *minor_cfgs):
        if cfg_item:
            for lecture, labs in cfg_item.get("coreq_map", {}).items():
                if lecture in completed_courses:
                    completed_courses |= {lab for lab in labs if not catalog_store.credits(lab, default=0)}

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
    # Hours earned so far, for class standing ("junior standing" = 60):
    # the checked-off courses plus hours typed in for requirement rows
    # (checked courses only fill what typed hours leave open, so the two
    # don't overlap)
    earned_hours = sum(catalog_store.credits(code, default=3) or 0 for code in validated_completed_courses) + sum(
        min(max(_as_float(value), 0.0), 200.0) for value in generic_hours.values()
        if math.isfinite(_as_float(value))
    )

    # Requirements that list specific courses ("Choose one: MATH 1530 or
    # BIA 2610") fill in from the courses the student checked off
    # ("Required" here means the primary major's own courses: a course the
    # second major requires can still fill the primary's gen-ed rows.)
    auto_hours, auto_applied = auto_generic_hours(
        planning_cfg, validated_completed_courses, required=_required_codes(degree_cfg),
        typed=generic_hours,
    )
    for gid, hours in auto_hours.items():
        generic_hours[gid] = max(_as_float(generic_hours.get(gid)), hours)

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
    # (Same for a second major that requires a minor.)
    audit_generic_hours = dict(generic_hours)
    minor_buckets = {cfg_item.get("minor_bucket_id") for cfg_item in (degree_cfg, second_cfg) if cfg_item} - {None}
    if minor_cfgs and minor_buckets:
        planning_cfg = dict(planning_cfg)
        planning_cfg["supporting_generic"] = [
            row for row in planning_cfg["supporting_generic"] if row[0] not in minor_buckets
        ]
        minor_progress = sum(_as_float(m["completed_hours"]) for m in minor_audits)
        for bucket in minor_buckets:
            # Hours typed for the minor row still count (e.g. a minor
            # finished at another school)
            audit_generic_hours[bucket] = max(_as_float(generic_hours.get(bucket)), minor_progress)

    if second_cfg:
        second_audit = build_second_major_audit(second_cfg, completed_courses, audit_generic_hours, catalog)

    # Courses already used to fill a choice or gen-ed row aren't also
    # picked (or counted) as upper-division electives
    planning_cfg = dict(planning_cfg)
    planning_cfg["_auto_applied_codes"] = {code for codes in auto_applied.values() for code in codes}

    equivalencies = _course_equivalencies()

    # Any real catalog course the student took that no requirement used (a
    # fourth course for a full 9-hour pool, an extra gen-ed, ...) counts
    # toward free electives -- the way MTSU counts it -- instead of vanishing
    pre_audit = build_audit(
        validated_completed_courses, audit_generic_hours, catalog,
        degree_cfg={**degree_cfg, "_applied": auto_applied}, equivalencies=equivalencies,
    )
    used = {code for codes in auto_applied.values() for code in codes}
    for group in pre_audit["groups"] + (second_audit["groups"] if second_audit else []) + minor_audits:
        for entry in group["entries"]:
            used.update(entry.get("applied", []))
    for cfg_item in (degree_cfg, second_cfg, *minor_cfgs):
        if cfg_item:
            used |= _required_codes(cfg_item) | {code for code, _, _ in cfg_item.get("required_courses", [])}
    for labs in planning_cfg.get("coreq_map", {}).values():
        used |= set(labs)
    has_free_electives = any(g["key"] == "electives" for g in pre_audit["groups"])
    extra_courses = sorted(
        code for code in validated_completed_courses
        if code not in used and catalog_store.course(code)
    )
    # With no free-elective room left, extra courses are listed as not counted
    uncounted_courses = [] if has_free_electives else extra_courses
    if extra_courses and has_free_electives:
        free_hours = sum(catalog_store.credits(code, default=3) for code in extra_courses)
        for hours_map in (generic_hours, audit_generic_hours):
            hours_map[ELECTIVES_GENERIC_ID] = max(_as_float(hours_map.get(ELECTIVES_GENERIC_ID)), free_hours)
        auto_applied = {**auto_applied, ELECTIVES_GENERIC_ID: extra_courses}

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
        earned_hours=earned_hours,
    )

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

    # A course that only runs in summer can't fit a plan without summers
    if not include_summer:
        for term, items in plan.items():
            if term.startswith("Remaining"):
                for item in items:
                    if item.get("kind") == "course" and catalog_store.summer_only(item["code"]):
                        prereq_warnings.append({
                            "course": item["code"], "term": None, "prereq": None, "type": "summer_only",
                        })

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
    if second_audit:
        for group in second_audit["groups"]:
            for entry in group["entries"]:
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
                "offered": catalog_store.offering_summary(item["code"]),
                "check_prereq": prereq_notes.get(item["code"]),
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

    # Courses whose catalog prerequisites aren't done or planned earlier --
    # usually a gen-ed course the plan covers with a "GEN" row, an ACT-score
    # alternative, or "permission of instructor"; the tag shows the catalog's
    # own wording so the student can check
    prereq_notes = {}
    seen = set(completed_courses)
    for term, term_items in plan.items():
        codes = {item["code"] for item in term_items if item.get("kind") == "course"}
        if not term.startswith("Remaining"):
            for code in codes:
                info = catalog_store.course(code) or {}
                missing = [p for p in info.get("prereqs", []) if p not in seen and catalog_store.course(p)]
                missing += [" or ".join(g) for g in info.get("prereq_any", []) if not seen.intersection(g)]
                if missing and info.get("prereq_text"):
                    prereq_notes[code] = "Prerequisite (catalog): " + info["prereq_text"]
        seen |= codes

    # Convert planner data into display structures
    completed_display = [completed_label(code) for code in sorted(completed_courses)]

    plan_display = {
        term: [display_item(item) for item in term_items]
        for term, term_items in plan.items()
    }

    # The first term with real courses, for the hand-off to MTSU's Schedule
    # Planner (sections, times, open seats) where students build a schedule
    next_term, next_courses = None, []
    for term, term_items in plan.items():
        codes = [item["code"] for item in term_items if item.get("kind") == "course"]
        if codes and not term.startswith("Remaining"):
            next_term, next_courses = term, codes
            break

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
        next_term=next_term,
        next_courses=next_courses,
        schedule_planner_url=SCHEDULE_PLANNER_URL,
        uncounted_courses=[c for c in uncounted_courses if c not in unrecognized_courses],
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
    errors = []
    total = 0
    try:
        from scrape_courses import RAW_DIR, export

        data = export(out_file=catalog_store.RUNTIME_FILE, raw_dir=RAW_DIR, refresh=True)
        total = len(data["courses"])
        catalog_store.reload()
        clear_catalog_cache()
    except Exception as exc:
        app.logger.exception("Catalog export failed")
        errors.append(type(exc).__name__ + ": " + str(exc)[:160])
    finally:
        # Always clears the running flag, even if something above raised --
        # otherwise the lock never clears.
        try:
            _set_sync_state(
                running=False, total=total, fresh=total, cached=0,
                errors=errors, done=True,
            )
        except Exception:
            app.logger.exception("Couldn't record catalog export status")


@app.route("/sync", methods=["POST"])
@limiter.limit("5 per minute")
def sync():
    """Re-export the catalog on a running server (admin only).

    The normal way to refresh data is `python scrape_courses.py` + commit (or
    the monthly GitHub Action); this is for an urgent fix between deploys.
    POST with an X-Requested-With header, so a link or image on another site
    can't trigger it with a browser's cached admin credentials:

        curl -u admin:pass -X POST -H "X-Requested-With: curl" https://.../sync
    """
    if os.getenv("VERCEL"):
        # Serverless functions freeze after responding; the export would never finish
        return jsonify({"status": "unavailable", "reason": "Refresh the catalog with scrape_courses.py and redeploy."}), 501
    if not _admin_auth_ok():
        return _admin_auth_challenge()
    if not request.headers.get("X-Requested-With"):
        return jsonify({"status": "error", "reason": "X-Requested-With header required"}), 400
    try:
        claimed = _claim_sync_lock()
    except Exception:
        app.logger.exception("Couldn't claim the catalog export lock")
        return jsonify({"status": "error", "reason": "database unavailable"}), 503
    if not claimed:
        return jsonify({"status": "running", "total": _get_sync_state()["total"]})
    threading.Thread(target=_run_sync_background, daemon=True).start()
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
    ok = catalog_store.has_data()
    sched = catalog_store.schedule() or {}

    def _age_days(stamp):
        try:
            return (date.today() - date.fromisoformat((stamp or "")[:10])).days
        except ValueError:
            return None

    catalog_age = _age_days(meta.get("fetched_at"))
    schedule_age = _age_days(sched.get("fetched_at"))
    return jsonify({
        "ok": ok,
        # The refresh job runs monthly (catalog) and weekly (schedule); data
        # much older than that means it has been failing
        "stale": bool((catalog_age or 0) > 45 or (schedule_age or 0) > 21),
        "catalog_age_days": catalog_age,
        "schedule_fetched_at": sched.get("fetched_at"),
        "schedule_age_days": schedule_age,
        "schedule_terms": [f"{t['season']} {t['year']}" for t in sched.get("terms", [])],
        "catalog": meta.get("name"),
        "fetched_at": meta.get("fetched_at"),
        "courses": len(catalog_store.courses()),
        "majors": len(picker_majors()),
        "minors": len(picker_minors()),
    }), (200 if ok else 503)


if __name__ == "__main__":
    # Local development only; production runs under gunicorn (see Procfile)
    app.run(debug=os.getenv("FLASK_DEBUG") == "1", host="127.0.0.1", port=5000)
