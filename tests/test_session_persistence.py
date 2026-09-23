"""Regression tests: the planner must not remember a visitor's previous
selections. It used to stash completed courses, generic-requirement hours,
term settings, and the chosen major in the Flask session and silently
restore them on the next page load, so re-opening the same link later (same
browser) showed whatever the last person to use it had entered. The planner
must come back blank every time, with the major reset to the CS default
unless the URL itself says otherwise.
"""
import re

from app import app


def _checked_major(html: str) -> str:
    match = re.search(
        r'<input type="radio" name="major" value="([^"]+)"\s+class="major-radio"\s+checked',
        html,
    )
    return match.group(1) if match else None


def test_major_choice_does_not_persist_across_requests():
    client = app.test_client()

    resp = client.post(
        "/",
        data={
            "major": "math",
            "completed_courses": "MATH 1910",
            "target_semesters": "4",
            "start_season": "Fall",
            "start_year": "2026",
        },
    )
    assert resp.status_code == 200

    # A fresh visit with no ?major= in the URL must default back to CS, not
    # remember the "math" choice submitted above.
    resp2 = client.get("/")
    assert _checked_major(resp2.get_data(as_text=True)) == "cs"


def test_completed_courses_are_not_echoed_back_on_reload():
    client = app.test_client()

    client.post(
        "/",
        data={
            "major": "cs",
            "completed_courses": "CSCI 1170\nCSCI 2170",
            "target_semesters": "4",
            "start_season": "Fall",
            "start_year": "2026",
        },
    )

    resp = client.get("/")
    html = resp.get_data(as_text=True)
    # The old code embedded previously completed courses as a JS array
    # (savedCompletedCourses) that pre-checked boxes on load; that array must
    # no longer exist at all.
    assert "savedCompletedCourses" not in html


def test_no_planner_state_cookie_is_set():
    client = app.test_client()

    resp = client.post(
        "/",
        data={
            "major": "cs",
            "completed_courses": "CSCI 1170",
            "target_semesters": "4",
            "start_season": "Fall",
            "start_year": "2026",
        },
    )
    # No session cookie should be issued for planner selections at all --
    # nothing server-side is stored to restore later.
    set_cookie_headers = resp.headers.get_all("Set-Cookie")
    assert not any("session=" in h for h in set_cookie_headers)
