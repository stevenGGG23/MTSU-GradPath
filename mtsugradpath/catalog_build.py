"""Turn raw Acalog API responses into GradPath's catalog JSON.

Input:  the raw directory written by catalog_fetch.fetch_raw()
Output: one JSON document (see build_catalog) that the app loads at startup.

The output keeps only what GradPath needs, already cleaned:

  courses[code]  title, credits, prerequisite/corequisite codes, minimum
                 grades, and a short description
  programs[]     every major/minor/certificate with its requirement sections
                 in catalog order -- courses plus the OR/AND connectors and
                 free-text lines ("CSCI upper-division electives 9 credit
                 hours") exactly as the catalog lists them

Turning a program's sections into a planner config happens at load time in
catalog_programs.py, so conversion rules can improve without a re-export.
"""

from __future__ import annotations

import json
import re
from html import unescape
from pathlib import Path

CODE_RE = re.compile(r"\b([A-Z]{2,4})\s*(\d{4}[A-Z]?)\b")
_HOURS_IN_NAME_RE = re.compile(r"\((\d+(?:\.\d+)?)(?:\s*(?:-|–|to)\s*(\d+(?:\.\d+)?))?\s*(?:credit\s*)?hours?\b", re.I)
# "6 credit hours", "3 to 4 credit hours", "6 upper-division credit hours"
_CREDIT_LINE_RE = re.compile(
    r"(\d+(?:\.\d+)?)(?:\s*(?:-|–|to)\s*(\d+(?:\.\d+)?))?\s*(?:(?!credit)[A-Za-z/-]+\s+){0,2}?credit hours?",
    re.I,
)
# Grade letters are matched case-sensitively so "with a minimum" isn't an A
_GRADE_RE = re.compile(
    r"(?:[Mm]inimum grades? of|[Gg]rades? of|[Ww]ith)\s+(?:an?\s+)?([ABCD][+-]?)(?![A-Za-z])"
    r"|(?<![A-Za-z])([ABCD][+-]?)(?:\s*\(\d\.\d+\))?\s+or\s+(?:better|higher|greater)\b"
)
_PAIR_RE = re.compile(r"\b([A-Z]{2,4}\s*\d{4}[A-Z]?)\s*/\s*[A-Z]{2,4}\s*\d{4}[A-Z]?\b")


def html_to_text(html: str | None) -> str:
    if not html:
        return ""
    text = re.sub(r"<\s*(br|/p|/li|/div|hr)\s*/?>", "\n", html, flags=re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    text = unescape(text).replace(" ", " ")
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in text.split("\n")]
    return "\n".join(line for line in lines if line)


def one_line(html: str | None) -> str:
    return re.sub(r"\s+", " ", html_to_text(html)).strip()


def _num(value: str) -> float | int:
    f = float(value)
    return int(f) if f.is_integer() else f


def parse_hours_range(text: str, pattern=_HOURS_IN_NAME_RE):
    """'(31-32 hours)' -> (31, 32); '(18 hours)' -> (18, 18); none -> None."""
    m = pattern.search(text or "")
    if not m:
        return None
    lo = _num(m.group(1))
    hi = _num(m.group(2)) if m.group(2) else lo
    return [lo, hi]


# ── Courses ──────────────────────────────────────────────────────────────────

def _split_title(title: str):
    title = (title or "").replace(" ", " ").strip()
    m = re.match(r"^([A-Z]{2,4})\s*(\d{4}[A-Z]?)\s*[-–]?\s*(.*)$", title)
    if not m:
        return None, title
    return f"{m.group(1)} {m.group(2)}", m.group(3).strip()


