import re
import time
from datetime import date
from typing import List, Set, Dict

from . import catalog_store
from .config import PROGRAM_PREFIX
from .degree import (
    CS_CORE_COURSES,
    CONCENTRATION_REQUIRED_COURSES,
    CONCENTRATION_ELECTIVE_HOURS,
    HIGH_LEVEL_LANGUAGE_OPTIONS,
    SUPPORTING_COURSES,
    ELECTIVES_GENERIC_ID,
    is_upper_division_csci,
    generic_remaining,
    course_offered_in_term,
    clamp_hours,
)
from .degree_configs import CS_CONFIG, DEGREE_CONFIGS, get_full_degree_config, major_prefixes
# Maps a course prefix (e.g. "MATH") to that major's own config, so a
# cross-prefix requirement -- e.g. a Physics major's PHYS course needing a
# MATH course -- can be checked against the offering pattern MATH's own
# config declares, not (incorrectly) against the current major's. A major's
# extra_prefixes (e.g. Construction Management's CCM courses) map to it too.
_PREFIX_TO_CONFIG = {p: c for c in DEGREE_CONFIGS.values() for p in major_prefixes(c)}

# ── Catalog cache ──────────────────────────────────────────────────────────────
# Per-prefix planner catalogs built from the in-memory catalog export. Cheap to
# build, but cached anyway since every plan for a major asks for the same one.
_catalog_cache: Dict[str, tuple] = {}  # prefix -> (monotonic_time, catalog_dict)
_CATALOG_TTL = 300  # seconds (5 min)


def clear_catalog_cache() -> None:
    """Flush cached planner catalogs -- called after a catalog re-export."""
    _catalog_cache.clear()


# Function that checks whether *code* is offered in a given season/year,
# using the offering pattern from the course's own major (not necessarily
# degree_cfg, the major currently being planned) -- a supporting course like
# MATH 1910 on a Physics plan is restricted by MATH's own config.
def _offered_in_term(code: str, season: str, year: int, degree_cfg: dict) -> bool:
    # MTSU's own class schedules come first: a posted term says exactly what
    # runs, and past terms show fall-only / no-summer / alternate-year patterns
    observed = catalog_store.offered_in(code, season, year)
    if observed is not None:
        return observed
    code_prefix = code.split()[0] if " " in code else None
    home_cfg = (
        degree_cfg if code_prefix == degree_cfg.get("prefix")
        else _PREFIX_TO_CONFIG.get(code_prefix)
    )
    # A catalog-generated major has no offering data of its own; the
    # hand-curated config for the same subject (e.g. CS_CONFIG for any CSCI
    # course) is the best source we have.
    if home_cfg is not None and home_cfg.get("catalog_driven") and code_prefix in _PREFIX_TO_CONFIG:
        home_cfg = _PREFIX_TO_CONFIG[code_prefix]
    if home_cfg is None:
        # Never seen in a recent schedule: assume fall/spring, not summer
        return season != "Summer" or catalog_store.schedule() is None
    if code in home_cfg.get("odd_year_spring_only", set()):
        return season == "Spring" and year % 2 == 1
    allowed = home_cfg.get("offering_seasons", {}).get(code)
    if allowed is None:
        return True
    return season in allowed


# Function that merges a course's declared prerequisites the same way for
# both scheduling (generate_plan) and the independent check (validate_plan).
#
# Two different sources, two different trust levels:
#  - catalog[code]["prereqs"] is regex-parsed from free-form catalog
#    description text -- reliable for courses within the major's own prefix,
#    but noisy outside it (e.g. CSCI 1170's description mentions placement
#    courses MATH 1730/1810, which aren't real degree prerequisites), so
#    it's restricted to *prefix*.
#  - prereq_map is hand-curated by the team and always trusted in full,
#    including legitimate cross-major entries (e.g. PHYS 2120 needing
#    MATH 1910) -- never prefix-filtered.
# prereq_override_map patches specific catalog quirks (e.g. a lab-section
# code substituted for the lecture course) and replaces just the
# catalog-derived component; it's still merged with prereq_map, never a full
# replacement, so a legitimate prereq_map entry for the same course doesn't
# get silently dropped just because an override also exists.
def _effective_prereqs(
    code: str, catalog: dict, prereq_map: dict, prereq_override_map: dict, prefix: str
) -> Set[str]:
    if code in prereq_override_map:
        catalog_prereqs = set(prereq_override_map[code])
    elif code in catalog:
        catalog_prereqs = {p for p in catalog[code].get("prereqs", set()) if p.startswith(prefix)}
    else:
        catalog_prereqs = set()
    return catalog_prereqs | prereq_map.get(code, set())


# Function that returns a course's full prerequisite set regardless of which
# major's course list it belongs to on the plan currently being built.
#
# A "supporting course" on one major's plan (e.g. PHYS 2120 on a Chemistry
# plan) is a full major-required course on its *own* major's plan, and that
# home config's prereq_map/prereq_override_map is the authoritative source
# for its real prerequisites (e.g. PHYS 2120 needing MATH 1910) -- a
# major's own supporting_prereq_map only encodes what that major's own team
# bothered to declare, which isn't guaranteed complete (see CHEM_CONFIG's
# supporting_prereq_map for PHYS 2120: only lists PHYS 2110). Catalog-scraped
# text is only consulted for a cross-prefix course when the caller loaded
# that prefix into the catalog (a second major), and then only its
# same-prefix prereqs, since scraped text is only reliable within a course's
# own major's catalog.
def plan_universe(cfg: dict) -> Set[str]:
    """Every course a config schedules by name."""
    codes = {code for key in ("core_courses", "concentration_courses", "supporting_courses")
             for code, _, _ in cfg.get(key, [])}
    return codes | set(cfg.get("high_level_options", []))


def _trim_to_plan(prereqs: Set[str], code: str, home_cfg: dict, universe: Set[str]) -> Set[str]:
    """Drop catalog-scraped prerequisites the plan never schedules.

    "MATH 1710 or high school precalculus" can't be enforced against a plan
    that doesn't contain MATH 1710 -- the course would wait forever. The
    team's curated prereq_map / prereq_override_map entries always stay.
    """
    curated = set(home_cfg.get("prereq_map", {}).get(code, ())) | set(
        home_cfg.get("prereq_override_map", {}).get(code, ()) or ()
    )
    return {p for p in prereqs if p in universe or p in curated}


