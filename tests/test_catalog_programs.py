"""Converting catalog programs into planner configs, on a small made-up catalog."""
import json

import pytest

from mtsugradpath import catalog_programs as cp
from mtsugradpath import catalog_store as store
from mtsugradpath.degree import build_audit
from mtsugradpath.planner import generate_plan, load_catalog_for, validate_plan

COURSES = {
    "EXMP 1000": {"title": "Intro", "credits": 4},
    "EXMP 1001": {"title": "Intro Lab", "credits": 0},
    "EXMP 2000": {"title": "Middle", "credits": 3, "prereqs": ["EXMP 1000"]},
    "EXMP 3000": {"title": "Upper A", "credits": 3, "prereqs": ["EXMP 2000"]},
    "EXMP 3100": {"title": "Upper B", "credits": 3, "prereqs": ["EXMP 2000"]},
    "EXMP 3200": {"title": "Upper C", "credits": 3},
    "EXMP 4000": {"title": "Capstone", "credits": 3, "prereqs": ["EXMP 3000", "EXMP 9999"],
                  "prereq_any": [["EXMP 3100", "EXMP 3200"]]},
    "MATH 1530": {"title": "Applied Statistics", "credits": 3},
    "MATH 1910": {"title": "Calculus I", "credits": 4},
    "OTHR 2000": {"title": "Option One", "credits": 3},
    "OTHR 2100": {"title": "Option Two", "credits": 3},
    "OTHR 2200": {"title": "Option Three", "credits": 3},
    "BUSN 3000": {"title": "Business Core A", "credits": 3},
    "BUSN 3100": {"title": "Business Core B", "credits": 3},
}

MAJOR = {
    "id": 900, "name": "Example Studies, B.S.", "type": "Major", "degree": "Bachelor of Science",
    "department": "Example", "active": True, "url": "https://catalog.example/major",
    "sections": [
        {"name": "Degree Requirements", "text": "True Blue Core 41 hours\nTOTAL\n120 hours", "children": [
            {"name": "True Blue Core (41 hours)", "text": "The following courses required by the major meet "
                                                        "True Blue Core requirements: MATH 1910 (Quant Lit)"},
            {"name": "Major Requirements (22 hours)", "items": [
                {"course": "EXMP 1000"}, {"op": "AND"}, {"course": "EXMP 1001"},
                {"course": "EXMP 2000"}, {"course": "EXMP 3000"}, {"course": "EXMP 4000"},
                {"text": "EXMP upper-division electives", "hours": [6, 6]},
            ], "children": [
                {"name": "Choose one from the following:", "items": [
                    {"course": "EXMP 3100"}, {"course": "EXMP 3200"},
                ]},
            ]},
            {"name": "Supporting Courses (13 hours)", "items": [
                {"course": "MATH 1910"}, {"note": "(may be counted in the True Blue Core)"},
                {"course": "MATH 1530"}, {"op": "OR"}, {"course": "OTHR 2000"},
            ], "children": [
                {"name": "Select two:", "items": [
                    {"course": "OTHR 2100"}, {"course": "OTHR 2200"}, {"course": "OTHR 2000"},
                ]},
            ]},
            {"name": "College of Example Core (6 hours)"},
            {"name": "Electives (20 hours)"},
        ]},
        {"name": "Curriculum: Example", "children": [
            {"name": "Freshman", "items": [{"course": "EXMP 1000"}, {"course": "BUSN 3000"}, {"course": "BUSN 3100"}]},
        ]},
    ],
}

MINOR = {
    "id": 901, "name": "Example Minor", "type": "Minor", "active": True,
    "sections": [
        {"name": "Required (7 hours)", "items": [{"course": "EXMP 1000"}, {"course": "EXMP 2000"}]},
        {"name": "Electives (6 hours)", "text": "Choose from:", "items": [
            {"course": "EXMP 3100"}, {"course": "EXMP 3200"}, {"course": "OTHR 2100"},
        ]},
    ],
}


