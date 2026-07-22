from sqlalchemy import Boolean, Column, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import declarative_base, relationship

Base = declarative_base()


class Course(Base):
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

    course_types = relationship("CourseType", secondary="course_course_type", back_populates="courses")
    prerequisites = relationship("Prerequisite", back_populates="course", cascade="all, delete-orphan")


class CourseType(Base):
    __tablename__ = "course_types"

    id = Column(Integer, primary_key=True)
    legacy_id = Column(Integer, unique=True, nullable=False)
    catalog_id = Column(Integer, nullable=False)
    name = Column(String(256))
    category = Column(String(64))
    visible = Column(Boolean, default=True)

    courses = relationship("Course", secondary="course_course_type", back_populates="course_types")


class CourseCourseType(Base):
    __tablename__ = "course_course_type"

    course_id = Column(Integer, ForeignKey("courses.id", ondelete="CASCADE"), primary_key=True)
    course_type_id = Column(Integer, ForeignKey("course_types.id", ondelete="CASCADE"), primary_key=True)


class Prerequisite(Base):
    __tablename__ = "prerequisites"

    id = Column(Integer, primary_key=True)
    course_id = Column(Integer, ForeignKey("courses.id", ondelete="CASCADE"), nullable=False)
    prerequisite_text = Column(Text)

    course = relationship("Course", back_populates="prerequisites")