def _full_prereqs(code: str, cfg: dict, catalog: dict) -> Set[str]:
    code_prefix = code.split()[0] if " " in code else None
    # Catalog-generated configs (and minors/second majors folded into a plan)
    # carry a resolved prerequisite set for the courses they plan
    override = cfg.get("prereq_override_map", {})
    universe = plan_universe(cfg)
    if cfg.get("catalog_driven"):
        # Catalog programs: the program's own resolved prerequisites, plus
        # every catalog prerequisite that's also on this plan, whatever its
        # prefix -- with a second major or minor folded in, a prerequisite
        # the other program brings (MATH 1730 before MATH 1910) counts too.
        # (The catalog's prerequisite graph has no loops; any it ever has
        # are dropped in catalog_store.)
        prereqs = set(override.get(code, ())) | set(cfg.get("prereq_map", {}).get(code, ()))
        if code in catalog:
            prereqs |= {p for p in catalog[code].get("prereqs", set()) if p in universe}
        return prereqs
    if code in override and code_prefix != cfg.get("prefix"):
        return set(override[code]) | set(cfg.get("prereq_map", {}).get(code, set()))
    if code_prefix == cfg.get("prefix"):
        return _trim_to_plan(_effective_prereqs(
            code, catalog, cfg.get("prereq_map", {}), cfg.get("prereq_override_map", {}), cfg["prefix"]
        ), code, cfg, universe)
    home_cfg = _PREFIX_TO_CONFIG.get(code_prefix)
    if home_cfg is None:
        return set()
    # A second major's courses are loaded into the catalog alongside the
    # primary's, so their scraped prereqs are available -- trusted only
    # within the course's own prefix, same as for the primary major.
    if code in catalog:
        return _trim_to_plan(_effective_prereqs(
            code, catalog, home_cfg.get("prereq_map", {}),
            home_cfg.get("prereq_override_map", {}), code_prefix,
        ), code, home_cfg, universe)
    required = set(home_cfg.get("prereq_map", {}).get(code, set()))
    override = home_cfg.get("prereq_override_map", {}).get(code)
    if override is not None:
        required |= set(override)
    return required

# Required CSCI courses used when building the degree planner
REQUIRED_COURSES = [code for code, _, _ in CS_CORE_COURSES] + [
    code for code, _, _ in CONCENTRATION_REQUIRED_COURSES
]

# Catalog used for prerequisite information is not available
PREREQUISITE_MAP = {
    "CSCI 2170": {"CSCI 1170"},
    "CSCI 3080": {"CSCI 1170"},
    "CSCI 3110": {"CSCI 2170", "CSCI 3080"},
    "CSCI 3130": {"CSCI 2170"},
    "CSCI 3240": {"CSCI 2170", "CSCI 3130"},
    "CSCI 3210": {"CSCI 3110"},
    "CSCI 4160": {"CSCI 3080", "CSCI 3110", "CSCI 3130"},
    "CSCI 4700": {"CSCI 3080", "CSCI 3110", "CSCI 3240"},
}

TARGET_HOURS_PER_TERM = 15
MAX_HOURS_PER_TERM = 17  # only when a plan would otherwise run past its last term

# When supporting course prerequisites are outside the CSCI catalog information
SUPPORTING_PREREQUISITE_MAP = {
    "MATH 1920": {"MATH 1910"},
}

GENERIC_CHUNK_SIZE = 3

# Semester order upon planner generation
SEASON_CYCLE = ["Spring", "Summer", "Fall"]

# Function that returns the default starting semester by month
def default_start_season(today=None):
    month = (today or date.today()).month

    if month in (1, 2, 3, 4):
        return "Spring"

    if month in (5, 6, 7):
        return "Summer"

    return "Fall"

# Function that creates a semester label
def term_label(term_index: int, start_season: str, start_year: int) -> str:
    start_pos = SEASON_CYCLE.index(start_season) if start_season in SEASON_CYCLE else 0

    slot = start_pos + term_index - 1
    season = SEASON_CYCLE[slot % 3]
    year = start_year + slot // 3

    return f"{season} {year}"

# Function that extracts course codes from the catalog
def parse_prereq_codes(text: str) -> Set[str]:
    if not text:
        return set()

    matches = re.findall(r"\b([A-Z]{2,4})\s*(\d{4})\b", text)

    return {f"{prefix} {number}" for prefix, number in matches}

    # Extracts minimum-grade requirements for prerequisite courses
def parse_prereq_grade_requirements(text: str) -> Dict[str, str]:
    if not text:
        return {}

    grade_match = re.search(
        r"\b(?:grade(?:s)?\s+of\s+|a\s+)?([ABCDF][+-]?)"
        r"(?:\s*\(\d+(?:\.\d+)?\))?\s+or\s+better\b",
        text,
        re.IGNORECASE,
    )

    if not grade_match:
        return {}

    minimum_grade = grade_match.group(1).upper()

    return {
        code: minimum_grade
        for code in parse_prereq_codes(text)
    }

GRADE_RANK = {
    "F": 0,
    "D-": 1,
    "D": 2,
    "D+": 3,
    "C-": 4,
    "C": 5,
    "C+": 6,
    "B-": 7,
    "B": 8,
    "B+": 9,
    "A-": 10,
    "A": 11,
    "A+": 12,
}

def meets_minimum_grade(grade: str, minimum_grade: str) -> bool:
    if not isinstance(grade, str) or not isinstance(minimum_grade, str) or not grade or not minimum_grade:
        return False

    grade = grade.strip().upper()
    minimum_grade = minimum_grade.strip().upper()

    if grade not in GRADE_RANK or minimum_grade not in GRADE_RANK:
        return False

    return GRADE_RANK[grade] >= GRADE_RANK[minimum_grade]

# Function that loads course information from the database for a given prefix
def load_catalog_courses(prefix: str = None) -> Dict[str, Dict[str, object]]:
    """Planner catalog ({code: title/credits/prereqs/prereq_grades}) for one
    course prefix, from the in-memory catalog export."""
    prefix = prefix or PROGRAM_PREFIX

    cached = _catalog_cache.get(prefix)
    if cached is not None:
        ts, data = cached
        if time.monotonic() - ts < _CATALOG_TTL:
            return data

    catalog = catalog_store.planner_catalog(prefixes=[prefix])
    _catalog_cache[prefix] = (time.monotonic(), catalog)
    return catalog


def config_course_codes_all(cfg: dict) -> Set[str]:
    """Every course a config names: required lists, choices and minor lists."""
    codes = set()
    for key in ("core_courses", "concentration_courses", "supporting_courses", "required_courses"):
        codes.update(code for code, _, _ in cfg.get(key, []))
    codes.update(cfg.get("high_level_options", []))
    for options in (cfg.get("generic_options") or {}).values():
        codes.update(options)
    codes.update(cfg.get("elective_options", []))
    return codes


def load_catalog_for(*cfgs, completed=()) -> Dict[str, Dict[str, object]]:
    """Planner catalog covering every course the given configs can plan.

    "A or B" prerequisites resolve to whichever option the student completed
    or the programs require, so nobody is told to take a course they've
    already satisfied another way.
    """
    prefixes, codes, required = set(), set(), set()
    for cfg in cfgs:
        if not cfg:
            continue
        prefixes.add(cfg["prefix"])
        prefixes.update(cfg.get("extra_prefixes", []))
        codes |= config_course_codes_all(cfg)
        required |= plan_universe(cfg) | {code for code, _, _ in cfg.get("required_courses", [])}
    # Only options that are done or actually scheduled count -- a course that
    # is merely one choice among several (e.g. a minor elective) must not
    # become the prerequisite the plan waits on
    prefer = set(completed) | required
    return catalog_store.planner_catalog(prefixes=prefixes, codes=codes, prefer=prefer)

# Function to sort course codes by department prefix and course number
def sorted_course_codes(courses: Set[str]) -> List[str]:

    # Nested function that creates the sorting value for one course code
    def key(code: str):
        parts = code.split()
        prefix = parts[0] if parts else ""
        number = parts[1] if len(parts) > 1 else ""

        return prefix, number

    return sorted(courses, key=key)

