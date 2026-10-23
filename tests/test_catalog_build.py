"""Turning raw Acalog API responses into GradPath's catalog JSON."""
from mtsugradpath.catalog_build import (
    build_course,
    build_program,
    parse_hours_range,
    parse_requisites,
)


def _req(text):
    return {k: v for k, v in parse_requisites(text).items() if v and k != "prereq_text"}


# ── Prerequisite sentences ────────────────────────────────────────────────────

def test_all_of_prerequisites_with_minimum_grade():
    assert _req("Prerequisites: CSCI 2170 and CSCI 3080 with grades of C or better. Corequisite: CSCI 3081.") == {
        "prereqs": ["CSCI 2170", "CSCI 3080"],
        "coreqs": ["CSCI 3081"],
        "min_grades": {"CSCI 2170": "C", "CSCI 3080": "C"},
    }


def test_or_alternatives_become_one_group():
    assert _req("Prerequisite: MATH 1710 or MATH 1010 or approval of instructor. Basic principles.") == {
        "prereq_any": [["MATH 1710", "MATH 1010"]],
    }


def test_bare_number_after_or_reuses_the_prefix():
    assert _req("Prerequisite: MATH 1910 or 1920 with a grade of C- or better. Then")["prereq_any"] == [
        ["MATH 1910", "MATH 1920"]
    ]


def test_lecture_lab_pair_counts_as_the_lecture():
    assert _req("Prerequisite: BIOL 3250 / BIOL 3251 . Corequisite: BIOL 3021 . Vertebrate") == {
        "prereqs": ["BIOL 3250"],
        "coreqs": ["BIOL 3021"],
    }


def test_sentence_ends_at_the_period_not_at_later_years():
    # "(c. 1300-1600 CE)" in the description must not leak into the prereqs
    req = _req("Prerequisites: ART 1920 and ART 1930 with C (2.0) or better or permission of instructor. "
               "Examination of the visual arts of the Italian Renaissance (c. 1300-1600 CE).")
    assert req["prereqs"] == ["ART 1920", "ART 1930"]
    assert "prereq_any" not in req


def test_with_a_minimum_grade_is_not_an_a():
    req = _req("Prerequisites: AERO 2331 and ET 3610, both with a minimum grade of C (2.0). Practical")
    assert req["min_grades"] == {"AERO 2331": "C", "ET 3610": "C"}


def test_no_codes_means_no_prerequisites():
    assert _req("Prerequisite: Junior or senior standing. Fundamental concepts.") == {}


def test_hours_ranges():
    assert parse_hours_range("Major Requirements (31-32 hours)") == [31, 32]
    assert parse_hours_range("Electives (6 hours)") == [6, 6]
    assert parse_hours_range("Freshman") is None


# ── Course records ────────────────────────────────────────────────────────────

def test_build_course_from_api_detail():
    code, course = build_course({
        "id": 32820,
        "title": "ACTG 3120 - Intermediate Accounting II",
        "body": '3 credit hours<hr>Prerequisites: <a href="#">ACTG 3110</a> and <a href="#">ACTG 4510</a>'
                " with minimum grade of C (2.0); admission into the College of Business. "
                "Continuation of Intermediate Accounting I.<br><br>",
        "course_types": [{"name": "Accounting"}],
    })
    assert code == "ACTG 3120"
    assert course["title"] == "Intermediate Accounting II"
    assert course["credits"] == 3
    assert course["prereqs"] == ["ACTG 3110", "ACTG 4510"]
    assert course["min_grades"] == {"ACTG 3110": "C", "ACTG 4510": "C"}
    assert course["desc"].startswith("Continuation of Intermediate Accounting I")
    assert course["subject"] == "Accounting"


def test_zero_credit_lab_keeps_its_zero():
    _, course = build_course({"id": 1, "title": "BIOL 1111 - General Biology I Lab", "body": "0 credit hours<hr>Corequisite: BIOL 1110 ."})
    assert course["credits"] == 0
    assert course["coreqs"] == ["BIOL 1110"]


# ── Programs ──────────────────────────────────────────────────────────────────

