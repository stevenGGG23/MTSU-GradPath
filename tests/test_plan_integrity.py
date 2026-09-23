"""Independent correctness checks for generate_plan()/validate_plan() across
every supported major.

These deliberately do NOT import generate_plan's internal helpers
(_effective_prereqs, _offered_in_term) -- the whole point is to re-derive the
right answer separately, from the raw DEGREE_CONFIGS data, so a bug in the
scheduler's own logic can't also hide from its own test. If a plan violates
one of these checks, a student could be told to register for a course they
aren't actually eligible for.
"""
import random

import pytest

from mtsugradpath.degree_configs import DEGREE_CONFIGS, major_prefixes
from mtsugradpath.planner import generate_plan, validate_plan, load_catalog_courses

MAJOR_KEYS = list(DEGREE_CONFIGS.keys())

# Config lookup by prefix, built independently of planner.py's own
# _PREFIX_TO_CONFIG, for the same reason noted above.
_CFG_BY_PREFIX = {p: cfg for cfg in DEGREE_CONFIGS.values() for p in major_prefixes(cfg)}


def _known_codes(cfg):
    """Every course code this major's config declares by name (core,
    concentration, and supporting) -- used to catch a plan recommending a
    course that isn't part of any real requirement list.
    """
    codes = {c for c, _, _ in cfg["core_courses"]}
    codes |= {c for c, _, _ in cfg["concentration_courses"]}
    codes |= {c for c, _, _ in cfg["supporting_courses"]}
    return codes


def _oracle_prereqs(code):
    """What SHOULD be required to take *code*, derived independently from
    whichever major's config actually owns that course prefix (falls back to
    an empty requirement if the prefix isn't tracked by any config -- e.g. a
    True Blue Core elective).
    """
    home_cfg = _CFG_BY_PREFIX.get(code.split()[0])
    if home_cfg is None:
        return set()
    required = set(home_cfg.get("prereq_map", {}).get(code, set()))
    override = home_cfg.get("prereq_override_map", {}).get(code)
    if override is not None:
        required |= set(override)
    for other_cfg in DEGREE_CONFIGS.values():
        required |= other_cfg.get("supporting_prereq_map", {}).get(code, set())
    return required


def _oracle_offered(code, season, year):
    """Whether *code* is offered in a given season/year, per its own major's
    declared offering pattern (or unrestricted if untracked).
    """
    home_cfg = _CFG_BY_PREFIX.get(code.split()[0])
    if home_cfg is None:
        return True
    if code in home_cfg.get("odd_year_spring_only", set()):
        return season == "Spring" and year % 2 == 1
    allowed = home_cfg.get("offering_seasons", {}).get(code)
    return True if allowed is None else season in allowed


def _check_plan(plan, completed_courses, known_codes):
    """Returns a list of human-readable violation strings; empty means the
    plan is logically sound by every check below.
    """
    violations = []
    seen = set(completed_courses)
    ever_scheduled = set()

    for term, items in plan.items():
        if term.startswith("Remaining"):
            continue

        season, year_str = term.split()
        year = int(year_str)
        this_term_codes = {i["code"] for i in items if i.get("kind") == "course"}

        for code in this_term_codes:
            if code in ever_scheduled:
                violations.append(f"{code} scheduled more than once (again in {term})")
            ever_scheduled.add(code)

            required = _oracle_prereqs(code)
            same_term = required & this_term_codes
            if same_term:
                violations.append(
                    f"{code} in {term} is scheduled together with its own prerequisite(s) {same_term}"
                )
            not_done = required - seen - this_term_codes
            if not_done:
                violations.append(
                    f"{code} in {term} is scheduled before completing prerequisite(s) {not_done}"
                )

            if not _oracle_offered(code, season, year):
                violations.append(f"{code} scheduled in {term}, which it is not offered in")

        # Hour budget: nothing should ever be wildly over the 15-hour target
        # (a couple hours of legitimate overshoot from an indivisible course
        # landing right at the edge is fine; 19+ would signal a real bug).
        term_hours = sum(i.get("hours", 0) or 0 for i in items if i.get("kind") == "course")
        if term_hours > 19:
            violations.append(f"{term} totals {term_hours} hours, unreasonably over the 15-hour target")

        seen |= this_term_codes

    # No fabricated courses: everything recommended (scheduled or left in
    # "Remaining") must be a real course this major's config or the synced
    # catalog actually knows about.
    all_recommended = set(ever_scheduled)
    for term, items in plan.items():
        if term.startswith("Remaining"):
            all_recommended |= {i["code"] for i in items if i.get("kind") == "course"}
    fabricated = {c for c in all_recommended if c not in known_codes}
    if fabricated:
        violations.append(f"plan recommends unknown/fabricated course code(s): {fabricated}")

    return violations


