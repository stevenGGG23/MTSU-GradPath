"""Convert catalog programs into planner configs.

Every major and minor in the exported catalog becomes a config in the same
shape as the hand-written ones in degree_configs.py / minors.py, so the
planner, audit, double-major and minor code work on all of them unchanged.

How a program's requirement sections are read:

  * a course on its own                -> required course
  * courses joined by "OR"             -> one choice; any completed option counts
  * courses joined by "AND"            -> taken together; a 0-credit lab rides
                                          along with its lecture (coreq)
  * "Choose five from the following",
    or more courses listed than the
    section's hours                    -> a pool: N hours from the listed courses
  * "<PREFIX> upper-division electives
    9 credit hours"                    -> the major's upper-division elective hours,
                                          filled by real catalog courses
  * any other "... N credit hours" line -> an hours requirement the student fills
  * a section with hours but no courses -> filled from the four-year academic map
                                          when the map lists courses that appear
                                          nowhere else (e.g. a College of Business
                                          Core), otherwise an hours requirement
  * True Blue Core                     -> the eight standard gen-ed areas (41 hrs),
                                          minus hours that required courses already
                                          cover ("MATH 1630 (Quant Lit)")
  * top-level "Electives"              -> free electives (whatever is left of 120)

Hours requirements that list specific courses carry those codes in
``generic_options`` so completed courses fill them automatically
(see auto_generic_hours).
"""

from __future__ import annotations

import re
from collections import Counter

from . import catalog_store as store

TOTAL_HOURS_DEFAULT = 120

# The eight True Blue Core areas (41 hours). Ids match the hand-written
# configs' rows where those exist, so saved form values carry over.
TBC_AREAS = [
    ("tbc_written_comm", "Written Communication", 3, ["ENGL 1010"]),
    ("tbc_info_lit", "Information Literacy", 3, ["ENGL 1020", "UNIV 1010"]),
    ("tbc_nwc", "Non-Written Communication", 3, ["COMM 2200"]),
    ("tbc_quant_lit", "Quantitative Literacy", 3, ["MATH 1530", "MATH 1710", "MATH 1010"]),
    ("tbc_hssr", "Human Society and Social Relationships", 6, ["PSY 1410", "SOC 1010", "ECON 2410"]),
    ("tbc_sci_lit", "Scientific Literacy (two lab sciences)", 8, ["BIOL 1030", "CHEM 1010", "GEOL 1030"]),
    ("tbc_cce", "Creativity and Cultural Expression (incl. literature)", 9, ["ENGL 2030", "ART 1030", "MUS 1030"]),
    ("tbc_history_civic", "History and Civic Learning", 6, ["HIST 2010", "HIST 2020"]),
]

_AREA_TAGS = [
    ("tbc_quant_lit", r"quant(?:itative)?\.?\s*lit|\bQL\b"),
    ("tbc_info_lit", r"info(?:rmation)?\.?\s*lit|\bIL\b"),
    ("tbc_sci_lit", r"sci(?:entific)?\.?\s*lit|\bSL\b"),
    ("tbc_nwc", r"\bNWC\b|non-?written"),
    ("tbc_written_comm", r"(?<!N)\bWC\b|written comm"),
    ("tbc_hssr", r"\bHSSR\b|human society"),
    ("tbc_cce", r"\bCCE\b|creativity"),
    ("tbc_history_civic", r"\bHCL\b|history and civic"),
]

# When a note only says "(may be counted in the True Blue Core)"
_PREFIX_AREA = {
    "MATH": "tbc_quant_lit", "STAT": "tbc_quant_lit",
    "BIOL": "tbc_sci_lit", "CHEM": "tbc_sci_lit", "PHYS": "tbc_sci_lit", "GEOL": "tbc_sci_lit",
    "ASTR": "tbc_sci_lit", "PSCI": "tbc_sci_lit", "PGEO": "tbc_sci_lit", "SCI": "tbc_sci_lit",
    "COMM": "tbc_nwc", "HIST": "tbc_history_civic",
    "PSY": "tbc_hssr", "SOC": "tbc_hssr", "ECON": "tbc_hssr", "ANTH": "tbc_hssr", "GEOG": "tbc_hssr",
    "PS": "tbc_hssr", "HLTH": "tbc_hssr", "WGST": "tbc_hssr", "AAS": "tbc_hssr",
    "ART": "tbc_cce", "MUS": "tbc_cce", "THEA": "tbc_cce", "PHIL": "tbc_cce", "DANC": "tbc_cce",
    "HUM": "tbc_cce", "FREN": "tbc_cce", "SPAN": "tbc_cce", "GERM": "tbc_cce", "ENGL": "tbc_cce",
}
_CODE_AREA = {"ENGL 1010": "tbc_written_comm", "ENGL 1020": "tbc_info_lit"}

_SKIP_SECTION = re.compile(r"^(academic map|curriculum|notes?:?$|note:|subtotal|freshman|sophomore|junior|senior|year \d)", re.I)
_TBC_SECTION = re.compile(r"true blue core|general education", re.I)
_SUPPORT_SECTION = re.compile(
    r"supporting|auxiliary|business core|college of .*core|cognate|related|professional education|"
    r"teacher licensure|licensure|world language|foreign language|minor\b|education core|interdisciplinary",
    re.I,
)
_CHOOSE_WORDS = re.compile(r"\b(choose|choosing|select|selected|chosen|one of|two of|three of|from the following|from:|options?)\b", re.I)
_ELECTIVE_TEXT = re.compile(r"elective", re.I)
_UD_TEXT = re.compile(r"upper[- ]?division|\bUD\b|\b[34]000[- ]level", re.I)

_cache: dict = {}


def reset_cache() -> None:
    _cache.clear()


def slugify(text: str) -> str:
    text = text.lower().replace("&", "and")
    text = re.sub(r"[.,()']", "", text)
    return re.sub(r"[^a-z0-9]+", "-", text).strip("-")


def _credits(code: str) -> float:
    c = store.credits(code, default=None)
    return 3 if c is None else c


def _nice(n):
    return int(n) if float(n).is_integer() else n


