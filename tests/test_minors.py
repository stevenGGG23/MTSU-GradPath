"""Tests for optional minors, using the Mathematics minor as the worked
example. Per MTSU's Department of Mathematical Sciences, the minor is 18
hours: MATH 1910 and MATH 1920 plus 10 hours of MATH electives.
"""
import pytest

from app import app
from mtsugradpath.degree_configs import DEGREE_CONFIGS, CS_CONFIG, BIOL_CONFIG, major_prefixes
from mtsugradpath.minors import (
    MATH_MINOR,
    MINOR_CONFIGS,
    MINOR_ELECTIVE_ID_PREFIX,
    apply_minor_to_config,
    build_minor_audit,
    minor_allowed_for_major,
    _split_hours,
)
from mtsugradpath.planner import generate_plan, validate_plan, load_catalog_courses

# Minimal catalog so the audit doesn't depend on DB contents
MATH_CATALOG = {
    "MATH 2010": {"title": "Elements of Linear Algebra", "credits": 3, "prereqs": set()},
    "MATH 3110": {"title": "Calculus III", "credits": 4, "prereqs": set()},
    "MATH 3120": {"title": "Ordinary Differential Equations", "credits": 3, "prereqs": set()},
}


def _planned_codes(plan):
    return {
        item["code"]
        for term, items in plan.items()
        if not term.startswith("Remaining")
        for item in items
        if item.get("kind") == "course"
    }


# ── Math minor matches the catalog ────────────────────────────────────────────

def test_math_minor_matches_catalog_requirements():
    required = {code: hours for code, hours, _ in MATH_MINOR["required_courses"]}
    assert required == {"MATH 1910": 4, "MATH 1920": 4}
    assert MATH_MINOR["elective_hours"] == 10
    assert MATH_MINOR["total_hours"] == 18
    assert sum(required.values()) + MATH_MINOR["elective_hours"] == MATH_MINOR["total_hours"]


def test_every_minor_total_adds_up():
    for minor in MINOR_CONFIGS.values():
        required = sum(h for _, h, _ in minor["required_courses"])
        assert required + minor["elective_hours"] == minor["total_hours"], minor["key"]


# ── Audit ─────────────────────────────────────────────────────────────────────

def test_minor_audit_empty():
    audit = build_minor_audit(MATH_MINOR, set(), MATH_CATALOG)
    assert audit["completed_hours"] == 0
    assert audit["required_hours"] == 18
    assert audit["percent"] == 0


def test_minor_audit_counts_required_and_elective_hours():
    audit = build_minor_audit(MATH_MINOR, {"MATH 1910", "MATH 2010"}, MATH_CATALOG)
    # 4 (Calc I) + 3 (Linear Algebra)
    assert audit["completed_hours"] == 7
    elective = audit["entries"][-1]
    assert elective["partial_hours"] == 3
    assert not elective["done"]


def test_minor_audit_complete():
    done = {"MATH 1910", "MATH 1920", "MATH 2010", "MATH 3110", "MATH 3120"}
    audit = build_minor_audit(MATH_MINOR, done, MATH_CATALOG)
    # Electives cap at 10 even though 3 + 4 + 3 = 10 exactly here
    assert audit["completed_hours"] == 18
    assert audit["percent"] == 100
    assert all(entry["done"] for entry in audit["entries"])


def test_minor_electives_are_capped():
    done = {"MATH 1910", "MATH 1920", "MATH 2010", "MATH 3110", "MATH 3120", "MATH 4250"}
    audit = build_minor_audit(MATH_MINOR, done, MATH_CATALOG)
    assert audit["completed_hours"] == 18


def test_lower_division_and_other_prefixes_do_not_count_as_electives():
    # MATH 1530 is a gen-ed stats course (below 2000); CSCI isn't MATH at all
    audit = build_minor_audit(MATH_MINOR, {"MATH 1530", "CSCI 3080"}, MATH_CATALOG)
    assert audit["completed_hours"] == 0


# ── Folding the minor into a major's plan ─────────────────────────────────────

def test_split_hours_has_no_tiny_chunks():
    assert _split_hours(10) == [3, 3, 4]
    assert _split_hours(9) == [3, 3, 3]
    assert _split_hours(3) == [3]
    assert sum(_split_hours(10)) == 10


def test_apply_minor_does_not_mutate_major_config():
    before_supporting = list(BIOL_CONFIG["supporting_courses"])
    before_generic = list(BIOL_CONFIG["supporting_generic"])
    apply_minor_to_config(BIOL_CONFIG, MATH_MINOR, set())
    assert BIOL_CONFIG["supporting_courses"] == before_supporting
    assert BIOL_CONFIG["supporting_generic"] == before_generic


