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
    # A summer-only course can't fit a plan without summers (the plan page
    # warns about it); everything else must be scheduled
    stuck = {code for code in stuck if not catalog_store.summer_only(code)}
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


# ── Regressions from the correctness review ───────────────────────────────────

def _plan_for(cfg, completed=frozenset(), terms=8, summer=False, catalog=None, **kw):
    catalog = catalog or load_catalog_for(cfg)
    return generate_plan(set(completed), kw.pop("hours", {}), terms, summer, start_season="Fall",
                         start_year=2026, degree_cfg=cfg, catalog=catalog), catalog


def test_an_empty_term_does_not_end_the_plan():
    # Summer has no CSCI offerings; the plan must carry on into fall
    from mtsugradpath.degree_configs import CS_CONFIG
    done = {"CSCI 1170", "COMM 2200", "MATH 1910", "MATH 1920", "MATH 2050", "PHIL 3170"}
    hours = {gid: 99 for gid, _, _, _ in CS_CONFIG["supporting_generic"] + CS_CONFIG["tbc_generic"]}
    hours["general_electives"] = 99
    catalog = load_catalog_for(CS_CONFIG)
    plan = generate_plan(done, hours, 10, True, start_season="Spring", start_year=2027,
                         degree_cfg=CS_CONFIG, catalog=catalog)
    assert not any(t.startswith("Remaining") for t in plan), list(plan)


def test_zero_credit_courses_count_zero_hours():
    cfg = catalog_majors()["forensic-science-bs"]
    plan, _ = _plan_for(cfg, terms=16)
    hours = {i["code"]: i["hours"] for items in plan.values() for i in items if i.get("kind") == "course"}
    assert hours["BIOL 1111"] == 0 and hours["CHEM 1111"] == 0


def test_lab_lands_in_the_same_term_as_its_lecture():
    cfg = catalog_majors()["forensic-science-bs"]
    plan, _ = _plan_for(cfg, terms=16)
    term_of = {i["code"]: t for t, items in plan.items() for i in items if i.get("kind") == "course"}
    for lecture, labs in cfg["lab_partners"].items():
        for lab in labs:
            if lecture in term_of and lab in term_of:
                assert term_of[lecture] == term_of[lab], (lecture, lab)


def test_no_course_is_scheduled_twice():
    for key in ("information-systems-bba", "marketing-bba", "finance-bba"):
        plan, _ = _plan_for(catalog_majors()[key], terms=12)
        codes = _courses_in(plan) + _courses_in(plan, remaining=True)
        assert len(codes) == len(set(codes)), key


def test_requirement_rows_are_split_into_course_sized_pieces():
    plan, _ = _plan_for(catalog_majors()["communication-bs"], terms=12)
    for term, items in plan.items():
        if not term.startswith("Remaining"):
            assert sum(i.get("hours", 0) for i in items) <= 15, term


def test_a_minor_does_not_strand_the_majors_math():
    from mtsugradpath.degree_configs import CS_CONFIG
    from mtsugradpath.minors import get_minor_config
    minor = get_minor_config("statistics-minor")
    cfg, extra = apply_minor_to_config(CS_CONFIG, minor, set())
    catalog = load_catalog_for(CS_CONFIG, minor)
    plan = generate_plan(set(), extra, 16, False, start_season="Fall", start_year=2026, degree_cfg=cfg, catalog=catalog)
    assert "MATH 2050" in _courses_in(plan)


def test_minor_courses_keep_their_order():
    from mtsugradpath.minors import get_minor_config
    major = catalog_majors()["nursing-bsn"]
    minor = get_minor_config("arabic-minor")
    cfg, extra = apply_minor_to_config(major, minor, set())
    catalog = load_catalog_for(major, minor)
    plan = generate_plan(set(), extra, 16, False, start_season="Fall", start_year=2026, degree_cfg=cfg, catalog=catalog)
    order = [i["code"] for t, items in plan.items() if not t.startswith("Remaining") for i in items if i.get("kind") == "course"]
    term_of = {i["code"]: t for t, items in plan.items() for i in items if i.get("kind") == "course"}
    arab = [c for c in ("ARAB 1010", "ARAB 1020", "ARAB 2010", "ARAB 2020") if c in term_of]
    assert len({term_of[c] for c in arab}) == len(arab)  # one per term, in order
    assert [c for c in order if c in arab] == arab
    assert validate_plan(plan, set(), catalog, degree_cfg=cfg) == []