# ── Section items -> units ───────────────────────────────────────────────────

def parse_units(items: list) -> list:
    """Fold a section's items into units.

    unit kinds:
      course  {"code", "with": [other codes taken together], "notes": [...]}
      choice  {"options": [course/text units]}
      text    {"text", "hours": [lo, hi]}
      pool    {"text", "hours", "options": [course units]} -- a "Choose from
              the following (4 hours)" line partway down a list and the
              courses after it; the courses above it stay required
    """
    units: list = []
    pending = None  # "OR" / "AND"
    pool = None  # open "choose from the following" list
    bundle = None  # option collecting the courses after "text OR"
    ends_with_or = False

    def last_course(unit):
        if unit["kind"] == "choice":
            return last_course(unit["options"][-1])
        return unit if unit["kind"] == "course" else None

    for item in items or []:
        if item.get("sep") or "op" in item:
            bundle = None
        m = _CODE_AT_START.match(item.get("text") or "")
        if m and store.course(m.group(1)):
            # "AGBS 4150 - Agricultural Policy (3 hours)" written as text
            item = {"course": m.group(1)}
        if item.get("sep"):
            pending = None
            if pool is not None and pool["options"]:
                pool = None
            continue
        if "op" in item:
            pending = item["op"] if units else None
            continue
        if "note" in item:
            hours = _choose_hours(item["note"])
            if hours:
                # "Choose 6 hours from:" heading the courses below it
                pool = {"kind": "pool", "text": item["note"].rstrip(": "), "hours": [hours, hours], "options": [],
                        "count": _choose_count(item["note"])}
                units.append(pool)
                pending = None
                continue
            if units:
                units[-1].setdefault("notes", []).append(item["note"])
            continue

        if "course" in item:
            unit = {"kind": "course", "code": item["course"], "with": []}
        elif item.get("hours"):
            if item["text"].startswith("(") or not item["text"]:
                # "(3 credit hours may be counted in the True Blue Core)"
                if units:
                    units[-1].setdefault("notes", []).append(item["text"] or "")
                if re.search(r"\bOR\s*$", item["text"]):
                    pending = "OR"  # "JOUR 3580 (1 hour) OR JOUR 4000"
                continue
            ends_with_or = bool(re.search(r"\bOR\s*$", item["text"]))
            # "MUAP 2000-2999 ( ) OR": drop the empty "( )" left where the
            # catalog page had a footnote, and the trailing OR
            clean = re.sub(r"\s*\(\s*\)|\s*\bOR\s*$", "", item["text"]).strip() or item["text"]
            unit = {"kind": "text", "text": clean, "hours": item["hours"]}
            pool = None
            if _CHOOSE_START.match(item["text"]):
                inline = list(dict.fromkeys(_CODE_IN_TEXT.findall(item["text"])))
                if inline:
                    # "Choose one: MATH 1010, MATH 1630, ... or MATH 1910" names
                    # its own options; the courses after it are separate
                    units.append({"kind": "pool", "text": item["text"], "hours": item["hours"],
                                  "options": [{"kind": "course", "code": c, "with": []} for c in inline]})
                    pending = None
                    continue
                if len(item["text"]) < 120:
                    pool = {"kind": "pool", "text": item["text"], "hours": item["hours"], "options": []}
                    units.append(pool)
                    pending = None
                    continue
        else:
            text = item.get("text", "")
            hours = _choose_hours(text) if not _CODE_IN_TEXT.search(text) else None
            if hours:
                pool = {"kind": "pool", "text": text.rstrip(": "), "hours": [hours, hours], "options": [],
                        "count": _choose_count(text)}
                units.append(pool)
                pending = None
                continue
            if pending == "OR" and units and len(text) < 120:
                unit = {"kind": "text", "text": text, "hours": None}
            else:
                if units and len(text) < 200:
                    units[-1].setdefault("notes", []).append(text)
                pending = None
                continue

        if pending is None and unit["kind"] == "course":
            # "PHYS 2110 PHYS 2111" with no AND between: the lab goes with it
            host = (pool["options"][-1] if pool is not None and pool["options"]
                    else last_course(units[-1]) if units and pool is None else None)
            if host is not None and _is_lab_of(unit["code"], host["code"]):
                host["with"].append(unit["code"])
                host.setdefault("loose", []).append(unit["code"])
                continue
        if pool is not None and pool.get("or_chain") and pending is None:
            # "Choose from: A or B or C" then a course with no "or" in front:
            # the list is over ("HIST 2010 or ... HIST 2065", then "HIST 3010")
            pool = None
        if pool is not None:
            if pending == "AND" and pool["options"] and unit["kind"] == "course":
                pool["options"][-1]["with"].append(unit["code"])
            elif unit["kind"] == "course":
                if pending == "OR":
                    pool["or_chain"] = True
                pool["options"].append(unit)
            pending = None
            continue
        if pending == "AND" and units and unit["kind"] == "course":
            target = last_course(units[-1])
            if target is not None:
                target["with"].append(unit["code"])
                pending = None
                continue
        if pending is None and bundle is not None and unit["kind"] == "course":
            # "MUAP 2000-2999 (4 hours) OR MUS 1510 MUS 1550 MUS 2530 MUS 2540":
            # the courses after the OR, up to the next break, are one option
            bundle["with"].append(unit["code"])
            continue
        bundle = None
        if pending == "OR" and units:
            prev = units[-1]
            if prev["kind"] == "choice":
                prev["options"].append(unit)
            else:
                units[-1] = {"kind": "choice", "options": [prev, unit]}
            if prev["kind"] == "text" and unit["kind"] == "course":
                bundle = unit
            pending = None
            continue
        units.append(unit)
        pending = "OR" if unit["kind"] == "text" and ends_with_or else None
    # A "choose" line with nothing listed under it is just an hours line
    # (its options are in a sub-list)
    # A lab paired above that ended up as a plain required course goes back
    # to its own row (it's planned as its own 0-credit line); inside a
    # choice it stays with its lecture
    split = []
    for unit in units:
        loose = unit.pop("loose", None) if unit["kind"] == "course" else None
        if loose:
            unit["with"] = [c for c in unit["with"] if c not in loose]
            split.append(unit)
            split.extend({"kind": "course", "code": c, "with": []} for c in loose)
        else:
            split.append(unit)
    units[:] = split
    for unit in units:
        for opt in unit.get("options", []):
            opt.pop("loose", None)
            for inner in opt.get("options", []):
                inner.pop("loose", None)
    for i, unit in enumerate(units):
        unit.pop("or_chain", None)
        count = unit.pop("count", None)
        if unit["kind"] == "pool" and not unit["options"]:
            units[i] = {"kind": "text", "text": unit["text"], "hours": unit["hours"]}
        elif unit["kind"] == "pool" and count:
            # "Choose one:" from 4-credit lecture+lab pairs is 4 hours, not 3
            each = sorted(h for h in (_unit_hours(o) for o in unit["options"]) if h)
            if each:
                hours = sum(each[:count])
                unit["hours"] = [hours, hours]
    return units


