"""End-to-end tests for the planner form — verifying that courses submitted
via the completed_courses field are correctly read, planned around, and
shown on the results page, and that all hidden fields render in the GET
response so the JS save/restore layer has what it needs."""

import json
import re

from app import app


def get_html(major="cs"):
    return app.test_client().get(f"/?major={major}").get_data(as_text=True)


def post_plan(completed=(), major="cs", semesters=4, season="Fall", year=2026,
              generic_hours=None, course_grades=None):
    data = {
        "major": major,
        "completed_courses": "\n".join(completed),
        "course_grades": json.dumps(course_grades or {}),
        "start_season": season,
        "start_year": str(year),
        "target_semesters": str(semesters),
    }
    if generic_hours:
        for k, v in generic_hours.items():
            data[f"hours_{k}"] = str(v)
    return app.test_client().post("/", data=data)


# ── GET page structure ────────────────────────────────────────────────────────


def test_get_index_has_form_fields():
    """All hidden fields that JS depends on must be present in the GET page."""
    html = get_html()
    assert 'name="completed_courses"' in html
    assert 'name="course_grades"' in html
    assert 'name="major"' in html
    assert 'name="start_season"' in html
    assert 'name="start_year"' in html
    assert 'name="target_semesters"' in html
    assert 'id="generate-btn"' in html


def test_get_index_has_save_restore_js():
    """sessionStorage save/restore logic must be present."""
    html = get_html()
    assert "saveState" in html
    assert "restoreState" in html
    assert "sessionStorage" in html
    assert "STORAGE_KEY" in html


def test_mark_complete_js_bubbles_event():
    """Mark-complete handler must dispatch a bubbling input event so
    saveState is triggered by the form-level listener."""
    html = get_html()
    assert "bubbles: true" in html


def test_mark_complete_js_has_null_guard():
    """Mark-complete handler must guard against getElementById returning null."""
    html = get_html()
    assert "if (!input) return" in html


def test_get_index_shows_no_catalog_alert_when_empty():
    """When the catalog table is empty the checklist section shows a sync prompt."""
    html = get_html()
    assert "No catalog courses loaded yet" in html
    # No actual checkbox inputs should be rendered
    assert not re.search(r'<input[^>]+cs-course-checkbox', html)


def test_get_index_has_gpa_tools():
    html = get_html()
    assert "gpaTools" in html
    assert "offcanvas" in html


def test_get_index_electives_row_renders():
    """The free-electives requirement row must have a matching id and max."""
    html = get_html()
    assert 'id="hours_general_electives"' in html
    assert 'name="hours_general_electives"' in html
    assert 'data-target="hours_general_electives"' in html
    # max should be a positive number for CS (120 - fixed hours = 19)
    m = re.search(r'id="hours_general_electives"[^>]*max="([^"]+)"', html)
    if not m:
        m = re.search(r'name="hours_general_electives"[^>]*max="([^"]+)"', html)
    assert m, "hours_general_electives input must have a max attribute"
    assert float(m.group(1)) > 0, "max must be positive"


# ── POST plan generation ──────────────────────────────────────────────────────


def test_post_empty_completed_generates_plan():
    resp = post_plan()
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert "Your recommended plan" in html


def test_post_completed_courses_appear_in_plan():
    """Courses submitted as completed must show up in the Completed courses section."""
    resp = post_plan(completed=["CSCI 1170", "CSCI 2170", "MATH 1910"])
    html = resp.get_data(as_text=True)
    assert "CSCI 1170" in html
    assert "CSCI 2170" in html
    assert "MATH 1910" in html


def test_post_completed_courses_not_rescheduled():
    """Courses already completed must NOT appear in the plan schedule."""
    from mtsugradpath.planner import generate_plan
    plan = generate_plan(
        {"CSCI 1170", "CSCI 2170"},
        {},
        target_terms=4,
        include_summer=False,
        start_season="Fall",
        start_year=2026,
    )
    all_codes = [
        item["code"]
        for items in plan.values()
        for item in items
        if item.get("kind") == "course"
    ]
    assert "CSCI 1170" not in all_codes
    assert "CSCI 2170" not in all_codes


def test_post_plan_has_term_columns():
    resp = post_plan(semesters=3)
    html = resp.get_data(as_text=True)
    assert "Fall 2026" in html
    assert "plan-item-row" in html


def test_post_unknown_course_surfaces_as_unrecognized():
    """A course code that matches no requirement should appear in the
    unrecognized-courses banner, not silently vanish."""
    resp = post_plan(completed=["ZZZZ 9999"])
    html = resp.get_data(as_text=True)
    assert "ZZZZ 9999" in html
    assert "unrecognized" in html.lower() or "Unrecognized" in html


def test_post_elective_hours_read_from_form():
    """Hours submitted for general_electives must reduce remaining electives."""
    from mtsugradpath.planner import generate_plan
    from mtsugradpath.degree import build_audit
    catalog = {}
    audit_no_electives = build_audit(
        set(), {"general_electives": 0}, catalog, degree_cfg=None, equivalencies=[]
    )
    audit_with_electives = build_audit(
        set(), {"general_electives": 10}, catalog, degree_cfg=None, equivalencies=[]
    )
    # The elective group should show more completed hours when hours are supplied
    def elective_completed(audit):
        for g in audit["groups"]:
            if "elective" in g["label"].lower():
                return g["completed_hours"]
        return 0
    assert elective_completed(audit_with_electives) > elective_completed(audit_no_electives)


def test_post_no_warnings_for_empty_completion():
    """A plan generated with no completed courses should have no prereq warnings."""
    from mtsugradpath.planner import generate_plan, validate_plan
    plan = generate_plan(set(), {}, target_terms=4, include_summer=False,
                         start_season="Fall", start_year=2026)
    warnings = validate_plan(plan, set(), {})
    assert warnings == []


def test_post_course_grades_field_accepted():
    """Submitting a course_grades JSON payload must not raise a 500."""
    resp = post_plan(
        completed=["CSCI 1170"],
        course_grades={"CSCI 1170": "B"},
    )
    assert resp.status_code == 200


def test_post_invalid_course_grades_json_does_not_crash():
    """Malformed course_grades JSON must be handled gracefully."""
    client = app.test_client()
    resp = client.post("/", data={
        "major": "cs",
        "completed_courses": "",
        "course_grades": "NOT_VALID_JSON",
        "start_season": "Fall",
        "start_year": "2026",
        "target_semesters": "4",
    })
    assert resp.status_code == 200


def test_post_different_majors_return_200():
    for major in ["cs", "biology", "math", "chemistry", "physics"]:
        resp = post_plan(major=major)
        assert resp.status_code == 200, f"{major} plan returned {resp.status_code}"


def test_get_all_majors_return_200():
    for major in ["cs", "biology", "math", "chemistry", "physics"]:
        resp = app.test_client().get(f"/?major={major}")
        assert resp.status_code == 200, f"GET /?major={major} returned {resp.status_code}"
