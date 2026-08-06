"""Degree requirements for the B.S. Computer Science, Professional Computer
Science Concentration, per MTSU's undergraduate catalog. General-education
and supporting requirements that aren't CSCI courses (math, science, TBC gen
ed, free electives) are tracked as partial-hour buckets with example course
suggestions, rather than matched against specific course data -- this
doesn't need to be exact, just give an honest picture of what's left.
"""

TOTAL_PROGRAM_HOURS = 120
UPPER_DIVISION_MIN = 3000

# Requires CSCI courses
CS_CORE_COURSES = [
    ("CSCI 1010", 1, "Computer Science Colloquium"),
    ("CSCI 1170", 4, "Computer Science I"),
    ("CSCI 2170", 4, "Computer Science II"),
    ("CSCI 3080", 3, "Discrete Structures"),
    ("CSCI 3110", 3, "Algorithms and Data Structures"),
    ("CSCI 3130", 4, "Assembly and Computer Organization"),
    ("CSCI 3240", 4, "Introduction to Computer Systems"),
    ("CSCI 4700", 3, "Software Engineering"),
]

# Professional CSCI courses
CONCENTRATION_REQUIRED_COURSES = [
    ("CSCI 3210", 3, "Theory of Programming Languages"),
    ("CSCI 4160", 3, "Compiler Design and Software Development"),
]

# Covers the high-level and upper-division electives
CONCENTRATION_ELECTIVE_HOURS = 12

# Regulates only one of these is needed
HIGH_LEVEL_LANGUAGE_OPTIONS = ["CSCI 3033", "CSCI 3037", "CSCI 3038"]

# List of semesters where regularly scheduled undergraduate CSCI courses are offered
COURSE_OFFERING_SEASONS = {
    "CSCI 1010": {"Fall", "Spring"},
    "CSCI 1150": {"Fall", "Spring"},
    "CSCI 1170": {"Fall", "Spring"},
    "CSCI 2170": {"Fall", "Spring"},
    "CSCI 3033": {"Fall", "Spring"},
    "CSCI 3037": {"Fall", "Spring"},
    "CSCI 3038": {"Fall", "Spring"},
    "CSCI 3080": {"Fall", "Spring"},
    "CSCI 3110": {"Fall", "Spring"},
    "CSCI 3130": {"Fall", "Spring"},
    "CSCI 3160": {"Fall", "Spring"},
    "CSCI 3180": {"Fall", "Spring"},
    "CSCI 3210": {"Spring"},
    "CSCI 3240": {"Fall", "Spring"},
    "CSCI 4160": {"Fall"},
    "CSCI 4250": {"Fall"},
    "CSCI 4300": {"Fall"},
    "CSCI 4330": {"Spring"},
    "CSCI 4350": {"Fall"},
    "CSCI 4410": {"Spring"},
    "CSCI 4560": {"Fall"},
    "CSCI 4700": {"Fall", "Spring"},
}

# CSCI courses offered during odd intervals in the year
ODD_YEAR_SPRING_ONLY_COURSES = {"CSCI 4360"}

# Function to check if a course is offered during a particular term
def course_offered_in_term(code, season, year):

    if code in ODD_YEAR_SPRING_ONLY_COURSES:
        return season == "Spring" and year % 2 == 1

    allowed_seasons = COURSE_OFFERING_SEASONS.get(code)

    if allowed_seasons is None:
        return True

    return season in allowed_seasons

# Required supporting courses
SUPPORTING_COURSES = [
    ("COMM 2200", 3, "Audience-Centered Communication"),
    ("MATH 1910", 4, "Calculus I"),
    ("MATH 1920", 4, "Calculus II"),
    ("MATH 2050", 3, "Probability and Statistics"),
    ("PHIL 3170", 3, "Ethics and Computing Technology"),
]

# Required generic supporting courses tracked by credit hours
SUPPORTING_GENERIC = [
    ("math_elective", "Math elective", 4, [
        "MATH 2050 - Probability and Statistics",
        "MATH 2010 - Elements of Linear Algebra",
        "MATH 3110 - Calculus III",
    ]),
    ("science_sequence", "Year-long lab science sequence", 8, [
        "BIOL 1110/1120 - General Biology I & II",
        "CHEM 1110/1120 - General Chemistry I & II",
        "PHYS 2110/2120 - General Physics I & II",
    ]),
    ("science_second", "Second science course (different prefix)", 4, [
        "CHEM 1010 - Intro General Chemistry I",
        "PHYS 2010 - General Physics I",
        "BIOL 1110 - General Biology I",
    ]),
]