_CODE_AT_START = re.compile(r"^\s*([A-Z]{2,4} \d{4}[A-Z]?)\s+[-–]\s+\w")
_CODE_IN_TEXT = re.compile(r"\b([A-Z]{2,4} \d{4}[A-Z]?)\b")
_CHOOSE_START = re.compile(r"^\s*(choose|chosen|select|pick|complete one|take one|one of|two of|three of|from the following|at least)\b", re.I)
_CHOOSE_HEADING = re.compile(
    r"(^|[-–:]\s*|\band\s+)(choose|select|take|pick)\s+(\d+|one|two|three|four|five|six|seven|eight|nine|ten|twelve)\b"
    r"|^\s*(choose|select)\s+\d+.*\bfrom\b",
    re.I,
)


def _choose_hours(text: str):
    """Hours in a pick-list heading partway down a list -- "Choose one:",
    "Take two of the following:", "Choose 9 hours (3 courses) from the
    following:", "Guided Electives - choose three courses from the
    following" -- else None."""
    if not text or len(text) > 120 or not _CHOOSE_HEADING.search(text):
        return None
    if re.search(r"\bup to\b|\bmay\b", text, re.I):
        return None  # "Students may complete up to ..." is optional
    return section_hours({"name": text})


def _choose_count(text: str):
    """2 for "Choose two:" / "Take two of the following" (a course count with
    no hours stated), else None."""
    if re.search(r"\bhours?\b", text, re.I):
        return None
    m = re.search(r"\b(choose|select|take|pick)\s+(\d+|" + "|".join(_NUM_WORDS) + r")\b", text, re.I)
    if not m:
        return None
    raw = m.group(2).lower()
    return _NUM_WORDS[raw] if raw in _NUM_WORDS else int(raw)


def _is_lab_of(code: str, lecture: str) -> bool:
    """PHYS 2111 is the lab of PHYS 2110."""
    a, b = code.split(), lecture.split()
    return (len(a) == len(b) == 2 and a[0] == b[0] and a[1].isdigit() and b[1].isdigit()
            and b[1].endswith("0") and int(a[1]) == int(b[1]) + 1)


def _counts_in_core(unit) -> bool:
    return any(re.search(r"True Blue Core|\bTBC\b", n or "") for n in unit.get("notes", []))


def _unit_codes(unit) -> list:
    if unit["kind"] == "course":
        return [unit["code"], *unit["with"]]
    if unit["kind"] in ("choice", "pool"):
        return [c for opt in unit["options"] for c in _unit_codes(opt)]
    return []


def _unit_hours(unit) -> float:
    if unit["kind"] == "course":
        return sum(_credits(c) for c in [unit["code"], *unit["with"]])
    if unit["kind"] == "choice":
        hours = [_unit_hours(o) for o in unit["options"]]
        hours = [h for h in hours if h]
        return min(hours) if hours else 0
    if unit["kind"] in ("text", "pool") and unit.get("hours"):
        return unit["hours"][0]
    return 0


def _option_label(unit) -> str:
    if unit["kind"] == "course":
        main = store.label(unit["code"])
        extra = [c for c in unit["with"] if _credits(c)]
        return main + (" + " + " + ".join(extra) if extra else "")
    if unit["kind"] == "text":
        return unit["text"]
    return " or ".join(_option_label(o) for o in unit["options"])


_NUM_WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8,
              "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "fifteen": 15, "eighteen": 18}
_HOURS_TEXT = re.compile(
    r"(?:choose|select|complete|take|at least|minimum of|a total of)?\s*(\d+(?:\.\d+)?|"
    + "|".join(_NUM_WORDS) + r")\s*(?:\(\d+\)\s*)?(?:additional\s+|more\s+|semester\s+|credit\s+)*hours?\b",
    re.I,
)
_N = "|".join(_NUM_WORDS) + r"|\d"
_COUNT_TEXT = re.compile(
    r"\b(?:choose|select|complete|take|pick|plus)\s+(" + _N + r")\b(?!\s*(?:\(\d+\)\s*)?(?:additional\s+|semester\s+|credit\s+)*hours?)"
    r"|^\s*(" + _N + r")\s+(?:course\s+|class\s+)?(?:from|of)\b",
    re.I,
)


def section_hours(sec) -> float | None:
    """A section's hours: '(18 hours)' in its name, else 'Choose 6 credit
    hours from...' / 'At least five hours from:' / 'Choose two courses' in
    its name or text."""
    if sec.get("hours"):
        return sec["hours"][0]
    for text in (sec["name"], sec.get("text", "")):
        m = _HOURS_TEXT.search(text or "")
        if m and re.search(r"\bup to\s*$", (text or "")[: m.start()], re.I):
            m = None  # "up to 9 credit hours" is a cap, not a requirement
        if m:
            raw = m.group(1).lower()
            return float(_NUM_WORDS.get(raw, raw)) if raw not in _NUM_WORDS else _NUM_WORDS[raw]
        m = _COUNT_TEXT.search(text or "")
        if m:
            raw = (m.group(1) or m.group(2)).lower()
            return 3 * (_NUM_WORDS[raw] if raw in _NUM_WORDS else int(raw))
    return None


