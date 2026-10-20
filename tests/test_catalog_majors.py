"""Every major and minor in the bundled catalog export plans cleanly.

These run against catalog_data/catalog.json -- the real 2026-27 MTSU
undergraduate catalog -- so a converter change that breaks some program's
plan (a prerequisite that can never be met, a course scheduled before its
prerequisite) fails here instead of in front of a student.
"""
import random

import pytest

from app import app
from mtsugradpath import catalog_store
from mtsugradpath.catalog_programs import catalog_majors, catalog_minors
from mtsugradpath.degree import build_audit
from mtsugradpath.degree_configs import CS_CONFIG, picker_majors
from mtsugradpath.minors import apply_minor_to_config, picker_minors
from mtsugradpath.planner import generate_plan, load_catalog_for, validate_plan

pytestmark = pytest.mark.skipif(not catalog_store.has_data(), reason="catalog_data/catalog.json missing")

MAJOR_KEYS = sorted(catalog_majors())
PICKER_KEYS = [key for key, _ in picker_majors()]
MINOR_KEYS = sorted(catalog_minors())


def _courses_in(plan, remaining=False):
    return [
        item["code"]
        for term, items in plan.items()
        if term.startswith("Remaining") == remaining
        for item in items
        if item.get("kind") == "course"
    ]


def test_catalog_has_the_whole_undergraduate_catalog():
    meta = catalog_store.meta()
    assert "Undergraduate" in meta["name"]
    assert len(catalog_store.courses()) > 3000
    assert len(MAJOR_KEYS) > 200
    assert len(MINOR_KEYS) > 140


@pytest.mark.parametrize("key", MAJOR_KEYS)
def test_every_catalog_major_plans_from_scratch_without_warnings(key):
    cfg = catalog_majors()[key]
    catalog = load_catalog_for(cfg)
    plan = generate_plan(set(), {}, 16, False, start_season="Fall", start_year=2026, degree_cfg=cfg, catalog=catalog)

    warnings = validate_plan(plan, set(), catalog, degree_cfg=cfg)
    assert warnings == [], f"{cfg['name']}: {warnings[:3]}"

    # Every required course gets scheduled -- nothing is stuck waiting on a
    # prerequisite the plan never provides
    required = {code for k in ("core_courses", "concentration_courses", "supporting_courses") for code, _, _ in cfg[k]}
    stuck = required & set(_courses_in(plan, remaining=True))
    assert not stuck, f"{cfg['name']}: never scheduled {sorted(stuck)}"


@pytest.mark.parametrize("key", MAJOR_KEYS)
def test_every_catalog_major_hours_add_up(key):
    cfg = catalog_majors()[key]
    audit = build_audit(set(), {}, load_catalog_for(cfg), degree_cfg=cfg)
    # The degree is at least its stated total; requirements never shrink it
    assert audit["total_required"] >= cfg["total_hours"]
    assert audit["total_completed"] == 0