def _codes_in(text: str) -> list[str]:
    """Course codes in order, expanding bare numbers that follow a code
    ('MATH 1910 or 1920' -> MATH 1910, MATH 1920)."""
    codes: list[str] = []
    last_prefix = None
    last_end = 0
    for m in re.finditer(r"\b([A-Z]{2,4})\s*(\d{4}[A-Z]?)\b|\b(\d{4})\b", text):
        if m.group(1):
            last_prefix = m.group(1)
            codes.append(f"{m.group(1)} {m.group(2)}")
        elif last_prefix and re.fullmatch(r"\s*(?:,|/|or|and|,\s*or|,\s*and)?\s*", text[last_end:m.start()]):
            codes.append(f"{last_prefix} {m.group(3)}")
        else:
            continue
        last_end = m.end()
    seen, out = set(), []
    for c in codes:
        if c not in seen:
            seen.add(c)
            out.append(c)
    return out


def _clause(text: str, label_re: str) -> str:
    """The sentence after 'Prerequisite(s):' (or a corequisite label)."""
    m = re.search("(?i:" + label_re + r")\s*:?\s*(.+?)(?:(?<![A-Z(\d])\.(?!\d)|$)", text, re.S)
    return re.sub(r"\s+", " ", m.group(1)).strip() if m else ""


def parse_requisites(text: str) -> dict:
    """Split a course description into prerequisite/corequisite structure.

    prereqs     codes that are all required
    prereq_any  groups where any one code is enough ("MATH 1730 or 1810")
    coreqs      codes that may be taken in the same term
    min_grades  {code: "C"} when the clause names a minimum grade
    """
    # "BIOL 3250 / BIOL 3251" is a lecture/lab pair -- the lecture stands for both
    text = _PAIR_RE.sub(r"\1", text)
    prereq_text = _clause(text, r"\bPrerequisites?(?!\s+or)")
    coreq_text = _clause(text, r"\b(?:Pre-?\s*or\s*corequisites?|Corequisites?)")
    # "Prerequisites: X; corequisite: Y" -- the coreq part isn't a prereq
    prereq_text = re.split(r";?\s*\b(?:pre-?\s*or\s*)?co-?requisites?\b", prereq_text, flags=re.I)[0]

    # "...; must be taken concurrently with X and Y" / "X (may be taken
    # concurrently)": same-term courses, not ones that must come first
    concurrent = re.compile(r"concurrent|simultaneous", re.I)
    hard_parts, soft_codes = [], []
    for segment in re.split(r";", prereq_text):
        m = re.search(r"(?:must be\s+)?taken concurrently with|concurrent(?:ly)? enrollment in|concurrently with", segment, re.I)
        if m:
            hard_parts.append(segment[: m.start()])
            soft_codes += _codes_in(segment[m.end():])
        elif concurrent.search(segment):
            if re.search(r"\b(both|all|each|either)\b[^.;]*(concurrent|simultaneous)", segment, re.I):
                soft_codes += _codes_in(segment)
            else:
                # "AERO 3203 and AERO 3215 or simultaneous completion of AERO 3215"
                k = concurrent.search(segment).start()
                hard_parts.append(segment[:k])
                soft_codes += _codes_in(segment[k:])
        else:
            hard_parts.append(segment)
    prereq_text_hard = ";".join(hard_parts)

    all_of: list[str] = []
    any_of: list[list[str]] = []
    for part in re.split(r";|,?\s+\band\b\s+|,\s+(?=[A-Z]{2,4}\s*\d{4})", prereq_text_hard):
        codes = _codes_in(part)
        if not codes:
            continue
        if len(codes) > 1 and re.search(r"\bor\b", part, re.I):
            any_of.append(codes)
        else:
            all_of.extend(c for c in codes if c not in all_of)

    all_of = [c for c in all_of if c not in soft_codes]
    any_of = [g for g in any_of if not set(g) & set(soft_codes)]

    grades: dict[str, str] = {}
    gm = _GRADE_RE.search(prereq_text)
    if gm:
        grade = (gm.group(1) or gm.group(2)).upper()
        for code in _codes_in(prereq_text_hard):
            if code not in soft_codes:
                grades[code] = grade

    coreqs = [c for c in dict.fromkeys(_codes_in(coreq_text) + soft_codes) if c not in all_of]
    return {
        "prereq_text": prereq_text,
        "prereqs": all_of,
        "prereq_any": any_of,
        "coreqs": coreqs,
        "min_grades": grades,
    }


