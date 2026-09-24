"""Planning a second major on top of the primary one.

A double major is planned the same way a minor is: the second major's
required courses are folded into a copy of the primary major's config as
supporting courses, and its upper-division elective hours become generic
requirements. The planner already resolves a supporting course's
prerequisites and offering seasons from the course's own home major, so the
second major's courses keep their real sequencing.

General education (True Blue Core) is shared between the two majors, so only
the primary major's gen-ed rows are used. The second major's courses come out
of the primary's free electives first; anything beyond that runs past 120
hours, which is how a real double major works.
"""

from mtsugradpath.degree import build_audit

# Generic requirement ids for second-major electives start with this.
SECOND_MAJOR_ELECTIVE_ID_PREFIX = "second_major_elective_"

_COURSE_KEYS = ("core_courses", "concentration_courses", "supporting_courses")


def _course_number(code):
    parts = code.split()
    if len(parts) != 2 or not parts[1].isdigit():
        return None
    return int(parts[1])


def _nice(number):
    return int(number) if float(number).is_integer() else number


def _split_hours(total, size=3):
    """10 -> [3, 3, 4]: a remainder smaller than a course folds into the last chunk."""
    chunks = []
    remaining = total
    while remaining > 0:
        if remaining < 2 * size:
            chunks.append(remaining)
            break
        chunks.append(size)
        remaining -= size
    return chunks


def config_course_codes(cfg):
    """Every course code a config names in its course lists."""
    return {code for key in _COURSE_KEYS for code, _, _ in cfg[key]}


def second_major_allowed(primary_cfg, second_cfg):
    """A second major must be a different program with a different subject."""
    return (
        second_cfg is not None
        and second_cfg["key"] != primary_cfg["key"]
        and second_cfg["prefix"] != primary_cfg["prefix"]
    )


def second_major_elective_codes(second_cfg, completed_courses):
    """Completed courses that count toward the second major's elective hours."""
    required = {code for code, _, _ in second_cfg["core_courses"] + second_cfg["concentration_courses"]}
    min_num = second_cfg.get("upper_division_min", 3000)
    return sorted(
        code for code in completed_courses
        if code.split()[0] == second_cfg["prefix"]
        and (_course_number(code) or 0) >= min_num
        and code not in required
    )


def second_major_elective_hours_done(second_cfg, completed_courses, catalog=None):
    catalog = catalog or {}
    hours = sum(
        (catalog.get(code) or {}).get("credits") or 3
        for code in second_major_elective_codes(second_cfg, completed_courses)
    )
    return min(hours, second_cfg.get("concentration_elective_hours", 0))


def extra_supporting_generic(primary_cfg, second_cfg):
    """The second major's supporting generic rows the primary doesn't already have.

    These are entered by hand on the planner form, like the primary's own rows.
    """
    primary_ids = {gid for gid, _, _, _ in primary_cfg["supporting_generic"] + primary_cfg["tbc_generic"]}
    return [row for row in second_cfg["supporting_generic"] if row[0] not in primary_ids]


def apply_second_major_to_config(primary_cfg, second_cfg, completed_courses, catalog=None):
    """Return (planning_cfg, generic_hours_extra) with the second major folded in.

    planning_cfg is a shallow copy -- degree configs are shared module state.
    generic_hours_extra holds the already-completed hours for each
    second-major elective chunk.
    """
    cfg = dict(primary_cfg)
    known = config_course_codes(primary_cfg)

    extra_courses = []
    for key in _COURSE_KEYS:
        for course in second_cfg[key]:
            if course[0] not in known:
                extra_courses.append(course)
                known.add(course[0])
    cfg["supporting_courses"] = list(primary_cfg["supporting_courses"]) + extra_courses

    generic_extra = {}
    elective_rows = []
    done = second_major_elective_hours_done(second_cfg, completed_courses, catalog)
    suggestions = [f"Any upper-division {second_cfg['prefix']} course not already required"]
    for index, hours in enumerate(_split_hours(second_cfg.get("concentration_elective_hours", 0)), start=1):
        generic_id = f"{SECOND_MAJOR_ELECTIVE_ID_PREFIX}{index}"
        elective_rows.append((generic_id, f"{second_cfg['prefix']} elective (second major)", hours, suggestions))
        applied = min(done, hours)
        generic_extra[generic_id] = applied
        done -= applied

    cfg["supporting_generic"] = (
        list(primary_cfg["supporting_generic"])
        + extra_supporting_generic(primary_cfg, second_cfg)
        + elective_rows
    )
    # Supporting-course prerequisites the second major declares for its own
    # supporting courses (e.g. a lab needing its lecture) still apply.
    cfg["supporting_prereq_map"] = {
        **second_cfg.get("supporting_prereq_map", {}),
        **primary_cfg.get("supporting_prereq_map", {}),
    }
    return cfg, generic_extra


def build_second_major_audit(second_cfg, completed_courses, generic_hours, catalog=None):
    """Audit summary for the second major's own requirements (no gen-ed or free
    electives -- those are shared with and counted on the primary major)."""
    audit = build_audit(completed_courses, generic_hours, catalog, degree_cfg=second_cfg)
    groups = [g for g in audit["groups"] if g["key"] in ("core", "concentration", "supporting")]
    for group in groups:
        group["label"] = f"{second_cfg['name']}: {group['label'].replace(second_cfg['name'] + ' ', '')}"

    required = sum(g["required_hours"] for g in groups)
    completed = sum(g["completed_hours"] for g in groups)
    return {
        "key": "second",
        "label": f"{second_cfg['name']} (second major)",
        "required_hours": _nice(required),
        "completed_hours": _nice(completed),
        "percent": round(100 * completed / required, 1) if required else 0,
        "groups": groups,
        "course_codes": config_course_codes(second_cfg),
    }