@pytest.mark.parametrize("major_key", MAJOR_KEYS)
def test_full_degree_plan_from_scratch_is_sound(major_key):
    """A brand-new student with nothing completed should get a plan (run out
    to enough terms to cover the whole degree) with zero prerequisite,
    offering-season, duplicate, or fabricated-course violations.
    """
    cfg = DEGREE_CONFIGS[major_key]
    catalog = load_catalog_courses(cfg["prefix"])
    known_codes = _known_codes(cfg) | set(catalog.keys())

    plan = generate_plan(
        set(), {}, target_terms=14, include_summer=True,
        start_season="Fall", start_year=2026, degree_cfg=cfg, catalog=catalog,
    )

    violations = _check_plan(plan, set(), known_codes)
    assert violations == [], "\n".join(violations)

    # validate_plan() is the app's own safety net -- it must agree.
    warnings = validate_plan(plan, set(), catalog, degree_cfg=cfg)
    assert warnings == [], f"validate_plan reported warnings on a plan the oracle found clean: {warnings}"


@pytest.mark.parametrize("major_key", MAJOR_KEYS)
def test_random_partial_completion_is_sound(major_key):
    """Fuzzes generate_plan with many random completed-course states
    (including unrealistic ones a real audit form wouldn't produce, since
    the app doesn't itself enforce that completed-course input is
    prerequisite-closed) and checks every resulting plan independently.
    """
    cfg = DEGREE_CONFIGS[major_key]
    catalog = load_catalog_courses(cfg["prefix"])
    all_codes = sorted(_known_codes(cfg))
    known_codes = _known_codes(cfg) | set(catalog.keys())

    rng = random.Random(f"gradpath-{major_key}")

    for trial in range(25):
        subset_size = rng.randint(0, len(all_codes))
        completed = set(rng.sample(all_codes, subset_size))

        plan = generate_plan(
            completed, {}, target_terms=6, include_summer=True,
            start_season=rng.choice(["Fall", "Spring"]), start_year=2026 + trial % 3,
            degree_cfg=cfg, catalog=catalog,
        )

        violations = _check_plan(plan, completed, known_codes)
        assert violations == [], (
            f"{major_key}, trial {trial}, completed={sorted(completed)}:\n" + "\n".join(violations)
        )

        warnings = validate_plan(plan, completed, catalog, degree_cfg=cfg)
        assert warnings == [], (
            f"{major_key}, trial {trial}: validate_plan reported warnings on an oracle-clean plan: {warnings}"
        )