@pytest.mark.parametrize("key", MAJOR_KEYS[::7])
def test_partial_progress_plans_never_break_prerequisite_order(key):
    cfg = catalog_majors()[key]
    catalog = load_catalog_for(cfg)
    required = sorted({code for k in ("core_courses", "concentration_courses", "supporting_courses") for code, _, _ in cfg[k]})
    rng = random.Random(key)
    for _ in range(5):
        completed = set(rng.sample(required, k=len(required) // 3)) if required else set()
        # Only keep completions whose own prerequisites were also completed
        overrides = cfg["prereq_override_map"]
        completed = {c for c in completed if overrides.get(c, set()) <= completed}
        plan = generate_plan(completed, {}, 12, True, start_season="Spring", start_year=2027, degree_cfg=cfg, catalog=catalog)
        warnings = validate_plan(plan, completed, catalog, degree_cfg=cfg)
        assert warnings == [], f"{cfg['name']} with {sorted(completed)}: {warnings[:3]}"
        assert not completed & set(_courses_in(plan))


@pytest.mark.parametrize("key", MINOR_KEYS)
def test_every_catalog_minor_plans_with_cs(key):
    minor = catalog_minors()[key]
    cfg, extra = apply_minor_to_config(CS_CONFIG, minor, set())
    catalog = load_catalog_for(CS_CONFIG, minor)
    plan = generate_plan(set(), extra, 16, False, start_season="Fall", start_year=2026, degree_cfg=cfg, catalog=catalog)
    assert [w for w in validate_plan(plan, set(), catalog, degree_cfg=cfg) if w["type"] != "not_offered"] == []
    assert 6 <= minor["total_hours"] <= 60


def test_every_major_page_renders():
    client = app.test_client()
    for key in PICKER_KEYS:
        resp = client.get(f"/?major={key}")
        assert resp.status_code == 200, key
        assert 'class="form-check-input cs-course-checkbox"' in resp.get_data(as_text=True), key


@pytest.mark.parametrize("key", MAJOR_KEYS[::5])
def test_plan_page_for_catalog_majors(key):
    resp = app.test_client().post("/", data={
        "major": key, "target_semesters": "8", "start_season": "Fall", "start_year": "2026",
    })
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert "Your recommended plan" in html
    assert "plan warning" not in html.lower()


def test_accounting_matches_the_catalog_page():
    """Spot check against catalog.mtsu.edu's Accounting, B.B.A. page."""
    cfg = catalog_majors()["accounting-bba"]
    codes = {code for k in ("core_courses", "concentration_courses", "supporting_courses") for code, _, _ in cfg[k]}
    # Major requirements and auxiliary courses, listed by name
    assert {"ACTG 3110", "ACTG 3120", "ACTG 3300", "ACTG 4110", "ACTG 4510", "ACTG 4550", "ACTG 4620",
            "BLAW 3430", "COMM 2200", "INFS 2200", "MATH 1630"} <= codes
    # College of Business Core comes from the four-year map
    assert {"ACTG 2110", "ACTG 2125", "BIA 2610", "MGMT 3610", "FIN 3010", "BUAD 4980"} <= codes
    assert cfg["concentration_elective_hours"] == 6
    assert cfg["total_hours"] == 120


def test_a_chosen_option_fills_its_requirement_on_the_plan_page():
    html = app.test_client().post("/", data={
        "major": "economics-bs", "completed_courses": "BIA 2610",
        "target_semesters": "8", "start_season": "Fall", "start_year": "2026",
    }).get_data(as_text=True)
    assert "Counted: BIA 2610" in html
    assert "Unrecognized" not in html


def test_course_search_json_and_health():
    client = app.test_client()
    resp = client.get("/courses.json")
    assert resp.status_code == 200
    assert resp.headers["Cache-Control"].startswith("public")
    courses = resp.get_json()
    assert any(c["code"] == "ASTR 1030" for c in courses)
    again = client.get("/courses.json", headers={"If-None-Match": resp.headers["ETag"]})
    assert again.status_code == 304

    health = client.get("/healthz").get_json()
    assert health["ok"] and health["majors"] > 200


def test_prerequisite_map_for_any_major():
    resp = app.test_client().get("/prerequisites?major=accounting-bba")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert "ACTG_3110 --&gt; ACTG_3120" in html
    assert 'value="accounting-bba" selected' in html


def test_minor_picker_lists_catalog_minors():
    html = app.test_client().get("/?major=cs").get_data(as_text=True)
    assert '"key": "accounting-minor"' in html
    assert len(picker_minors()) > 140


def test_completed_prerequisite_without_a_grade_counts_as_passed():
    """ACTG 3110 needs ACTG 2110 and 2125 "with minimum grade of C". The
    planner doesn't ask for grades, so a course checked off without one must
    not block what comes after it (it used to strand ACTG 3110 onward)."""
    cfg = catalog_majors()["accounting-bba"]
    catalog = load_catalog_for(cfg)
    done = {"ACTG 2110", "ACTG 2125"}
    plan = generate_plan(done, {}, 8, False, start_season="Fall", start_year=2026, degree_cfg=cfg, catalog=catalog)
    assert "ACTG 3110" in _courses_in(plan)
    assert validate_plan(plan, done, catalog, degree_cfg=cfg) == []

    # A grade that's reported and too low still holds the course back
    low = {"ACTG 2110": "D", "ACTG 2125": "B"}
    plan = generate_plan(done, {}, 8, False, start_season="Fall", start_year=2026,
                         degree_cfg=cfg, catalog=catalog, course_grades=low)
    assert "ACTG 3110" not in _courses_in(plan)


def test_pages_are_gzipped_for_browsers_that_ask():
    client = app.test_client()
    resp = client.get("/?major=cs", headers={"Accept-Encoding": "gzip, br"})
    assert resp.headers["Content-Encoding"] == "gzip"
    import gzip
    assert b"Build your academic plan" in gzip.decompress(resp.data)
    assert "Content-Encoding" not in client.get("/?major=cs").headers