# Function to check if a course is upper-division for a given prefix
def _is_upper_division(code: str, prefix: str, min_num: int) -> bool:
    parts = code.split()
    if len(parts) != 2 or parts[0] != prefix or not parts[1].isdigit():
        return False
    return int(parts[1]) >= min_num


def course_hours(code: str, catalog: Dict[str, Dict[str, object]], config_hours: Dict[str, float] = None,
                 fallback: float = 3) -> float:
    """A course's credit hours: the program's own figure, else the catalog's
    (0 for a 0-credit lab is a real value), else *fallback* when unknown."""
    if config_hours and config_hours.get(code) is not None:
        return config_hours[code]
    credits = (catalog.get(code) or {}).get("credits")
    return fallback if credits is None else credits


def _elective_candidates(
    completed: Set[str],
    catalog: Dict[str, Dict[str, object]],
    required_codes: Set[str],
    cfg: dict,
    exclude: Set[str] = None,
):
    """(hours still needed, candidate codes in order) for the major's
    upper-division elective hours.

    *exclude* lists courses already spoken for elsewhere in the plan
    (supporting courses, courses that filled a choice row, choice options)
    so nothing counts or gets scheduled twice.
    """
    prefix = cfg["prefix"]
    min_num = cfg.get("upper_division_min", 3000)
    elective_hours_needed_total = cfg.get("concentration_elective_hours", 12)
    high_level_opts = cfg.get("high_level_options", [])
    exclude = set(exclude or ()) | set(required_codes)
    spoken_for = set(cfg.get("_auto_applied_codes", ())) | {
        code for key in ("supporting_courses",) for code, _, _ in cfg.get(key, [])
    } | set(required_codes)

    done_electives = {
        code for code in completed
        if _is_upper_division(code, prefix, min_num) and code not in spoken_for
    }
    hours_done = sum(course_hours(code, catalog) for code in done_electives)
    hours_needed = max(elective_hours_needed_total - hours_done, 0)
    if hours_needed <= 0:
        return 0, []

    # A candidate is only useful if its own-subject prerequisites are things
    # the student has done or this plan will schedule -- otherwise it would
    # sit in "Remaining" forever waiting on a course nobody plans.
    reachable = set(completed) | set(required_codes) | plan_universe(cfg)

    def _schedulable(code):
        prereqs = {p for p in catalog.get(code, {}).get("prereqs", set()) if p.startswith(prefix)}
        return prereqs <= reachable

    candidate_codes = {
        code for code in catalog
        if _is_upper_division(code, prefix, min_num)
        and code not in exclude
        and code not in completed
        and (catalog.get(code, {}).get("credits") or 0) > 0  # skip 0-credit lab sections
        and _schedulable(code)
        # electives are interchangeable: don't pick one that only runs in summer
        and not catalog_store.summer_only(code)
    }

    # High-level language courses can be alternatives, one should be in the recommended section
    if high_level_opts:
        language_done = any(code in completed for code in high_level_opts)
        available_language_options = [code for code in high_level_opts if code in candidate_codes]

        if language_done:
            candidate_codes -= set(high_level_opts)
        elif available_language_options:
            candidate_codes -= set(available_language_options[1:])

    return hours_needed, sorted_course_codes(candidate_codes)


# Function to select upper-division electives that can fill the elective hours
# Accepts an optional degree_cfg for multi-major support; defaults to CS.
def build_elective_pool(
    completed: Set[str],
    catalog: Dict[str, Dict[str, object]],
    required_codes: Set[str],
    degree_cfg: dict = None,
    exclude: Set[str] = None,
) -> Set[str]:
    cfg = degree_cfg or CS_CONFIG
    hours_needed, candidates = _elective_candidates(completed, catalog, required_codes, cfg, exclude)
    pool = []
    hours_acc = 0
    for code in candidates:
        if hours_acc >= hours_needed:
            break
        pool.append(code)
        hours_acc += course_hours(code, catalog)
    return set(pool)


def elective_shortfall(completed, catalog, required_codes, cfg, exclude=None) -> float:
    """Upper-division elective hours the catalog has no plannable courses
    for (e.g. 24 hours of BUS electives when BUS has three courses)."""
    hours_needed, candidates = _elective_candidates(completed, catalog, required_codes, cfg, exclude)
    available = sum(course_hours(code, catalog) for code in candidates)
    return max(hours_needed - available, 0)

# Function that returns major prerequisites that are completed
# Accepts an optional prefix to filter prereqs; defaults to CSCI.
def next_courses(
    completed: Set[str],
    available: Set[str],
    catalog: Dict[str, Dict[str, object]],
    prefix: str = None,
    prereq_map: dict = None,
) -> List[str]:
    effective_prefix = prefix or PROGRAM_PREFIX
    effective_prereq_map = prereq_map if prereq_map is not None else PREREQUISITE_MAP
    candidates = []

    for course in sorted_course_codes(available):
        if course in catalog:
            prereqs = catalog[course].get("prereqs", set())
        else:
            prereqs = effective_prereq_map.get(course, set())

        prereqs = prereqs | effective_prereq_map.get(course, set())

        # Only require prereqs from this major's prefix
        required = {prereq for prereq in prereqs if prereq.startswith(effective_prefix)}

        if required.issubset(completed):
            candidates.append(course)

    return candidates


# Function that returns elective courses that remain unfinished (CS default)
def _remaining_pool(completed: Set[str], catalog: Dict[str, Dict[str, object]], required_codes: Set[str]) -> Set[str]:
    remaining_required = {code for code in required_codes if code not in completed}

    if not catalog:
        return remaining_required

    elective_pool = build_elective_pool(completed, catalog, required_codes)

    return remaining_required | elective_pool


def _consistent_coreqs(universe: Set[str], prereqs_of, catalog: Dict[str, Dict[str, object]]) -> Dict[str, Set[str]]:
    """{course: corequisites that can go in its term or earlier}, limited to
    *universe*, with any corequisite that contradicts the prerequisites left out.

    Each link is an ordering rule: a prerequisite must come strictly before,
    a corequisite no later. A corequisite link "c no later than a" is
    impossible when a path from *a* back to *c* passes through a strictly-
    before step (then c would have to come after a)."""
    strict = {code: {p for p in prereqs_of(code) if p in universe} for code in universe}
    coreqs = {
        code: {c for c in ((catalog.get(code) or {}).get("coreqs") or ()) if c in universe and c != code}
        for code in universe
    }
    # Edges x -> y: x comes no later than y (coreq) / strictly before y (prereq)
    later_than: Dict[str, List] = {}
    for code in universe:
        for p in strict[code]:
            later_than.setdefault(p, []).append((code, True))
        for c in coreqs[code]:
            later_than.setdefault(c, []).append((code, False))

    def forces_after(start, target):
        """Some path start -> ... -> target uses a strictly-before step."""
        seen = set()
        stack = [(start, False)]
        while stack:
            node, used_strict = stack.pop()
            if node == target and used_strict:
                return True
            if (node, used_strict) in seen:
                continue
            seen.add((node, used_strict))
            for nxt, is_strict in later_than.get(node, ()):
                stack.append((nxt, used_strict or is_strict))
        return False

    # One-way links are checked (and dropped) before links two courses list
    # for each other -- "take together" is the more deliberate statement
    links = sorted(
        ((code, c) for code in universe for c in coreqs[code]),
        key=lambda link: (link[0] in coreqs.get(link[1], ()), link),
    )
    for code, c in links:
        # Link: c no later than code. Impossible if code forces c later.
        if forces_after(code, c):
            coreqs[code].discard(c)
            later_than[c] = [(n, st) for n, st in later_than.get(c, []) if not (n == code and not st)]
    return {code: cs for code, cs in coreqs.items() if cs}


