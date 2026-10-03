from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import app as app_module
from app import app
from mtsugradpath import scraper
from mtsugradpath.models import Base, CatalogCourseSummary


def _session_factory(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'course-search-test.db'}", future=True)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, autoflush=False, future=True)


def test_sync_course_search_index_parses_and_refreshes_all_summaries(monkeypatch, tmp_path):
    test_sessions = _session_factory(tmp_path)
    monkeypatch.setattr(scraper, "SessionLocal", test_sessions)
    monkeypatch.setattr(scraper, "CATALOG_IDS", [36])
    monkeypatch.setattr(
        scraper,
        "fetch_all_courses",
        lambda catalog_id: [
            {
                "id": 101,
                "catalog-id": catalog_id,
                "title": "ACSI 2100\u00a0\u2013\u00a0Introduction to Actuarial Science",
            },
            {"id": 102, "catalog-id": catalog_id, "title": "BIOL 1010 - General Biology"},
            {"id": 103, "catalog-id": catalog_id, "title": "Catalog category, not a course"},
        ],
    )

    assert scraper.sync_course_search_index() == 2

    with test_sessions() as session:
        rows = session.query(CatalogCourseSummary).order_by(CatalogCourseSummary.prefix).all()
        assert [(row.prefix, row.number, row.title) for row in rows] == [
            ("ACSI", "2100", "Introduction to Actuarial Science"),
            ("BIOL", "1010", "General Biology"),
        ]

    monkeypatch.setattr(scraper, "fetch_all_courses", lambda catalog_id: [
        {"id": 201, "catalog-id": catalog_id, "title": "ASTR 1030 - Introduction to Astronomy"}
    ])
    assert scraper.sync_course_search_index(force=True) == 1

    with test_sessions() as session:
        rows = session.query(CatalogCourseSummary).all()
        assert [(row.prefix, row.number) for row in rows] == [("ASTR", "1030")]


def test_planner_search_options_include_unrelated_catalog_departments(monkeypatch, tmp_path):
    test_sessions = _session_factory(tmp_path)
    monkeypatch.setattr(app_module, "SessionLocal", test_sessions)
    with test_sessions() as session:
        session.add(
            CatalogCourseSummary(
                catalog_id=36,
                course_id=301,
                prefix="ASTR",
                number="1030",
                title="Introduction to Astronomy",
            )
        )
        session.commit()

    response = app.test_client().get("/?major=cs")

    assert response.status_code == 200
    assert '"code": "ASTR 1030"' in response.get_data(as_text=True)
    assert "ASTR 1030 - Introduction to Astronomy" in response.get_data(as_text=True)
