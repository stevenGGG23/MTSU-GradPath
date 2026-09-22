import re
from datetime import date
from typing import List, Set, Dict

from .config import PROGRAM_PREFIX
from .db import SessionLocal
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
from .degree_configs import CS_CONFIG, get_full_degree_config
from .models import Course

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

# Function that loads course information from the database for a given prefix
def load_catalog_courses(prefix: str = None) -> Dict[str, Dict[str, object]]:
    prefix = prefix or PROGRAM_PREFIX
    catalog = {}

    try:
        with SessionLocal() as session:
            course_rows = (
                session.query(Course)
                .filter(Course.prefix == prefix)
                .order_by(Course.prefix, Course.number)
                .all()
            )

            # Converts the course records to code-based dictionaries
            for course in course_rows:
                if not course.prefix or not course.number:
                    continue

                code = f"{course.prefix} {course.number}"
                prereq_codes = set()

                # Courses may have more than one stored prerequisite
                for prereq in course.prerequisites:
                    prereq_codes.update(parse_prereq_codes(prereq.prerequisite_text))
                catalog[code] = {
                    "title": course.title or "",
                    "credits": course.credits or 0,
                    "prereqs": prereq_codes,
                }
    # Empty the catalog for the planner to fall back if needed
    except Exception:
        return {}

    return catalog

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


# Function to select an upper-division elective that can fill the elective hours
# Accepts an optional degree_cfg for multi-major support; defaults to CS.
def build_elective_pool(
    completed: Set[str],
    catalog: Dict[str, Dict[str, object]],
    required_codes: Set[str],
    degree_cfg: dict = None,
) -> Set[str]:
    cfg = degree_cfg or CS_CONFIG
    prefix = cfg["prefix"]
    min_num = cfg.get("upper_division_min", 3000)
    elective_hours_needed_total = cfg.get("concentration_elective_hours", 12)
    high_level_opts = cfg.get("high_level_options", [])

    # Selects the upper-division course to fill
    done_electives = {
        code for code in completed
        if _is_upper_division(code, prefix, min_num) and code not in required_codes
    }

    hours_done = sum((catalog.get(code, {}).get("credits") or 3) for code in done_electives)
    hours_needed = max(elective_hours_needed_total - hours_done, 0)

    if hours_needed <= 0:
        return set()

    candidate_codes = {
        code for code in catalog
        if _is_upper_division(code, prefix, min_num)
        and code not in required_codes
        and code not in completed
        and (catalog.get(code, {}).get("credits") or 0) > 0  # skip 0-credit lab sections
    }

    # High-level language courses can be alternatives, one should be in the recommended section
    if high_level_opts:
        language_done = any(code in completed for code in high_level_opts)
        available_language_options = [code for code in high_level_opts if code in candidate_codes]

        if language_done:
            candidate_codes -= set(high_level_opts)
        elif available_language_options:
            candidate_codes -= set(available_language_options[1:])

    candidates = sorted_course_codes(candidate_codes)

    pool = []
    hours_acc = 0

    # Adds enough elective courses to cover the remaining hours
    for code in candidates:
        if hours_acc >= hours_needed:
            break

        pool.append(code)

        hours_acc += catalog.get(code, {}).get("credits") or 3

    return set(pool)

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