# Function that returns elective courses that remain unfinished (degree-cfg aware)
def _remaining_pool_cfg(
    completed: Set[str],
    catalog: Dict[str, Dict[str, object]],
    required_codes: Set[str],
    cfg: dict,
    exclude: Set[str] = None,
) -> Set[str]:
    remaining_required = {code for code in required_codes if code not in completed}

    if not catalog:
        return remaining_required

    elective_pool = build_elective_pool(completed, catalog, required_codes, degree_cfg=cfg, exclude=exclude)

    return remaining_required | elective_pool

# Function that converts supporting course information into a code-based dictionary
def _supporting_course_info() -> Dict[str, Dict[str, object]]:
    return {code: {"title": title, "credits": hours} for code, hours, title in SUPPORTING_COURSES}

# Function that creates the dictionary for displaying a course.
# config_hours is a {code: hours} dict built from the degree config's course lists;
# it is used as a fallback when catalog credits is 0 or missing (e.g. lecture/lab splits).
def _course_item(
    code: str,
    catalog: Dict[str, Dict[str, object]],
    supporting_info: Dict[str, Dict[str, object]],
    config_hours: Dict[str, float] = None,
) -> Dict[str, object]:
    cat_info = catalog.get(code) or {}
    sup_info = supporting_info.get(code) or {}
    title = cat_info.get("title", "") or sup_info.get("title", "")
    if (config_hours or {}).get(code) is not None:
        hours = config_hours[code]
    elif cat_info.get("credits") is not None:
        hours = cat_info["credits"]
    elif sup_info.get("credits") is not None:
        hours = sup_info["credits"]
    else:
        hours = 3
    label = f"{code} - {title}" if title else code

    return {
        "kind": "course",
        "code": code,
        "label": label,
        "hours": hours,
    }

# Function that converts generic requirement hours into items
def _build_generic_queue(remaining_map: Dict[str, Dict[str, object]]) -> List[Dict[str, object]]:
    queue = []

    for generic_id in remaining_map:
        info = remaining_map[generic_id]
        label = info["label"]
        hours = info["hours"]
        suggestion = info["suggestions"][0] if info.get("suggestions") else None

        if generic_id == ELECTIVES_GENERIC_ID:
            remaining_hours = hours

            while remaining_hours > 0:
                size = min(GENERIC_CHUNK_SIZE, remaining_hours)
                queue.append({
                    "kind": "requirement", 
                    "id": generic_id, 
                    "label": label, 
                    "hours": size, 
                    "suggestion": suggestion,
                })

                remaining_hours -= size

        else:
            for size in split_requirement_hours(hours):
                queue.append({
                    "kind": "requirement",
                    "id": generic_id,
                    "label": label,
                    "hours": size,
                    "suggestion": suggestion,
                })

    return queue


