"""Double majors, multiple minors, and the plan page's Sync Catalog button."""
import pytest

from app import app
from mtsugradpath import minors
from mtsugradpath.degree_configs import DEGREE_CONFIGS
from mtsugradpath.double_major import (
    SECOND_MAJOR_ELECTIVE_ID_PREFIX,
    apply_second_major_to_config,
    config_course_codes,
    second_major_allowed,
)
from mtsugradpath.planner import generate_plan, validate_plan

PLAN_FORM = {"target_semesters": "8", "start_season": "Fall", "start_year": "2026"}


@pytest.fixture
def chem_minor(monkeypatch):
    """A second minor so picking several can be exercised (only Math exists)."""
    chem = dict(
        minors.MATH_MINOR,
        key="chem",
        name="Chemistry Minor",
        prefix="CHEM",
        required_courses=[("CHEM 1110", 4, "General Chemistry I")],
        elective_hours=6,
        total_hours=10,
        elective_suggestions=["CHEM 3010 - Organic Chemistry I"],
    )
    monkeypatch.setitem(minors.MINOR_CONFIGS, "chem", chem)
    return chem


# ── Folding a second major into the plan ─────────────────────────────────────

def test_second_major_courses_join_the_plan_without_mutating_configs():
    cs, math = DEGREE_CONFIGS["cs"], DEGREE_CONFIGS["math"]
    before = list(cs["supporting_courses"])
    cfg, extra = apply_second_major_to_config(cs, math, set())

    assert cs["supporting_courses"] == before
    planned = config_course_codes(cfg)
    assert config_course_codes(math) <= planned
    assert extra and all(k.startswith(SECOND_MAJOR_ELECTIVE_ID_PREFIX) for k in extra)


def test_second_major_must_differ_from_the_first():
    cs, math = DEGREE_CONFIGS["cs"], DEGREE_CONFIGS["math"]
    assert second_major_allowed(cs, math)
    assert not second_major_allowed(cs, cs)
    assert not second_major_allowed(cs, None)


@pytest.mark.parametrize("primary,second", [("cs", "math"), ("biology", "chemistry"), ("physics", "cs")])
def test_double_major_plan_has_no_prerequisite_warnings(primary, second):
    cfg, extra = apply_second_major_to_config(DEGREE_CONFIGS[primary], DEGREE_CONFIGS[second], set())
    plan = generate_plan(set(), extra, 12, False, "Fall", 2026, degree_cfg=cfg, catalog={})
    assert validate_plan(plan, set(), {}, degree_cfg=cfg) == []


# ── Web app ───────────────────────────────────────────────────────────────────

def test_plan_page_has_no_sync_catalog_button():
    html = app.test_client().post("/", data={"major": "cs", **PLAN_FORM}).get_data(as_text=True)
    assert 'onclick="triggerSync()"' not in html


def test_index_still_offers_sync_catalog():
    assert 'id="sync-btn"' in app.test_client().get("/").get_data(as_text=True)


def test_index_with_second_major_lists_both_checklists():
    html = app.test_client().get("/?major=biology&second_major=cs").get_data(as_text=True)
    assert 'id="second_major_field" value="cs"' in html
    assert 'class="cs-major-heading">B.S. Biology<' in html
    assert 'class="cs-major-heading">B.S. Computer Science<' in html


def test_index_drops_same_major_as_second():
    html = app.test_client().get("/?major=cs&second_major=cs").get_data(as_text=True)
    assert 'id="second_major_field" value=""' in html
    assert "program-chip-second" not in html


def test_index_hides_minors_covered_by_second_major():
    html = app.test_client().get("/?major=cs&second_major=math").get_data(as_text=True)
    assert 'class="minor-picker' not in html


def test_plan_page_shows_second_major():
    html = app.test_client().post("/", data={
        "major": "cs", "second_major": "math", "completed_courses": "MATH 1910", **PLAN_FORM,
    }).get_data(as_text=True)
    assert "category-bar-row-second" in html
    assert "B.S. Mathematics (second major)" in html
    assert "plan-item-second" in html


def test_plan_page_shows_every_minor(chem_minor):
    resp = app.test_client().post("/", data={"major": "cs", "minor": ["math", "chem", "math"], **PLAN_FORM})
    html = resp.get_data(as_text=True)
    assert html.count("category-bar-row-minor") == 2
    assert "Mathematics Minor" in html and "Chemistry Minor" in html


def test_minor_elective_ids_are_unique_per_minor(chem_minor):
    cfg, extra_math = minors.apply_minor_to_config(DEGREE_CONFIGS["cs"], minors.MATH_MINOR, set())
    _, extra_chem = minors.apply_minor_to_config(cfg, chem_minor, set())
    assert not set(extra_math) & set(extra_chem)