@pytest.fixture
def example_catalog(tmp_path, monkeypatch):
    path = tmp_path / "catalog.json"
    path.write_text(json.dumps({
        "catalog": {"name": "Test Catalog", "fetched_at": "2026-10-20T00:00:00"},
        "courses": COURSES,
        "programs": [MAJOR, MINOR],
    }))
    monkeypatch.setenv("GRADPATH_CATALOG_FILE", str(path))
    store.reload()
    yield
    # monkeypatch restores the environment after this; make the store look
    # for the catalog file again on its next access
    store.reload()


def test_section_hours_from_names_and_text():
    assert cp.section_hours({"name": "Choose 6 credit hours from the following:"}) == 6
    assert cp.section_hours({"name": "At least five hours from:"}) == 5
    assert cp.section_hours({"name": "Diverse Voices (choose one):"}) == 3
    assert cp.section_hours({"name": "Plus two of the following:"}) == 6
    assert cp.section_hours({"name": "Study Abroad", "text": "up to 9 credit hours"}) is None
    assert cp.section_hours({"name": "History"}) is None


def test_parse_units_folds_or_and_and():
    units = cp.parse_units([
        {"course": "A 1000"}, {"op": "AND"}, {"course": "A 1001"},
        {"course": "B 1000"}, {"op": "OR"}, {"course": "C 1000"}, {"op": "OR"}, {"course": "D 1000"},
        {"text": "(3 credit hours may be counted in the True Blue Core)", "hours": [3, 3]},
    ])
    assert units[0] == {"kind": "course", "code": "A 1000", "with": ["A 1001"]}
    assert units[1]["kind"] == "choice"
    assert [o["code"] for o in units[1]["options"]] == ["B 1000", "C 1000", "D 1000"]
    # a parenthesized hours note is a note, not a requirement
    assert len(units) == 2


def test_major_conversion(example_catalog):
    cfg = cp.catalog_majors()["example-studies-bs"]
    assert cfg["catalog_driven"] and cfg["prefix"] == "EXMP"
    assert [c for c, _, _ in cfg["core_courses"]] == ["EXMP 1000", "EXMP 2000", "EXMP 3000", "EXMP 4000"]
    # The 0-credit lab rides along with its lecture
    assert cfg["coreq_map"] == {"EXMP 1000": {"EXMP 1001"}}
    assert cfg["concentration_elective_hours"] == 6
    # Empty "College Core (6 hours)" is filled from the four-year map
    assert {"BUSN 3000", "BUSN 3100"} <= {c for c, _, _ in cfg["supporting_courses"]}
    labels = {label: (hours, cfg["generic_options"].get(gid)) for gid, label, hours, _ in cfg["supporting_generic"]}
    assert labels["Choose one from the following"] == (3, ["EXMP 3100", "EXMP 3200"])
    assert labels["Choose one: MATH 1530 or OTHR 2000"] == (3, ["MATH 1530", "OTHR 2000"])
    assert labels["Select two"] == (6, ["OTHR 2100", "OTHR 2200", "OTHR 2000"])
    # MATH 1910 covers Quantitative Literacy, so that gen-ed row is gone
    assert "tbc_quant_lit" not in {gid for gid, _, _, _ in cfg["tbc_generic"]}
    assert cfg["total_hours"] == 120
    assert cfg["catalog_url"] == "https://catalog.example/major"


def test_major_prereqs_only_wait_on_planned_courses(example_catalog):
    cfg = cp.catalog_majors()["example-studies-bs"]
    # EXMP 9999 isn't in the program (would block forever) and the
    # "3100 or 3200" choice isn't a scheduled course, so neither is enforced
    assert cfg["prereq_override_map"]["EXMP 4000"] == {"EXMP 3000"}
    assert cfg["prereq_override_map"]["EXMP 3000"] == {"EXMP 2000"}


def test_choices_fill_from_completed_courses(example_catalog):
    cfg = cp.catalog_majors()["example-studies-bs"]
    hours, applied = cp.auto_generic_hours(cfg, {"OTHR 2000", "OTHR 2100", "EXMP 3200"})
    by_label = {label: hours.get(gid, 0) for gid, label, _, _ in cfg["supporting_generic"]}
    # OTHR 2000 fits two requirements but is only used once
    assert by_label["Choose one: MATH 1530 or OTHR 2000"] == 3
    assert by_label["Select two"] == 3
    assert by_label["Choose one from the following"] == 3
    assert sum(len(v) for v in applied.values()) == 3


