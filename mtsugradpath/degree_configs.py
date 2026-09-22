"""Full degree requirement configs for each supported major.

Each config dict has:
  key, prefix, name
  core_courses:           [(code, hours, title), ...]
  concentration_courses:  [(code, hours, title), ...]
  concentration_elective_hours: int
  high_level_options:     [code, ...] – one of these satisfies the language/options req
  supporting_courses:     [(code, hours, title), ...]
  supporting_generic:     [(generic_id, label, hours, [suggestions]), ...]
  tbc_generic:            same format (shared gen-ed, same for all MTSU majors)
  total_hours:            120
  prereq_map:             {code: set(prereq_codes)} – fallback when catalog data missing
  supporting_prereq_map:  {code: set(prereq_codes)}
  offering_seasons:       {code: {seasons}} – empty dict = offered every semester
  odd_year_spring_only:   set of codes
  upper_division_prefix:  prefix to use for upper-division check ("CSCI", "BIOL", etc.)
  upper_division_min:     int, e.g. 3000 or 4000
"""

from .degree import TBC_GENERIC, TOTAL_PROGRAM_HOURS

# ── Shared gen-ed (True Blue Core) – identical across all MTSU undergrad majors ──
_TBC = TBC_GENERIC

# ── Computer Science ──────────────────────────────────────────────────────────────
CS_CONFIG = {
    "key": "cs",
    "prefix": "CSCI",
    "name": "B.S. Computer Science",
    "concentration": "Professional Concentration",
    "core_courses": [
        ("CSCI 1010", 1, "Computer Science Colloquium"),
        ("CSCI 1170", 4, "Computer Science I"),
        ("CSCI 2170", 4, "Computer Science II"),
        ("CSCI 3080", 3, "Discrete Structures"),
        ("CSCI 3110", 3, "Algorithms and Data Structures"),
        ("CSCI 3130", 4, "Assembly and Computer Organization"),
        ("CSCI 3240", 4, "Introduction to Computer Systems"),
        ("CSCI 4700", 3, "Software Engineering"),
    ],
    "concentration_courses": [
        ("CSCI 3210", 3, "Theory of Programming Languages"),
        ("CSCI 4160", 3, "Compiler Design and Software Development"),
    ],
    "concentration_elective_hours": 12,
    "high_level_options": ["CSCI 3033", "CSCI 3037", "CSCI 3038"],
    "supporting_courses": [
        ("COMM 2200", 3, "Audience-Centered Communication"),
        ("MATH 1910", 4, "Calculus I"),
        ("MATH 1920", 4, "Calculus II"),
        ("MATH 2050", 3, "Probability and Statistics"),
        ("PHIL 3170", 3, "Ethics and Computing Technology"),
    ],
    "supporting_generic": [
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
    ],
    "tbc_generic": _TBC,
    "total_hours": TOTAL_PROGRAM_HOURS,
    "prereq_map": {
        "CSCI 2170": {"CSCI 1170"},
        "CSCI 3080": {"CSCI 1170"},
        "CSCI 3110": {"CSCI 2170", "CSCI 3080"},
        "CSCI 3130": {"CSCI 2170"},
        "CSCI 3240": {"CSCI 2170", "CSCI 3130"},
        "CSCI 3210": {"CSCI 3110"},
        "CSCI 4160": {"CSCI 3080", "CSCI 3110", "CSCI 3130"},
        "CSCI 4700": {"CSCI 3080", "CSCI 3110", "CSCI 3240"},
    },
    "supporting_prereq_map": {
        "MATH 1920": {"MATH 1910"},
    },
    "offering_seasons": {
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
    },
    "odd_year_spring_only": {"CSCI 4360"},
    "upper_division_prefix": "CSCI",
    "upper_division_min": 3000,
    "available": True,
}

