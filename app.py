from flask import Flask, render_template, request, redirect, url_for, flash
from mtsugradpath.config import PROGRAM_PREFIX
from mtsugradpath.db import init_db, SessionLocal
from mtsugradpath.degree import (
    SUPPORTING_COURSES,
    SUPPORTING_GENERIC,
    TBC_GENERIC,
    ELECTIVES_GENERIC_ID,
    elective_required_hours,
    build_audit,
)
from mtsugradpath.models import Course
from mtsugradpath.planner import (
    generate_plan,
    load_catalog_courses,
    default_start_season,
    SEASON_CYCLE,
    validate_plan,
    build_prereq_graph,
    render_prereq_mermaid,
)
from mtsugradpath.scraper import sync_courses
from datetime import date

app = Flask(__name__)
app.config["SECRET_KEY"] = "change-this-in-production"

with app.app_context():
    init_db()


def credit_label(hours):
    if not hours:
        return ""
    hours_value = int(hours) if float(hours).is_integer() else hours
    unit = "credit hour" if hours_value == 1 else "credit hours"
    return f"{hours_value} {unit}"


def credit_short(hours):
    if not hours:
        return ""
    hours_value = int(hours) if float(hours).is_integer() else hours
    return f"{hours_value} cr"


def read_generic_hours(form):
    generic_hours = {}
    for generic_id, _, hours, _ in SUPPORTING_GENERIC + TBC_GENERIC:
        generic_hours[generic_id] = form.get(f"hours_{generic_id}", 0)
    generic_hours[ELECTIVES_GENERIC_ID] = form.get(f"hours_{ELECTIVES_GENERIC_ID}", 0)
    return generic_hours


@app.route("/", methods=["GET", "POST"])
def index():
    with SessionLocal() as session:
        course_list = (
            session.query(Course)
            .filter(Course.prefix == PROGRAM_PREFIX)
            .order_by(Course.prefix, Course.number)
            .all()
        )
        cs_courses = [
            {
                "code": f"{course.prefix} {course.number}",
                "label": f"{course.prefix} {course.number} - {course.title}",
                "credits": course.credits or 0,
                "level": f"{course.number[0]}000-Level",
            }
            for course in course_list
            if course.prefix and course.number
        ]
        cs_courses.sort(key=lambda c: int(c["code"].split()[1]))

    courses = list(cs_courses)
    for code, hours, title in SUPPORTING_COURSES:
        courses.append({"code": code, "label": f"{code} - {title}", "credits": hours})
    courses.sort(key=lambda c: c["code"])

    if request.method == "POST":
        completed_text = request.form.get("completed_courses", "")
        completed_courses = {
            code.strip().upper()
            for code in completed_text.splitlines()
            if code.strip()
        }
        generic_hours = read_generic_hours(request.form)
        target_semesters = int(request.form.get("target_semesters", 4))
        include_summer = bool(request.form.get("include_summer"))
        start_season = request.form.get("start_season") or default_start_season()
        start_year = int(request.form.get("start_year") or date.today().year)

        plan = generate_plan(
            completed_courses, generic_hours, target_semesters, include_summer,
            start_season=start_season, start_year=start_year,
        )
        catalog = load_catalog_courses()
        audit = build_audit(completed_courses, generic_hours, catalog)
        prereq_warnings = validate_plan(plan, completed_courses, catalog)

        course_map = {course["code"]: course for course in courses}

        def warning_label(code):
            course = course_map.get(code)
            return course["label"] if course else code

        warnings_display = [
            {
                "course": warning_label(w["course"]),
                "prereq": warning_label(w["prereq"]),
                "term": w["term"],
                "type": w["type"],
            }
            for w in prereq_warnings
        ]

        def completed_label(code):
            course = course_map.get(code)
            if course:
                hours = credit_label(course["credits"])
                return f"{course['label']} — {hours}" if hours else course["label"]
            return code

        def display_item(item):
            kind = item["kind"]
            if kind == "course":
                title = item["label"].split(" - ", 1)[1] if " - " in item["label"] else ""
                return {
                    "kind": kind,
                    "code": item["code"],
                    "title": title,
                    "hours": credit_short(item["hours"]),
                }
            if kind == "requirement":
                return {
                    "kind": kind,
                    "code": None,
                    "title": item["label"],
                    "hours": credit_short(item["hours"]),
                    "suggestion": item.get("suggestion"),
                }
            return {"kind": kind, "code": None, "title": item["label"], "hours": ""}

        completed_display = [completed_label(code) for code in sorted(completed_courses)]
        plan_display = {
            term: [display_item(item) for item in term_items]
            for term, term_items in plan.items()
        }
        term_hours_display = {
            term: sum(item.get("hours", 0) for item in term_items)
            for term, term_items in plan.items()
            if not term.startswith("Remaining")
        }
        term_hours_display = {
            term: (int(hours) if float(hours).is_integer() else hours)
            for term, hours in term_hours_display.items()
        }
        max_term_hours = max(term_hours_display.values()) if term_hours_display else 0

        return render_template(
            "plan.html",
            completed=completed_display,
            plan=plan_display,
            semesters=target_semesters,
            include_summer=include_summer,
            audit=audit,
            term_hours=term_hours_display,
            max_term_hours=max_term_hours,
            warnings=warnings_display,
        )

    return render_template(
        "index.html",
        courses=courses,
        cs_courses=cs_courses,
        supporting_generic=SUPPORTING_GENERIC,
        tbc_generic=TBC_GENERIC,
        electives_generic_id=ELECTIVES_GENERIC_ID,
        electives_hours=elective_required_hours(),
        season_options=SEASON_CYCLE,
        default_season=default_start_season(),
        default_year=date.today().year,
    )


@app.route("/prerequisites")
def prerequisites():
    catalog = load_catalog_courses()
    nodes, edges = build_prereq_graph(catalog)
    mermaid_source = render_prereq_mermaid(nodes, edges)
    return render_template("prereqs.html", mermaid_source=mermaid_source)


@app.route("/sync")
def sync():
    try:
        count = sync_courses()
        flash(f"Catalog synced successfully — {count} CSCI courses updated.", "success")
    except Exception as exc:
        flash(f"Catalog sync failed: {exc}", "danger")
    return redirect(url_for("index"))


if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=5000)