def test_apply_minor_skips_courses_major_already_requires():
    # CS already requires MATH 1910/1920, so they must not be added twice
    cfg, _ = apply_minor_to_config(CS_CONFIG, MATH_MINOR, set())
    codes = [code for code, _, _ in cfg["supporting_courses"]]
    assert codes.count("MATH 1910") == 1
    assert codes.count("MATH 1920") == 1


def test_apply_minor_credits_completed_electives():
    _, extra = apply_minor_to_config(BIOL_CONFIG, MATH_MINOR, {"MATH 3110"}, MATH_CATALOG)
    assert sum(extra.values()) == 4
    assert all(key.startswith(MINOR_ELECTIVE_ID_PREFIX) for key in extra)


def test_biology_plan_with_math_minor_schedules_calculus_in_order():
    cfg, extra = apply_minor_to_config(BIOL_CONFIG, MATH_MINOR, set())
    catalog = load_catalog_courses(cfg["prefix"])
    plan = generate_plan(set(), extra, 8, False, "Fall", 2026, degree_cfg=cfg, catalog=catalog)

    planned = _planned_codes(plan)
    assert {"MATH 1910", "MATH 1920"} <= planned

    terms = [t for t in plan if not t.startswith("Remaining")]
    term_of = {
        item["code"]: i
        for i, t in enumerate(terms)
        for item in plan[t]
        if item.get("kind") == "course"
    }
    assert term_of["MATH 1910"] < term_of["MATH 1920"]
    assert validate_plan(plan, set(), catalog, degree_cfg=cfg) == []


def test_minor_electives_appear_in_plan():
    cfg, extra = apply_minor_to_config(BIOL_CONFIG, MATH_MINOR, set())
    catalog = load_catalog_courses(cfg["prefix"])
    plan = generate_plan(set(), extra, 12, False, "Fall", 2026, degree_cfg=cfg, catalog=catalog)
    minor_hours = sum(
        item["hours"]
        for items in plan.values()
        for item in items
        if item.get("kind") == "requirement" and item.get("id", "").startswith(MINOR_ELECTIVE_ID_PREFIX)
    )
    assert minor_hours == 10


@pytest.mark.parametrize("major_key", list(DEGREE_CONFIGS))
def test_every_major_with_math_minor_plans_without_warnings(major_key):
    major = DEGREE_CONFIGS[major_key]
    if not minor_allowed_for_major(MATH_MINOR, major_prefixes(major)):
        pytest.skip("can't minor in your own major")
    cfg, extra = apply_minor_to_config(major, MATH_MINOR, set())
    catalog = load_catalog_courses(cfg["prefix"])
    plan = generate_plan(set(), extra, 8, True, "Fall", 2026, degree_cfg=cfg, catalog=catalog)
    assert validate_plan(plan, set(), catalog, degree_cfg=cfg) == []


def test_math_major_cannot_take_math_minor():
    assert not minor_allowed_for_major(MATH_MINOR, ["MATH"])
    assert minor_allowed_for_major(MATH_MINOR, ["CSCI"])


# ── Web app ───────────────────────────────────────────────────────────────────

def test_app_imports_and_index_shows_minor_search():
    # The deploy failure: app.py imported a name that didn't exist
    html = app.test_client().get("/?major=cs").get_data(as_text=True)
    assert 'class="minor-picker' in html
    assert 'id="minor_input"' in html
    assert "<select" not in html.split('class="minor-picker', 1)[1].split("</div>", 1)[0]


def test_index_hides_minor_for_same_subject_major():
    html = app.test_client().get("/?major=math").get_data(as_text=True)
    assert 'class="minor-picker' not in html


def test_plan_page_shows_math_minor_progress():
    resp = app.test_client().post("/", data={
        "major": "biology",
        "minor": "math",
        "completed_courses": "MATH 1910",
        "target_semesters": "8",
        "start_season": "Fall",
        "start_year": "2026",
    })
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert "extra-progress-minor" in html
    assert "Mathematics Minor" in html
    assert "4 / 18 cr hrs" in html
    assert "plan-item-minor" in html


def test_plan_without_minor_is_unchanged():
    resp = app.test_client().post("/", data={"major": "cs", "target_semesters": "4"})
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert "extra-progress-minor" not in html
    assert "plan-item-minor" not in html


def test_unknown_or_same_subject_minor_is_ignored():
    client = app.test_client()
    for data in ({"major": "cs", "minor": "basket-weaving"}, {"major": "math", "minor": "math"}):
        resp = client.post("/", data={**data, "target_semesters": "4"})
        assert resp.status_code == 200
        assert "extra-progress-minor" not in resp.get_data(as_text=True)