# ── Biology ───────────────────────────────────────────────────────────────────────
BIOL_CONFIG = {
    "key": "biology",
    "prefix": "BIOL",
    "name": "B.S. Biology",
    "concentration": None,
    "core_courses": [
        ("BIOL 1110", 4, "General Biology I"),
        ("BIOL 1120", 4, "General Biology II"),
        ("BIOL 2230", 4, "Genetics"),
        ("BIOL 2430", 4, "Cell Biology"),
        ("BIOL 3020", 4, "Comparative Anatomy of the Vertebrates"),
        ("BIOL 3300", 4, "Ecology"),
        ("BIOL 4110", 4, "Evolution"),
        ("BIOL 4800", 1, "Biology Seminar"),
    ],
    "concentration_courses": [],
    "concentration_elective_hours": 12,
    "high_level_options": [],
    "supporting_courses": [
        ("CHEM 1110", 4, "General Chemistry I"),
        ("CHEM 1120", 4, "General Chemistry II"),
        ("MATH 1530", 3, "Probability and Statistics"),
    ],
    "supporting_generic": [
        ("biol_physics", "Physics sequence (PHYS 2010/2110 or equiv)", 8, [
            "PHYS 2010/2020 - Non-Calculus Physics I & II",
            "PHYS 2110/2120 - Calculus-based Physics I & II",
        ]),
        ("biol_chem_upper", "Upper-division Chemistry", 4, [
            "CHEM 2230 - Organic Chemistry I",
            "CHEM 3530 - Biochemistry I",
        ]),
    ],
    "tbc_generic": _TBC,
    "total_hours": 120,
    "prereq_map": {
        "BIOL 1120": {"BIOL 1110"},
        "BIOL 2230": {"BIOL 1120"},
        "BIOL 2430": {"BIOL 1120"},
        "BIOL 3020": {"BIOL 1120"},
        "BIOL 3300": {"BIOL 1120"},
        "BIOL 4110": {"BIOL 2230"},
        "BIOL 4800": {"BIOL 3300"},  # capstone seminar — require at least one upper-div course
        "CHEM 1120": {"CHEM 1110"},
    },
    # Catalog lists lab-section codes (BIOL 1111, BIOL 1121, BIOL 3250/3251) as
    # prerequisites — these are bundled with the lecture in practice.  Override
    # so the planner only checks for the corresponding lecture course.
    "prereq_override_map": {
        "BIOL 1120": {"BIOL 1110"},
        "BIOL 2230": {"BIOL 1120"},
        "BIOL 3020": {"BIOL 1120"},
        "BIOL 4110": {"BIOL 2230"},
    },
    "supporting_prereq_map": {
        "CHEM 1120": {"CHEM 1110"},
    },
    "offering_seasons": {
        "BIOL 1110": {"Fall", "Spring"},
        "BIOL 1120": {"Fall", "Spring"},
        "BIOL 2230": {"Fall", "Spring"},
        "BIOL 2430": {"Fall", "Spring"},
        "BIOL 3020": {"Fall"},
        "BIOL 3300": {"Spring"},
        "BIOL 4110": {"Spring"},
        "BIOL 4800": {"Fall", "Spring"},
    },
    "odd_year_spring_only": set(),
    "upper_division_prefix": "BIOL",
    "upper_division_min": 3000,
    "available": True,
}