def _section_total(sec) -> float:
    """Hours a section stands for: its stated hours, else what it lists
    ("Business Courses" with five 3-hour courses and no stated hours = 15)."""
    hours = section_hours(sec)
    if hours is not None:
        return hours
    listed = sum(_unit_hours(u) for u in parse_units(sec.get("items")))
    return listed + sum(_section_total(c) for c in sec.get("children") or [])


def _listed_prefixes(text: str) -> list:
    """Course prefixes listed as allowed in a requirement's text ("courses
    with ACTG, BCED, BIA ... prefixes"), when it reads like such a list."""
    if not re.search(r"prefix|courses (with|in|from)|chosen from|selected from", text or "", re.I):
        return []
    found = []
    for token in re.findall(r"\b([A-Z]{2,4})\b(?!\s*\d)", text or ""):
        if token not in found and store.codes_for_prefix(token):
            found.append(token)
    return found


def _walk(sections, depth=0, parent=None):
    for sec in sections or []:
        yield sec, depth, parent
        yield from _walk(sec.get("children"), depth + 1, sec)


def _clean_label(name: str) -> str:
    name = re.sub(r"\s*\((?:[^()]*\bhours?\b[^()]*)\)\s*:?", "", name).strip(" :")
    return name or "Requirement"


def _area_for(code: str, note: str = "") -> str | None:
    for area, pattern in _AREA_TAGS:
        if re.search(pattern, note or "", re.I):
            return area
    if note and re.search(r"true blue|TBC|general education", note, re.I):
        return _CODE_AREA.get(code) or _PREFIX_AREA.get(code.split()[0])
    return None


# ── Program conversion ───────────────────────────────────────────────────────