def test_upper_division_elective_shortfall_is_visible():
    # More upper-division ACTG hours than the catalog has ACTG courses for:
    # the missing hours must still show up as a placeholder
    cfg = dict(catalog_majors()["accounting-bba"], concentration_elective_hours=150)
    plan, _ = _plan_for(cfg, terms=16)
    labels = [i.get("label", "") for items in plan.values() for i in items if i.get("kind") == "requirement"]
    assert any(label == "ACTG upper-division elective" for label in labels)


def test_prefix_list_requirement_accepts_any_listed_prefix():
    """Commerce's 24 hours are 'business electives chosen from ACTG, BCED,
    BIA, BLAW, BUS, ECON, ...' -- not just BUS."""
    cfg = catalog_majors()["commerce-bs"]
    row = next(r for r in cfg["supporting_generic"] if r[1] == "Commerce Major")
    options = set(cfg["generic_options"][row[0]])
    assert {"ACTG 3110", "FIN 3810", "MGMT 3620"} & options
    assert len({c.split()[0] for c in options}) >= 8


def test_second_major_keeps_primary_gen_ed_credit():
    form = {"major": "history-ba", "completed_courses": "ECON 2410\nECON 2420",
            "target_semesters": "8", "start_season": "Fall", "start_year": "2026"}
    alone = app.test_client().post("/", data=form).get_data(as_text=True)
    both = app.test_client().post("/", data={**form, "second_major": "accounting-bba"}).get_data(as_text=True)
    assert "Counted: ECON 2410" in alone and "Counted: ECON 2410" in both


def test_courses_beyond_a_full_requirement_count_as_free_electives():
    """A 9-hour cognate pool filled by three courses: a fourth and fifth
    checked-off option used to vanish (not counted, not flagged)."""
    resp = app.test_client().post("/", data={
        "major": "political-and-global-affairs-public-policy-and-management-concentration-ba",
        "completed_courses": "LSTS 4120\nSOC 3300\nECON 2410\nPSY 4380\nSOC 4100",
        "target_semesters": "8", "start_season": "Fall", "start_year": "2026",
    })
    html = resp.get_data(as_text=True)
    free = html.split("Free electives", 1)[1].split("audit-item-suggestions", 1)[0]
    assert "PSY 4380" in free and "SOC 4100" in free
    assert "Unrecognized" not in html


def test_course_not_in_the_catalog_is_still_flagged():
    html = app.test_client().post("/", data={
        "major": "accounting-bba", "completed_courses": "ZZZZ 9999",
        "target_semesters": "4", "start_season": "Fall", "start_year": "2026",
    }).get_data(as_text=True)
    assert "Unrecognized" in html and "ZZZZ 9999" in html


@pytest.mark.parametrize("key", PICKER_KEYS)
def test_finishing_everything_reads_100_percent(key):
    """Check off every required course, enough upper-division electives, and
    fill every hours row: the audit must read 100% and nothing is left to plan."""
    import re
    from mtsugradpath.degree import ELECTIVES_GENERIC_ID
    from mtsugradpath.degree_configs import get_full_degree_config

    cfg = get_full_degree_config(key)
    required = [c for k in ("core_courses", "concentration_courses", "supporting_courses") for c, _, _ in cfg[k]]
    completed = list(required)
    need = cfg.get("concentration_elective_hours", 0)
    for code in catalog_store.codes_for_prefix(cfg["prefix"]):
        if need <= 0:
            break
        number = int(re.sub(r"\D", "", code.split()[1])[:4])
        credits = catalog_store.credits(code, default=0) or 0
        if number >= cfg.get("upper_division_min", 3000) and code not in required and credits > 0:
            completed.append(code)
            need -= credits
    form = {"major": key, "completed_courses": "\n".join(completed),
            "target_semesters": "12", "start_season": "Fall", "start_year": "2026",
            f"hours_{ELECTIVES_GENERIC_ID}": "200"}
    for gid, _, hours, _ in cfg["supporting_generic"] + cfg["tbc_generic"]:
        form[f"hours_{gid}"] = str(hours)
    html = app.test_client().post("/", data=form).get_data(as_text=True)
    done, total = map(float, re.search(r"([\d.]+) / ([\d.]+) credit hours", html).groups())
    assert done == total, f"{key}: {done}/{total}"
    assert not re.findall(r'class="plan-item-code">([^<]+)<', html)
    assert "plan warning" not in html.lower()