def split_requirement_hours(hours) -> List[float]:
    """Course-sized pieces of a requirement row: 3 -> [3], 8 -> [4, 4],
    9 -> [3, 3, 3], 40 -> ten 4s. Rows of 4 hours or less stay whole."""
    if hours <= 4:
        return [hours]
    pieces = -(-hours // 4)  # ceil
    pieces = int(max(pieces, round(hours / 3) if hours % 3 == 0 else pieces))
    base = hours / pieces
    if float(hours).is_integer():
        whole, extra = divmod(int(hours), pieces)
        return [whole + (1 if i < extra else 0) for i in range(pieces)]
    return [base] * pieces

# Function that generates the graduation plan
# Accepts an optional degree_cfg for multi-major support; defaults to CS.
def generate_plan(
    completed_courses: Set[str],
    generic_hours: Dict[str, float],
    target_terms: int,
    include_summer: bool,
    start_season: str = None,
    start_year: int = None,
    degree_cfg: dict = None,
    catalog: Dict[str, Dict[str, object]] = None,
    course_grades: Dict[str, str] = None,
    earned_hours: float = None,
    max_courses_per_term: int = None,
) -> Dict[str, List[Dict[str, object]]]:
    """*earned_hours*: credit hours the student has already earned (for class
    standing). Defaults to the hours of *completed_courses*.

    *max_courses_per_term*: "healthy course mix" -- at most this many named
    courses of 3+ credits (labs don't count) per term, with gen-ed and other
    requirement rows filling the rest. When a degree has more courses left
    than fit at that pace, the limit rises just enough to finish (spread
    evenly), and a course whose chain needs every remaining term may go
    over. None: no limit."""

    cfg = degree_cfg or CS_CONFIG
    prefix = cfg["prefix"]
    course_grades = course_grades or {}

    if target_terms < 1:
        target_terms = 1

    if start_season not in SEASON_CYCLE:
        start_season = default_start_season()

    if not start_year:
        start_year = date.today().year

    # Callers that already loaded the catalog (e.g. the web app, which also
    # needs it for the audit/warnings) can pass it in to skip a second,
    # redundant DB round trip -- otherwise it's loaded here as before.
    if catalog is None:
        catalog = load_catalog_courses(prefix)

    core_courses = cfg["core_courses"]
    conc_courses = cfg["concentration_courses"]
    supporting_courses_list = cfg["supporting_courses"]
    supporting_info = {code: {"title": title, "credits": hours} for code, hours, title in supporting_courses_list}
    required_codes = {code for code, _, _ in core_courses} | {code for code, _, _ in conc_courses}
    supporting_codes = {code for code, _, _ in supporting_courses_list}
    supporting_prereq_map = cfg.get("supporting_prereq_map", {})

    # Courses the elective pool must not pick: they're already required, or
    # they fill (or could fill) one of the program's choice rows
    elective_exclude = supporting_codes | set(cfg.get("_auto_applied_codes", ()))
    for options in (cfg.get("generic_options") or {}).values():
        elective_exclude |= set(options)

    # Labs that go with a lecture are scheduled in the same term as it
    lab_partners = {lecture: list(labs) for lecture, labs in (cfg.get("lab_partners") or {}).items()}
    lecture_of = {lab: lecture for lecture, labs in lab_partners.items() for lab in labs}

    # Build a config_hours dict from all course lists so a course's hours
    # come from the program first (lecture/lab splits), then the catalog.
    config_hours: Dict[str, float] = {}
    for code, hours, _ in core_courses + conc_courses + supporting_courses_list:
        config_hours[code] = hours

    def _hrs(code):
        return course_hours(code, catalog, config_hours)

    def _offered(code, season, year):
        return _offered_in_term(code, season, year, cfg)

    def _prereqs_satisfied(code, completed):
        prereqs = _full_prereqs(code, cfg, catalog)

        if not prereqs.issubset(completed):
            return False

        grade_requirements = catalog.get(code, {}).get(
            "prereq_grades", {}
        )

        for prereq_code, minimum_grade in grade_requirements.items():
            # Only validate grades for courses the student reported
            # as already completed. Future planned courses are assumed
            # to be completed successfully.
            # A course reported without a grade is assumed passed with
            # whatever grade it needed -- the planner doesn't ask for grades.
            if prereq_code in completed_courses and course_grades.get(prereq_code):
                if not meets_minimum_grade(
                    course_grades.get(prereq_code),
                    minimum_grade,
                ):
                    return False

        return True

    current_completed = set(completed_courses)
    # Hours already earned, for class standing (sophomore 30, junior 60,
    # senior 90 -- MTSU's classification by earned hours)
    if earned_hours is None:
        earned_hours = sum(course_hours(c, catalog) or 0 for c in current_completed)
    supporting_remaining = [code for code, _, _ in supporting_courses_list if code not in current_completed]

    # Build generic queue for this major
    cfg_generic = cfg["supporting_generic"] + cfg["tbc_generic"]
    fixed_hours = (
        sum(h for _, h, _ in core_courses)
        + sum(h for _, h, _ in conc_courses)
        + cfg.get("concentration_elective_hours", 0)
        + sum(h for _, h, _ in supporting_courses_list)
        + sum(h for _, _, h, _ in cfg_generic)
    )
    elective_total = max(cfg["total_hours"] - fixed_hours, 0)
    # Queue order is scheduling order: gen-ed (Written Communication first),
    # then the program's own pick-lists, then free electives last
    all_generic = cfg["tbc_generic"] + cfg["supporting_generic"]

    remaining_map = {}
    for gid, label, hours, suggestions in all_generic:
        entered = clamp_hours(generic_hours.get(gid, 0), hours)
        left = hours - entered
        if left > 0:
            remaining_map[gid] = {"label": label, "hours": left, "suggestions": suggestions}

    # Upper-division elective hours the catalog has no plannable courses for
    # still have to show up somewhere
    shortfall = elective_shortfall(current_completed, catalog, required_codes, cfg, elective_exclude) if catalog else 0
    if shortfall > 0:
        remaining_map["ud_elective_shortfall"] = {
            "label": f"{prefix} upper-division elective",
            "hours": shortfall,
            "suggestions": ["Choose with your advisor"],
        }
    entered = clamp_hours(generic_hours.get(ELECTIVES_GENERIC_ID, 0), elective_total)
    if elective_total - entered > 0:
        remaining_map[ELECTIVES_GENERIC_ID] = {"label": "General elective", "hours": elective_total - entered,
                                               "suggestions": ["Any elective course"]}
    generic_queue = _build_generic_queue(remaining_map)

    # Corequisites the plan can honor. The catalog's corequisite lines are
    # occasionally contradictory (IDES 3330 lists IDES 4100 as a corequisite,
    # while IDES 4100 needs IDES 3320, which needs IDES 3330 alongside), so a
    # corequisite link that would force a course both before and after
    # another is dropped -- one at a time, in a fixed order, keeping the rest
    coreqs_of = _consistent_coreqs(
        set(_remaining_pool_cfg(current_completed, catalog, required_codes, cfg, elective_exclude))
        | set(supporting_remaining),
        lambda code: _full_prereqs(code, cfg, catalog) | supporting_prereq_map.get(code, set()),
        catalog,
    )

    # How many terms each course's chain of later courses needs: a course
    # that unlocks four more in sequence has to start early, one that
    # unlocks nothing can wait for a term with room
    chain_codes = required_codes | supporting_codes
    dependents: Dict[str, Set[str]] = {}
    for code in chain_codes:
        for pre in _full_prereqs(code, cfg, catalog) | supporting_prereq_map.get(code, set()):
            if pre in chain_codes:
                dependents.setdefault(pre, set()).add(code)
    chain_len: Dict[str, int] = {}
    map_year = cfg.get("map_year") or {}

    def _level(code):
        number = code.split()[-1]
        return min(max(int(number[0]), 1), 4) if number[:1].isdigit() else 4

    def _chain(code, path=()):
        if code in chain_len:
            return chain_len[code]
        if code in path:
            return 1  # a prerequisite loop; _break_cycles normally prevents these
        length = 1 + max((_chain(d, path + (code,)) for d in dependents.get(code, ())), default=0)
        chain_len[code] = length
        return length

    plan = {}
    idle_terms = 0

    def _work_left():
        pool = _remaining_pool_cfg(current_completed, catalog, required_codes, cfg, elective_exclude)
        return bool(pool or supporting_remaining or generic_queue)

    # Skipped summers don't use up any of the terms asked for
    term_index = 0
    terms_done = 0
    while terms_done < target_terms:
        term_index += 1
        semester_label = term_label(term_index, start_season, start_year)
        if not _work_left():
            break
        if not include_summer and semester_label.startswith("Summer"):
            plan[semester_label] = [{
                "kind": "note",
                "label": "No classes planned this term (summer skipped)",
            }]
            continue
        terms_done += 1
        terms_left = target_terms - terms_done + 1  # including this one
        # The plan's remaining terms after this one (summers skipped when off)
        upcoming, next_index = [], term_index
        while len(upcoming) < terms_left - 1:
            next_index += 1
            label = term_label(next_index, start_season, start_year)
            if include_summer or not label.startswith("Summer"):
                season, year = label.split()
                upcoming.append((season, int(year)))

        term_items = []
        hours_used = 0

        # Courses scheduled for a term are kept separate to not
        # satisfy prerequisites for another course in the same semester
        newly_completed = set()

        term_season, term_year = semester_label.split()
        term_year = int(term_year)

        # A term may go up to MAX_HOURS_PER_TERM only when the plan is behind
        # (the work left won't fit the remaining terms at the usual load)
        work_left = (
            sum(_hrs(c) for c in set(_remaining_pool_cfg(current_completed, catalog, required_codes, cfg, elective_exclude)))
            + sum(_hrs(c) for c in supporting_remaining)
            + sum(chunk["hours"] for chunk in generic_queue)
        )
        term_cap = MAX_HOURS_PER_TERM if work_left > terms_left * TARGET_HOURS_PER_TERM else TARGET_HOURS_PER_TERM

        def _fits(hrs):
            return not hours_used or hours_used + hrs <= term_cap

        def _labs_ready(code):
            """This lecture's labs that still need scheduling (and can be)."""
            return [
                lab for lab in lab_partners.get(code, [])
                if lab not in current_completed and lab not in newly_completed
                and (lab in remaining_required_now or lab in supporting_remaining)
            ]

        def _schedule(code):
            nonlocal hours_used
            term_items.append(_course_item(code, catalog, supporting_info, config_hours))
            hours_used += _hrs(code)
            newly_completed.add(code)
            if code in supporting_remaining:
                supporting_remaining.remove(code)

        def _wait_terms(code):
            """Terms until *code* is offered again after this one (a big
            number when it isn't, within the plan)."""
            for wait, (season, year) in enumerate(upcoming, start=1):
                if _offered(code, season, year):
                    return wait
            return terms_left + 1

        def _try(code):
            """Schedule *code* -- with its labs and every corequisite not yet
            taken ("X or simultaneous completion of X"), which go in the same
            term or earlier, never later -- if they can all go in together."""
            if code in newly_completed:
                return
            group, stack = [], [code]
            while stack:
                member = stack.pop()
                if member in group:
                    continue
                group.append(member)
                stack += [lab for lab in _labs_ready(member) if lab not in group]
                stack += [co for co in coreqs_of.get(member, ())
                          if co in plan_codes and co not in current_completed
                          and co not in newly_completed and co not in group]
            for member in group:
                lecture = lecture_of.get(member)
                if lecture and lecture not in current_completed and lecture not in newly_completed \
                        and lecture not in group:
                    return  # a lab waits for its lecture
                if member == code:
                    continue
                if member in lecture_of:
                    ok = (_full_prereqs(member, cfg, catalog) <= current_completed
                          and _offered(member, term_season, term_year))
                else:
                    ok = _ready(member)
                if not ok:
                    return  # part of the group can't go this term: wait
            if not _fits(sum(_hrs(member) for member in group)):
                return
            # A course can wait only if, starting at its next offering, it and
            # the courses after it still fit before the plan ends
            if (max_courses_per_term and not cap_relaxed
                    and not (can_finish and _wait_terms(code) + _chain(code) > terms_left)):
                # Healthy course mix: no more than the limit of full courses this term
                full_now = sum(1 for item in term_items
                               if item.get("kind") == "course" and (item.get("hours") or 0) >= 3)
                full_new = sum(1 for member in group if _hrs(member) >= 3)
                if full_new and full_now + full_new > course_limit:
                    return
            for member in group:
                _schedule(member)

        cs_pool = _remaining_pool_cfg(current_completed, catalog, required_codes, cfg, elective_exclude)
        remaining_required_now = {code for code in required_codes if code not in current_completed}
        plan_codes = set(cs_pool) | set(supporting_remaining)
        relax_standing = False
        cap_relaxed = False

        def _standing_ok(code):
            # "Junior standing" etc. from the catalog's prerequisite line
            return relax_standing or (catalog.get(code) or {}).get("min_hours", 0) <= earned_hours

        def _ready(code):
            if not _standing_ok(code):
                return False
            if code in supporting_remaining and code not in cs_pool:
                # Supporting courses: prereqs from the course's own catalog
                # entry plus any the config adds (e.g. PHYS 2120 needs
                # PHYS 2110 *and* MATH 1910)
                prereqs = _full_prereqs(code, cfg, catalog) | supporting_prereq_map.get(code, set())
                ok = prereqs.issubset(current_completed)
            else:
                ok = _prereqs_satisfied(code, current_completed)
            return ok and _offered(code, term_season, term_year)

        # Order: a course that has to start now to finish on time (its
        # prerequisite chain needs every remaining term) goes first; the rest
        # follow MTSU's four-year map for the major (freshman-year courses
        # before sophomore-year ...), then course level (1000s before 3000s),
        # then longer chains -- so "Introduction to Aerospace" isn't pushed
        # behind a 2000-level course that merely starts a longer chain.
        # "Has to start now" only applies while finishing in the terms asked
        # for is still possible; past that, MTSU's order is kept.
        can_finish = work_left <= terms_left * MAX_HOURS_PER_TERM

        def _critical(code):
            return can_finish and _chain(code) >= terms_left - 1

        def _priority(c):
            critical = _critical(c)
            return (not critical, map_year.get(c) or _level(c), _level(c), -_chain(c), c.split()[-1], c)

        candidates = sorted(set(cs_pool) | set(supporting_remaining), key=_priority)

        # Leave room each term for an even share of the gen-ed and other
        # requirement rows, so they don't all pile up at the end -- but never
        # so much that the named courses can't keep their own even share
        # (otherwise they pile up at the end instead). A course whose chain
        # needs every remaining term goes in regardless.
        generic_left = sum(chunk["hours"] for chunk in generic_queue)
        courses_left = sum(_hrs(c) for c in set(cs_pool) | set(supporting_remaining))
        course_share = -(-courses_left // terms_left) if terms_left else 0
        reserve = min(generic_left, -(-generic_left // terms_left)) if terms_left else 0
        reserve = max(0, min(reserve, TARGET_HOURS_PER_TERM - course_share))
        course_room = TARGET_HOURS_PER_TERM - reserve
        # Healthy course mix: the limit per term, raised only as far as needed
        # when a degree has more full courses left than fit at the limit
        full_left = sum(1 for c in set(cs_pool) | set(supporting_remaining) if _hrs(c) >= 3)
        course_limit = max(max_courses_per_term or 0, -(-full_left // terms_left) if terms_left else 0)

        def _fill_generic():
            """Requirement rows in queue order, skipping any that don't fit.
            Free electives only take room no other row can use."""
            nonlocal hours_used
            leftover = []
            for chunk in generic_queue:
                if chunk["id"] == ELECTIVES_GENERIC_ID and any(
                    c["id"] != ELECTIVES_GENERIC_ID and c not in term_items for c in generic_queue
                ) and hours_used >= TARGET_HOURS_PER_TERM - 3:
                    leftover.append(chunk)
                elif _fits(chunk["hours"]):
                    term_items.append(chunk)
                    hours_used += chunk["hours"]
                else:
                    leftover.append(chunk)
            generic_queue[:] = leftover

        # Class standing from hours earned so far (30 a year). A course waits
        # until the student is within a year of where the map (or its level)
        # puts it -- no senior seminar in the first semester just because it
        # lists no prerequisites -- unless it has to start now
        standing = 1 + int(earned_hours // 30)

        def _in_year(code):
            return _critical(code) or (map_year.get(code) or _level(code)) <= standing + 1

        def _fill_term():
            nonlocal cap_relaxed
            # Courses for the student's current year (or that must start now)
            # go in before the gen-ed share; next year's courses use what's
            # left of the course share
            for code in candidates:
                if not _ready(code) or not _in_year(code):
                    continue
                critical = _critical(code)
                this_year = (map_year.get(code) or _level(code)) <= standing
                if critical or this_year or hours_used + _hrs(code) <= course_room:
                    _try(code)
            _fill_generic()
            # Room left over (few requirement rows remain): more courses, then rows
            for code in candidates:
                if code not in newly_completed and _ready(code) and _in_year(code):
                    _try(code)

            # Fills remaining space with requirement rows, skipping any piece
            # that doesn't fit rather than stopping at it
            _fill_generic()
            # A term still light after that: a later-year course rather than
            # an empty slot (finishing on time matters more than the ideal year).
            # The course-mix limit gives way only when no gen-ed or other
            # requirement row is left to mix in.
            if hours_used <= TARGET_HOURS_PER_TERM - 3:
                cap_relaxed = not generic_queue
                for code in candidates:
                    if code not in newly_completed and _ready(code):
                        _try(code)
                cap_relaxed = False

        _fill_term()
        if not term_items and not generic_queue and plan_codes and all(
            (catalog.get(code) or {}).get("min_hours", 0) > earned_hours for code in plan_codes
        ):
            # Every course left needs more class standing than the student's
            # recorded hours, and nothing else can add hours (likely hours
            # earned elsewhere that weren't entered): plan them rather than stall
            relax_standing = True
            _fill_term()

        if not term_items:
            # Nothing can be taken this term (e.g. a summer with no offerings,
            # or waiting on a fall-only course) -- keep going, but stop after
            # a full year of no progress
            plan[semester_label] = [{
                "kind": "note",
                "label": "No courses available this term",
            }]
            idle_terms += 1
            # Alternate-year courses can mean a two-year wait; stop only
            # after three years with nothing schedulable
            if idle_terms >= 9:
                break
            continue

        idle_terms = 0
        plan[semester_label] = term_items

        # Makes a course available as a prerequisite next semester
        current_completed.update(newly_completed)
        earned_hours += hours_used

    remaining_items = []

    # Uses anything that could not fit in the requested number of terms
    cs_pool = _remaining_pool_cfg(current_completed, catalog, required_codes, cfg, elective_exclude)

    for code in sorted_course_codes(cs_pool):
        remaining_items.append(_course_item(code, catalog, supporting_info, config_hours))

    for code in supporting_remaining:
        remaining_items.append(_course_item(code, catalog, supporting_info, config_hours))

    remaining_items.extend(generic_queue)

    if remaining_items:
        plan[f"Remaining after {target_terms} terms"] = remaining_items

    return plan

# Function that checks the generated plan for prerequisite problems in scheduling
def validate_plan(
    plan: Dict[str, List[Dict[str, object]]],
    completed_courses: Set[str],
    catalog: Dict[str, Dict[str, object]] = None,
    degree_cfg: dict = None,
    course_grades: Dict[str, str] = None,
) -> List[Dict[str, str]]:

    catalog = catalog or {}
    course_grades = course_grades or {}
    warnings = []

    if degree_cfg is not None:
        cfg_support_prereq = degree_cfg.get("supporting_prereq_map", {})
        coreq_map = degree_cfg.get("coreq_map", {})
        effective_cfg = degree_cfg
    else:
        cfg_support_prereq = SUPPORTING_PREREQUISITE_MAP
        coreq_map = {}
        effective_cfg = {"prefix": PROGRAM_PREFIX, "prereq_map": PREREQUISITE_MAP, "prereq_override_map": {}}

    verified_completed = {c.strip().upper() for c in completed_courses}

    for term, items in plan.items():
        if term.startswith("Remaining"):
            continue

        this_term_codes = {item["code"] for item in items if item.get("kind") == "course"}

        term_parts = term.split()
        term_season, term_year = (term_parts[0], int(term_parts[1])) if len(term_parts) == 2 else (None, None)

        for code in sorted_course_codes(this_term_codes):
            # Prerequisite check: re-derives the full requirement set the
            # same way generate_plan does -- including a supporting course's
            # own home-major prereqs (_full_prereqs), not just whatever this
            # major's supporting_prereq_map happens to declare for it --
            # rather than trusting generate_plan's own bookkeeping. This is
            # the independent check, so it has to compute its own answer,
            # not just echo the scheduler's.
            prereqs = _full_prereqs(code, effective_cfg, catalog)
            prereqs |= cfg_support_prereq.get(code, set())

            grade_requirements = catalog.get(code, {}).get(
                "prereq_grades", {}
            )

            for prereq in sorted(prereqs):
                if prereq in verified_completed:
                    minimum_grade = grade_requirements.get(prereq)

                    # Grade validation only applies to courses the student
                    # reported as already completed.
                    if (
                        prereq in completed_courses
                        and minimum_grade
                        and course_grades.get(prereq)
                        and not meets_minimum_grade(
                            course_grades.get(prereq),
                            minimum_grade,
                        )
                    ):
                        warnings.append({
                            "course": code,
                            "term": term,
                            "prereq": prereq,
                            "type": "insufficient_grade",
                            "minimum_grade": minimum_grade,
                        })

                    continue

                warnings.append({
                    "course": code,
                    "term": term,
                    "prereq": prereq,
                    "type": "same_term" if prereq in this_term_codes else "not_yet_completed",
                })

            # Offering-season check -- generate_plan has its own _offered()
            # gate, but nothing previously re-verified that independently;
            # a bug there would have produced a bad plan with zero warnings.
            if term_season and term_year is not None:
                is_offered = _offered_in_term(code, term_season, term_year, degree_cfg or CS_CONFIG)
                if not is_offered:
                    warnings.append({
                        "course": code,
                        "term": term,
                        "prereq": None,
                        "type": "not_offered",
                    })

        verified_completed.update(this_term_codes)

    return warnings


# Groups to style courses in the graph
GRAPH_GROUPS = ("core", "concentration", "language", "supporting", "external")

# Function that builds the course nodes and edges of the graph
def build_prereq_graph(catalog: Dict[str, Dict[str, object]] = None):
    catalog = catalog or {}
    nodes = {}

    for code, hours, title in CS_CORE_COURSES:
        nodes[code] = {
            "title": title, 
            "hours": hours, 
            "group": "core",
        }

    for code, hours, title in CONCENTRATION_REQUIRED_COURSES:
        nodes[code] = {
            "title": title, 
            "hours": hours, 
            "group": "concentration",
        }

    for code in HIGH_LEVEL_LANGUAGE_OPTIONS:
        info = catalog.get(code, {})
        nodes[code] = {
            "title": info.get("title", ""), 
            "hours": info.get("credits") or 3, 
            "group": "language",
        }
    
    for code, hours, title in SUPPORTING_COURSES:
        nodes[code] = {
            "title": title, 
            "hours": hours, 
            "group": "supporting",
        }

    edges = []

    # Add prerequisite connection for nodes outside the requirements
    for code in list(nodes.keys()):
        prereqs = set()

        if code in catalog:
            prereqs |= catalog[code].get("prereqs", set())
        else:
            prereqs |= PREREQUISITE_MAP.get(code, set())

        prereqs |= SUPPORTING_PREREQUISITE_MAP.get(code, set())

        for prereq in sorted(prereqs):
            edges.append((prereq, code))

            if prereq not in nodes:
                info = catalog.get(prereq, {})
                nodes[prereq] = {
                    "title": info.get("title", ""), 
                    "hours": info.get("credits"), 
                    "group": "external",
                }

    return nodes, edges

# Function that builds a student-specific prereq graph from their completed
# courses and generated plan.  Each node carries a ``status`` field:
#   'completed' – student already passed this course (shown green)
#   'next'      – scheduled in the first two planned terms (shown blue)
#   'planned'   – in a later planned term (shown light blue)
#   'external'  – a prerequisite outside the tracked course list (shown gray)
def build_personal_prereq_graph(
    catalog: Dict[str, Dict[str, object]],
    completed_courses,
    plan: Dict[str, List[Dict[str, object]]],
    degree_cfg: dict = None,
):
    cfg = degree_cfg or CS_CONFIG
    core_courses = cfg["core_courses"]
    conc_courses = cfg["concentration_courses"]
    high_level_opts = cfg.get("high_level_options", [])
    supporting_courses_list = cfg["supporting_courses"]
    supporting_prereq_map = cfg.get("supporting_prereq_map", {})

    completed = {c.strip().upper() for c in (completed_courses or [])}

    # Map each planned course code to the index of the term it falls in
    terms_in_order = [t for t in (plan or {}) if not t.startswith("Remaining")]
    planned_by_index: Dict[str, int] = {}
    for idx, term in enumerate(terms_in_order):
        for item in (plan or {}).get(term, []):
            if item.get("kind") == "course" and item.get("code"):
                planned_by_index.setdefault(item["code"], idx)

    NEXT_THRESHOLD = 2  # first two terms count as "next up"

    def _status(code: str) -> str:
        if code in completed:
            return "completed"
        idx = planned_by_index.get(code)
        if idx is None:
            return "external"
        return "next" if idx < NEXT_THRESHOLD else "planned"

    nodes: Dict[str, Dict] = {}

    for code, hours, title in core_courses:
        nodes[code] = {"title": title, "hours": hours, "group": "core", "status": _status(code)}

    for code, hours, title in conc_courses:
        nodes[code] = {"title": title, "hours": hours, "group": "concentration", "status": _status(code)}

    for code in high_level_opts:
        info = catalog.get(code, {})
        nodes[code] = {
            "title": info.get("title", ""),
            "hours": info.get("credits") or 3,
            "group": "language",
            "status": _status(code),
        }

    for code, hours, title in supporting_courses_list:
        nodes[code] = {"title": title, "hours": hours, "group": "supporting", "status": _status(code)}

    edges: List[tuple] = []
    for code in list(nodes.keys()):
        prereqs = _full_prereqs(code, cfg, catalog)
        prereqs |= supporting_prereq_map.get(code, set())

        for prereq in sorted(prereqs):
            edges.append((prereq, code))
            if prereq not in nodes:
                info = catalog.get(prereq, {})
                nodes[prereq] = {
                    "title": info.get("title", ""),
                    "hours": info.get("credits"),
                    "group": "external",
                    "status": "completed" if prereq in completed else "external",
                }

    return nodes, edges


# Function that renders a student-specific top-down Mermaid prerequisite tree.
# Completed nodes are green, next-up nodes are blue, planned nodes are light
# blue, and external prerequisites are gray/dashed.
def render_personal_prereq_mermaid(
    nodes: Dict[str, Dict[str, object]],
    edges: List[tuple],
) -> str:

    def node_id(code: str) -> str:
        return re.sub(r"[^A-Za-z0-9_]", "_", code)

    def node_label(code: str, info: dict) -> str:
        check = "Done: " if info.get("status") == "completed" else ""
        title = _mermaid_text(info.get("title", ""))
        return f"{check}{code}<br/>{title}" if title else f"{check}{code}"

    # Left to right, like the Prerequisite Map page: each chain reads as a row
    lines = ["graph LR"]

    for code, info in nodes.items():
        lines.append(f'  {node_id(code)}["{node_label(code, info)}"]')

    for prereq, course in edges:
        lines.append(f"  {node_id(prereq)} --> {node_id(course)}")

    status_class_map = {
        "completed": "doneNode",
        "next": "nextNode",
        "planned": "plannedNode",
        "external": "extNode",
    }
    for code, info in nodes.items():
        cls = status_class_map.get(info.get("status"), "plannedNode")
        lines.append(f"  class {node_id(code)} {cls}")

    lines.extend([
        "  classDef doneNode fill:#16a34a,color:#ffffff,stroke:#15803d,stroke-width:2px",
        "  classDef nextNode fill:#2563eb,color:#ffffff,stroke:#1e40af,stroke-width:3px",
        "  classDef plannedNode fill:#bfdbfe,color:#1e3a5f,stroke:#3b82f6,stroke-width:1px",
        "  classDef extNode fill:#f1f5f9,color:#475569,stroke:#cbd5e1,stroke-dasharray: 4 2",
    ])

    return "\n".join(lines)


def _mermaid_text(text) -> str:
    """Catalog text made safe inside a Mermaid ["..."] label."""
    text = " ".join(str(text or "").split())
    return text.replace('"', "#quot;").replace("<", "#lt;").replace(">", "#gt;")


# Function to convert the prerequisite graph into Mermaid format
def render_prereq_mermaid(nodes: Dict[str, Dict[str, object]], edges: List[tuple]) -> str:

    # Nested function that creates a Mermaid identifier
    def node_id(code):
        return re.sub(r"[^A-Za-z0-9_]", "_", code)

    # Nested function that creates a visible label for a Mermaid node
    def node_label(code, info):
        title = _mermaid_text(info.get("title", ""))
        return f"{code}<br/>{title}" if title else code

    lines = ["graph LR"]

    for code, info in nodes.items():
        lines.append(f'  {node_id(code)}["{node_label(code, info)}"]')

    for prereq, course in edges:
        lines.append(f"  {node_id(prereq)} --> {node_id(course)}")

    class_names = {
        "core": "coreNode",
        "concentration": "concNode",
        "language": "langNode",
        "supporting": "supNode",
        "external": "extNode",
    }

    for code, info in nodes.items():
        class_name = class_names.get(info["group"])
        
        if class_name:
            lines.append(f"  class {node_id(code)} {class_name}")

    lines.extend([
        "  classDef coreNode fill:#2563eb,color:#ffffff,stroke:#1d4ed8",
        "  classDef concNode fill:#7c3aed,color:#ffffff,stroke:#6d28d9",
        "  classDef langNode fill:#f59e0b,color:#1f2937,stroke:#d97706",
        "  classDef supNode fill:#059669,color:#ffffff,stroke:#047857",
        "  classDef extNode fill:#e5e7eb,color:#374151,stroke:#9ca3af,stroke-dasharray: 4 2",
    ])

    return "\n".join(lines)


# Function that builds the prerequisite graph for any major's required courses
def build_program_prereq_graph(cfg: dict, catalog: Dict[str, Dict[str, object]] = None):
    catalog = catalog or {}
    nodes: Dict[str, Dict[str, object]] = {}

    def add(code, hours, title, group):
        if code not in nodes:
            info = catalog.get(code, {})
            nodes[code] = {
                "title": title or info.get("title", ""),
                "hours": hours or info.get("credits") or 3,
                "group": group,
            }

    for code, hours, title in cfg["core_courses"]:
        add(code, hours, title, "core")
    for code, hours, title in cfg["concentration_courses"]:
        add(code, hours, title, "concentration")
    for code in cfg.get("high_level_options", []):
        add(code, None, None, "language")
    for code, hours, title in cfg["supporting_courses"]:
        add(code, hours, title, "supporting")

    supporting_prereq_map = cfg.get("supporting_prereq_map", {})
    edges = []
    for code in list(nodes):
        prereqs = _full_prereqs(code, cfg, catalog) | supporting_prereq_map.get(code, set())
        for prereq in sorted(prereqs):
            edges.append((prereq, code))
            if prereq not in nodes:
                info = catalog.get(prereq, {})
                nodes[prereq] = {"title": info.get("title", ""), "hours": info.get("credits"), "group": "external"}
    return nodes, edges