class _Builder:
    def __init__(self, program: dict, key: str, kind: str):
        self.program = program
        self.key = key
        self.kind = kind  # "major" / "minor"
        self.generic_seq = 0
        self.required: dict[str, dict] = {}  # code -> {hours, title, role}
        self.generic: list = []  # (gid, label, hours, suggestions)
        self.generic_options: dict[str, list] = {}
        self.generic_role: dict[str, str] = {}
        self.ud_hours = 0
        self.ud_rows: list = []
        self.coreq_map: dict[str, set] = {}
        self.minor_bucket = None
        self.display: list = []  # audit sections
        self.tbc_overlap: dict[str, float] = {}
        self.overlap_counted: set = set()
        self.consumed: set = set()
        self.alternatives_note = None
        self.text_generic: set = set()  # requirements from "... N credit hours" lines
        self.last_pool = None  # pool made by the previous section

    # ids are stable per program + position so saved form values survive reloads
    def _gid(self):
        self.generic_seq += 1
        return f"p{self.program['id']}_{self.generic_seq}"

    def add_required(self, code, role, rows):
        if code in self.required:
            rows.append({"type": "course", "code": code})
            return
        hours = _credits(code)
        self.required[code] = {"hours": hours, "title": store.title(code), "role": role}
        rows.append({"type": "course", "code": code})

    def add_course_unit(self, unit, role, rows):
        main = unit["code"]
        self.add_required(main, role, rows)
        for other in unit["with"]:
            if _credits(other) == 0:
                self.coreq_map.setdefault(main, set()).add(other)
            else:
                self.add_required(other, role, rows)
        for note in unit.get("notes", []):
            self.note_overlap(main, note)

    def add_generic(self, label, hours, options=(), suggestions=(), rows=None, role="supporting"):
        if not hours or hours <= 0:
            return None
        gid = self._gid()
        options = [c for c in dict.fromkeys(options) if c]
        sugg = list(suggestions) or [store.label(c) for c in options[:4]]
        self.generic.append((gid, label, _nice(hours), sugg))
        self.generic_role[gid] = role
        if options:
            self.generic_options[gid] = options
        if rows is not None:
            rows.append({"type": "generic", "id": gid})
        return gid

    def note_overlap(self, code, note):
        area = _area_for(code, note)
        if area and code not in self.overlap_counted:
            self.overlap_counted.add(code)
            self.tbc_overlap[area] = self.tbc_overlap.get(area, 0) + _credits(code)

    # ── one section ──
    def convert_section(self, sec, role, curriculum_extra, parent=None):
        rows: list = []
        label = _clean_label(sec["name"])
        H = section_hours(sec)
        if H is not None and sec.get("children"):
            # "English Core (18 hours)": 4 listed courses + two "One from the
            # following:" sub-lists, where the sub-lists are part of the 18.
            # (Some programs list sub-lists *beside* the parent's hours
            # instead -- then the numbers don't add up this way.)
            child_hours = sum(_section_total(c) for c in sec["children"])
            listed_here = sum(_unit_hours(u) for u in parse_units(sec.get("items")))
            if child_hours and listed_here + child_hours <= H + max(3, 0.1 * H):
                H = max(H - child_hours, 0)
        units = parse_units(sec.get("items"))
        text = sec.get("text", "")
        unit_text = " ".join(u.get("text", "") for u in units if u["kind"] == "text")

        listed = sum(_unit_hours(u) for u in units)
        has_courses = any(u["kind"] in ("course", "choice", "pool") for u in units)
        choose_hint = bool(_CHOOSE_WORDS.search(text) or _CHOOSE_WORDS.search(sec["name"]))

        # A paragraph that merely mentions hours ("Interdisciplinary minors
        # require a minimum of 15 to 21 hours...") isn't a requirement
        if not units and not sec.get("hours") and len(text) > 120:
            return rows

        # Elective-hours sections with no listed courses: "BIOL UD elective (3 hours)"
        if not units and H:
            prefixes = _listed_prefixes(text)
            if len(prefixes) >= 2:
                # "24 hours of business electives chosen from courses with
                # ACTG, BCED, BIA, BLAW, BUS, ... prefixes": any course with
                # one of those prefixes (upper-division if the text says so)
                upper = bool(_UD_TEXT.search(text))
                options = [
                    code for prefix in prefixes for code in store.codes_for_prefix(prefix)
                    if _credits(code) and (not upper or int(re.sub(r"\D", "", code.split()[1])[:4]) >= 3000)
                ]
                self.add_generic(label, H, options=options, rows=rows, role=role,
                                 suggestions=[f"Any {', '.join(prefixes[:6])}{'…' if len(prefixes) > 6 else ''} course"])
            elif self.kind == "major" and self._is_major_elective(sec["name"] + " " + text):
                self.ud_hours += H
                rows.append({"type": "ud", "hours": H})
            elif re.search(r"\bminor\b", sec["name"], re.I) and self.kind == "major" and not self.minor_bucket:
                named = _clean_label(sec["name"])
                label = "Minor (any MTSU minor)" if re.fullmatch(r"minor", named, re.I) else named
                self.minor_bucket = self.add_generic(label, H, rows=rows,
                                                     suggestions=["Add your minor above to plan its courses"])
            else:
                filled = self._fill_from_curriculum(H, role, rows, curriculum_extra) if not _ELECTIVE_TEXT.search(sec["name"]) else 0
                if H - filled >= 1:
                    hint = re.sub(r"\s+", " ", text)[:140]
                    self.add_generic(label if not filled else f"{label} (other)", H - filled, rows=rows,
                                     suggestions=[hint] if hint else [], role=role)
            return rows

        # "Choose/select ... from" wording makes a pool even when the listed
        # courses alone don't reach the hours (more options may follow in
        # sibling lists); otherwise listing well over the hours does. Courses
        # marked "(may be counted in the True Blue Core)" don't count toward
        # the section's own hours, and "16-20 hours" allows up to 20.
        own_hours = sum(_unit_hours(u) for u in units if not _counts_in_core(u))
        top = H + (sec["hours"][1] - sec["hours"][0]) if sec.get("hours") and H is not None else H
        # A pick-list lists well over its hours (9 hours from 38 courses); a
        # requirement list that only runs a little over -- track alternatives,
        # ensembles "may be taken for 0 credit" -- is still a requirement list
        is_pool = has_courses and H is not None and (
            (choose_hint and (listed > H + 0.5 or not any(u["kind"] in ("text", "pool") for u in units)))
            or own_hours > max(top + 2, top * 1.4)
            # "Approved BIA Electives (9 hours)" listing four courses: pick three
            or (re.search(r"\belectives?\b", sec["name"], re.I) and own_hours > top + 0.5)
        )
        if is_pool:
            options = [c for u in units for c in _unit_codes(u) if _credits(c)]
            twin = self._matching_generic(label, H)
            if twin:
                # e.g. "Cognate Elective Areas (15 hours)" spelling out the options
                # for a "Cognate electives 15 credit hours" line counted earlier
                self.generic_options.setdefault(twin, [])
                self.generic_options[twin] = list(dict.fromkeys(self.generic_options[twin] + options))
                return rows
            gid = self.add_generic(label, H, options=options, rows=rows, role=role,
                                   suggestions=[store.label(c) for c in options[:3]] + (["…"] if len(options) > 3 else []))
            self.last_pool = gid
            return rows

        for unit in units:
            if unit["kind"] == "course":
                self.add_course_unit(unit, role, rows)
            elif unit["kind"] == "choice":
                opts = unit["options"]
                courses_only = [o for o in opts if o["kind"] == "course"]
                h = _unit_hours(unit) or 3
                codes = [c for o in opts for c in _unit_codes(o) if _credits(c)]
                desc = "Choose one: " + " or ".join(
                    (o["code"] if o["kind"] == "course" else o["text"][:40]) for o in opts
                )
                self.add_generic(desc, h, options=codes, rows=rows, role=role,
                                 suggestions=[_option_label(o) for o in opts][:4])
                for o in courses_only:
                    area = next((_area_for(o["code"], n) for n in o.get("notes", []) if _area_for(o["code"], n)), None)
                    if area:
                        # Whichever option is taken also covers that gen-ed area
                        key = "choice:" + "|".join(o2.get("code", "") for o2 in courses_only)
                        if key not in self.overlap_counted:
                            self.overlap_counted.add(key)
                            self.tbc_overlap[area] = self.tbc_overlap.get(area, 0) + h
                        break
            elif unit["kind"] == "pool":
                h = unit["hours"][0]
                codes = [c for c in _unit_codes(unit) if _credits(c)]
                self.add_generic(f"{label}: choose {_nice(h)} hours", h, options=codes, rows=rows, role=role,
                                 suggestions=[store.label(c) for c in codes[:3]] + (["…"] if len(codes) > 3 else []))
            elif unit["kind"] == "text" and unit.get("hours"):
                h = unit["hours"][0]
                if self.kind == "major" and self._is_major_elective(unit["text"]):
                    self.ud_hours += h
                    rows.append({"type": "ud", "hours": h, "label": unit["text"]})
                elif self.kind == "major" and re.search(r"^(free\s+)?electives?$", unit["text"], re.I):
                    # A plain "Electives N credit hours" line is part of free electives
                    continue
                else:
                    gid = self.add_generic(unit["text"], h, rows=rows, role=role,
                                           suggestions=[re.sub(r"\s+", " ", text)[:140]] if text else [])
                    if gid:
                        self.text_generic.add(gid)

        # Listed less than the section's hours -> the rest is an hours requirement
        # -- unless sections beside it carry those hours: "Major Requirements
        # (45)" = Core 15 + Methods 3 + Concentration 27 + Comparative 6 +
        # Electives 6 adds up to 12 too many exactly when the concentration's
        # 27 includes the two lists after it
        rest = H - listed if H is not None else 0
        if parent is not None and parent.get("hours") and rest > 1.5:
            siblings = sum(section_hours(c) or 0 for c in parent.get("children", []))
            if abs(siblings - parent["hours"][0] - rest) < 0.5:
                rest = 0
        if H is not None and has_courses and listed < H - 1.5 and rest > 1.5:
            words = text or unit_text
            if self.kind == "major" and self._is_major_elective(sec["name"] + " " + words):
                self.ud_hours += rest
                rows.append({"type": "ud", "hours": rest})
            else:
                self.add_generic(f"{label}: additional hours", rest, rows=rows, role=role,
                                 suggestions=[re.sub(r"\s+", " ", words)[:140]] if words else [])
        return rows

    def _matching_generic(self, label, hours):
        word = re.match(r"[A-Za-z]+", label)
        if not word or len(word.group(0)) < 4:
            return None
        for gid, other, h, _ in self.generic:
            if gid not in self.text_generic:
                continue
            if other.lower().startswith(word.group(0).lower()) and abs(h - hours) < 0.5:
                return gid
        return None

    def _is_major_elective(self, text):
        if not _ELECTIVE_TEXT.search(text or ""):
            return False
        prefix = self.prefix
        return bool(
            re.search(rf"\b{re.escape(prefix)}\b", text)
            or (_UD_TEXT.search(text) and re.search(r"major|guided|departmental|concentration", text, re.I))
            or re.search(r"^(major|concentration)\b", text.strip(), re.I)
        )

    def _fill_from_curriculum(self, hours, role, rows, curriculum_extra):
        filled = 0
        while curriculum_extra and filled < hours - 0.5:
            code = curriculum_extra[0]
            if filled + _credits(code) > hours + 0.5:
                break
            curriculum_extra.pop(0)
            self.add_required(code, role, rows)
            filled += _credits(code)
        return filled

    # ── whole program ──
    def build(self):
        program = self.program
        sections = program["sections"]

        req_roots = [s for s in sections if not _SKIP_SECTION.match(s["name"])]
        alternatives = [s for s in req_roots if re.search(r"\b(emphasis|track)\b", s["name"], re.I) and s.get("hours")]
        if len(alternatives) >= 2:
            # "Biology Emphasis (24 hours)" / "Chemistry Emphasis (24 hours)":
            # pick-one alternatives. Plan the first; the catalog link covers the rest.
            req_roots = [s for s in req_roots if s not in alternatives[1:]]
            self.alternatives_note = f"Planned with {alternatives[0]['name']}; other options are in the catalog."
        curriculum = [s for s in sections if re.match(r"curriculum|academic map", s["name"], re.I)]

        # Main prefix: most common among the major's own (non-supporting) courses
        major_codes = Counter()
        all_codes = Counter()
        for sec, _, _ in _walk(req_roots):
            codes = [c for u in parse_units(sec.get("items")) for c in _unit_codes(u)]
            all_codes.update(c.split()[0] for c in codes)
            if not _SUPPORT_SECTION.search(sec["name"]) and not _TBC_SECTION.search(sec["name"]):
                major_codes.update(c.split()[0] for c in codes)
        counts = major_codes or all_codes
        self.prefix = counts.most_common(1)[0][0] if counts else "GEN"

        # Courses the four-year map lists that no requirement section does
        listed_codes = set()
        for sec, _, _ in _walk(req_roots):
            for u in parse_units(sec.get("items")):
                listed_codes.update(_unit_codes(u))
        curriculum_extra = []
        map_notes = []
        for sec, _, _ in _walk(curriculum):
            for u in parse_units(sec.get("items")):
                if u["kind"] == "course":
                    for note in u.get("notes", []):
                        map_notes.append((u["code"], note))
                    if u["code"] not in listed_codes and u["code"] not in curriculum_extra and _credits(u["code"]):
                        curriculum_extra.append(u["code"])

        total = TOTAL_HOURS_DEFAULT
        for sec, _, _ in _walk(req_roots):
            m = re.search(r"TOTAL\s*(\d{2,3})", sec.get("text", ""))
            if m:
                total = int(m.group(1))
                break

        for sec, depth, parent in _walk(req_roots):
            name = sec["name"]
            if _SKIP_SECTION.match(name):
                continue
            if _TBC_SECTION.search(name):
                for m in re.finditer(r"\b([A-Z]{2,4} \d{4})\s*\(([^)]*)\)", sec.get("text", "").replace("\n", " ")):
                    self.note_overlap(m.group(1), m.group(2))
                continue
            top_level = parent is None or (parent and re.match(r"degree requirements", parent["name"], re.I))
            is_elective_heading = re.match(r"^(free\s+|general\s+|other\s+|unrestricted\s+)?electives?\b", name, re.I)
            if self.kind == "major" and is_elective_heading and not sec.get("items") and (
                top_level or re.search(r"unrestricted|free elective|any (course|elective)", sec.get("text", ""), re.I)
            ):
                continue  # free electives: whatever is left of the total
            if (
                not sec.get("items") and not sec.get("children")
                and re.match(r"^(major|degree|program)\s+requirements\b", name, re.I)
            ):
                continue  # a summary line ("Major Requirements (81 hours)") over the sections that follow
            if id(sec) in self.consumed:
                continue
            children = sec.get("children") or []
            open_lists = [c for c in children if c.get("items") and section_hours(c) is None]
            parent_hours = section_hours(sec)
            if not sec.get("items") and parent_hours and open_lists:
                # "Course Options by Program (18 hours)" / "Cognate Elective
                # Areas (15 hours)": sub-lists with no hours of their own are
                # the options for the parent's hours
                for c in open_lists:
                    for d, _, _ in _walk([c]):
                        self.consumed.add(id(d))
                known = sum(section_hours(c) or 0 for c in children if c not in open_lists)
                options = [code for c in open_lists for d, _, _ in _walk([c]) for u in parse_units(d.get("items")) for code in _unit_codes(u)]
                pool_hours = parent_hours - known
                if pool_hours > 0:
                    sec = {**sec, "hours": [pool_hours, pool_hours],
                           "items": [{"course": code} for code in dict.fromkeys(options)], "text": "Choose from:"}
                    children = []
            if not sec.get("items") and section_hours(sec) is None:
                continue  # a heading like "Degree Requirements" with children
            if children and not sec.get("items"):
                continue
            # "Choose 18 hours from the following:" then sibling lists
            # "Languages", "Literature" with no hours: more options for that pool
            if (
                self.last_pool and sec.get("items") and section_hours(sec) is None
                and not _CHOOSE_WORDS.search(name + " " + sec.get("text", ""))
            ):
                extra = [c for u in parse_units(sec.get("items")) for c in _unit_codes(u) if _credits(c)]
                self.generic_options[self.last_pool] = list(dict.fromkeys(self.generic_options.get(self.last_pool, []) + extra))
                continue
            self.last_pool = None
            if re.search(r"concentration", name, re.I) and not _SUPPORT_SECTION.search(name):
                role = "concentration"
            elif _SUPPORT_SECTION.search(name):
                role = "supporting"
            else:
                role = "core"
            rows = self.convert_section(sec, role, curriculum_extra, parent)
            if rows:
                self.display.append({"label": _clean_label(name), "role": role, "rows": rows})

        # Gen-ed areas already covered by required courses (tagged in the map)
        for code, note in map_notes:
            if code in self.required:
                self.note_overlap(code, note)

        return self._assemble(total)

    def _assemble(self, total):
        program = self.program
        core = [(c, i["hours"], i["title"]) for c, i in self.required.items() if i["role"] == "core"]
        conc = [(c, i["hours"], i["title"]) for c, i in self.required.items() if i["role"] == "concentration"]
        supp = [(c, i["hours"], i["title"]) for c, i in self.required.items() if i["role"] == "supporting"]

        tbc = []
        for gid, label, hours, sugg in TBC_AREAS:
            left = max(hours - self.tbc_overlap.get(gid, 0), 0)
            if left:
                tbc.append((gid, label, _nice(left), [store.label(c) for c in sugg]))

        required_codes = set(self.required)
        # Prerequisites the planner enforces: only ones that are themselves
        # planned courses, so nothing waits on a course that never gets scheduled
        override = {}
        prereq_map = {}
        for code in required_codes:
            prereqs = store.resolve_prereqs(code, prefer=required_codes)
            override[code] = {p for p in prereqs if p in required_codes and p != code}
        _break_cycles(override)

        # Lecture/lab pairs the program lists as separate required courses
        # ("BIOL 1120" and "BIOL 1121"): the planner keeps them in one term
        lab_partners: dict[str, list] = {}
        for code in sorted(required_codes):
            for co in (store.course(code) or {}).get("coreqs", []):
                if co in required_codes and _is_lab(co) and not _is_lab(code):
                    lab_partners.setdefault(code, []).append(co)

        name = program["name"]
        concentration = None
        m = re.match(r"^(.*?),\s*(.+?Concentration.*?),\s*([A-Z][A-Za-z.]+\.?)\s*$", name)
        if m:
            concentration = m.group(2)

        base = {
            "key": self.key,
            "prefix": self.prefix,
            "name": name,
            "catalog_driven": True,
            "catalog_program_id": program["id"],
            "catalog_url": program.get("url"),
            "department": program.get("department"),
            "description": (program.get("intro") or "")[:300],
            "concentration": concentration,
            "available": True,
        }
        if self.kind == "minor":
            elective_hours = sum(h for _, _, h, _ in self.generic)
            options = [c for g in self.generic_options.values() for c in g]
            text = " ".join(row[1] for row in self.generic) + " " + " ".join(
                " ".join(s) for _, _, _, s in self.generic)
            required_hours = sum(h for _, h, _ in core + conc + supp)
            return {
                **base,
                "required_courses": core + conc + supp,
                "elective_hours": _nice(elective_hours),
                "elective_min_number": 3000 if _UD_TEXT.search(text) else 1000,
                "elective_options": list(dict.fromkeys(options)),
                "elective_prefix_rule": not options or bool(re.search(r"upper[- ]division|additional hours|electives?", text, re.I)),
                "elective_suggestions": [store.label(c) for c in options[:3]] or [f"{self.prefix} electives"],
                "total_hours": _nice(required_hours + elective_hours),
                "description": self.alternatives_note or self._minor_summary(core + conc + supp, elective_hours),
                "coreq_map": self.coreq_map,
            }

        return {
            **base,
            "core_courses": core,
            "concentration_courses": conc,
            "concentration_elective_hours": _nice(self.ud_hours),
            "high_level_options": [],
            "supporting_courses": supp,
            "supporting_generic": self.generic,
            "generic_options": self.generic_options,
            "tbc_generic": tbc,
            "total_hours": total,
            "prereq_map": prereq_map,
            "prereq_override_map": override,
            "supporting_prereq_map": {},
            "offering_seasons": {},
            "odd_year_spring_only": set(),
            "coreq_map": self.coreq_map,
            "lab_partners": lab_partners,
            "upper_division_prefix": self.prefix,
            "upper_division_min": 3000,
            "minor_bucket_id": self.minor_bucket,
            "display_sections": self.display,
        }

    def _minor_summary(self, required, elective_hours):
        codes = [c for c, _, _ in required]
        parts = []
        if codes:
            shown = ", ".join(codes[:4]) + ("…" if len(codes) > 4 else "")
            parts.append(shown)
        if elective_hours:
            parts.append(f"{_nice(elective_hours)} hrs of electives")
        return " plus ".join(parts) if parts else "See the catalog for requirements."