# ── Mathematics ───────────────────────────────────────────────────────────────────
MATH_CONFIG = {
    "key": "math",
    "prefix": "MATH",
    "name": "B.S. Mathematics",
    "concentration": None,
    "core_courses": [
        ("MATH 1910", 4, "Calculus I"),
        ("MATH 1920", 4, "Calculus II"),
        ("MATH 2010", 3, "Elements of Linear Algebra"),
        ("MATH 2050", 3, "Probability and Statistics"),
        ("MATH 3110", 4, "Calculus III"),
        ("MATH 3120", 3, "Ordinary Differential Equations"),
        ("MATH 4250", 3, "Abstract Algebra"),
        ("MATH 4420", 3, "Real Analysis I"),
    ],
    "concentration_courses": [],
    "concentration_elective_hours": 9,
    "high_level_options": [],
    "supporting_courses": [
        ("CSCI 1170", 4, "Computer Science I"),
    ],
    "supporting_generic": [
        ("math_science", "Lab science sequence", 8, [
            "PHYS 2110/2120 - General Physics I & II",
            "CHEM 1110/1120 - General Chemistry I & II",
        ]),
        ("math_upper_elective", "Additional upper-division MATH electives", 9, [
            "MATH 4260 - Number Theory",
            "MATH 4430 - Real Analysis II",
            "MATH 4510 - Complex Analysis",
        ]),
    ],
    "tbc_generic": _TBC,
    "total_hours": 120,
    "prereq_map": {
        "MATH 1920": {"MATH 1910"},
        "MATH 3110": {"MATH 1920"},
        "MATH 3120": {"MATH 1920"},
        "MATH 4250": {"MATH 2010"},
        "MATH 4420": {"MATH 3110"},
        "MATH 2010": {"MATH 1910"},
        "MATH 2050": {"MATH 1910"},
    },
    # MATH 1730 (Pre-Calculus) is a placement requirement, not a course students
    # take as part of the degree.  Override the catalog prereq so incoming students
    # with calculus readiness can start at MATH 1910 on day one.
    "prereq_override_map": {
        "MATH 1910": set(),
    },
    "supporting_prereq_map": {},
    "offering_seasons": {
        "MATH 1910": {"Fall", "Spring"},
        "MATH 1920": {"Fall", "Spring"},
        "MATH 2010": {"Fall", "Spring"},
        "MATH 2050": {"Fall", "Spring"},
        "MATH 3110": {"Fall", "Spring"},
        "MATH 3120": {"Fall", "Spring"},
        "MATH 4250": {"Fall"},
        "MATH 4420": {"Spring"},
    },
    "odd_year_spring_only": set(),
    "upper_division_prefix": "MATH",
    "upper_division_min": 3000,
    "available": True,
}

# ── Chemistry ─────────────────────────────────────────────────────────────────────
CHEM_CONFIG = {
    "key": "chemistry",
    "prefix": "CHEM",
    "name": "B.S. Chemistry",
    "concentration": None,
    "core_courses": [
        ("CHEM 1110", 4, "General Chemistry I"),
        ("CHEM 1120", 4, "General Chemistry II"),
        ("CHEM 2230", 4, "Organic Chemistry I"),
        ("CHEM 2240", 4, "Organic Chemistry II"),
        ("CHEM 3010", 4, "Analytical Chemistry"),
        ("CHEM 3510", 3, "Physical Chemistry I"),
        ("CHEM 3520", 3, "Physical Chemistry II"),
        ("CHEM 4900", 1, "Chemistry Seminar"),
    ],
    "concentration_courses": [],
    "concentration_elective_hours": 8,
    "high_level_options": [],
    "supporting_courses": [
        ("MATH 1910", 4, "Calculus I"),
        ("MATH 1920", 4, "Calculus II"),
        ("PHYS 2110", 4, "General Physics I"),
        ("PHYS 2120", 4, "General Physics II"),
    ],
    "supporting_generic": [
        ("chem_upper", "Upper-division CHEM electives", 8, [
            "CHEM 4510 - Biochemistry II",
            "CHEM 4780 - Polymer and Materials Chemistry",
            "CHEM 4880 - Research",
        ]),
    ],
    "tbc_generic": _TBC,
    "total_hours": 120,
    "prereq_map": {
        "CHEM 1120": {"CHEM 1110"},
        "CHEM 2230": {"CHEM 1120"},
        "CHEM 2240": {"CHEM 2230"},
        "CHEM 3010": {"CHEM 1120"},
        "CHEM 3510": {"CHEM 2240", "MATH 1920"},
        "CHEM 3520": {"CHEM 3510"},
        "PHYS 2120": {"PHYS 2110"},
        "MATH 1920": {"MATH 1910"},
        "CHEM 4900": {"CHEM 2240"},  # capstone seminar — require at least Organic II
    },
    # Catalog lists lab-section codes (CHEM 1111, CHEM 1121) as prerequisites.
    "prereq_override_map": {
        "CHEM 1120": {"CHEM 1110"},
        "CHEM 2230": {"CHEM 1120"},
        "CHEM 3010": {"CHEM 1120"},
    },
    "supporting_prereq_map": {
        "MATH 1920": {"MATH 1910"},
        "PHYS 2120": {"PHYS 2110"},
    },
    "offering_seasons": {
        "CHEM 1110": {"Fall", "Spring"},
        "CHEM 1120": {"Fall", "Spring"},
        "CHEM 2230": {"Fall", "Spring"},
        "CHEM 2240": {"Fall", "Spring"},
        "CHEM 3010": {"Fall"},
        "CHEM 3510": {"Fall"},
        "CHEM 3520": {"Spring"},
        "CHEM 4900": {"Fall", "Spring"},
    },
    "odd_year_spring_only": set(),
    "upper_division_prefix": "CHEM",
    "upper_division_min": 3000,
    "available": True,
}

