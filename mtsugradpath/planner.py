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
)
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

# Function that loads the CSCI course information from the database
def load_catalog_courses() -> Dict[str, Dict[str, object]]:
    catalog = {}

    try:
        with SessionLocal() as session:
            course_rows = (
                session.query(Course)
                .filter(Course.prefix == PROGRAM_PREFIX)
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

# Function to select an upper-division elective that can fill the elective hours
def build_elective_pool(
    completed: Set[str], 
    catalog: Dict[str, Dict[str, object]], 
    required_codes: Set[str],
) -> Set[str]:
    
    # Selects the upper-division course to fill
    done_electives = {
        code for code in completed
        if is_upper_division_csci(code) and code not in required_codes
    }

    hours_done = sum((catalog.get(code, {}).get("credits") or 3) for code in done_electives)
    hours_needed = max(CONCENTRATION_ELECTIVE_HOURS - hours_done, 0)

    if hours_needed <= 0:
        return set()

    candidate_codes = {
        code for code in catalog
        if is_upper_division_csci(code) and code not in required_codes and code not in completed
    }

    # High-level language courses can be alternatives, one should be in the recommended section
    language_done = any(code in completed for code in HIGH_LEVEL_LANGUAGE_OPTIONS)
    available_language_options = [code for code in HIGH_LEVEL_LANGUAGE_OPTIONS if code in candidate_codes]

    if language_done:
        candidate_codes -= set(HIGH_LEVEL_LANGUAGE_OPTIONS)
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

# Function that returns CSCI prerequisites that are completed
def next_courses(completed: Set[str], available: Set[str], catalog: Dict[str, Dict[str, object]]) -> List[str]:
    candidates = []

    for course in sorted_course_codes(available):
        if course in catalog:
            prereqs = catalog[course].get("prereqs", set())
        else:
            prereqs = PREREQUISITE_MAP.get(course, set())
        
        # Can only be CSCI prerequisites
        required = {prereq for prereq in prereqs if prereq.startswith(PROGRAM_PREFIX)}

        if required.issubset(completed):
            candidates.append(course)

    return candidates

# Function that returns elective CSCI courses that remain unfinished
def _remaining_pool(completed: Set[str], catalog: Dict[str, Dict[str, object]], required_codes: Set[str]) -> Set[str]:
    remaining_required = {code for code in required_codes if code not in completed}

    if not catalog:
        return remaining_required

    elective_pool = build_elective_pool(completed, catalog, required_codes)

    return remaining_required | elective_pool

# Function that converts supporting course information into a code-based dictionary
def _supporting_course_info() -> Dict[str, Dict[str, object]]:
    return {code: {"title": title, "credits": hours} for code, hours, title in SUPPORTING_COURSES}

# Function that creates the dictionary for diaplying a course
def _course_item(code: str, catalog: Dict[str, Dict[str, object]], supporting_info: Dict[str, Dict[str, object]]) -> Dict[str, object]:
    info = catalog.get(code) or supporting_info.get(code) or {}
    title = info.get("title", "")
    hours = info.get("credits") or 3
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
def generate_plan(
    completed_courses: Set[str],
    generic_hours: Dict[str, float],
    target_terms: int,
    include_summer: bool,
    start_season: str = None,
    start_year: int = None,
) -> Dict[str, List[Dict[str, object]]]:

    if target_terms < 1:
        target_terms = 1

    if start_season not in SEASON_CYCLE:
        start_season = default_start_season()

    if not start_year:
        start_year = date.today().year

    catalog = load_catalog_courses()
    supporting_info = _supporting_course_info()
    required_codes = set(REQUIRED_COURSES)
    current_completed = set(completed_courses)

    supporting_remaining = [code for code, _, _ in SUPPORTING_COURSES if code not in current_completed]
    generic_queue = _build_generic_queue(generic_remaining(generic_hours))

    plan = {}

    for term_index in range(1, target_terms + 1):
        semester_label = term_label(term_index, start_season, start_year)
        if not include_summer and semester_label.startswith("Summer"):
            plan[semester_label] = [{
                "kind": "note", 
                "label": 
                "No classes planned this term (summer skipped)",
            }]

            continue

        term_items = []
        hours_used = 0

        # Courses scheduled for a term are kept separate to not 
        # satisfy prerequisites for another course in the same semester
        newly_completed = set()

        term_season, term_year = semester_label.split()
        term_year = int(term_year)

        cs_pool = _remaining_pool(current_completed, catalog, required_codes)

        cs_candidates = [
            code for code in next_courses(current_completed, cs_pool, catalog)
            if course_offered_in_term(code, term_season, term_year)
        ]

        # Add avaialbe courses without exceeding target
        for code in cs_candidates:
            hrs = catalog.get(code, {}).get("credits") or 3

            if hours_used and hours_used + hrs > TARGET_HOURS_PER_TERM:
                break

            term_items.append(_course_item(code, catalog, supporting_info))

            hours_used += hrs
            newly_completed.add(code)

        i = 0

        # Add supporting courses that were completed earlier
        while i < len(supporting_remaining):
            code = supporting_remaining[i]
            prereqs = SUPPORTING_PREREQUISITE_MAP.get(code, set())

            if not prereqs.issubset(current_completed):
                i += 1
                continue

            hrs = supporting_info.get(code, {}).get("credits") or 3

            if hours_used and hours_used + hrs > TARGET_HOURS_PER_TERM:
                i += 1
                continue

            term_items.append(_course_item(code, catalog, supporting_info))

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

        # Makes a course available as a prerequiste next semester
        current_completed.update(newly_completed)

    remaining_items = []

    # Uses anything that could not fit in the requested number of terms
    cs_pool = _remaining_pool(current_completed, catalog, required_codes)

    for code in sorted_course_codes(cs_pool):
        remaining_items.append(_course_item(code, catalog, supporting_info))

    for code in supporting_remaining:
        remaining_items.append(_course_item(code, catalog, supporting_info))

    remaining_items.extend(generic_queue)

    if remaining_items:
        plan[f"Remaining after {target_terms} terms"] = remaining_items

    return plan

# Function that checks the generated plan for prerequisite problems in scheduling
def validate_plan(
    plan: Dict[str, List[Dict[str, object]]],
    completed_courses: Set[str],
    catalog: Dict[str, Dict[str, object]] = None,
) -> List[Dict[str, str]]:

    catalog = catalog or {}
    warnings = []

    verified_completed = {c.strip().upper() for c in completed_courses}

    for term, items in plan.items():
        if term.startswith("Remaining"):
            continue

        this_term_codes = {item["code"] for item in items if item.get("kind") == "course"}

        for code in sorted_course_codes(this_term_codes):
            prereqs = set()

            if code in catalog:
                prereqs |= {p for p in catalog[code].get("prereqs", set()) if p.startswith(PROGRAM_PREFIX)}
            else:
                prereqs |= PREREQUISITE_MAP.get(code, set())

            prereqs |= SUPPORTING_PREREQUISITE_MAP.get(code, set())

            # Finds prerequisites that were not compleneted in a term
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