def test_program_items_keep_catalog_order_with_connectors():
    detail = {
        "id": 1, "legacy-id": 2, "name": "Example, B.S.",
        "program_types": [{"name": "Major"}], "degree_types": [{"name": "Bachelor of Science"}],
        "parents": [{"name": "Example Department"}], "status": {"active": True},
        "cores": [{
            "name": "Major Requirements (11 hours)", "description": "", "sort_order": 1, "children": [],
            "courses": [
                {"id": 10, "title": "EXMP 1000 - One", "sort_order": 1},
                {"id": 11, "title": "EXMP 1001 - One Lab", "sort_order": 2},
                {"id": 12, "title": "EXMP 2000 - Two", "sort_order": 3},
                {"id": 13, "title": "EXMP 2100 - Other Two", "sort_order": 4},
            ],
            "adhocs": [
                {"content": "<p>AND</p>", "course-id": 10, "placement": "after"},
                {"content": "<p>OR</p>", "course-id": 12, "placement": "after"},
                {"content": "<ul><li>Upper-division EXMP electives <strong>4 credit hours</strong></li></ul>",
                 "course-id": 13, "placement": "after"},
            ],
        }],
    }
    program = build_program(detail, {}, "https://catalog.mtsu.edu", 49)
    assert program["type"] == "Major"
    assert program["url"] == "https://catalog.mtsu.edu/preview_program.php?catoid=49&poid=2"
    section = program["sections"][0]
    assert section["hours"] == [11, 11]
    assert section["items"] == [
        {"course": "EXMP 1000"}, {"op": "AND"}, {"course": "EXMP 1001"},
        {"course": "EXMP 2000"}, {"op": "OR"}, {"course": "EXMP 2100"},
        {"text": "Upper-division EXMP electives", "hours": [4, 4]},
    ]


def test_concurrent_courses_are_corequisites_not_prerequisites():
    req = _req("Prerequisites: IDES 2100 and IDES 3200 with C or better; "
               "must be taken concurrently with IDES 3110 and IDES 3140. Studio")
    assert req["prereqs"] == ["IDES 2100", "IDES 3200"]
    assert req["coreqs"] == ["IDES 3110", "IDES 3140"]
    assert set(req["min_grades"]) == {"IDES 2100", "IDES 3200"}


def test_simultaneous_completion_only_softens_the_named_course():
    req = _req("Prerequisites: AERO 3203 and AERO 3215 or simultaneous completion of AERO 3215 . Flight")
    assert req["prereqs"] == ["AERO 3203"]
    assert req["coreqs"] == ["AERO 3215"]


def test_a_serial_comma_or_list_is_one_choice():
    # AERO 3020: "AERO 1010, AERO 1020, and MATH 1530, MATH 1710, MATH 1810,
    # or MATH 1910, and CHEM 1010, or CHEM 1110, ..." -- any one math course
    req = _req("Prerequisites: AERO 1010 , AERO 1020 , and MATH 1530 , MATH 1710 , MATH 1810 , or MATH 1910 , "
               "and CHEM 1010 , or CHEM 1110 , or PHYS 2010 , or PHYS 2110 , all with minimum grades of C (2.0). Text")
    assert req["prereqs"] == ["AERO 1010", "AERO 1020"]
    assert req["prereq_any"] == [["MATH 1530", "MATH 1710", "MATH 1810", "MATH 1910"],
                                 ["CHEM 1010", "CHEM 1110", "PHYS 2010", "PHYS 2110"]]


def test_an_or_pair_followed_by_a_comma_keeps_the_next_course_required():
    req = _req("Prerequisites: MATH 1910 or MATH 1920, CSCI 1170. Text")
    assert req["prereqs"] == ["CSCI 1170"]
    assert req["prereq_any"] == [["MATH 1910", "MATH 1920"]]
    # "or permission" isn't a course choice
    assert _req("Prerequisites: Junior standing, CSCI 3080, or permission of instructor. Text")["prereqs"] == ["CSCI 3080"]


def test_may_be_taken_concurrently_only_softens_that_course():
    req = _req("Prerequisites: CHEM 1110 and MATH 1730 (may be taken concurrently). Text")
    assert req["prereqs"] == ["CHEM 1110"]
    assert req["coreqs"] == ["MATH 1730"]


def test_a_sentence_mentioning_prerequisite_for_is_not_a_prerequisite_line():
    # "HIST 1010 is NOT a prerequisite for HIST 1020" -- the surveys go in either order
    assert parse_requisites("A survey to 1715. HIST 1010 is NOT a prerequisite for HIST 1020 . TBC: CCE")["prereqs"] == []
    assert parse_requisites("Prerequisite: For Honors Fellows only; permission of department required.")["prereq_text"]