def test_summer_only_course_is_called_out_when_summers_are_off():
    form = {"major": "geosciences-environmental-science-concentration-bs",
            "target_semesters": "8", "start_season": "Fall", "start_year": "2026"}
    html = app.test_client().post("/", data=form).get_data(as_text=True)
    assert "GEOL 3040" in html and "only offered in summer terms" in html
    html = app.test_client().post("/", data={**form, "include_summer": "on", "target_semesters": "12"}).get_data(as_text=True)
    assert "only offered in summer terms" not in html


def test_course_beyond_a_full_degree_is_reported_not_hidden():
    html = app.test_client().post("/", data={
        "major": "leisure-and-sport-management-bs", "completed_courses": "LSTS 4902\nLSTS 4903",
        "target_semesters": "4", "start_season": "Fall", "start_year": "2026",
    }).get_data(as_text=True)
    assert "Not counted toward this degree" in html
    assert html.split("uncounted-alert", 1)[1].split("</div>\n  </div>", 1)[0].count("LSTS 4903") == 1


# ── Reading the catalog the way a student would ──────────────────────────────
# These don't reuse the converter's logic. They read each catalog page with
# plain rules a person would use, and fail if the converter disagrees -- so a
# new page layout MTSU introduces can't quietly turn required courses into a
# vague "choose from" row (what happened to Professional Pilot).

import re as _re

from mtsugradpath.catalog_programs import _SKIP_SECTION, _credits, _walk

_PICK_WORDS = _re.compile(
    r"\b(choose|chosen|select|one of|two of|three of|four of|at least|from the following|options?|or)\b", _re.I)


def _plainly_required(program):
    """Courses on their own line in a requirement list: no pick-list wording
    in the section, not joined to another course by "or", and listed before
    any "Choose ..." line."""
    out = set()
    roots = [s for s in program["sections"] if not _SKIP_SECTION.match(s["name"])]
    for sec, _, _ in _walk(roots):
        name, hours, items = sec["name"], sec.get("hours"), sec.get("items") or []
        if _re.match(r"(curriculum|academic map|freshman|sophomore|junior|senior|subtotal|true blue)", name, _re.I):
            continue
        if _PICK_WORDS.search(name) or _PICK_WORDS.search(sec.get("text") or "") or _re.search(
                r"elective|track|emphasis|area|minor|option|maximum|up to", name, _re.I):
            continue
        if not hours:
            continue
        # Only the courses above a "Choose ..." line count toward the size test
        head = []
        for item in items:
            text = (item.get("text") or item.get("note") or "").strip()
            if text and not text.startswith("(") and _PICK_WORDS.search(text) and not _re.search(r"\bOR\s*$", text):
                break
            head.append(item)
        if sum(_credits(i["course"]) for i in head if "course" in i) > hours[1] + 6:
            continue  # lists far more than its hours: a pick-list
        tokens = []
        for item in items:
            if "course" in item or "op" in item or item.get("sep"):
                tokens.append(item)
                continue
            text = (item.get("text") or item.get("note") or "").strip()
            if _re.search(r"\bOR\s*$", text):
                tokens.append({"op": "OR"})
            elif text.startswith("(") or len(text) <= 3 or text.endswith(":") and not _PICK_WORDS.search(text):
                continue  # an aside: "(may be counted in the True Blue Core)", "*", "Strings:"
            elif _PICK_WORDS.search(text):
                break  # "Choose ...": the rest is a pick-list
            else:
                tokens.append({"sep": True})
        # A separator right before "OR" doesn't end the choice ("LEAD 3010 / OR / MGMT 4200")
        tokens = [t for k, t in enumerate(tokens)
                  if not (t.get("sep") and k + 1 < len(tokens) and tokens[k + 1].get("op") == "OR")]
        group, has_or, linked = [], False, False
        for token in tokens + [{"sep": True}]:
            if "course" in token:
                if group and not linked:
                    out.update(c for c in group if not has_or)
                    group, has_or = [], False
                group.append(token["course"])
                linked = False
            elif "op" in token:
                linked, has_or = True, has_or or token["op"] == "OR"
            else:
                out.update(c for c in group if not has_or)
                group, has_or, linked = [], False, False
    return {c for c in out if _credits(c)}