def build_course(detail: dict) -> tuple[str | None, dict]:
    code, title = _split_title(detail.get("title") or "")
    if not code:
        return None, {}
    body_html = detail.get("body") or ""
    head, _, rest = body_html.partition("<hr>")
    head_text = one_line(head)
    rest_text = one_line(rest) if rest else one_line(body_html)

    credits = None
    rng = parse_hours_range(head_text, _CREDIT_LINE_RE) or parse_hours_range(rest_text, _CREDIT_LINE_RE)
    if rng:
        # Variable credit ("1 to 12 credit hours", internships): count a
        # typical 3 when that's in range, else the minimum -- never the max
        credits = rng[0] if rng[0] == rng[1] else (3 if rng[0] <= 3 <= rng[1] else rng[0])

    req = parse_requisites(rest_text)
    req["prereqs"] = [c for c in req["prereqs"] if c != code]
    req["prereq_any"] = [[c for c in g if c != code] for g in req["prereq_any"] if len(set(g) - {code}) > 1]

    # Description without the requisite sentences, trimmed for tooltips
    desc = re.sub(r"(?i:Pre-?\s*or\s*co-?requisites?|Prerequisites?|Corequisites?)\s*:.*?(?:(?<![A-Z(\d])\.(?!\d)|$)", "", rest_text)
    desc = re.sub(r"\s+", " ", desc).strip()
    if len(desc) > 320:
        desc = desc[:317].rsplit(" ", 1)[0] + "…"

    subject = next((t.get("name") for t in detail.get("course_types") or [] if t.get("name")), None)
    course = {
        "id": detail.get("id"),
        "title": title,
        "credits": credits,
        "credits_text": head_text if rng and rng[0] != rng[1] else None,
        **{k: v for k, v in req.items() if v},
        "desc": desc or None,
        "subject": subject,
    }
    return code, {k: v for k, v in course.items() if v not in (None, "", [], {})}


# ── Programs ─────────────────────────────────────────────────────────────────

def _section_items(core: dict, course_code_by_id: dict) -> list[dict]:
    """Courses and adhoc lines in display order.

    Acalog attaches each adhoc (an "OR", "AND", a note, or an elective line)
    to a course with placement before/after, so they're spliced in there.
    """
    courses = sorted(core.get("courses") or [], key=lambda c: c.get("sort_order") or 0)
    before: dict[int, list] = {}
    after: dict[int, list] = {}
    loose: list = []
    for adhoc in core.get("adhocs") or []:
        target = adhoc.get("course-id")
        bucket = before if adhoc.get("placement") == "before" else after
        if target is not None and any(c["id"] == target for c in courses):
            bucket.setdefault(target, []).append(adhoc)
        else:
            loose.append(adhoc)
    # "right" sits inline next to its course (e.g. "MATH 1920 ... OR"), so it
    # comes before anything placed on the lines "after" it
    for adhocs in after.values():
        adhocs.sort(key=lambda a: a.get("placement") != "right")

    def adhoc_items(adhoc):
        text = html_to_text(adhoc.get("content"))
        out = []
        if not text:
            # A blank line on the catalog page: it separates groups, so an
            # "OR"/"AND" before it doesn't join across it (the catalog has a
            # few stray trailing connectors, e.g. "BIOL 4351 OR" + blank line)
            return [{"sep": True}] if adhoc.get("placement") != "before" else out
        # An "OR"/"AND" connector, possibly trailing a footnote marker or
        # gen-ed tag: "(Sci Lit) AND", "§ OR"
        m = re.match(r"^(?P<note>.*?)\s*\b(?P<op>OR|AND)\s*$", text.replace("\n", " "))
        if m and len(m.group("note")) <= 60 and not _CREDIT_LINE_RE.search(m.group("note")):
            note = m.group("note").strip()
            if note:
                out.append({"note": note})
            out.append({"op": m.group("op")})
            return out
        for line in text.split("\n"):
            line = line.strip()
            if not line:
                continue
            hours = parse_hours_range(line, _CREDIT_LINE_RE)
            item = {"text": line}
            if hours:
                item["hours"] = hours
                item["text"] = _CREDIT_LINE_RE.sub(" ", line).strip(" *§:-")
                item["text"] = re.sub(r"\s+", " ", item["text"])
            elif len(line) <= 40 and re.fullmatch(r"[\s*§†‡#]*\(?[^()]*\)?[\s*§†‡#]*", line):
                item = {"note": line}
            out.append(item)
        return out

    items: list[dict] = []
    for course in courses:
        for adhoc in before.get(course["id"], []):
            items.extend(adhoc_items(adhoc))
        code, _ = _split_title(course.get("title") or "")
        code = code or course_code_by_id.get(course["id"])
        if code:
            items.append({"course": code})
        for adhoc in after.get(course["id"], []):
            items.extend(adhoc_items(adhoc))
    for adhoc in loose:
        items.extend(adhoc_items(adhoc))
    return items


