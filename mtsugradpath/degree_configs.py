"""Full degree requirement configs for each supported major.

Each config dict has:
  key, prefix, name
  extra_prefixes:         optional [prefix, ...] – other prefixes the major's own courses use
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

# ── Political Science (Political and Global Affairs) ──────────────────────────────
# MTSU's current catalog no longer has a standalone "Political Science" major --
# it was restructured into "Political and Global Affairs" (B.A./B.S.), with an
# optional concentration in International Relations, Public Policy and
# Management, or Pre-Law. This models the no-concentration B.S. track.
#
# Several requirement slots are catalog "pick one of N" choices (Methods,
# Comparative/International Relations, Political Theory, Experiential
# Component) rather than a single fixed course -- those are modeled as
# supporting_generic hour buckets with the real options listed as suggestions,
# the same way the STEM majors handle a science-sequence choice. The major
# also requires two separate minors (15-18 hours each per the catalog); those
# aren't trackable course-by-course here, so each is a nominal 15-hour bucket.
PS_CONFIG = {
    "key": "political_science",
    "prefix": "PS",
    "name": "B.S. Political and Global Affairs",
    "concentration": None,
    "core_courses": [
        ("PS 1005", 3, "Introduction to American Politics"),
        ("PS 1010", 3, "Introduction to Global Politics"),
    ],
    "concentration_courses": [],
    "concentration_elective_hours": 15,
    "high_level_options": [],
    "supporting_courses": [
        ("GEOG 2000", 3, "Introduction to Regional Geography"),
        ("PGA 2000", 3, "Professional Development in Political and Global Affairs"),
        ("PGA 4800", 3, "Senior Seminar"),
    ],
    "supporting_generic": [
        ("pga_methods", "Methods requirement", 3, [
            "PGA 2100 - Understanding Data and Research in Political and Global Affairs",
            "PS 3001 - Research Methods in Political Science",
            "PS 3360 - Law and Policy",
        ]),
        ("pga_comparative_ir", "Comparative/International Relations", 3, [
            "PS 3210 - International Relations",
            "PS 3220 - Comparative Politics",
        ]),
        ("pga_political_theory", "Political Theory", 3, [
            "PS 4230 - Classical Political Theory",
            "PS 4700 - American Political Thought",
            "PS 4920 - Modern Political Theory",
        ]),
        ("pga_experiential", "Experiential component", 3, [
            "PS 4290 - Public Service Internship",
            "PS 4360 - Legislative Internship",
            "PS 4280 - The Washington Experience",
        ]),
        ("pga_minor_1", "Minor #1 (required)", 15, [
            "Any approved minor, e.g. History, Criminal Justice, Economics",
        ]),
        ("pga_minor_2", "Minor #2 (required, different from Minor #1)", 15, [
            "A second, different approved minor",
        ]),
    ],
    "tbc_generic": _TBC,
    "total_hours": TOTAL_PROGRAM_HOURS,
    "prereq_map": {},
    # The catalog's "PS 1005 or PS 1010" style prereqs are simplified to a
    # single required course, same convention as the STEM configs' own
    # placement-style overrides -- otherwise catalog scraping would AND both
    # codes together instead of treating them as alternatives.
    "prereq_override_map": {
        "PS 3001": {"PS 1005"},
        "PS 3210": {"PS 1010"},
        "PS 3220": {"PS 1010"},
        "PS 4230": {"PS 1005"},
        "PS 4700": {"PS 1005"},
        "PS 4920": {"PS 1005"},
        "PS 4930": {"PS 1005"},
    },
    # PGA 4800 is a senior capstone seminar; PGA isn't a tracked major prefix
    # so its real "senior standing" prereq (not itself a course) is proxied by
    # requiring both intro core courses be done first.
    "supporting_prereq_map": {
        "PGA 4800": {"PS 1005", "PS 1010"},
    },
    "offering_seasons": {},
    "odd_year_spring_only": set(),
    "upper_division_prefix": "PS",
    "upper_division_min": 3000,
    "available": True,
}

# ── Aerospace, Professional Pilot Concentration ────────────────────────────────────
# The catalog's "Professional Pilot Concentration (47 hours)" section lists 30
# AERO courses, but several belong to other concentrations sharing the same
# course pool (Maintenance Management's AERO 3362, Air Traffic Control's AERO
# 3630, the optional Flight Instructor add-on AERO 4201-4210) or are
# alternative single-credit flight labs -- the 17 courses below plus a 1-hour
# elective flight lab (via the normal elective-pool mechanism) sum to exactly
# 47 hours and match the real prerequisite chain from the catalog.
AERO_CONFIG = {
    "key": "aerospace",
    "prefix": "AERO",
    "name": "B.S. Aerospace",
    "concentration": "Professional Pilot Concentration",
    "core_courses": [
        ("AERO 1010", 3, "Introduction to Aerospace"),
        ("AERO 1020", 3, "Theory of Flight"),
        ("AERO 3020", 3, "Aerospace Materials"),
        ("AERO 3030", 3, "Propulsion Fundamentals"),
        ("AERO 4040", 1, "Professional Aviation Pathways"),
    ],
    "concentration_courses": [
        ("AERO 1230", 3, "Aviation Laws and Regulations"),
        ("AERO 2010", 3, "Aviation Weather"),
        ("AERO 2230", 3, "Professional Pilot I"),
        ("AERO 2201", 2, "Professional Pilot Flight Lab I"),
        ("AERO 3170", 3, "Aviation Safety"),
        ("AERO 3210", 3, "Professional Pilot II"),
        ("AERO 3203", 2, "Professional Pilot Flight Lab II"),
        ("AERO 3215", 3, "Professional Pilot III"),
        ("AERO 3204", 2, "Professional Pilot Flight Lab III"),
        ("AERO 3240", 3, "Professional Pilot IV"),
        ("AERO 3261", 1, "Professional Pilot Flight Lab IV"),
        ("AERO 3230", 3, "Crew Resource Management"),
        ("AERO 3440", 3, "Fundamentals of Aerodynamics"),
        ("AERO 4250", 3, "Professional Pilot V"),
        ("AERO 4310", 3, "Aerospace Vehicle Systems"),
        ("AERO 4440", 3, "Aircraft Performance"),
        ("AERO 3080", 3, "Aviation Weather II"),
    ],
    "concentration_elective_hours": 1,
    "high_level_options": [],
    "supporting_courses": [
        ("MATH 1710", 3, "College Algebra"),
        ("MATH 1810", 3, "Applied Calculus I"),
        ("CHEM 1010", 4, "Introductory General Chemistry I"),
        ("PHYS 2010", 4, "Non-Calculus-Based Physics I"),
        ("BCED 3510", 3, "Business Communication"),
        ("COMM 2560", 3, "Intercultural Communication"),
        ("GS 2010", 3, "Introduction to Cross-Cultural Experiences"),
        ("PHIL 3150", 3, "Ethics"),
    ],
    "supporting_generic": [],
    "tbc_generic": _TBC,
    "total_hours": TOTAL_PROGRAM_HOURS,
    "prereq_map": {
        "AERO 3020": {"AERO 1010", "AERO 1020", "MATH 1810", "PHYS 2010"},
        "AERO 3030": {"AERO 1010", "AERO 1020"},
        "AERO 4040": {"AERO 1010", "AERO 1020", "AERO 3020", "AERO 3030"},
        "AERO 2201": {"AERO 2230"},
        "AERO 3210": {"AERO 2230", "AERO 2201"},
        "AERO 3203": {"AERO 3210"},
        "AERO 3215": {"AERO 2010", "AERO 3203"},
        "AERO 3204": {"AERO 3203", "AERO 3215"},
        "AERO 3230": {"AERO 2230"},
        "AERO 3240": {"AERO 3204"},
        "AERO 3261": {"AERO 3204", "AERO 3215"},
        "AERO 4250": {"AERO 3240"},
        "AERO 4310": {"AERO 1010", "AERO 1020"},
        "AERO 4440": {"AERO 3440"},
        "AERO 3440": {"MATH 1810", "PHYS 2010"},
        "AERO 3080": {"AERO 2010"},
        "AERO 3170": {"AERO 1020"},
    },
    # Real prereqs like "Private Pilot Certificate" or "Instrument Rating"
    # aren't course codes -- proxied by the AERO course sequence that actually
    # earns them (e.g. AERO 2230 + its flight lab before AERO 3210).
    "prereq_override_map": {},
    "supporting_prereq_map": {
        "MATH 1810": {"MATH 1710"},
        "PHYS 2010": {"MATH 1710"},
    },
    "offering_seasons": {},
    "odd_year_spring_only": set(),
    "upper_division_prefix": "AERO",
    "upper_division_min": 3000,
    "available": True,
}

# ── Construction Management, Commercial Construction Management Concentration ──
# Built from catalog 36 ("Construction Management, Commercial Construction
# Management Concentration, B.S."). The major draws on two prefixes: CMT
# (Construction Management) and CCM (School of Concrete and Construction
# Management, shared with the Concrete Industry Management major), so CCM is
# listed in extra_prefixes to be synced, shown in the checklist, and have its
# prerequisites resolved through this config.
#
# Catalog quirks handled here:
#  - The core's "CCM 4010 or BLAW 3400" law choice is a supporting_generic
#    bucket, the same way Political and Global Affairs handles pick-one slots.
#  - CMT 3300 (internship) is variable 1-9 hours; the core requires 3.
#  - GEOL 1041 is a 0-hour lab taken with GEOL 1040, so it isn't listed.
#  - Several catalog prereqs still name CCM 2050 (Plan Reading), which has
#    been replaced by CCM 2060 (Construction Plan Reading) -- prereq_map
#    uses CCM 2060 instead.
#  - "Junior standing" / "Permission of department" prereqs aren't course
#    codes -- proxied by the course that precedes them on the academic map.
# Hours: core 39 + concentration 18 + supporting 17 + TBC 41 = 115, leaving
# 5 hours of general electives, matching the catalog's 5-12.
CM_CONFIG = {
    "key": "construction_mgmt",
    "prefix": "CMT",
    "extra_prefixes": ["CCM"],
    "name": "B.S. Construction Management",
    "concentration": "Commercial Construction Management Concentration",
    "core_courses": [
        ("CCM 1010", 1, "Introduction to the Concrete and Construction Industry"),
        ("CCM 1500", 3, "Land Surveying"),
        ("CCM 1501", 1, "Land Surveying Lab"),
        ("CCM 2060", 3, "Construction Plan Reading"),
        ("CCM 2200", 3, "Project Estimating"),
        ("CMT 2100", 3, "Construction Means and Methods"),
        ("CMT 2320", 3, "Architectural Computer-Aided Drafting and Design"),
        ("CMT 3100", 3, "Mechanical and Electrical Systems"),
        ("CMT 3210", 3, "Construction Codes and Regulation"),
        ("CMT 3300", 3, "Construction Management Internship"),
        ("CMT 3800", 3, "Soil Mechanics for Construction"),
        ("CMT 3801", 1, "Soil Mechanics for Construction Lab"),
        ("CMT 4120", 3, "Scheduling"),
        ("CMT 4160", 3, "Construction Safety and Health Management"),
    ],
    "concentration_courses": [
        ("CCM 2550", 3, "Engineering Mechanics for Construction"),
        ("CMT 3000", 3, "Commercial Construction and Materials"),
        ("CMT 4140", 3, "Construction Management Principles"),
        ("CMT 4200", 3, "Commercial Cost Estimating and Bidding"),
        ("CMT 4280", 3, "Commercial Construction Capstone"),
        ("CMT 4320", 3, "Software Applications for Virtual Design and Construction"),
    ],
    "concentration_elective_hours": 0,
    "high_level_options": [],
    "supporting_courses": [
        ("MATH 1730", 4, "Pre-Calculus"),
        ("GEOL 1040", 4, "Physical Geology (with GEOL 1041 lab)"),
        ("ACTG 3000", 3, "Survey of Accounting for General Business"),
        ("FIN 3000", 3, "Survey of Finance"),
        ("MKT 3820", 3, "Principles of Marketing"),
    ],
    "supporting_generic": [
        ("cm_law", "Construction law requirement", 3, [
            "CCM 4010 - Concrete and Construction Law",
            "BLAW 3400 - Legal Environment of Business",
        ]),
    ],
    "tbc_generic": _TBC,
    "total_hours": TOTAL_PROGRAM_HOURS,
    "prereq_map": {
        "CCM 2200": {"CCM 2060"},
        "CCM 2550": {"CMT 2100"},
        "CMT 2100": {"CCM 1010", "CCM 2060"},
        "CMT 2320": {"CCM 2060"},
        "CMT 3100": {"CMT 2100"},
        "CMT 3300": {"CMT 2100"},  # permission of department
        "CMT 3800": {"CMT 2100"},
        "CMT 3801": {"CMT 2100"},
        "CMT 4120": {"CCM 2200"},
        "CMT 4140": {"CMT 2100"},  # junior or senior standing
        "CMT 4160": {"CCM 1010", "CCM 2060"},
        "CMT 4200": {"CCM 2060", "CCM 2200"},
        "CMT 4280": {"CMT 4140", "CMT 4200"},  # capstone, permission of department
        "CMT 4320": {"CMT 2320"},
    },
    # Catalog text for CMT 3801 reads "Prerequisite: CMT 2100; corequisite:
    # CMT 3800", which the scraper parses as both being prerequisites -- the
    # lab is taken alongside CMT 3800, not after it.
    "prereq_override_map": {
        "CMT 3801": {"CMT 2100"},
    },
    "supporting_prereq_map": {
        "ACTG 3000": {"MATH 1730"},  # TBC Quantitative Literacy, sophomore standing
        "FIN 3000": {"CMT 2100"},    # junior standing
        "MKT 3820": {"CMT 2100"},    # junior standing
    },
    "offering_seasons": {},
    "odd_year_spring_only": set(),
    "upper_division_prefix": "CMT",
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
    "political_science": PS_CONFIG,
    "aerospace": AERO_CONFIG,
    "construction_mgmt": CM_CONFIG,
}


def major_prefixes(cfg: dict) -> list:
    """Every course prefix a major's own courses use, main prefix first."""
    return [cfg["prefix"], *cfg.get("extra_prefixes", [])]


def get_full_degree_config(key: str) -> dict:
    """Return the full degree config for *key*, defaulting to CS."""
    return DEGREE_CONFIGS.get(key) or CS_CONFIG
