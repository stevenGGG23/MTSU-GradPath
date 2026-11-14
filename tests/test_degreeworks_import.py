"""The Degree Works PDF import (static/degreeworks.js), run under Node.

The audit is read in the browser, so the parser is JavaScript; these tests
feed it text laid out the way pdf.js reads a real MTSU Degree Works audit
(made-up student) and check what comes out. Skipped where Node isn't installed.
"""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

MODULE = (Path(__file__).resolve().parent.parent / "static" / "degreeworks.js").as_uri()
NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(not NODE, reason="Node.js is not installed")

DW_META = {"Creator": "Ellucian Degree Works", "Author": "Middle Tennessee State University"}


def line(*cells):
    """One text line: (x, text) pairs, or bare strings spaced out."""
    return [{"x": c[0], "str": c[1]} if isinstance(c, tuple) else {"x": 40 + 60 * i, "str": c}
            for i, c in enumerate(cells)]


def row(label, code, title, grade, credits, term):
    return line((46, label), (225, code), (282, title), (431, grade), (465, credits), (483, term))


AUDIT_LINES = [
    line((57, "Middle Tennessee State University"), (444, "Raider, Test - M00000000")),
    line((28, "Student Name"), (125, "Raider, Test")),
    line((28, "Student ID"), (125, "M00000000")),
    line((28, "Degree"), (125, "Bachelor of Science")),
    line((418, "Overall GPA")),
    line((418, "3.112")),
    line((28, "Level"), (51, "Undergraduate"), (195, "Major"), (219, "Computer Science"),
         (291, "Minor"), (315, "Data Science"), (369, "Program"), (404, "BS -Computer Science - Professional")),
    line((28, "Computer Sci"), (83, "Concentration"), (140, "Professional Computer Science")),
    line((28, "BS - Bachelor of Science"), (216, "IN-PROGRESS")),
    line((28, "Credits required: 120"), (119, "Credits applied: 90"), (208, "Catalog year: 2024-2025 Fall")),
    line((28, "General Education"), (174, "COMPLETE")),
    row("Communication: Writing", "ENGL 1010", "Expository Writing", "A", "3", "Spring 2024"),
    row("HISTORY", "HIST 2010", "Survey United States History I", "B", "3", "Fall 2024"),
    line((225, "Complete 9 Hours With 3 hours from Literature: ENGL 2020, 2030, HUM 2610.")),
    line((28, "Major - Computer Science, BS"), (250, "IN-PROGRESS")),
    row("Computer Science I", "CSCI 1170", "Computer Science I", "B+", "4", "Fall 2024"),
    row("Computer Science II", "CSCI 2170", "Computer Science II", "A", "4", "Spring 2025"),
    row("Calculus II", "MATH 1920", "Calculus II", "TA", "4", "Summer 2025"),
    line((230, "Satisfied by:"), (287, "MATH1920 - Calculus - Columbia State Community Coll")),
    row("Software Engineering", "CSCI 4700", "Software Engineering", "NA", "(3)", "Fall 2026"),
    line((28, "Insufficient"), (119, "Credits applied: 3"), (197, "Classes applied: 2")),
    line((28, "Course"), (117, "Title"), (345, "Grade"), (389, "Credits"), (434, "Term")),
    line((28, "CSCI 2170"), (117, "Computer Science II"), (345, "W"), (389, "0"), (434, "Fall 2024")),
    line((28, "MATH 1910"), (117, "Calculus I"), (345, "F"), (389, "4"), (434, "Fall 2023")),
    line((28, "In-progress"), (121, "Credits applied: 6"), (200, "Classes applied: 2")),
    line((28, "CSCI 4700"), (117, "Software Engineering"), (345, "NA"), (389, "(3)"), (434, "Fall 2026")),
    line((28, "DATA 3550"), (117, "Applied Predictive Modeling"), (345, "NA"), (389, "(3)"), (434, "Fall 2026")),
    line((28, "Exceptions")),
    line((28, "Remove Course and/or"), (139, "CSCI 1170 completed in COSC major")),
    line((150, "Ellucian Degree Works - © Copyright 1995-2026 Ellucian Company L.P.")),
]


