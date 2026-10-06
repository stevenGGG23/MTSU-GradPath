"""SQLAlchemy ORM models for the MTSU catalog database."""

from sqlalchemy import (
    Boolean,
    Column,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import declarative_base, relationship

Base = declarative_base()


class SyncStatus(Base):
    """Single-row table tracking catalog sync progress.

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


class Course(Base):
    """One course from the MTSU catalog."""
    __tablename__ = "courses"

    id = Column(Integer, primary_key=True)
    legacy_id = Column(Integer, unique=True, nullable=False)
    catalog_id = Column(Integer, nullable=False)
    prefix = Column(String(16))
    number = Column(String(16))
    title = Column(String(256))
    credits = Column(Float)
    body = Column(Text)
    url = Column(String(512))
    updated_at = Column(String(64))

    # A course may belong to multiple category types (e.g. Lecture, Lab)
    course_types = relationship(
        "CourseType",
        secondary="course_course_type",
        back_populates="courses",
    )

    # Deleting a course also removes its stored prerequisite rows
    prerequisites = relationship(
        "Prerequisite",
        back_populates="course",
        cascade="all, delete-orphan",
    )


class CatalogCourseSummary(Base):
    """Lightweight catalog entry used to search courses outside planned majors."""
    __tablename__ = "catalog_course_summaries"

    catalog_id = Column(Integer, primary_key=True)
    course_id = Column(Integer, primary_key=True)
    prefix = Column(String(16), nullable=False)
    number = Column(String(16), nullable=False)
    title = Column(String(256), nullable=False)


class CourseType(Base):
    """A category tag assigned to one or more courses (e.g. Lecture, Lab)."""
    __tablename__ = "course_types"

    id = Column(Integer, primary_key=True)
    legacy_id = Column(Integer, unique=True, nullable=False)
    catalog_id = Column(Integer, nullable=False)
    name = Column(String(256))
    category = Column(String(64))
    visible = Column(Boolean, default=True)

    # Reverse side of the many-to-many relationship with Course
    courses = relationship(
        "Course",
        secondary="course_course_type",
        back_populates="course_types",
    )


class CourseCourseType(Base):
    """Bridge table for the Course ↔ CourseType many-to-many relationship."""
    __tablename__ = "course_course_type"

    course_id = Column(
        Integer,
        ForeignKey("courses.id", ondelete="CASCADE"),
        primary_key=True,
    )
    course_type_id = Column(
        Integer,
        ForeignKey("course_types.id", ondelete="CASCADE"),
        primary_key=True,
    )


class Prerequisite(Base):
    """Raw prerequisite text extracted from a course's catalog description."""
    __tablename__ = "prerequisites"

    id = Column(Integer, primary_key=True)
    course_id = Column(
        Integer,
        ForeignKey("courses.id", ondelete="CASCADE"),
        nullable=False,
    )
    prerequisite_text = Column(Text)

    # Back-reference to the owning course
    course = relationship("Course", back_populates="prerequisites")


class CourseEquivalency(Base):
    """Maps an old or alternate course code to its current equivalent."""
    __tablename__ = "course_equivalencies"

    id = Column(Integer, primary_key=True)
    old_course_code = Column(String(32), nullable=False)
    new_course_code = Column(String(32), nullable=False)
    notes = Column(Text)
