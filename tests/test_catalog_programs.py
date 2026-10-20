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
