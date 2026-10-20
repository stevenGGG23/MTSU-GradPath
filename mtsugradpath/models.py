"""SQLAlchemy ORM models.

Course and program data no longer lives in the database -- it ships as
catalog_data/catalog.json (see catalog_store.py). The database only holds
small shared state: catalog-export progress and course equivalencies.
Tables from the old scraper (courses, prerequisites, ...) may still exist in
older databases; nothing reads them.
"""

from sqlalchemy import Boolean, Column, Float, Integer, String, Text
from sqlalchemy.orm import declarative_base

Base = declarative_base()


class SyncStatus(Base):
    """Single-row table tracking catalog re-export progress.

    Stored in the DB (not a process-local dict) so status is consistent
    when more than one gunicorn worker is running — whichever worker started
    the sync writes here, and any worker can answer /sync/status.
    """
    __tablename__ = "sync_status"

    id = Column(Integer, primary_key=True, default=1)
    running = Column(Boolean, default=False)
    total = Column(Integer, default=0)
    fresh = Column(Integer, default=0)
    cached = Column(Integer, default=0)
    errors = Column(Text, default="")  # newline-joined error messages
    done = Column(Boolean, default=False)
    # Unix timestamp (time.time()) set when a sync starts.  Used to detect
    # and recover from a lock left behind by a sync whose worker thread was
    # killed mid-run (deploy, OOM restart) before it could set running=False.
    started_at = Column(Float, nullable=True)


class CourseEquivalency(Base):
    """Maps an old or alternate course code to its current equivalent."""
    __tablename__ = "course_equivalencies"

    id = Column(Integer, primary_key=True)
    old_course_code = Column(String(32), nullable=False)
    new_course_code = Column(String(32), nullable=False)
    notes = Column(Text)
