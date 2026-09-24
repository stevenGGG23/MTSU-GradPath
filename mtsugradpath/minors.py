"""Optional minors a student can add on top of their major.

Each minor config has:
  key, name, prefix
  required_courses:      [(code, hours, title), ...]
  elective_hours:        hours of electives beyond the required courses
  elective_min_number:   lowest course number (same prefix) that counts as a
                         minor elective, e.g. 2000 excludes gen-ed MATH 1xxx
  elective_suggestions:  example electives shown in the planner and audit
  total_hours:           required course hours + elective_hours
  description:           one-line summary of the catalog rule

A minor is planned by folding it into the major's config (see
apply_minor_to_config): its required courses become supporting courses and
its electives become a generic requirement, so the existing planner handles
prerequisites and offering seasons. Minor hours come out of the major's free
electives rather than adding to the 120 -- which is how a minor actually fits
into an MTSU degree.
"""

# Generic requirement ids for minor electives start with this, so the
# planner can split them into course-sized chunks.
MINOR_ELECTIVE_ID_PREFIX = "minor_elective_"

# ── Mathematics ───────────────────────────────────────────────────────────────────
# Per MTSU Department of Mathematical Sciences: 18 hours including MATH 1910
# and 1920; the remaining 10 hours are chosen from mathematics courses for
# majors and minors with the minor advisor's approval.
MATH_MINOR = {
    "key": "math",
    "name": "Mathematics Minor",
    "prefix": "MATH",
    "required_courses": [
        ("MATH 1910", 4, "Calculus I"),
        ("MATH 1920", 4, "Calculus II"),
    ],
    "elective_hours": 10,
    "elective_min_number": 2000,
    "elective_suggestions": [
        "MATH 2010 - Elements of Linear Algebra",
        "MATH 3110 - Calculus III",
        "MATH 3120 - Ordinary Differential Equations",
    ],
    "total_hours": 18,
    "description": "MATH 1910 and 1920 plus 10 hours of approved MATH electives.",
}

MINOR_CONFIGS = {
    "math": MATH_MINOR,
}

# Name kept for the index template's minor search list.
AVAILABLE_MINORS = MINOR_CONFIGS


def get_minor_config(key):
    """Return the minor config for *key*, or None when no minor is chosen."""
    return MINOR_CONFIGS.get(key or "")


def minor_allowed_for_major(minor_cfg, major_prefixes):
    """A student can't minor in their own major's subject (with a double
    major, pass both majors' prefixes)."""
    return minor_cfg["prefix"] not in major_prefixes


def _course_number(code):
    parts = code.split()
    if len(parts) != 2 or not parts[1].isdigit():
        return None
    return int(parts[1])


def _nice(number):
    return int(number) if float(number).is_integer() else number


def minor_elective_codes(minor_cfg, completed_courses):
    """Completed courses that count toward the minor's elective hours."""
    required = {code for code, _, _ in minor_cfg["required_courses"]}
    codes = []
    for code in completed_courses:
        number = _course_number(code)
        if (
            number is not None
            and code.split()[0] == minor_cfg["prefix"]
            and number >= minor_cfg["elective_min_number"]
            and code not in required
        ):
            codes.append(code)
    return sorted(codes)


def minor_elective_hours_done(minor_cfg, completed_courses, catalog=None):
    catalog = catalog or {}
    hours = sum(
        (catalog.get(code) or {}).get("credits") or 3
        for code in minor_elective_codes(minor_cfg, completed_courses)
    )
    return min(hours, minor_cfg["elective_hours"])


def _split_hours(total, size=3):
    """Split *total* hours into course-sized chunks; a remainder smaller than
    a course folds into the last chunk (10 -> 3, 3, 4) instead of leaving a
    1-hour stub."""
    chunks = []
    remaining = total
    while remaining > 0:
        if remaining < 2 * size:
            chunks.append(remaining)
            break
        chunks.append(size)
        remaining -= size
    return chunks


def apply_minor_to_config(degree_cfg, minor_cfg, completed_courses, catalog=None):
    """Return (planning_cfg, generic_hours_extra) with the minor folded in.

    planning_cfg is a shallow copy of *degree_cfg* -- the original config is
    shared module state and must not be mutated. generic_hours_extra holds the
    already-completed hours for each minor elective chunk, to be merged into
    the student's generic_hours before planning.
    """
    cfg = dict(degree_cfg)
    major_codes = {
        code
        for key in ("core_courses", "concentration_courses", "supporting_courses")
        for code, _, _ in degree_cfg[key]
    }

    extra_supporting = [
        course for course in minor_cfg["required_courses"] if course[0] not in major_codes
    ]
    cfg["supporting_courses"] = list(degree_cfg["supporting_courses"]) + extra_supporting

    done = minor_elective_hours_done(minor_cfg, completed_courses, catalog)
    chunks = _split_hours(minor_cfg["elective_hours"])
    generic_extra = {}
    minor_generic = []
    for index, hours in enumerate(chunks, start=1):
        # Keyed by minor so several minors' electives don't share an id
        generic_id = f"{MINOR_ELECTIVE_ID_PREFIX}{minor_cfg['key']}_{index}"
        minor_generic.append((
            generic_id,
            f"{minor_cfg['name']} elective",
            hours,
            minor_cfg["elective_suggestions"][index - 1:] or minor_cfg["elective_suggestions"],
        ))
        applied = min(done, hours)
        generic_extra[generic_id] = applied
        done -= applied
    cfg["supporting_generic"] = list(degree_cfg["supporting_generic"]) + minor_generic

    return cfg, generic_extra


def build_minor_audit(minor_cfg, completed_courses, catalog=None):
    """Audit group for the minor, in the same shape as build_audit's groups."""
    completed_courses = {c.strip().upper() for c in completed_courses}
    entries = []
    hours_done = 0

    for code, hours, title in minor_cfg["required_courses"]:
        done = code in completed_courses
        if done:
            hours_done += hours
        entries.append({"label": f"{code} - {title}", "hours": hours, "done": done})

    elective_codes = minor_elective_codes(minor_cfg, completed_courses)
    elective_done = minor_elective_hours_done(minor_cfg, completed_courses, catalog)
    hours_done += elective_done
    entries.append({
        "label": f"{minor_cfg['prefix']} electives ({len(elective_codes)} course(s) applied)",
        "hours": minor_cfg["elective_hours"],
        "done": elective_done >= minor_cfg["elective_hours"],
        "partial_hours": _nice(elective_done),
        "suggestions": minor_cfg["elective_suggestions"],
    })

    required = minor_cfg["total_hours"]
    return {
        "key": "minor",
        "label": minor_cfg["name"],
        "description": minor_cfg["description"],
        "required_hours": _nice(required),
        "completed_hours": _nice(hours_done),
        "percent": round(100 * hours_done / required, 1) if required else 0,
        "entries": entries,
        "course_codes": {code for code, _, _ in minor_cfg["required_courses"]},
    }
