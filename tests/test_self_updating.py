"""The site follows MTSU's data with no code changes: catalog edits flow into
majors, the class schedule drives offering terms, and refreshes can't
publish broken data."""
import json

import pytest

from mtsugradpath import catalog_store as store
from mtsugradpath import schedule_fetch
from mtsugradpath.degree_configs import find_degree_config, get_full_degree_config, picker_majors

CS_NAME = "Computer Science, Professional Computer Science Concentration, B.S."


def _required(cfg):
    return {c for k in ("core_courses", "concentration_courses", "supporting_courses") for c, _, _ in cfg[k]}


@pytest.fixture
def edited_catalog(tmp_path, monkeypatch):
    """A copy of the real catalog with a program edited the way MTSU might."""
    data = json.loads(store.BUNDLED_FILE.read_text(encoding="utf-8"))

    def use(edit):
        edit(data)
        path = tmp_path / "catalog.json"
        path.write_text(json.dumps(data))
        monkeypatch.setenv("GRADPATH_CATALOG_FILE", str(path))
        store.reload()

    yield use
    store.reload()


def test_dropping_calculus_from_cs_in_the_catalog_drops_it_from_the_site(edited_catalog):
    assert "MATH 1910" in _required(get_full_degree_config("cs"))

    def drop_calc(data):
        program = next(p for p in data["programs"] if p["name"] == CS_NAME)

        def strip(sections):
            for sec in sections:
                sec["items"] = [i for i in sec.get("items", []) if i.get("course") != "MATH 1910"]
                strip(sec.get("children", []))
        strip(program["sections"])

    edited_catalog(drop_calc)
    cfg = get_full_degree_config("cs")
    assert cfg["name"] == CS_NAME
    assert "MATH 1910" not in _required(cfg)
    assert "MATH 1920" in _required(cfg)


def test_a_new_program_in_the_catalog_appears_in_the_picker(edited_catalog):
    def add_program(data):
        base = next(p for p in data["programs"] if p["name"] == CS_NAME)
        data["programs"].append({**base, "id": 999999, "name": "Computer Science, Quantum Software Concentration, B.S."})

    edited_catalog(add_program)
    names = {cfg["name"] for _, cfg in picker_majors()}
    assert "Computer Science, Quantum Software Concentration, B.S." in names


def test_old_short_links_resolve_to_catalog_programs():
    for key in ("cs", "chemistry", "aerospace", "construction_mgmt", "political_science", "biology", "math", "physics"):
        cfg = find_degree_config(key)
        assert cfg is not None and cfg.get("catalog_driven"), key
    assert all(cfg.get("catalog_driven") for _, cfg in picker_majors())


# ── Offering terms from the class schedule ───────────────────────────────────

@pytest.fixture
def fake_schedule(tmp_path, monkeypatch):
    terms = [{"code": f"{y}{s}", "season": n, "year": y}
             for y in (2023, 2024, 2025, 2026) for s, n in (("10", "Spring"), ("50", "Summer"), ("80", "Fall"))]
    terms.append({"code": "202710", "season": "Spring", "year": 2027})
    filler = {f"FILL {1000 + i}": [t["code"] for t in terms] for i in range(400)}
    offered = {
        **filler,
        "AAA 1000": [t["code"] for t in terms],                                   # every term
        "BBB 2000": [t["code"] for t in terms if t["season"] == "Fall"],          # fall only
        "CCC 3000": [t["code"] for t in terms if t["season"] == "Spring" and t["year"] % 2]  # odd springs
        + [],
        "DDD 4000": [t["code"] for t in terms if t["season"] != "Summer" and t["code"] != "202710"],
        "EEE 5000": [t["code"] for t in terms if t["season"] == "Summer"],        # summer only
        "NEW 1000": ["202680"],                                                    # new this fall
    }
    path = tmp_path / "schedule.json"
    path.write_text(json.dumps({"fetched_at": "2026-10-20T00:00:00", "terms": terms, "offered": offered}))
    monkeypatch.setattr(store, "SCHEDULE_FILE", path)
    store._sched.update(mtime=None, data=None, checked=0.0)
    yield
    store._sched.update(mtime=None, data=None, checked=0.0)


def test_offering_patterns_from_the_schedule(fake_schedule):
    assert store.offered_in("AAA 1000", "Summer", 2028) is True
    assert store.offered_in("BBB 2000", "Fall", 2028) is True
    assert store.offered_in("BBB 2000", "Spring", 2028) is False
    assert store.offered_in("CCC 3000", "Spring", 2029) is True
    assert store.offered_in("CCC 3000", "Spring", 2028) is False
    # Spring 2027 is posted: exact
    assert store.offered_in("DDD 4000", "Spring", 2027) is False
    assert store.offered_in("DDD 4000", "Spring", 2028) is True
    assert store.summer_only("EEE 5000") and not store.summer_only("AAA 1000")
    # One sighting isn't a pattern: a new course isn't ruled out of spring
    assert store.offered_in("NEW 1000", "Fall", 2027) is True
    assert store.offered_in("NEW 1000", "Spring", 2028) is None
    assert store.offered_in("ZZZ 9999", "Fall", 2027) is None
    assert store.offering_summary("BBB 2000") == "Fall only"
    assert store.offering_summary("CCC 3000") == "Odd-year springs"
    assert store.offering_summary("AAA 1000") == ""


def test_planner_never_places_a_course_in_a_term_it_does_not_run(fake_schedule):
    from mtsugradpath.planner import _offered_in_term
    from mtsugradpath.degree_configs import CS_CONFIG
    assert _offered_in_term("BBB 2000", "Spring", 2028, CS_CONFIG) is False
    assert _offered_in_term("CCC 3000", "Spring", 2028, CS_CONFIG) is False
    # No history and no team data: fall/spring yes, summer no
    assert _offered_in_term("ZZZ 9999", "Fall", 2027, CS_CONFIG) is True
    assert _offered_in_term("ZZZ 9999", "Summer", 2027, CS_CONFIG) is False


def test_schedule_page_parsing(monkeypatch):
    page = (
        '<th CLASS="ddtitle" scope="colgroup"><a href="x">Computer Science I - 80266 - CSCI 1170 - 001</a></th>'
        '<th CLASS="ddtitle" scope="colgroup"><a href="x">Computer Science I - 80267 - CSCI 1170 - 002</a></th>'
        '<th CLASS="ddtitle" scope="colgroup"><a href="x">Topics &amp; Ideas - 81000 - ENGL 4999H - 001</a></th>'
    )
    monkeypatch.setattr(schedule_fetch, "_subjects", lambda term: ["CSCI", "ENGL"])
    monkeypatch.setattr(schedule_fetch, "_post", lambda url, data, retries=4: page)
    assert schedule_fetch.term_courses("202680") == {"CSCI 1170", "ENGL 4999H"}


def test_refresh_refuses_to_publish_a_shrunken_catalog(tmp_path, monkeypatch):
    import scrape_courses
    out = tmp_path / "catalog.json"
    out.write_text(json.dumps({"catalog": {}, "courses": {f"C {i}": {} for i in range(3000)},
                               "programs": [{}] * 400}))
    small = {"catalog": {"name": "x"}, "courses": {f"C {i}": {} for i in range(2100)}, "programs": [{"name": "p"}] * 390}
    monkeypatch.setattr(scrape_courses, "build_catalog", lambda raw_dir: small)
    with pytest.raises(RuntimeError, match="shrank"):
        scrape_courses.export(out_file=out, raw_dir=tmp_path, fetch=False)
    assert len(json.loads(out.read_text())["courses"]) == 3000  # untouched