# True Blue Core requirements
TBC_GENERIC = [
    ("tbc_written_comm", "Written Communication", 3, [
        "ENGL 1010 - Expository Writing",
    ]),
    ("tbc_info_lit", "Information Literacy", 3, [
        "Any course flagged Information Literacy",
    ]),
    ("tbc_history_civic", "History and Civic Learning", 6, [
        "HIST 2010 - Survey of US History I",
        "HIST 2020 - Survey of US History II",
    ]),
    ("tbc_hssr", "Human Society and Social Relationships", 6, [
        "PSY 1410 - General Psychology",
        "SOC 1010 - Introductory Sociology",
    ]),
    ("tbc_cce", "Creativity and Cultural Expression", 6, [
        "ART 1030 - Introduction to Visual Arts",
        "MUS 1030 - Introduction to Music",
        "ENGL 2030 - The Experience of Literature",
    ]),
]

ELECTIVES_GENERIC_ID = "general_electives"
ELECTIVES_SUGGESTIONS = ["Any elective course; at least 4 hrs must be upper-division"]

# Function to return the total credit hours required for CSCI Core
def core_required_hours():
    return sum(hours for _, hours, _ in CS_CORE_COURSES)

# Function to return the total hours required for a concentration
def concentration_required_hours():
    return sum(hours for _, hours, _ in CONCENTRATION_REQUIRED_COURSES) + CONCENTRATION_ELECTIVE_HOURS

# Function to return the total hours required for supporting courses
def supporting_required_hours():
    return sum(hours for _, hours, _ in SUPPORTING_COURSES) + sum(hours for _, _, hours, _ in SUPPORTING_GENERIC)

# Function to return the total TBC requirement hours
def tbc_required_hours():
    return sum(hours for _, _, hours, _ in TBC_GENERIC)

# Function that calculates general elective hours that are needed to reach 120
def elective_required_hours():
    used = (
        core_required_hours()
        + concentration_required_hours()
        + supporting_required_hours()
        + tbc_required_hours()
    )

    return max(TOTAL_PROGRAM_HOURS - used, 0)

# Function to check for upper-division courses
def is_upper_division_csci(code):
    parts = code.split()

    if len(parts) != 2 or parts[0] != "CSCI" or not parts[1].isdigit():
        return False

    return int(parts[1]) >= UPPER_DIVISION_MIN

# Function to return a course's catalog hour or fallback value
def _course_hours(code, catalog, fallback=3):
    info = catalog.get(code) if catalog else None

    if info and info.get("credits"):
        return info["credits"]

    return fallback

# Function that keeps an entered hour value between 0 and the requirement max
def clamp_hours(value, maximum):
    try:
        value = float(value)
    except (TypeError, ValueError):
        value = 0.0

    return max(0.0, min(value, maximum))

# Function to display whole number floats as ints
def _nice(number):
    return int(number) if float(number).is_integer() else number

# Function that returns every generic requirement used in audit
def all_generic_items():
    return SUPPORTING_GENERIC + TBC_GENERIC + [
        (ELECTIVES_GENERIC_ID, "General elective", elective_required_hours(), ELECTIVES_SUGGESTIONS)
    ]

# Function that returns generic requirements that have hours remaining
def generic_remaining(generic_hours):
    generic_hours = generic_hours or {}

    remaining = {}

    for generic_id, label, hours, suggestions in all_generic_items():
        entered = clamp_hours(
            generic_hours.get(generic_id), 
            hours
        )

        left = hours - entered

        if left > 0:
            remaining[generic_id] = {"label": label, "hours": left, "suggestions": suggestions}

    return remaining