def test_major_plans_without_warnings_and_audits(example_catalog):
    cfg = cp.catalog_majors()["example-studies-bs"]
    catalog = load_catalog_for(cfg)
    plan = generate_plan(set(), {}, 10, False, start_season="Fall", start_year=2026, degree_cfg=cfg, catalog=catalog)
    assert validate_plan(plan, set(), catalog, degree_cfg=cfg) == []
    planned = [i["code"] for items in plan.values() for i in items if i.get("kind") == "course"]
    assert planned.index("EXMP 1000") < planned.index("EXMP 2000") < planned.index("EXMP 3000") < planned.index("EXMP 4000")

    audit = build_audit({"EXMP 1000", "EXMP 3300"}, {}, catalog, degree_cfg=cfg)
    assert audit["total_required"] == 120
    labels = [g["label"] for g in audit["groups"]]
    assert labels[0] == "Major Requirements" and "True Blue Core (general education)" in labels


def test_minor_conversion(example_catalog):
    cfg = cp.catalog_minors()["example-minor"]
    assert [c for c, _, _ in cfg["required_courses"]] == ["EXMP 1000", "EXMP 2000"]
    assert cfg["elective_hours"] == 6
    assert cfg["elective_options"] == ["EXMP 3100", "EXMP 3200", "OTHR 2100"]
    assert cfg["total_hours"] == 13


def test_blank_catalog_line_ends_an_or_chain():
    # The published Physiology page reads "BIOL 4351 OR" then a blank line
    # then "CHEM 1110" -- CHEM 1110 is required, not another option
    units = cp.parse_units([
        {"course": "MATH 1920"}, {"op": "OR"}, {"course": "BIOL 4350"}, {"op": "OR"}, {"sep": True},
        {"course": "CHEM 1110"},
    ])
    assert units[0]["kind"] == "choice" and len(units[0]["options"]) == 2
    assert units[1] == {"kind": "course", "code": "CHEM 1110", "with": []}


def test_physiology_requires_general_chemistry(tmp_path):
    from mtsugradpath.catalog_programs import catalog_majors
    cfg = catalog_majors().get("biology-physiology-concentration-bs")
    if cfg is None:
        pytest.skip("catalog export missing")
    required = {c for k in ("core_courses", "concentration_courses", "supporting_courses") for c, _, _ in cfg[k]}
    assert {"CHEM 1110", "CHEM 1120", "MATH 1910"} <= required


# ── Catalog list layouts (each one a shape a real MTSU page uses) ────────────

def _codes(units, kind=None):
    return [u.get("code") for u in units if kind is None or u["kind"] == kind]


def test_choose_line_partway_down_a_list_keeps_the_courses_above_it_required():
    # Aerospace, Professional Pilot Concentration: 16 required AERO courses,
    # then "Choose from the following" (4 hours) and the options
    units = cp.parse_units([
        {"course": "AERO 1230"}, {"course": "AERO 2010"}, {"sep": True},
        {"text": "Choose from the following", "hours": [4, 4]},
        {"course": "AERO 3080"}, {"course": "AERO 3202"}, {"course": "AERO 3205"},
    ])
    assert _codes(units, "course") == ["AERO 1230", "AERO 2010"]
    pool = units[-1]
    assert pool["kind"] == "pool" and pool["hours"] == [4, 4]
    assert [o["code"] for o in pool["options"]] == ["AERO 3080", "AERO 3202", "AERO 3205"]


def test_choose_heading_without_hours_counts_its_courses():
    # "Choose two:" (Computer Science, AI concentration) and "Choose one of
    # the following:" over 4-credit lecture+lab pairs (Speech-Language Pathology)
    two = cp.parse_units([{"course": "CSCI 4350"}, {"sep": True}, {"note": "Choose two:"},
                          {"course": "CSCI 3350"}, {"course": "CSCI 4330"}, {"course": "CSCI 4410"}])
    assert two[0] == {"kind": "course", "code": "CSCI 4350", "with": []}
    assert two[1]["kind"] == "pool" and two[1]["hours"] == [6, 6]
    one = cp.parse_units([{"note": "Choose one of the following:"},
                          {"course": "EXMP 1000"}, {"op": "AND"}, {"course": "EXMP 1001"}, {"op": "OR"},
                          {"course": "MATH 1910"}])
    assert one[0]["kind"] == "pool" and one[0]["hours"] == [4, 4]


