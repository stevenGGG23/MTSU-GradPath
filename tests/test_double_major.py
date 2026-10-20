"""Double majors, multiple minors, and page chrome."""
import json
import re

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


def test_index_has_no_public_catalog_sync():
    # Catalog data ships with the app; re-exporting it is an admin-only route
    html = app.test_client().get("/").get_data(as_text=True)
    assert 'id="sync-btn"' not in html
    # Old short keys resolve to the live catalog program
    assert 'href="/prerequisites?major=computer-science-professional-computer-science-concentration-bs"' in html


def _minor_keys(html):
    return {m["key"] for m in json.loads(re.search(r"const minorOptions = (\[.*?\]);\n", html).group(1))}


def test_index_with_second_major_lists_both_checklists():
    html = app.test_client().get("/?major=biology&second_major=cs").get_data(as_text=True)
    assert 'id="second_major_field" value="computer-science-professional-computer-science-concentration-bs"' in html
    assert 'class="cs-major-heading">Biology, Physiology Concentration, B.S.<' in html
    assert 'class="cs-major-heading">Computer Science, Professional Computer Science Concentration, B.S.<' in html


def test_index_drops_same_major_as_second():
    html = app.test_client().get("/?major=cs&second_major=cs").get_data(as_text=True)
    assert 'id="second_major_field" value=""' in html
    assert 'id="second_row" hidden' in html


def test_index_hides_minors_covered_by_second_major():
    html = app.test_client().get("/?major=cs&second_major=math").get_data(as_text=True)
    assert "math" not in _minor_keys(html)


def test_plan_page_shows_second_major():
    html = app.test_client().post("/", data={
        "major": "cs", "second_major": "math", "completed_courses": "MATH 1910", **PLAN_FORM,
    }).get_data(as_text=True)
    assert "extra-progress-second" in html
    assert "Mathematics, General Mathematics Concentration, B.S. (second major)" in html
    assert "plan-item-second" in html


def test_plan_page_shows_every_minor(chem_minor):
    resp = app.test_client().post("/", data={"major": "cs", "minor": ["math", "chem", "math"], **PLAN_FORM})
    html = resp.get_data(as_text=True)
    assert html.count("extra-progress-card extra-progress-minor") == 2
    assert "Mathematics Minor" in html and "Chemistry Minor" in html


def test_minor_elective_ids_are_unique_per_minor(chem_minor):
    cfg, extra_math = minors.apply_minor_to_config(DEGREE_CONFIGS["cs"], minors.MATH_MINOR, set())
    _, extra_chem = minors.apply_minor_to_config(cfg, chem_minor, set())
    assert not set(extra_math) & set(extra_chem)


def test_second_major_row_opens_filled_when_chosen():
    html = app.test_client().get("/?major=biology&second_major=cs").get_data(as_text=True)
    assert ('id="second_input" class="form-control combo-input" '
            'value="Computer Science, Professional Computer Science Concentration, B.S."') in html
    assert 'id="second_add" title="Add a second major" aria-label="Add a second major" hidden' in html


def test_remaining_list_shows_every_item():
    html = app.test_client().post("/", data={"major": "cs", "target_semesters": "1"}).get_data(as_text=True)
    assert "term-card-remaining" in html
    assert "is-collapsed" not in html
    assert "remaining-toggle" not in html


def test_plan_page_offers_pdf_download_with_print_header():
    html = app.test_client().post("/", data={"major": "cs", **PLAN_FORM}).get_data(as_text=True)
    assert 'id="download-pdf"' in html
    assert "print-header" in html


def test_gpa_tools_are_on_every_page():
    client = app.test_client()
    assert 'id="gpaTools"' in client.get("/").get_data(as_text=True)
    assert 'id="gpaTools"' in client.post("/", data={"major": "cs", **PLAN_FORM}).get_data(as_text=True)


def test_audit_lists_courses_counted_toward_electives():
    html = app.test_client().post("/", data={
        "major": "cs", "completed_courses": "CSCI 1170\nCSCI 3038", **PLAN_FORM,
    }).get_data(as_text=True)
    assert "Counted: CSCI 3038" in html