def test_physics_never_schedules_phys_2120_before_math_1910():
    """Regression test for a real bug: PHYS 2120's prereq_override_map entry
    (added to fix a catalog lab-section quirk) was replacing its entire
    prerequisite set instead of merging with prereq_map, silently dropping
    the MATH 1910 requirement. generate_plan scheduled PHYS 2120 the same
    term as MATH 1910 with zero warnings from validate_plan.
    """
    cfg = DEGREE_CONFIGS["physics"]
    catalog = load_catalog_courses(cfg["prefix"])
    completed = {"PHYS 2110"}  # MATH 1910 deliberately NOT completed

    plan = generate_plan(
        completed, {}, target_terms=1, include_summer=False,
        start_season="Fall", start_year=2026, degree_cfg=cfg, catalog=catalog,
    )

    # Only actual term buckets count as "scheduled" -- a course correctly and
    # harmlessly appears in the "Remaining after N terms" bucket when it
    # isn't eligible yet, so that key must be excluded here.
    scheduled = {
        i["code"] for term, items in plan.items() if not term.startswith("Remaining")
        for i in items if i.get("kind") == "course"
    }
    assert "PHYS 2120" not in scheduled, "PHYS 2120 must not be offered before MATH 1910 is completed"

    warnings = validate_plan(plan, completed, catalog, degree_cfg=cfg)
    assert warnings == []


def test_chemistry_never_schedules_chem_3510_before_math_1920():
    """Regression test for a real bug: CHEM 3510 requires MATH 1920 per
    prereq_map, but the cross-major requirement was silently dropped by a
    blanket 'prereq.startswith(major_prefix)' filter in both the scheduler
    and validate_plan.
    """
    cfg = DEGREE_CONFIGS["chemistry"]
    catalog = load_catalog_courses(cfg["prefix"])
    completed = {"CHEM 2240"}  # MATH 1920 deliberately NOT completed

    plan = generate_plan(
        completed, {}, target_terms=1, include_summer=False,
        start_season="Fall", start_year=2026, degree_cfg=cfg, catalog=catalog,
    )

    # Only actual term buckets count as "scheduled" -- a course correctly and
    # harmlessly appears in the "Remaining after N terms" bucket when it
    # isn't eligible yet, so that key must be excluded here.
    scheduled = {
        i["code"] for term, items in plan.items() if not term.startswith("Remaining")
        for i in items if i.get("kind") == "course"
    }
    assert "CHEM 3510" not in scheduled, "CHEM 3510 must not be offered before MATH 1920 is completed"

    warnings = validate_plan(plan, completed, catalog, degree_cfg=cfg)
    assert warnings == []


def test_validate_plan_flags_wrong_season_course():
    """validate_plan previously had no way to catch a course scheduled in a
    term it isn't offered in -- it only checked prerequisite ordering. A bug
    in generate_plan's own season logic would have produced a bad plan with
    no warning at all.
    """
    cfg = DEGREE_CONFIGS["physics"]
    catalog = load_catalog_courses(cfg["prefix"])

    # PHYS 3110 is Fall-only per PHYS_CONFIG's offering_seasons.
    broken_plan = {
        "Spring 2027": [
            {"kind": "course", "code": "PHYS 3110", "label": "PHYS 3110 - Mechanics", "hours": 3},
        ],
    }
    completed = {"PHYS 2110", "PHYS 2120", "MATH 1910", "MATH 1920"}
    warnings = validate_plan(broken_plan, completed, catalog, degree_cfg=cfg)

    assert any(w["type"] == "not_offered" and w["course"] == "PHYS 3110" for w in warnings), warnings


def test_generate_plan_never_schedules_wrong_season_course_for_any_major():
    """Exhaustively checks -- across all 5 majors, many terms, with summers
    included -- that nothing generate_plan schedules ever lands in a term
    its own major's offering_seasons/odd_year_spring_only data disallows.
    """
    for major_key, cfg in DEGREE_CONFIGS.items():
        catalog = load_catalog_courses(cfg["prefix"])
        plan = generate_plan(
            set(), {}, target_terms=16, include_summer=True,
            start_season="Fall", start_year=2026, degree_cfg=cfg, catalog=catalog,
        )
        for term, items in plan.items():
            if term.startswith("Remaining"):
                continue
            season, year = term.split()
            year = int(year)
            for item in items:
                code = item.get("code")
                if code:
                    assert _oracle_offered(code, season, year), (
                        f"[{major_key}] {code} scheduled in {term}, which its offering pattern does not allow"
                    )