def test_or_list_under_a_choose_line_ends_at_the_next_plain_course():
    # History: "Choose from: HIST 2010 or HIST 2020 or ..." then HIST 3010 or 3011 (required)
    units = cp.parse_units([
        {"text": "Choose from", "hours": [6, 6]},
        {"course": "OTHR 2000"}, {"op": "OR"}, {"course": "OTHR 2100"}, {"op": "OR"}, {"course": "OTHR 2200"},
        {"text": "NOTE: Six credit hours may be counted in the True Blue Core."},
        {"course": "EXMP 3000"}, {"op": "OR"}, {"course": "EXMP 3100"},
    ])
    assert units[0]["kind"] == "pool" and len(units[0]["options"]) == 3
    assert units[1]["kind"] == "choice" and _codes(units[1]["options"]) == ["EXMP 3000", "EXMP 3100"]


def test_choose_line_that_names_its_own_courses_does_not_swallow_the_next_ones():
    # Agribusiness: "Choose one: MATH 1010, MATH 1630, ..." then "INFS 2200 or INFS 3100"
    units = cp.parse_units([
        {"text": "Choose one: MATH 1530 , MATH 1910", "hours": [3, 4]},
        {"course": "OTHR 2000"}, {"op": "OR"}, {"course": "OTHR 2100"},
    ])
    assert units[0]["kind"] == "pool" and _codes(units[0]["options"]) == ["MATH 1530", "MATH 1910"]
    assert units[1]["kind"] == "choice"


def test_lab_written_right_after_its_lecture_stays_with_it_in_a_choice():
    # Chemistry: "PHYS 2010 and PHYS 2011 or PHYS 2110 PHYS 2111"
    units = cp.parse_units([
        {"course": "EXMP 1000"}, {"op": "AND"}, {"course": "EXMP 1001"}, {"op": "OR"},
        {"course": "EXMP 2000"}, {"course": "EXMP 2001"},
    ])
    assert len(units) == 1 and units[0]["kind"] == "choice"
    assert units[0]["options"][1] == {"kind": "course", "code": "EXMP 2000", "with": ["EXMP 2001"]}
    # On its own line in a required list the lab keeps its own row
    plain = cp.parse_units([{"course": "EXMP 2000"}, {"course": "EXMP 2001"}])
    assert _codes(plain) == ["EXMP 2000", "EXMP 2001"]


def test_courses_counted_in_the_gen_ed_core_do_not_make_a_list_look_like_a_pick_list(example_catalog):
    # Chemistry "Supporting Courses (16-20 hours)" lists 28 hours because
    # MATH 1910 and the physics sequence also count in the True Blue Core
    section = {"name": "Supporting Courses (7-8 hours)", "hours": [7, 8], "items": [
        {"course": "MATH 1910"}, {"note": "(may be counted in the True Blue Core)"},
        {"course": "EXMP 1000"}, {"course": "EXMP 2000"}, {"course": "MATH 1530"},
    ]}
    b = cp._Builder({**MAJOR, "sections": []}, "x", "major")
    b.prefix = "EXMP"
    b.convert_section(section, "supporting", [])
    assert {"MATH 1910", "EXMP 1000", "EXMP 2000", "MATH 1530"} <= set(b.required)
    assert not b.generic