def _build_section(core: dict, course_code_by_id: dict) -> dict:
    name = re.sub(r"\s+", " ", (core.get("name") or "").replace(" ", " ")).strip()
    section = {"name": name}
    hours = parse_hours_range(name)
    if hours:
        section["hours"] = hours
    text = html_to_text(core.get("description"))
    if text:
        section["text"] = text
    items = _section_items(core, course_code_by_id)
    if items:
        section["items"] = items
    children = [
        _build_section(child, course_code_by_id)
        for child in sorted(core.get("children") or [], key=lambda c: c.get("sort_order") or 0)
    ]
    if children:
        section["children"] = children
    return section


def build_program(detail: dict, course_code_by_id: dict, gateway: str, legacy_catalog_id) -> dict:
    sections = [
        _build_section(core, course_code_by_id)
        for core in sorted(detail.get("cores") or [], key=lambda c: c.get("sort_order") or 0)
    ]
    program = {
        "id": detail["id"],
        "name": re.sub(r"\s+", " ", detail.get("name") or "").strip(),
        "type": next((t["name"] for t in detail.get("program_types") or []), None),
        "degree": next((t["name"] for t in detail.get("degree_types") or []), None),
        "department": next((p["name"] for p in detail.get("parents") or []), None),
        "active": bool((detail.get("status") or {}).get("active", True)),
        "url": f"{gateway}/preview_program.php?catoid={legacy_catalog_id}&poid={detail.get('legacy-id')}",
        "sections": sections,
    }
    intro = html_to_text(detail.get("description"))
    if intro:
        program["intro"] = intro[:1200]
    return program


# ── Entry point ──────────────────────────────────────────────────────────────

def build_catalog(raw_dir: Path, gateway: str = "https://catalog.mtsu.edu") -> dict:
    raw_dir = Path(raw_dir)
    meta = json.loads((raw_dir / "meta.json").read_text())
    catalog_meta = meta["catalog"]

    courses: dict[str, dict] = {}
    code_by_id: dict[int, str] = {}
    for cid in meta["course_ids"]:
        path = raw_dir / "courses" / f"{cid}.json"
        if not path.exists():
            continue
        code, course = build_course(json.loads(path.read_text()))
        if code and (code not in courses or not courses[code].get("credits")):
            courses[code] = course
            code_by_id[cid] = code

    programs = []
    for pid in meta["program_ids"]:
        path = raw_dir / "programs" / f"{pid}.json"
        if not path.exists():
            continue
        programs.append(build_program(json.loads(path.read_text()), code_by_id, gateway, catalog_meta.get("legacy-id")))
    programs.sort(key=lambda p: p["name"].lower())

    return {
        "catalog": {
            "name": catalog_meta.get("name"),
            "api_id": catalog_meta.get("id"),
            "catoid": catalog_meta.get("legacy-id"),
            "fetched_at": meta.get("fetched_at"),
            "source": f"{gateway}/widget-api/catalog/{catalog_meta.get('id')}/",
        },
        "courses": dict(sorted(courses.items())),
        "programs": programs,
    }