# Function that returns elective courses that remain unfinished (degree-cfg aware)
def _remaining_pool_cfg(
    completed: Set[str],
    catalog: Dict[str, Dict[str, object]],
    required_codes: Set[str],
    cfg: dict,
) -> Set[str]:
    remaining_required = {code for code in required_codes if code not in completed}

    if not catalog:
        return remaining_required

    elective_pool = build_elective_pool(completed, catalog, required_codes, degree_cfg=cfg)

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
    cat_credits = cat_info.get("credits") or None
    sup_credits = sup_info.get("credits") or None
    cfg_credits = (config_hours or {}).get(code) or None
    hours = cat_credits or cfg_credits or sup_credits or 3
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

    for generic_id in sorted(remaining_map):
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
            queue.append({
                "kind": "requirement", 
                "id": generic_id, 
                "label": label, 
                "hours": hours, 
                "suggestion": suggestion,
            })

    return queue

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
) -> Dict[str, List[Dict[str, object]]]:

    cfg = degree_cfg or CS_CONFIG
    prefix = cfg["prefix"]

    if target_terms < 1:
        target_terms = 1

    if start_season not in SEASON_CYCLE:
        start_season = default_start_season()

    if not start_year:
        start_year = date.today().year

    catalog = load_catalog_courses(prefix)

    core_courses = cfg["core_courses"]
    conc_courses = cfg["concentration_courses"]
    supporting_courses_list = cfg["supporting_courses"]
    supporting_info = {code: {"title": title, "credits": hours} for code, hours, title in supporting_courses_list}
    required_codes = {code for code, _, _ in core_courses} | {code for code, _, _ in conc_courses}

    prereq_map = cfg.get("prereq_map", {})
    prereq_override_map = cfg.get("prereq_override_map", {})
    supporting_prereq_map = cfg.get("supporting_prereq_map", {})
    offering_seasons_cfg = cfg.get("offering_seasons", {})
    odd_year_spring = cfg.get("odd_year_spring_only", set())

    # Build a config_hours dict from all course lists so _course_item can fall back
    # to the catalog-defined hours when the DB entry has 0 credits (lecture/lab splits).
    config_hours: Dict[str, float] = {}
    for code, hours, _ in core_courses + conc_courses + supporting_courses_list:
        config_hours[code] = hours

    def _offered(code, season, year):
        if code in odd_year_spring:
            return season == "Spring" and year % 2 == 1
        allowed = offering_seasons_cfg.get(code)
        if allowed is None:
            return True
        return season in allowed

    def _next_courses_cfg(completed, available):
        candidates = []
        for code in sorted_course_codes(available):
            # prereq_override_map takes full precedence over catalog prereqs
            if code in prereq_override_map:
                prereqs = set(prereq_override_map[code])
            elif code in catalog:
                prereqs = catalog[code].get("prereqs", set())
                prereqs = prereqs | prereq_map.get(code, set())
            else:
                prereqs = prereq_map.get(code, set())
            required = {p for p in prereqs if p.startswith(prefix)}
            if required.issubset(completed):
                candidates.append(code)
        return candidates

    current_completed = set(completed_courses)
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
    all_generic = cfg_generic + [(ELECTIVES_GENERIC_ID, "General elective", elective_total, ["Any elective course"])]

    remaining_map = {}
    for gid, label, hours, suggestions in all_generic:
        entered = clamp_hours(generic_hours.get(gid, 0), hours)
        left = hours - entered
        if left > 0:
            remaining_map[gid] = {"label": label, "hours": left, "suggestions": suggestions}
    generic_queue = _build_generic_queue(remaining_map)

    plan = {}

    for term_index in range(1, target_terms + 1):
        semester_label = term_label(term_index, start_season, start_year)
        if not include_summer and semester_label.startswith("Summer"):
            plan[semester_label] = [{
                "kind": "note",
                "label": "No classes planned this term (summer skipped)",
            }]
            continue

        term_items = []
        hours_used = 0

        # Courses scheduled for a term are kept separate to not
        # satisfy prerequisites for another course in the same semester
        newly_completed = set()

        term_season, term_year = semester_label.split()
        term_year = int(term_year)

        cs_pool = _remaining_pool_cfg(current_completed, catalog, required_codes, cfg)

        cs_candidates = [
            code for code in _next_courses_cfg(current_completed, cs_pool)
            if _offered(code, term_season, term_year)
        ]

        # Add available courses without exceeding target
        for code in cs_candidates:
            hrs = catalog.get(code, {}).get("credits") or 3

            if hours_used and hours_used + hrs > TARGET_HOURS_PER_TERM:
                break

            term_items.append(_course_item(code, catalog, supporting_info, config_hours))

            hours_used += hrs
            newly_completed.add(code)

        i = 0

        # Add supporting courses whose prerequisites are satisfied
        while i < len(supporting_remaining):
            code = supporting_remaining[i]
            prereqs = supporting_prereq_map.get(code, set())

            if not prereqs.issubset(current_completed):
                i += 1
                continue

            hrs = supporting_info.get(code, {}).get("credits") or 3

            if hours_used and hours_used + hrs > TARGET_HOURS_PER_TERM:
                i += 1
                continue

            term_items.append(_course_item(code, catalog, supporting_info, config_hours))

            hours_used += hrs
            newly_completed.add(code)
            supporting_remaining.pop(i)

        # Fills remaining space with generic requirements
        while generic_queue:
            chunk = generic_queue[0]
            if hours_used and hours_used + chunk["hours"] > TARGET_HOURS_PER_TERM:
                break

            term_items.append(generic_queue.pop(0))
            hours_used += chunk["hours"]

        if not term_items:
            plan[semester_label] = [{
                "kind": "note",
                "label": "No available courses meet prerequisites",
            }]
            break

        plan[semester_label] = term_items

        # Makes a course available as a prerequisite next semester
        current_completed.update(newly_completed)

    remaining_items = []

    # Uses anything that could not fit in the requested number of terms
    cs_pool = _remaining_pool_cfg(current_completed, catalog, required_codes, cfg)

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
) -> List[Dict[str, str]]:

    catalog = catalog or {}
    warnings = []

    if degree_cfg is not None:
        cfg_prefix = degree_cfg["prefix"]
        cfg_prereq_map = degree_cfg.get("prereq_map", {})
        cfg_prereq_override = degree_cfg.get("prereq_override_map", {})
        cfg_support_prereq = degree_cfg.get("supporting_prereq_map", {})
    else:
        cfg_prefix = PROGRAM_PREFIX
        cfg_prereq_map = PREREQUISITE_MAP
        cfg_prereq_override = {}
        cfg_support_prereq = SUPPORTING_PREREQUISITE_MAP

    verified_completed = {c.strip().upper() for c in completed_courses}

    for term, items in plan.items():
        if term.startswith("Remaining"):
            continue

        this_term_codes = {item["code"] for item in items if item.get("kind") == "course"}

        for code in sorted_course_codes(this_term_codes):
            prereqs = set()

            # prereq_override_map takes full precedence over catalog prereqs
            if code in cfg_prereq_override:
                prereqs |= {p for p in cfg_prereq_override[code] if p.startswith(cfg_prefix)}
            elif code in catalog:
                prereqs |= {p for p in catalog[code].get("prereqs", set()) if p.startswith(cfg_prefix)}
                prereqs |= {p for p in cfg_prereq_map.get(code, set()) if p.startswith(cfg_prefix)}
            else:
                prereqs |= cfg_prereq_map.get(code, set())

            prereqs |= cfg_support_prereq.get(code, set())

            for prereq in sorted(prereqs):
                if prereq in verified_completed:
                    continue

                warnings.append({
                    "course": code,
                    "term": term,
                    "prereq": prereq,
                    "type": "same_term" if prereq in this_term_codes else "not_yet_completed",
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
    prereq_map = cfg.get("prereq_map", {})
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
        prereqs: set = set()
        if code in catalog:
            prereqs |= catalog[code].get("prereqs", set())
        prereqs |= prereq_map.get(code, set())
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
        title = info.get("title", "")
        return f"{check}{code}<br/>{title}" if title else f"{check}{code}"

    lines = ["graph TD"]

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


# Function to convert the prerequisite graph into Mermaid format
def render_prereq_mermaid(nodes: Dict[str, Dict[str, object]], edges: List[tuple]) -> str:

    # Nested function that creates a Mermaid identifier
    def node_id(code):
        return re.sub(r"[^A-Za-z0-9_]", "_", code)

    # Nested function that creates a visible label for a Mermaid node
    def node_label(code, info):
        return f"{code}<br/>{info['title']}" if info.get("title") else code

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