# ── Physics ───────────────────────────────────────────────────────────────────────
PHYS_CONFIG = {
    "key": "physics",
    "prefix": "PHYS",
    "name": "B.S. Physics",
    "concentration": None,
    "core_courses": [
        ("PHYS 2110", 4, "General Physics I"),
        ("PHYS 2120", 4, "General Physics II"),
        ("PHYS 3010", 3, "Modern Physics"),
        ("PHYS 3110", 3, "Mechanics"),
        ("PHYS 3210", 3, "Electrodynamics I"),
        ("PHYS 4200", 3, "Quantum Mechanics I"),
        ("PHYS 4950", 1, "Physics Seminar"),
    ],
    "concentration_courses": [],
    "concentration_elective_hours": 9,
    "high_level_options": [],
    "supporting_courses": [
        ("MATH 1910", 4, "Calculus I"),
        ("MATH 1920", 4, "Calculus II"),
        ("MATH 3110", 4, "Calculus III"),
        ("MATH 3120", 3, "Ordinary Differential Equations"),
    ],
    "supporting_generic": [
        ("phys_upper", "Upper-division PHYS electives", 9, [
            "PHYS 4210 - Electrodynamics II",
            "PHYS 4310 - Statistical Mechanics",
            "PHYS 4900 - Research",
        ]),
    ],
    "tbc_generic": _TBC,
    "total_hours": 120,
    "prereq_map": {
        "PHYS 2120": {"PHYS 2110", "MATH 1910"},
        "PHYS 3010": {"PHYS 2120"},
        "PHYS 3110": {"PHYS 2120", "MATH 1920"},
        "PHYS 3210": {"PHYS 2120", "MATH 1920"},
        "PHYS 4200": {"PHYS 3010"},
        "PHYS 4950": {"PHYS 3010"},  # capstone seminar — require Modern Physics first
        "MATH 1920": {"MATH 1910"},
        "MATH 3110": {"MATH 1920"},
        "MATH 3120": {"MATH 1920"},
    },
    # Catalog lists lab-section codes as prerequisites (PHYS 2111 for PHYS 2120,
    # PHYS 3100 for PHYS 3110).  These are bundled with the lecture in practice.
    "prereq_override_map": {
        "PHYS 2120": {"PHYS 2110"},
        "PHYS 3110": {"PHYS 2120"},
    },
    "supporting_prereq_map": {
        "MATH 1920": {"MATH 1910"},
        "MATH 3110": {"MATH 1920"},
        "MATH 3120": {"MATH 1920"},
    },
    "offering_seasons": {
        "PHYS 2110": {"Fall", "Spring"},
        "PHYS 2120": {"Fall", "Spring"},
        "PHYS 3010": {"Spring"},
        "PHYS 3110": {"Fall"},
        "PHYS 3210": {"Spring"},
        "PHYS 4200": {"Fall"},
        "PHYS 4950": {"Fall", "Spring"},
    },
    "odd_year_spring_only": set(),
    "upper_division_prefix": "PHYS",
    "upper_division_min": 3000,
    "available": True,
}

# ── Registry ──────────────────────────────────────────────────────────────────────
DEGREE_CONFIGS = {
    "cs": CS_CONFIG,
    "biology": BIOL_CONFIG,
    "math": MATH_CONFIG,
    "chemistry": CHEM_CONFIG,
    "physics": PHYS_CONFIG,
}


def get_full_degree_config(key: str) -> dict:
    """Return the full degree config for *key*, defaulting to CS."""
    return DEGREE_CONFIGS.get(key) or CS_CONFIG
