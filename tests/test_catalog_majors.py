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


_PART = _re.compile(r"^(.*\S)\s+(VI|IV|V|I{1,3}|[1-6])$")


@pytest.mark.parametrize("key", MAJOR_KEYS)
def test_every_catalog_major_takes_numbered_sequences_in_order(key):
    """Calculus I before II, Flight Lab I before II, Physics I before II --
    never the same term -- from scratch and from random partial progress."""
    cfg = catalog_majors()[key]
    catalog = load_catalog_for(cfg)
    required = sorted({code for k in ("core_courses", "concentration_courses", "supporting_courses") for code, _, _ in cfg[k]})
    rng = random.Random(key)
    for trial in range(3):
        done = set(rng.sample(required, k=len(required) // 4)) if trial and required else set()
        plan = generate_plan(done, {}, 8, trial == 2, start_season="Fall", start_year=2026, degree_cfg=cfg, catalog=catalog)
        term_of = {item["code"]: t for t, (term, items) in enumerate(plan.items()) if not term.startswith("Remaining")
                   for item in items if item.get("kind") == "course"}
        out_of_order = [(earlier, code) for code in term_of for earlier in catalog_store.previous_part(code)
                        if earlier in term_of and term_of[earlier] >= term_of[code]]
        assert not out_of_order, f"{cfg['name']}: {out_of_order}"


def test_flight_labs_are_never_in_the_same_term():
    cfg = catalog_majors().get("aerospace-professional-pilot-concentration-bs")
    if cfg is None:
        pytest.skip("program not in this catalog")
    for done in (set(), {"AERO 1010", "AERO 1020", "AERO 2230"}):
        plan, _ = _plan_for(cfg, completed=done, terms=8)
        term_of = {i["code"]: t for t, (term, items) in enumerate(plan.items()) for i in items if i.get("kind") == "course"}
        labs = [term_of[c] for c in ("AERO 2201", "AERO 3203", "AERO 3204", "AERO 3261") if c in term_of]
        assert labs == sorted(labs) and len(set(labs)) == len(labs)


def _level(code):
    number = code.split()[-1]
    return int(number[0]) if number[:1].isdigit() else 0


@pytest.mark.parametrize("key", MAJOR_KEYS)
def test_plans_follow_the_four_year_map_from_scratch(key):
    """Plans keep MTSU's own order: nothing the four-year map puts in an
    earlier year lands after a course it puts two or more years later, and
    no 1000-level course follows a 3000/4000-level course in its subject --
    unless the map orders them that way or the course only runs in some
    terms (fall only, odd-year springs, ...)."""
    cfg = catalog_majors()[key]
    catalog = load_catalog_for(cfg)
    plan = generate_plan(set(), {}, 8, False, start_season="Fall", start_year=2026, degree_cfg=cfg, catalog=catalog)
    term_of = {item["code"]: t for t, (term, items) in enumerate(plan.items()) if not term.startswith("Remaining")
               for item in items if item.get("kind") == "course"}
    year = cfg.get("map_year", {})
    problems = []
    for a, ta in term_of.items():
        if catalog_store.offering_summary(a):
            continue  # runs only in some terms: may have to wait
        for b, tb in term_of.items():
            if ta <= tb:
                continue
            if year.get(a) and year.get(b) and year[a] + 2 <= year[b]:
                problems.append(f"{a} (map year {year[a]}) after {b} (map year {year[b]})")
            elif (a.split()[0] == b.split()[0] and _level(a) == 1 and _level(b) >= 3
                  and not (year.get(a) and year.get(b) and year[a] >= year[b])):
                problems.append(f"{a} after {b}")
    assert not problems, f"{cfg['name']}: {problems[:4]}"


def test_introduction_to_aerospace_comes_first():
    """A student flagged Professional Pilot I planned before Introduction to Aerospace."""
    cfg = catalog_majors().get("aerospace-professional-pilot-concentration-bs")
    if cfg is None:
        pytest.skip("program not in this catalog")
    plan, _ = _plan_for(cfg, terms=8)
    first = [i["code"] for i in next(iter(plan.values())) if i.get("kind") == "course"]
    assert "AERO 1010" in first


@pytest.mark.parametrize("key", MAJOR_KEYS[::3])
def test_random_students_keep_the_four_year_map_order(key):
    """Random students (some courses done, summers on or off, 4-12 terms):
    a course the map puts 2+ years earlier never follows a later one unless
    it can't run that term or is waiting on its own prerequisite."""
    from mtsugradpath.planner import _offered_in_term
    cfg = catalog_majors()[key]
    catalog = load_catalog_for(cfg)
    year = cfg.get("map_year", {})
    required = sorted({code for k in ("core_courses", "concentration_courses", "supporting_courses") for code, _, _ in cfg[k]})
    rng = random.Random("order" + key)
    for _ in range(4):
        done = set(rng.sample(required, k=rng.randint(0, len(required) // 2))) if required else set()
        done = {c for c in done if cfg["prereq_override_map"].get(c, set()) <= done}
        plan = generate_plan(done, {}, rng.choice([4, 8, 12]), rng.random() < 0.5,
                             start_season=rng.choice(["Fall", "Spring", "Summer"]), start_year=2027,
                             degree_cfg=cfg, catalog=catalog)
        terms = list(plan)
        term_of = {i["code"]: t for t, (name, items) in enumerate(plan.items()) if not name.startswith("Remaining")
                   for i in items if i.get("kind") == "course"}
        for a, ta in term_of.items():
            if catalog_store.offering_summary(a):
                continue
            for b, tb in term_of.items():
                if not (ta > tb and year.get(a) and year.get(b) and year[a] + 2 <= year[b]):
                    continue
                season, yr = terms[tb].split()
                info = catalog_store.course(a) or {}
                waiting = any(p in term_of and term_of[p] >= tb
                              for p in set(catalog.get(a, {}).get("prereqs", ())) | set(catalog_store.previous_part(a)))
                waiting = waiting or any(
                    [g for g in group if g in term_of] and min(term_of[g] for g in group if g in term_of) >= tb
                    for group in info.get("prereq_any", []))
                waiting = waiting or catalog_store.standing_hours(a) > 0
                assert waiting or not _offered_in_term(a, season, int(yr), cfg), (
                    f"{cfg['name']} with {sorted(done)}: {a} (year {year[a]}) after {b} (year {year[b]})")



def _terms_and_hours(plan, completed=(), catalog=None):
    """{course: term index} and {course: hours earned before its term}."""
    term_of, before = {}, {}
    earned = sum(catalog_store.credits(c, default=3) or 0 for c in completed)
    for t, (term, items) in enumerate(plan.items()):
        if term.startswith("Remaining"):
            continue
        for item in items:
            if item.get("kind") == "course":
                term_of[item["code"]] = t
                before[item["code"]] = earned
        earned += sum(item.get("hours") or 0 for item in items)
    return term_of, before


@pytest.mark.parametrize("key", MAJOR_KEYS)
def test_every_catalog_major_respects_class_standing_and_corequisites(key):
    """"Junior standing" courses wait for 60 earned hours (sophomore 30,
    senior 90), and a corequisite ("X or simultaneous completion of X") is
    never planned after the course that needs it."""
    from mtsugradpath.planner import _consistent_coreqs, _full_prereqs
    cfg = catalog_majors()[key]
    catalog = load_catalog_for(cfg)
    plan = generate_plan(set(), {}, 8, False, start_season="Fall", start_year=2026, degree_cfg=cfg, catalog=catalog)
    term_of, before = _terms_and_hours(plan)
    early = [(c, catalog_store.standing_hours(c), before[c]) for c in term_of
             if before[c] < catalog_store.standing_hours(c)]
    assert not early, f"{cfg['name']}: before the class standing it needs: {early[:4]}"
    # Judge contradictions over every course the plan holds (the planner's
    # own view), including any left over after the last term
    on_plan = {item["code"] for items in plan.values() for item in items if item.get("kind") == "course"}
    honored = _consistent_coreqs(on_plan, lambda c: _full_prereqs(c, cfg, catalog), catalog)
    late = [(c, co) for c, cos in honored.items() for co in cos
            if c in term_of and co in term_of and term_of[co] > term_of[c]]
    assert not late, f"{cfg['name']}: corequisite planned after the course: {late[:4]}"


def _plan_page_terms(html):
    """[(term, [codes], hours)] from a plan page."""
    import re as re_
    cards = re_.findall(r'term-card-title">([^<]+)</span>\s*(?:<span class="badge bg-secondary">([\d.]+) cr hrs</span>)?'
                        r'(.*?)(?=term-card-title|Your prerequisite path|$)', html, re_.S)
    return [(name, re_.findall(r'plan-item-code">([^<]+)<', body), float(hours or 0)) for name, hours, body in cards]


def test_partial_progress_with_minors_and_second_majors_keeps_prerequisites():
    """Random students with a minor or two and sometimes a second major,
    through the real plan page: prerequisites (one program's prerequisite
    can come from another program), numbered sequences, class standing, no
    course twice, and no warnings other than the summer-only notice."""
    from werkzeug.datastructures import MultiDict
    rng = random.Random(2026)
    client = app.test_client()
    majors, minors = sorted(catalog_majors()), [k for k, _ in picker_minors()]
    for _ in range(60):
        major = rng.choice(majors)
        second = rng.choice(majors) if rng.random() < 0.4 else ""
        picked = rng.sample(minors, k=rng.randint(1, 2))
        pool = sorted({c for key in [major, second] if key for k in ("core_courses", "concentration_courses", "supporting_courses")
                       for c, _, _ in catalog_majors()[key][k]})
        done = rng.sample(pool, k=rng.randint(0, len(pool) // 3)) if pool else []
        form = MultiDict([("major", major), ("second_major", second), ("target_semesters", str(rng.choice([6, 8, 12]))),
                          ("start_season", rng.choice(["Fall", "Spring", "Summer"])), ("start_year", "2027"),
                          ("completed_courses", "\n".join(done))] + [("minor", m) for m in picked]
                         + ([("include_summer", "on")] if rng.random() < 0.5 else []))
        html = client.post("/", data=form).get_data(as_text=True)
        label = f"{major} + {second or '-'} + {picked} with {sorted(done)}"
        if "Plan warning" in html:
            warnings = html.split("Plan warning")[1].split("</ul>")[0]
            assert warnings.count("<li") == warnings.count("only offered in summer terms"), label
        terms = _plan_page_terms(html)
        codes = [c for _, cs, _ in terms for c in cs]
        assert len(codes) == len(set(codes)) and not set(codes) & set(done), label
        term_of, before, earned = {}, {}, sum(catalog_store.credits(c, default=3) or 0 for c in done)
        for t, (name, cs, hours) in enumerate(terms):
            if name.startswith("Remaining"):
                continue
            for c in cs:
                term_of[c], before[c] = t, earned
            earned += hours
        for c, t in term_of.items():
            info = catalog_store.course(c) or {}
            assert not [p for p in info.get("prereqs", []) if p in term_of and term_of[p] >= t], f"{label}: {c}"
            assert not [p for p in catalog_store.previous_part(c) if p in term_of and term_of[p] >= t], f"{label}: {c}"
            assert before[c] >= catalog_store.standing_hours(c), f"{label}: {c} needs class standing"


@pytest.mark.parametrize("major,second", [
    ("concrete-industry-management-concrete-contracting-concentration-bs", "computer-science-artificial-intelligence-concentration-bs"),
    ("recording-industry-commercial-songwriting-concentration-bs", "construction-management-commercial-construction-management-concentration-bs"),
    ("nursing-bsn", "biochemistry-bs"),
    ("geosciences-geospatial-science-concentration-bs", "economics-bba"),
])
def test_double_majors_honor_each_others_prerequisites(major, second):
    """One major's course can need a course only the other major lists
    (MATH 1910 needs MATH 1730): the combined plan keeps that order."""
    from werkzeug.datastructures import MultiDict
    if major not in catalog_majors() or second not in catalog_majors():
        pytest.skip("program not in this catalog")
    html = app.test_client().post("/", data=MultiDict([
        ("major", major), ("second_major", second), ("target_semesters", "8"),
        ("start_season", "Fall"), ("start_year", "2026"),
    ])).get_data(as_text=True)
    term_of = {c: t for t, (name, codes, _) in enumerate(_plan_page_terms(html)) if not name.startswith("Remaining")
               for c in codes}
    late = [(c, p) for c in term_of for p in (catalog_store.course(c) or {}).get("prereqs", [])
            if p in term_of and term_of[p] >= term_of[c]]
    assert not late, late


def _checkbox_prereqs(html):
    import re as re_
    return {code: [p for p in pre.split("|") if p] for code, pre in
            re_.findall(r'data-code="([^"]+)"[^>]*data-prereqs="([^"]*)"', html)}


def test_checklist_courses_carry_the_prerequisites_to_check_with_them():
    """Checking CSCI 2170 also checks CSCI 1170 (the page's script uses these
    lists); only prerequisites on the same checklist are listed."""
    html = app.test_client().get("/?major=cs").get_data(as_text=True)
    prereqs = _checkbox_prereqs(html)
    assert prereqs["CSCI 2170"] == ["CSCI 1170"]
    assert set(prereqs["CSCI 3110"]) == {"CSCI 2170", "CSCI 3080"}
    assert prereqs["CSCI 1170"] == []
    on_page = set(prereqs)
    assert all(set(p) <= on_page for p in prereqs.values())
    # The confirmation popup is on the page
    assert 'id="autoPrereqModal"' in html and 'id="autoPrereqProceed"' in html and 'id="autoPrereqEdit"' in html


def test_flight_lab_two_checks_flight_lab_one():
    cfg = catalog_majors().get("aerospace-professional-pilot-concentration-bs")
    if cfg is None:
        pytest.skip("program not in this catalog")
    prereqs = _checkbox_prereqs(app.test_client().get(f"/?major={cfg['key']}").get_data(as_text=True))
    assert prereqs["AERO 3203"] == ["AERO 2201"]  # "Private Pilot Certificate", earned in Flight Lab I
    assert prereqs["AERO 3204"] == ["AERO 3203"]


def test_an_either_or_prerequisite_is_not_guessed():
    """AERO 3020 needs one of MATH 1530/1710/1810/1910 and one science course:
    with several options on the checklist, none is checked for the student."""
    cfg = catalog_majors().get("aerospace-professional-pilot-concentration-bs")
    if cfg is None:
        pytest.skip("program not in this catalog")
    prereqs = _checkbox_prereqs(app.test_client().get(f"/?major={cfg['key']}").get_data(as_text=True))
    assert set(prereqs["AERO 3020"]) == {"AERO 1010", "AERO 1020"}


def test_plan_page_prerequisite_path_reads_left_to_right():
    """Like the Prerequisite Map page: a left-to-right graph of the linked
    courses, and courses with no links on the plan listed below it."""
    import re as re_
    html = app.test_client().post("/", data={
        "major": "cs", "completed_courses": "CSCI 1170", "target_semesters": "8",
        "start_season": "Fall", "start_year": "2026",
    }).get_data(as_text=True)
    graph = re_.search(r'<pre class="mermaid personal-mermaid">(.*?)</pre>', html, re_.S).group(1)
    assert graph.strip().startswith("graph LR")
    chips = re_.findall(r'class="badge prereq-chip prereq-chip-\w+"[^>]*>([A-Z]{2,4} \d{4})', html)
    assert chips and all(code.replace(" ", "_") not in graph for code in chips)
    assert "No prerequisite links on your plan" in html


def _full_courses_per_term(plan):
    return [sum(1 for i in items if i.get("kind") == "course" and (i.get("hours") or 0) >= 3)
            for term, items in plan.items() if not term.startswith(("Remaining", "Summer"))]


def test_healthy_course_mix_spreads_major_courses():
    """Computer Science over 8 semesters: no more than 3 full major courses in
    any semester, and gen-ed in every semester but none piled at the end."""
    from mtsugradpath.degree_configs import get_full_degree_config
    cfg = get_full_degree_config("cs")
    catalog = load_catalog_for(cfg)
    plan = generate_plan(set(), {}, 8, False, start_season="Fall", start_year=2026, degree_cfg=cfg,
                         catalog=catalog, max_courses_per_term=3)
    counts = _full_courses_per_term(plan)
    assert max(counts) <= 3 and min(counts) >= 1, counts
    assert not any(t.startswith("Remaining") for t in plan)
    assert validate_plan(plan, set(), catalog, degree_cfg=cfg) == []


@pytest.mark.parametrize("key", MAJOR_KEYS)
def test_every_catalog_major_plans_cleanly_with_a_healthy_course_mix(key):
    """The course-mix limit never breaks prerequisites, standing or the plan."""
    cfg = catalog_majors()[key]
    catalog = load_catalog_for(cfg)
    plan = generate_plan(set(), {}, 8, False, start_season="Fall", start_year=2026, degree_cfg=cfg,
                         catalog=catalog, max_courses_per_term=3)
    assert validate_plan(plan, set(), catalog, degree_cfg=cfg) == []
    term_of, before = _terms_and_hours(plan)
    for code, t in term_of.items():
        info = catalog_store.course(code) or {}
        assert not [p for p in info.get("prereqs", []) if p in term_of and term_of[p] >= t], code
        assert before[code] >= catalog_store.standing_hours(code), code


def test_planner_defaults_to_eight_terms_and_a_healthy_mix():
    re = _re
    page = app.test_client().get("/?major=cs").get_data(as_text=True)
    assert re.search(r'id="target_semesters"[^>]*value="8"', page)
    assert re.search(r'id="healthy_mix"[^>]*checked', page)
    client = app.test_client()
    base = {"major": "cs", "start_season": "Fall", "start_year": "2026", "healthy_mix_shown": "1"}
    on = client.post("/", data={**base, "healthy_mix": "on", "target_semesters": "8"}).get_data(as_text=True)
    off = client.post("/", data={**base, "target_semesters": "8"}).get_data(as_text=True)
    busiest = lambda html: max(len(re.findall(r'plan-item-tag">MAJ', body)) for body in
                               re.findall(r'term-card-title">(?!Remaining)[^<]+<(.*?)(?=term-card-title|Your prerequisite path)', html, re.S))
    assert busiest(on) <= 3 < busiest(off)