def run_js(body, **values):
    """Run *body* (an async function body with `dw` and the JSON *values* in
    scope) and return what it returns."""
    script = (
        f"import * as dw from {json.dumps(MODULE)};\n"
        + "".join(f"const {name} = {json.dumps(value)};\n" for name, value in values.items())
        + f"const out = await (async () => {{ {body} }})();\n"
        + "process.stdout.write(JSON.stringify(out));\n"
    )
    done = subprocess.run([NODE, "--input-type=module", "-e", script],
                          capture_output=True, text=True, timeout=60)
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


def parse(meta=DW_META, lines=AUDIT_LINES):
    return run_js("return dw.parseAudit({meta, lines});", meta=meta, lines=lines)


def test_reads_completed_and_in_progress_courses():
    result = parse()
    assert result["ok"]
    assert [c["code"] for c in result["completed"]] == [
        "CSCI 1170", "CSCI 2170", "ENGL 1010", "HIST 2010", "MATH 1920",
    ]
    # Transfer credit (TA) counts; in-progress classes are kept apart
    assert {c["code"]: c["grade"] for c in result["completed"]}["MATH 1920"] == "TA"
    assert [c["code"] for c in result["inProgress"]] == ["CSCI 4700", "DATA 3550"]


def test_withdrawn_and_failed_attempts_dont_count():
    result = parse()
    # CSCI 2170's W is superseded by its later A; MATH 1910's F stands
    assert [c["code"] for c in result["notCounted"]] == ["MATH 1910"]
    assert "MATH 1910" not in [c["code"] for c in result["completed"]]


def test_reads_the_students_programs():
    result = parse()
    assert result["majors"] == ["Computer Science"]
    assert result["concentrations"] == ["Professional Computer Science"]
    assert result["minors"] == ["Data Science"]
    assert result["catalogYear"] == "2024-2025"


def test_only_degree_works_pdfs_are_accepted():
    # Same text, but a PDF someone made another way (printed web page, Word, ...)
    for meta in ({"Creator": "Microsoft Word"}, {"Creator": "Skia/PDF m120"}, {}):
        assert parse(meta=meta) == {"ok": False, "error": "not-degree-works"}
    # A Degree Works PDF with no course rows
    assert parse(lines=AUDIT_LINES[:8])["error"] == "no-courses"


def test_next_term_after_the_one_in_progress():
    assert run_js("return dw.termAfter(dw.latestTerm(['Spring 2026', 'Fall 2026', 'Summer 2026']));") == {
        "season": "Spring", "year": 2027}
    assert run_js("return dw.termAfter('Spring 2027');") == {"season": "Fall", "year": 2027}
    assert run_js("return dw.termAfter('Spring 2027', true);") == {"season": "Summer", "year": 2027}


def test_matches_gradpath_programs():
    from mtsugradpath.degree_configs import display_name, picker_majors
    from mtsugradpath.minors import picker_minors

    majors = [{"key": key, "name": display_name(cfg)} for key, cfg in picker_majors()]
    minors = [{"key": key, "name": m["name"]} for key, m in picker_minors()]
    found = run_js(
        "return {"
        " conc: dw.matchMajor(majors, 'Computer Science', 'Professional Computer Science'),"
        " vague: dw.matchMajor(majors, 'Computer Science', ''),"
        " single: dw.matchMajor(majors, 'Accounting', ''),"
        " minor: dw.matchMinor(minors, 'Data Science'),"
        " none: dw.matchMinor(minors, 'Underwater Basket Weaving') };",
        majors=majors, minors=minors,
    )
    assert found["conc"]["name"] == "Computer Science, Professional Computer Science Concentration, B.S."
    # Several CS concentrations and none named: the student picks
    assert found["vague"] is None
    assert found["single"]["name"] == "Accounting, B.B.A."
    assert found["minor"]["name"] == "Data Science Minor"
    assert found["none"] is None