def _is_lab(code: str) -> bool:
    title = (store.title(code) or "").lower()
    return bool(re.search(r"\blab(oratory)?\b", title))


def _break_cycles(graph: dict) -> None:
    """Drop prerequisite edges that form a cycle (A needs B, B needs A).

    The catalog occasionally words same-term courses as prerequisites of
    each other; enforced as written, neither could ever be scheduled. Courses
    in a cycle are treated as taken together instead.
    """
    index, low, on_stack, stack, comps = {}, {}, set(), [], []
    counter = [0]

    def strongconnect(v):
        index[v] = low[v] = counter[0]
        counter[0] += 1
        stack.append(v)
        on_stack.add(v)
        for w in graph.get(v, ()):
            if w not in graph:
                continue
            if w not in index:
                strongconnect(w)
                low[v] = min(low[v], low[w])
            elif w in on_stack:
                low[v] = min(low[v], index[w])
        if low[v] == index[v]:
            comp = set()
            while True:
                w = stack.pop()
                on_stack.discard(w)
                comp.add(w)
                if w == v:
                    break
            if len(comp) > 1:
                comps.append(comp)

    for v in list(graph):
        if v not in index:
            strongconnect(v)
    for comp in comps:
        for v in comp:
            graph[v] = {p for p in graph[v] if p not in comp}


