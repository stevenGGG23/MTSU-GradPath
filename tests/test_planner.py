from mtsugradpath.planner import generate_plan, next_courses, term_label, validate_plan, load_catalog_courses
from mtsugradpath.degree import course_offered_in_term


def codes_in(term_items):
    return [item["code"] for item in term_items if item["kind"] == "course"]


def all_codes(plan):
    codes = []
    for term_items in plan.values():
        codes.extend(codes_in(term_items))
    return codes


def test_generate_plan_returns_terms():
    plan = generate_plan(set(), {}, target_terms=3, include_summer=False, start_season="Fall", start_year=2026)
    assert isinstance(plan, dict)
    first_term = term_label(1, "Fall", 2026)
    assert first_term == "Fall 2026"
    assert "CSCI 1170" in codes_in(plan.get(first_term, []))


def test_generate_plan_respects_prerequisites():
    # CSCI 2170 requires CSCI 1170, so it shouldn't be offered before that's done.
    catalog = {}
    candidates = next_courses(set(), {"CSCI 1170", "CSCI 2170"}, catalog)
    assert "CSCI 1170" in candidates
    assert "CSCI 2170" not in candidates

    candidates = next_courses({"CSCI 1170"}, {"CSCI 2170"}, catalog)
    assert "CSCI 2170" in candidates


def test_generate_plan_progresses_toward_completion():
    completed = {"CSCI 1010", "CSCI 1170", "CSCI 2170", "CSCI 3080", "CSCI 3110", "CSCI 3130"}
    plan = generate_plan(completed, {}, target_terms=4, include_summer=True)
    scheduled = all_codes(plan)
    remaining = codes_in(plan.get("Remaining after 4 terms", []))
    assert "CSCI 3240" in scheduled or "CSCI 3240" in remaining


def test_generate_plan_includes_generic_requirements():
    generic_hours = {"math_elective": 0}
    plan = generate_plan(set(), generic_hours, target_terms=6, include_summer=True)
    all_items = [item for term_items in plan.values() for item in term_items]
    requirement_ids = {item.get("id") for item in all_items if item["kind"] == "requirement"}
    assert "math_elective" in requirement_ids


def test_generate_plan_skips_completed_generic_requirements():
    generic_hours = {"math_elective": 4}
    plan = generate_plan(set(), generic_hours, target_terms=8, include_summer=True)
    all_items = [item for term_items in plan.values() for item in term_items]
    requirement_ids = {item.get("id") for item in all_items if item["kind"] == "requirement"}
    assert "math_elective" not in requirement_ids


def test_validate_plan_flags_same_term_violation():
    catalog = load_catalog_courses()
    broken_plan = {
        "Fall 2026": [
            {"kind": "course", "code": "MATH 1910", "label": "MATH 1910 - Calculus I", "hours": 4},
            {"kind": "course", "code": "MATH 1920", "label": "MATH 1920 - Calculus II", "hours": 4},
        ]
    }
    warnings = validate_plan(broken_plan, set(), catalog)
    assert len(warnings) == 1
    assert warnings[0]["course"] == "MATH 1920"
    assert warnings[0]["prereq"] == "MATH 1910"
    assert warnings[0]["type"] == "same_term"


def test_validate_plan_flags_not_yet_completed_violation():
    catalog = load_catalog_courses()
    broken_plan = {
        "Fall 2026": [{"kind": "course", "code": "CSCI 2170", "label": "CSCI 2170 - Computer Science II", "hours": 4}],
    }
    warnings = validate_plan(broken_plan, set(), catalog)
    assert any(w["course"] == "CSCI 2170" and w["prereq"] == "CSCI 1170" for w in warnings)


def test_generate_plan_never_schedules_calc_1_and_2_together():
    # Regression test: previously, once every CS requirement was already
    # done, the only remaining courses were supporting ones -- and MATH 1910
    # completing mid-term would incorrectly unlock MATH 1920 in that same
    # term, since current_completed was mutated live during scheduling.
    completed = {
        "CSCI 1010", "CSCI 1170", "CSCI 2170", "CSCI 3080", "CSCI 3110", "CSCI 3130",
        "CSCI 3240", "CSCI 4700", "CSCI 3210", "CSCI 4160",
        "CSCI 3033", "CSCI 3160", "CSCI 3180", "CSCI 3200",
    }
    catalog = load_catalog_courses()
    plan = generate_plan(completed, {}, target_terms=4, include_summer=True, start_season="Fall", start_year=2026)

    math_terms = {}
    for term, items in plan.items():
        for item in items:
            if item.get("code") in ("MATH 1910", "MATH 1920"):
                math_terms[item["code"]] = term

    assert "MATH 1910" in math_terms and "MATH 1920" in math_terms
    assert math_terms["MATH 1910"] != math_terms["MATH 1920"]

    warnings = validate_plan(plan, completed, catalog)
    assert warnings == []


def test_generate_plan_produces_no_warnings_from_scratch():
    catalog = load_catalog_courses()
    plan = generate_plan(set(), {}, target_terms=10, include_summer=True, start_season="Fall", start_year=2026)
    assert validate_plan(plan, set(), catalog) == []


def test_course_offered_in_term_matches_department_schedule():
    # CSCI 4160 (Compiler Design) is Fall-only per MTSU CS department scheduling patterns.
    assert course_offered_in_term("CSCI 4160", "Fall", 2026) is True
    assert course_offered_in_term("CSCI 4160", "Spring", 2027) is False

    # CSCI 3210 (Theory of Programming Languages) is Spring-only.
    assert course_offered_in_term("CSCI 3210", "Spring", 2027) is True
    assert course_offered_in_term("CSCI 3210", "Fall", 2026) is False

    # CSCI 4360 (Intelligent Robot System) is odd-year Springs only.
    assert course_offered_in_term("CSCI 4360", "Spring", 2027) is True
    assert course_offered_in_term("CSCI 4360", "Spring", 2028) is False
    assert course_offered_in_term("CSCI 4360", "Fall", 2027) is False

    # Courses with no published pattern are scheduled on demand -- always available.
    assert course_offered_in_term("CSCI 4900", "Summer", 2026) is True


def test_generate_plan_never_schedules_fall_only_course_in_spring():
    plan = generate_plan(set(), {}, target_terms=10, include_summer=True, start_season="Fall", start_year=2026)
    for term, items in plan.items():
        if term.startswith("Remaining"):
            continue
        season, year = term.split()
        for item in items:
            code = item.get("code")
            if code:
                assert course_offered_in_term(code, season, int(year)), (
                    f"{code} scheduled in {term}, which its offering pattern does not allow"
                )