@pytest.mark.parametrize("key", MAJOR_KEYS)
def test_every_catalog_major_requires_the_courses_its_page_plainly_lists(key):
    cfg = catalog_majors()[key]
    program = next(p for p in catalog_store.programs() if p["name"] == cfg["name"])
    planned = {code for k in ("core_courses", "concentration_courses", "supporting_courses") for code, _, _ in cfg[k]}
    labs = {lab for labs in cfg.get("coreq_map", {}).values() for lab in labs}
    in_a_choice = {code for options in cfg["generic_options"].values() for code in options
                   if len(options) <= 4}  # a short "A or B" choice, not a big pick-list
    missing = _plainly_required(program) - planned - labs - in_a_choice
    assert not missing, f"{cfg['name']}: listed as required on the catalog page but not planned as required: {sorted(missing)}"


@pytest.mark.parametrize("key", MAJOR_KEYS)
def test_every_catalog_major_plan_keeps_prerequisites_in_order(key):
    """From nothing over 8 semesters: no course lands in the same term as, or
    before, a catalog prerequisite that's also on the plan."""
    cfg = catalog_majors()[key]
    catalog = load_catalog_for(cfg)
    plan = generate_plan(set(), {}, 8, False, start_season="Fall", start_year=2026, degree_cfg=cfg, catalog=catalog)
    term_of = {item["code"]: t for t, (term, items) in enumerate(plan.items()) if not term.startswith("Remaining")
               for item in items if item.get("kind") == "course"}
    late = []
    for code, t in term_of.items():
        info = catalog_store.course(code) or {}
        late += [(code, p) for p in info.get("prereqs", []) if p in term_of and term_of[p] >= t]
        for group in info.get("prereq_any", []):
            placed = [term_of[g] for g in group if g in term_of]
            if placed and min(placed) >= t:
                late.append((code, " or ".join(group)))
    assert not late, f"{cfg['name']}: scheduled with or before a prerequisite: {late[:5]}"


def test_professional_pilot_plans_its_flight_courses_by_name():
    cfg = catalog_majors().get("aerospace-professional-pilot-concentration-bs")
    if cfg is None:
        pytest.skip("program not in this catalog")
    required = {code for k in ("core_courses", "concentration_courses", "supporting_courses") for code, _, _ in cfg[k]}
    assert len([c for c in required if c.startswith("AERO")]) >= 15
    biggest_pick_list = max((hours for _, _, hours, _ in cfg["supporting_generic"]), default=0)
    assert biggest_pick_list < 15


def test_skipped_summers_do_not_use_up_semesters():
    cfg = catalog_majors().get("aerospace-professional-pilot-concentration-bs") or catalog_majors()[MAJOR_KEYS[0]]
    plan, _ = _plan_for(cfg, terms=8)
    real = [t for t in plan if not t.startswith(("Summer", "Remaining"))]
    assert len(real) == 8


def test_gen_ed_is_spread_through_the_plan_not_left_for_the_end():
    cfg = catalog_majors().get("aerospace-professional-pilot-concentration-bs") or catalog_majors()[MAJOR_KEYS[0]]
    plan, _ = _plan_for(cfg, terms=8)
    first_year = [i["label"] for t in list(plan)[:2] for i in plan[t]]
    assert any("Written Communication" in label for label in first_year)
    remaining = next((items for t, items in plan.items() if t.startswith("Remaining")), [])
    assert not any(i.get("id", "").startswith("tbc_") for i in remaining)


def test_plan_flags_a_prerequisite_from_outside_the_plan():
    html = app.test_client().post("/", data={
        "major": "accounting-bba", "target_semesters": "8", "start_season": "Fall", "start_year": "2026",
    }).get_data(as_text=True)
    # ACTG 3000 needs ENGL 1010, which the plan covers with a gen-ed row
    assert 'class="plan-item-offered plan-item-prereq" title="Prerequisite (catalog):' in html