def _program_key(program: dict) -> str:
    return slugify(program["name"])


def _convert(program: dict, kind: str) -> dict | None:
    try:
        cfg = _Builder(program, _program_key(program), kind).build()
    except Exception as exc:  # one odd program shouldn't take the site down
        print(f"catalog_programs: skipped {program.get('name')}: {exc}")
        return None
    if kind == "major":
        n_courses = len(cfg["core_courses"]) + len(cfg["concentration_courses"]) + len(cfg["supporting_courses"])
        if n_courses == 0 and not cfg["generic_options"]:
            return None
    elif not cfg["required_courses"] and not cfg["elective_options"] and not cfg["elective_hours"]:
        return None
    return cfg


_DEGREE_SUFFIX = re.compile(r",\s*(B\.[A-Z.]+|BFA|BBA|BSN|BSW|BM|BS|BA)\s*$")


def _is_major(program) -> bool:
    # A few concentrations are filed under "Minor" in the catalog; the
    # degree suffix on the name ("..., B.S.") gives them away
    return (
        program.get("type") == "Major"
        or bool(_DEGREE_SUFFIX.search(program.get("name", "")))
        or (program.get("type") is None and (program.get("degree") or "").startswith("Bachelor"))
    )


def _ensure_built():
    store.data()  # re-reads the catalog file if it changed (and clears _cache)
    if "majors" in _cache:
        return
    majors, minors = {}, {}
    for program in store.programs():
        if not program.get("active", True):
            continue
        if _is_major(program):
            cfg = _convert(program, "major")
            if cfg:
                if cfg["key"] in majors:  # two catalog entries with one name
                    cfg["key"] = f"{cfg['key']}-{program['id']}"
                majors[cfg["key"]] = cfg
        elif program.get("type") == "Minor" or re.search(r"\bminor\b", program.get("name", ""), re.I):
            cfg = _convert(program, "minor")
            if cfg:
                if cfg["key"] in minors:  # cross-listed minors share a name
                    cfg["key"] = f"{cfg['key']}-{program['id']}"
                minors[cfg["key"]] = cfg
    _cache["majors"] = majors
    _cache["minors"] = minors


