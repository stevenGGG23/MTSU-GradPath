"""Production hardening: headers, input limits, bad input, admin routes."""
import json
import re

from app import app

PLAN = {"major": "cs", "target_semesters": "4", "start_season": "Fall", "start_year": "2026"}


def client():
    return app.test_client()


def test_security_headers_and_csp_nonce():
    resp = client().get("/")
    h = resp.headers
    assert h["X-Content-Type-Options"] == "nosniff"
    assert h["X-Frame-Options"] == "DENY"
    assert h["Referrer-Policy"] == "strict-origin-when-cross-origin"
    csp = h["Content-Security-Policy"]
    nonce = re.search(r"'nonce-([^']+)'", csp).group(1)
    html = resp.get_data(as_text=True)
    # Every inline script carries this request's nonce
    inline = re.findall(r"<script(?![^>]*\bsrc=)([^>]*)>", html)
    assert inline and all(f'nonce="{nonce}"' in attrs for attrs in inline)
    assert "onchange=" not in client().get("/prerequisites").get_data(as_text=True)


def test_nonce_changes_per_request():
    a = client().get("/").headers["Content-Security-Policy"]
    b = client().get("/").headers["Content-Security-Policy"]
    assert a != b


def test_oversized_form_is_rejected_politely():
    resp = client().post("/", data={**PLAN, "completed_courses": "ZZZZ 9999\n" * 20000})
    assert resp.status_code == 413
    assert "too much at once" in resp.get_data(as_text=True)


def test_junk_course_lines_are_ignored():
    resp = client().post("/", data={**PLAN, "completed_courses": "<script>alert(1)</script>\nCSCI 1170\nnot a course"})
    html = resp.get_data(as_text=True)
    assert resp.status_code == 200
    assert "<script>alert(1)</script>" not in html
    assert "CSCI 1170" in html


def test_malformed_grades_never_500():
    for grades in ['{"CSCI 1170": 5}', '{"CSCI 1170": {"a": 1}}', "[" * 5000, '"text"', '{"CSCI 1170": "Z"}', "x" * 9000]:
        resp = client().post("/", data={**PLAN, "completed_courses": "CSCI 1170", "course_grades": grades})
        assert resp.status_code == 200, grades[:20]


def test_too_many_minors_are_capped():
    minors = ["math", "accounting-minor", "biology-minor", "psychology-minor", "history-minor", "chemistry-minor"]
    html = client().post("/", data={**PLAN, "minor": minors}).get_data(as_text=True)
    assert html.count("extra-progress-card extra-progress-minor") == 4


def test_sync_is_post_only_and_fails_closed(monkeypatch):
    monkeypatch.delenv("ALLOW_OPEN_SYNC", raising=False)
    assert client().get("/sync").status_code == 405
    assert client().post("/sync", headers={"X-Requested-With": "test"}).status_code == 401


def test_sync_requires_a_custom_header(monkeypatch):
    monkeypatch.setenv("ALLOW_OPEN_SYNC", "1")
    resp = client().post("/sync")
    assert resp.status_code == 400  # a cross-site form or <img> can't add the header


def test_courses_json_conditional_requests():
    first = client().get("/courses.json")
    etag = first.headers["ETag"]
    assert client().get("/courses.json", headers={"If-None-Match": etag}).status_code == 304
    assert client().get("/courses.json", headers={"If-None-Match": "W/" + etag}).status_code == 304
    assert json.loads(first.data)


def test_gzip_respects_q_zero():
    resp = client().get("/", headers={"Accept-Encoding": "gzip;q=0, identity"})
    assert "Content-Encoding" not in resp.headers
    assert "Accept-Encoding" in resp.headers.get("Vary", "")


def test_static_css_is_versioned():
    html = client().get("/").get_data(as_text=True)
    assert re.search(r'href="/static/styles\.css\?v=\d+"', html)


def test_rate_limited_page_still_has_a_valid_csp(monkeypatch):
    from app import limiter
    monkeypatch.setattr(limiter, "enabled", True)
    c = client()
    resp = None
    for _ in range(80):
        resp = c.get("/prerequisites", environ_base={"REMOTE_ADDR": "203.0.113.9"})
        if resp.status_code == 429:
            break
    assert resp.status_code == 429
    csp = resp.headers["Content-Security-Policy"]
    nonce = re.search(r"'nonce-([^']+)'", csp).group(1)
    assert nonce and f'nonce="{nonce}"' in resp.get_data(as_text=True)