# Function that builds the complete degree audit from the completed hours
def build_audit(completed_courses, generic_hours, catalog=None):
    completed_courses = {c.strip().upper() for c in completed_courses}

    generic_hours = generic_hours or {}
    catalog = catalog or {}
    groups = []

    # Computer Science core section
    core_items = []
    core_hours_done = 0

    for code, hours, title in CS_CORE_COURSES:
        done = code in completed_courses

        if done:
            core_hours_done += hours

        core_items.append({"label": f"{code} - {title}", "hours": hours, "done": done})

    groups.append({
        "key": "cs_core",
        "label": "Computer Science Core",
        "required_hours": core_required_hours(),
        "completed_hours": core_hours_done,
        "entries": core_items,
    })

    # Required courses do not also count towards elective hours
    required_codes = {code for code, _, _ in CS_CORE_COURSES} | {
        code for code, _, _ in CONCENTRATION_REQUIRED_COURSES
    }

    # Professional CSCI section
    conc_items = []
    conc_hours_done = 0

    for code, hours, title in CONCENTRATION_REQUIRED_COURSES:
        done = code in completed_courses

        if done:
            conc_hours_done += hours

        conc_items.append({"label": f"{code} - {title}", "hours": hours, "done": done})

    # Completed upper-division CSCI courses outside the required list 
    # are put towards the elective requirement
    elective_codes = sorted(
        code for code in completed_courses
        if is_upper_division_csci(code) and code not in required_codes
    )

    elective_hours_done = min(
        sum(_course_hours(code, catalog) for code in elective_codes),
        CONCENTRATION_ELECTIVE_HOURS,
    )

    conc_items.append({
        "label": f"CSCI upper-division electives ({len(elective_codes)} course(s) applied)",
        "hours": CONCENTRATION_ELECTIVE_HOURS,
        "done": elective_hours_done >= CONCENTRATION_ELECTIVE_HOURS,
        "partial_hours": elective_hours_done,
    })

    conc_hours_done += elective_hours_done

    groups.append({
        "key": "concentration",
        "label": "Professional Computer Science Concentration",
        "required_hours": concentration_required_hours(),
        "completed_hours": conc_hours_done,
        "entries": conc_items,
    })

    # Supporting course section
    sup_items = []
    sup_hours_done = 0

    for code, hours, title in SUPPORTING_COURSES:
        done = code in completed_courses

        if done:
            sup_hours_done += hours

        sup_items.append({"label": f"{code} - {title}", "hours": hours, "done": done})

    # Generic supporting requirements can be partially completed
    for generic_id, label, hours, suggestions in SUPPORTING_GENERIC:
        entered = clamp_hours(generic_hours.get(generic_id), hours)

        sup_hours_done += entered

        sup_items.append({
            "label": label, 
            "hours": hours, 
            "done": entered >= hours,
            "partial_hours": entered, 
            "generic_id": generic_id, 
            "suggestions": suggestions,
        })

    groups.append({
        "key": "supporting",
        "label": "Supporting Courses",
        "required_hours": supporting_required_hours(),
        "completed_hours": sup_hours_done,
        "entries": sup_items,
    })

    # True Blue Core section
    tbc_items = []
    tbc_hours_done = 0

    for generic_id, label, hours, suggestions in TBC_GENERIC:
        entered = clamp_hours(generic_hours.get(generic_id), hours)

        tbc_hours_done += entered

        tbc_items.append({
            "label": label, 
            "hours": hours, 
            "done": entered >= hours,
            "partial_hours": entered, 
            "generic_id": generic_id, 
            "suggestions": suggestions,
        })

    groups.append({
        "key": "tbc",
        "label": "True Blue Core (general education)",
        "required_hours": tbc_required_hours(),
        "completed_hours": tbc_hours_done,
        "entries": tbc_items,
    })

    # Builds the general elective section using the remaining hours needed to reach 120
    elective_total = elective_required_hours()
    elective_entered = clamp_hours(generic_hours.get(ELECTIVES_GENERIC_ID), elective_total)

    groups.append({
        "key": "electives",
        "label": "General Electives",
        "required_hours": elective_total,
        "completed_hours": elective_entered,
        "entries": [{
            "label": "Free electives (at least 4 hrs upper-division)",
            "hours": elective_total,
            "done": elective_entered >= elective_total,
            "partial_hours": elective_entered,
            "generic_id": ELECTIVES_GENERIC_ID,
            "suggestions": ELECTIVES_SUGGESTIONS,
        }],
    })

    # Calculates the totals accross all degree audit courses
    total_required = sum(group["required_hours"] for group in groups)
    total_completed = min(sum(group["completed_hours"] for group in groups), total_required)

    for group in groups:
        group["required_hours"] = _nice(group["required_hours"])
        group["completed_hours"] = _nice(group["completed_hours"])
        for entry in group["entries"]:
            entry["hours"] = _nice(entry["hours"])
            if "partial_hours" in entry:
                entry["partial_hours"] = _nice(entry["partial_hours"])

    return {
        "groups": groups,
        "total_required": _nice(total_required),
        "total_completed": _nice(total_completed),
        "percent": round(100 * total_completed / total_required, 1) if total_required else 0,
    }