def test_part_two_of_a_numbered_sequence_waits_for_part_one(tmp_path, monkeypatch):
    # Flight Lab II's catalog line asks for a "Private Pilot Certificate" (earned
    # in Flight Lab I), not the course -- the titles still say which comes first
    courses = {
        "FLY 2201": {"title": "Pilot Flight Lab I", "credits": 2},
        "FLY 3203": {"title": "Pilot Flight Lab II", "credits": 2, "prereq_text": "Private Pilot Certificate"},
        "FLY 3204": {"title": "Pilot Flight Lab III", "credits": 2},
        "HIS 1010": {"title": "Survey I", "credits": 3, "desc": "HIS 1010 is NOT a prerequisite for HIS 1020."},
        "HIS 1020": {"title": "Survey II", "credits": 3},
        "OTH 1020": {"title": "Pilot Flight Lab II", "credits": 2},
    }
    path = tmp_path / "catalog.json"
    path.write_text(json.dumps({"catalog": {"name": "T"}, "courses": courses, "programs": []}))
    monkeypatch.setenv("GRADPATH_CATALOG_FILE", str(path))
    store.reload()
    try:
        assert store.resolve_prereqs("FLY 3203") == {"FLY 2201"}
        assert store.resolve_prereqs("FLY 3204") == {"FLY 3203"}
        assert store.resolve_prereqs("FLY 2201") == set()
        # The catalog says the order doesn't matter: no rule
        assert store.resolve_prereqs("HIS 1020") == set()
        # Same title in another subject isn't the same sequence
        assert store.resolve_prereqs("OTH 1020") == set()
    finally:
        monkeypatch.delenv("GRADPATH_CATALOG_FILE")
        store.reload()



def test_class_standing_is_read_from_the_prerequisite_line(tmp_path, monkeypatch):
    courses = {
        "ACT 3020": {"title": "A", "prereq_text": "ACT 2120 ; junior standing; admission into the College"},
        "ACT 4980": {"title": "B", "prereq_text": "Senior standing; 3.00 overall GPA"},
        "VET 4800": {"title": "C", "prereq_text": "ANSC 3000 and junior/senior standing"},
        "ANS 4410": {"title": "D", "prereq_text": "ANS 1401 ; junior-level classification or higher"},
        "ACT 2110": {"title": "E", "prereq_text": "A college-level math course; ENGL 1010 ; sophomore standing"},
        "MOD 3610": {"title": "F", "prereq_text": "Upper-division status recommended but not required"},
        "CHM 4600": {"title": "G", "prereq_text": "8 hours of BIOL beyond the freshman level; junior or senior standing"},
    }
    path = tmp_path / "catalog.json"
    path.write_text(json.dumps({"catalog": {"name": "T"}, "courses": courses, "programs": []}))
    monkeypatch.setenv("GRADPATH_CATALOG_FILE", str(path))
    store.reload()
    try:
        assert {c: store.standing_hours(c) for c in courses} == {
            "ACT 3020": 60, "ACT 4980": 90, "VET 4800": 60, "ANS 4410": 60,
            "ACT 2110": 30, "MOD 3610": 0, "CHM 4600": 60,
        }
    finally:
        monkeypatch.delenv("GRADPATH_CATALOG_FILE")
        store.reload()


def test_contradictory_corequisites_are_dropped_not_deadlocked():
    # IDES 3320 and 3330 list each other; 3330 also lists 4100, which needs
    # 3320 first -- impossible together. The one-way link is the one dropped.
    from mtsugradpath.planner import _consistent_coreqs
    prereqs = {"S4100": {"S3320"}}
    catalog = {"S3320": {"coreqs": {"S3330"}}, "S3330": {"coreqs": {"S3320", "S4100"}}, "S4100": {"coreqs": set()}}
    honored = _consistent_coreqs(set(catalog), lambda c: prereqs.get(c, set()), catalog)
    assert honored == {"S3320": {"S3330"}, "S3330": {"S3320"}}


def test_a_prerequisite_loop_in_the_catalog_is_ignored(tmp_path, monkeypatch):
    courses = {
        "LOP 1000": {"title": "One", "prereqs": ["LOP 2000"]},
        "LOP 2000": {"title": "Two", "prereqs": ["LOP 1000"]},
        "LOP 3000": {"title": "Three", "prereqs": ["LOP 2000"]},
    }
    path = tmp_path / "catalog.json"
    path.write_text(json.dumps({"catalog": {"name": "T"}, "courses": courses, "programs": []}))
    monkeypatch.setenv("GRADPATH_CATALOG_FILE", str(path))
    store.reload()
    try:
        assert store.resolve_prereqs("LOP 1000") == set() and store.resolve_prereqs("LOP 2000") == set()
        assert store.resolve_prereqs("LOP 3000") == {"LOP 2000"}  # outside the loop: kept
    finally:
        monkeypatch.delenv("GRADPATH_CATALOG_FILE")
        store.reload()
