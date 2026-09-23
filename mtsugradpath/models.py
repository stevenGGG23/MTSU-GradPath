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

    Lives in the DB (not a process-local dict) so status is consistent when
    more than one gunicorn worker is running -- whichever worker started the
    sync writes here, and any worker can answer /sync/status.
    """
    __tablename__ = "sync_status"

    id = Column(Integer, primary_key=True, default=1)
    running = Column(Boolean, default=False)
    total = Column(Integer, default=0)
    fresh = Column(Integer, default=0)
    cached = Column(Integer, default=0)
    errors = Column(Text, default="")  # newline-joined error messages
    done = Column(Boolean, default=False)
    # Unix timestamp (time.time()) set when a sync starts. Lets /sync detect
    # and recover from a lock left behind by a sync that never got to set
    # running=False -- e.g. the worker thread running it was killed by a
    # deploy or an out-of-memory restart mid-sync. Without this, that row
    # stays running=True forever and every future "Sync Catalog" click just
    # reports "still running" without ever starting a new sync.
    started_at = Column(Float, nullable=True)

class Course(Base):
    """Represents one course from the MTSU catalog."""
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

    # Courses may belong to multiple types
    course_types = relationship(
        "CourseType", 
        secondary="course_course_type", 
        back_populates="courses"
    )

    # Removing a course also removes the stored records
    prerequisites = relationship(
        "Prerequisite", 
        back_populates="course", 
        cascade="all, delete-orphan"
    )


class CourseType(Base):
    """Represents a catagory type assigned to a course"""
    __tablename__ = "course_types"

    id = Column(Integer, primary_key=True)
    legacy_id = Column(Integer, unique=True, nullable=False)
    catalog_id = Column(Integer, nullable=False)
    name = Column(String(256))
    category = Column(String(64))
    visible = Column(Boolean, default=True)

    # Reverse side of the many-tomany relationships
    courses = relationship(
        "Course", 
        secondary="course_course_type", 
        back_populates="course_types"
    )


class CourseCourseType(Base):
    """Bridges tables with course types"""
    __tablename__ = "course_course_type"

    course_id = Column(
        Integer, 
        ForeignKey("courses.id", ondelete="CASCADE"), 
        primary_key=True
    )

    course_type_id = Column(
        Integer, 
        ForeignKey("course_types.id", ondelete="CASCADE"), 
        primary_key=True
    )


class Prerequisite(Base):
    """Stores prerequisite text associated with a course"""
    __tablename__ = "prerequisites"

    id = Column(Integer, primary_key=True)

    course_id = Column(
        Integer, 
        ForeignKey("courses.id", ondelete="CASCADE"), 
        nullable=False
    )

    prerequisite_text = Column(Text)

    # Connects the records back to its course
    course = relationship(
        "Course", 
        back_populates="prerequisites"
    )