def catalog_majors() -> dict:
    _ensure_built()
    return _cache["majors"]


def catalog_minors() -> dict:
    _ensure_built()
    return _cache["minors"]


# ── True Blue Core course lists ──────────────────────────────────────────────

# The catalog's True Blue Core page describes these two areas in prose
# instead of listing courses
_TBC_FALLBACK = {
    "tbc_nwc": ["COMM 2200"],
    "tbc_quant_lit": ["MATH 1010", "MATH 1530", "MATH 1630", "MATH 1710", "MATH 1730", "MATH 1810", "MATH 1910"],
}


def tbc_area_options() -> dict:
    """{area id: [course codes]} from the catalog's True Blue Core page."""
    store.data()
    if "tbc" in _cache:
        return _cache["tbc"]
    areas: dict[str, list] = {}
    page = next((p for p in store.programs() if p["name"].strip().lower() == "true blue core"), None)
    if page:
        for sec, _, _ in _walk(page["sections"]):
            area = next((a for a, pat in _AREA_TAGS if re.search(pat, sec["name"], re.I)), None)
            if not area or not sec.get("hours"):
                continue
            codes = [c for d, _, _ in _walk([sec]) for u in parse_units(d.get("items")) for c in _unit_codes(u)]
            codes += [m.group(0) for m in re.finditer(r"\b[A-Z]{2,4} \d{4}\b", sec.get("text", ""))]
            areas[area] = [c for c in dict.fromkeys(codes) if _credits(c)]
    for area, codes in _TBC_FALLBACK.items():
        if not areas.get(area):
            areas[area] = codes
    _cache["tbc"] = areas
    return areas


# ── Auto-credit for hours requirements that list specific courses ────────────

def auto_generic_hours(cfg: dict, completed: set, required: set | None = None,
                       typed: dict | None = None) -> tuple[dict, dict]:
    """Hours each listed-course requirement gets from completed courses.

    Returns (hours_by_id, applied_codes_by_id). Each completed course is used
    at most once, and never for a course the program already requires by name.
    Hours the student *typed* for a row come first: a course only fills what
    the typed hours leave open, so it stays free for electives otherwise.
    Returned hours include the typed part.
    """
    typed = typed or {}

    def _typed(gid):
        try:
            return max(float(typed.get(gid) or 0), 0.0)
        except (TypeError, ValueError):
            return 0.0

    options = dict(cfg.get("generic_options") or {})
    caps = {gid: hours for gid, _, hours, _ in cfg.get("supporting_generic", [])}
    # Gen-ed rows fill from the True Blue Core course lists, after the
    # program's own choices have had first pick
    tbc_options = tbc_area_options()
    tbc_ids = [gid for gid, _, _, _ in cfg.get("tbc_generic", []) if gid in tbc_options]
    for gid, _, hours, _ in cfg.get("tbc_generic", []):
        caps[gid] = hours
    if required is None:
        required = {c for key in ("core_courses", "concentration_courses", "supporting_courses") for c, _, _ in cfg.get(key, [])}
    used: set = set()
    hours_by_id, applied = {}, {}
    # Narrowest choices first so a course that fits only one requirement lands there
    order = sorted(options, key=lambda g: len(options[g])) + tbc_ids
    options.update({gid: tbc_options[gid] for gid in tbc_ids})
    for gid in order:
        cap = caps.get(gid)
        if cap is None:
            continue
        already = min(_typed(gid), cap)
        total = already
        for code in options[gid]:
            if code in completed and code not in used and code not in required and total < cap:
                used.add(code)
                total += _credits(code)
                applied.setdefault(gid, []).append(code)
        if total > already:
            hours_by_id[gid] = min(total, cap)
    return hours_by_id, applied