def test_planner_page_has_the_drop_zone():
    from app import app

    resp = app.test_client().get("/?major=cs")
    html = resp.get_data(as_text=True)
    assert 'id="dw-file" accept="application/pdf,.pdf"' in html
    assert "degreeworks.js" in html
    # pdf.js runs its worker from a blob: wrapper
    assert "worker-src 'self' blob:" in resp.headers["Content-Security-Policy"]


def test_records_which_requirement_each_course_met():
    result = parse()
    reqs = {(r["block"], r["label"]): r["codes"] for r in result["requirements"]}
    assert reqs[("General Education", "HISTORY")] == ["HIST 2010"]
    assert reqs[("Major - Computer Science, BS", "Calculus II")] == ["MATH 1920"]
    # In-progress classes are recorded against their requirement too
    assert reqs[("Major - Computer Science, BS", "Software Engineering")] == ["CSCI 4700"]
    # Nothing from the Insufficient / In-progress tables or the exceptions
    assert all(r["block"] != "Insufficient" for r in result["requirements"])
    assert [b["title"] for b in result["blocks"]] == [
        "BS - Bachelor of Science", "General Education", "Major - Computer Science, BS"]
    assert [b["status"] for b in result["blocks"]] == ["IN-PROGRESS", "COMPLETE", "IN-PROGRESS"]
    assert not result["stillNeeded"]


def test_still_needed_marks_its_block():
    at = next(i for i, l in enumerate(AUDIT_LINES) if l[0]["str"].startswith("Major - ")) + 1
    lines = AUDIT_LINES[:at] + [line((40, "Still needed:"), (225, "1 Class in CSCI 3250"))] + AUDIT_LINES[at:]
    result = parse(lines=lines)
    assert result["stillNeeded"]
    assert [b["title"] for b in result["blocks"] if b["stillNeeded"]] == ["Major - Computer Science, BS"]


def test_plan_payload_has_no_name_or_id():
    payload = run_js("const r = dw.parseAudit({meta, lines}); return dw.planPayload(r, 'cs-key', true);",
                     meta=DW_META, lines=AUDIT_LINES)
    text = json.dumps(payload)
    assert "Raider" not in text and "M00000000" not in text
    assert payload["major"] == "cs-key" and payload["count_ip"] is True
    assert {"block", "label", "context", "codes"} == set(payload["rows"][0])


def test_reads_overall_gpa_and_gpa_hours():
    result = parse()
    assert result["gpa"] == 3.112
    worked = run_js("return dw.gpaFromAttempts(dw.parseAudit({meta, lines}).attempts);", meta=DW_META, lines=AUDIT_LINES)
    # ENGL 1010 A 3, HIST 2010 B 3, CSCI 1170 B+ 4, CSCI 2170 A 4, MATH 1910 F 4:
    # transfer (TA), W and classes in progress (NA) don't count; an F does
    assert worked["hours"] == 18
    assert abs(worked["gpa"] - (3 * 4 + 3 * 3 + 4 * 3.33 + 4 * 4 + 0) / 18) < 1e-9


def test_a_retake_counts_once_at_its_latest_grade():
    attempts = [
        {"code": "MATH 1730", "grade": "D+", "credits": 4, "term": "Fall 2023"},
        {"code": "MATH 1730", "grade": "B-", "credits": 4, "term": "Spring 2024"},
        {"code": "ENGL 1010", "grade": "A", "credits": 3, "term": "Spring 2023"},
        {"code": "CSCI 1010", "grade": "P", "credits": 1, "term": "Fall 2023"},
    ]
    worked = run_js("return dw.gpaFromAttempts(attempts);", attempts=attempts)
    assert worked["hours"] == 7
    assert abs(worked["gpa"] - (4 * 2.67 + 3 * 4) / 7) < 1e-9
