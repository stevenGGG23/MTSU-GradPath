"""In-memory access to the exported MTSU catalog.

The catalog (every course and every program in the current undergraduate
catalog) ships with the app as ``catalog_data/catalog.json``, produced by
``python scrape_courses.py``. It's loaded once per process and indexed, so
page loads and plan generation never wait on the network or the database.

An admin re-export (``/sync?force=1``) writes a fresh copy to the data
directory; whichever copy is newer wins, and other workers pick it up the
next time they check the file's modification time.
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
from collections import defaultdict
from pathlib import Path

from .config import BASE_DIR, DATA_DIR

BUNDLED_FILE = BASE_DIR / "catalog_data" / "catalog.json"
RUNTIME_FILE = DATA_DIR / "catalog.json"

log = logging.getLogger(__name__)
_lock = threading.Lock()
_state: dict = {"path": None, "mtime": None, "data": None, "by_prefix": None, "search": None,
               "sequence": None, "loops": None, "checked": 0.0}


def _candidate_file() -> Path | None:
    override = os.getenv("GRADPATH_CATALOG_FILE")
    if override:
        return Path(override)
    files = [p for p in (RUNTIME_FILE, BUNDLED_FILE) if p.exists()]
    if not files:
        return None
    if len(files) == 1:
        return files[0]

    def fetched(path):
        try:
            with open(path, encoding="utf-8") as fh:
                head = fh.read(4096)
            marker = '"fetched_at": "'
            i = head.find(marker)
            return head[i + len(marker): i + len(marker) + 19] if i >= 0 else ""
        except OSError:
            return ""

    return max(files, key=fetched)


def _empty() -> dict:
    return {"catalog": {}, "courses": {}, "programs": []}


def _read(path) -> dict | None:
    if not path or not Path(path).exists():
        return None
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        if not isinstance(data.get("courses"), dict) or not isinstance(data.get("programs"), list):
            raise ValueError("missing courses/programs")
        return data
    except (OSError, ValueError) as exc:
        log.error("Couldn't read catalog file %s: %s", path, exc)
        return None


_CHECK_EVERY = 5.0  # seconds between checks for a newer catalog file


def _load() -> None:
    now = time.monotonic()
    if _state["data"] is not None and now - _state["checked"] < _CHECK_EVERY:
        return
    _state["checked"] = now
    path = _candidate_file()
    mtime = path.stat().st_mtime if path and path.exists() else None
    if _state["data"] is not None and path == _state["path"] and mtime == _state["mtime"]:
        return
    with _lock:
        if _state["data"] is not None and path == _state["path"] and mtime == _state["mtime"]:
            return
        data = _read(path)
        if data is None and path != BUNDLED_FILE and BUNDLED_FILE.exists():
            # A broken runtime copy shouldn't take the site down
            data = _read(BUNDLED_FILE)
            path, mtime = BUNDLED_FILE, BUNDLED_FILE.stat().st_mtime
        if data is None:
            if _state["data"] is not None:
                return  # keep serving what we already have
            data = _empty()
        by_prefix = defaultdict(list)
        for code in data["courses"]:
            by_prefix[code.split()[0]].append(code)
        _state.update(path=path, mtime=mtime, data=data, by_prefix=dict(by_prefix), search=None, sequence=None,
                      loops=None)
        # Converted program configs depend on this data
        from . import catalog_programs
        catalog_programs.reset_cache()


def data() -> dict:
    _load()
    return _state["data"]


def reload() -> None:
    """Force the next access to re-read the catalog file."""
    _state["mtime"] = None
    _state["checked"] = 0.0


def meta() -> dict:
    return data().get("catalog", {})


def courses() -> dict:
    return data()["courses"]


def course(code: str) -> dict | None:
    return courses().get(code)


def programs() -> list:
    return data()["programs"]


def has_data() -> bool:
    return bool(courses())


def codes_for_prefix(prefix: str) -> list:
    data()
    return _state["by_prefix"].get(prefix, [])


def credits(code: str, default=3):
    info = course(code)
    value = info.get("credits") if info else None
    return default if value is None else value


def title(code: str) -> str:
    info = course(code)
    return info.get("title", "") if info else ""


def label(code: str) -> str:
    t = title(code)
    return f"{code} - {t}" if t else code


def search_index() -> list:
    """[{code, label, credits}] for the course search box (cached)."""
    data()
    if _state["search"] is None:
        _state["search"] = [
            {"code": code, "label": f"{code} - {info.get('title', '')}", "credits": info.get("credits") or 0}
            for code, info in courses().items()
        ]
    return _state["search"]


_PART_TITLE = re.compile(r"^(.*\S)\s+(VI|IV|V|I{1,3}|[1-6])$")
_ROMAN = {"I": 1, "II": 2, "III": 3, "IV": 4, "V": 5, "VI": 6}


def previous_part(code: str) -> list:
    """The course(s) one step earlier in a numbered sequence: same subject,
    same title, part number one lower -- "Calculus II" -> "Calculus I",
    "Professional Pilot Flight Lab II" -> "... Flight Lab I". Built from
    the catalog's titles, so new sequences are picked up automatically."""
    data()
    if _state["sequence"] is None:
        parts: dict = {}
        for other, info in courses().items():
            m = _PART_TITLE.match((info.get("title") or "").strip())
            if m:
                n = _ROMAN.get(m.group(2)) or int(m.group(2))
                key = (other.split()[0], m.group(1).lower())
                parts.setdefault(key, {}).setdefault(n, []).append(other)
        def independent(earlier, later):
            # "HIST 1010 is NOT a prerequisite for HIST 1020": either order
            desc = (course(earlier) or {}).get("desc") or ""
            return bool(re.search(r"not\s+a\s+prerequisite\s+for\s+" + re.escape(later), desc, re.I))

        sequence = {}
        for by_part in parts.values():
            for n, codes in by_part.items():
                for c in codes:
                    earlier = [e for e in by_part.get(n - 1, []) if not independent(e, c)]
                    if earlier:
                        sequence[c] = earlier
        _state["sequence"] = sequence
    return _state["sequence"].get(code, [])


def _loop_links() -> set:
    """(course, prerequisite) links that sit inside a prerequisite loop
    (A needs B, B needs A -- a catalog error). They're ignored so a plan can
    never wait forever. The 2026-27 catalog has none."""
    data()
    if _state["loops"] is None:
        graph = {}
        for code, info in courses().items():
            links = set(info.get("prereqs", []))
            for group in info.get("prereq_any", []):
                links |= set(group)
            links |= set(previous_part(code))
            graph[code] = {p for p in links if p in courses() and p != code}
        # Tarjan's strongly connected components, iteratively
        index, low, on_stack, stack, loops = {}, {}, set(), [], set()
        counter = 0
        for root in graph:
            if root in index:
                continue
            work = [(root, iter(graph[root]))]
            index[root] = low[root] = counter
            counter += 1
            stack.append(root)
            on_stack.add(root)
            while work:
                node, children = work[-1]
                for child in children:
                    if child not in index:
                        index[child] = low[child] = counter
                        counter += 1
                        stack.append(child)
                        on_stack.add(child)
                        work.append((child, iter(graph[child])))
                        break
                    if child in on_stack:
                        low[node] = min(low[node], index[child])
                else:
                    work.pop()
                    if work:
                        low[work[-1][0]] = min(low[work[-1][0]], low[node])
                    if low[node] == index[node]:
                        component = set()
                        while True:
                            member = stack.pop()
                            on_stack.discard(member)
                            component.add(member)
                            if member == node:
                                break
                        if len(component) > 1:
                            loops |= {(c, p) for c in component for p in graph[c] if p in component}
        _state["loops"] = loops
    return _state["loops"]


def resolve_prereqs(code: str, prefer: set | None = None) -> set:
    """A course's prerequisites as one set: every required code, plus one
    pick from each "A or B" group -- the option in *prefer* if there is one
    (e.g. one the student completed or the program requires), else the first.

    Part II of a numbered sequence also waits for part I, even where the
    catalog's prerequisite line doesn't name it (Flight Lab II asks for a
    "Private Pilot Certificate", which Flight Lab I earns). Like any other
    prerequisite, the planner only enforces it when both are on the plan."""
    info = course(code) or {}
    result = set(info.get("prereqs", []))
    prefer = prefer or set()
    for group in info.get("prereq_any", []):
        picked = next((c for c in group if c in prefer), None)
        result.add(picked or group[0])
    earlier = previous_part(code)
    if earlier and not result & set(earlier):
        result.add(next((c for c in earlier if c in prefer), earlier[0]))
    loops = _loop_links()
    return {p for p in result if (code, p) not in loops} if loops else result


def prerequisite_options(code: str) -> tuple[set, list]:
    """(courses all required, ["A or B" groups]) a course lists as
    prerequisites -- with part I of a numbered sequence counted as required,
    and links inside a catalog prerequisite loop left out."""
    info = course(code) or {}
    loops = _loop_links()
    required = {p for p in [*info.get("prereqs", []), *previous_part(code)] if (code, p) not in loops and p != code}
    groups = [[p for p in group if (code, p) not in loops and p != code] for group in info.get("prereq_any", [])]
    return required, [g for g in groups if g]


# "Junior standing", "junior or senior standing", "junior-level classification
# or higher", "Senior status", "upper division status": the lowest class named
_STANDING_PHRASE = re.compile(
    r"\b(sophomore|junior|senior|upper[- ]division)(?:[- ]level)?"
    r"(?:\s*(?:/|or|and)\s*(?:junior|senior)(?:[- ]level)?)*"
    r"\s+(standing|classification|status|class|level)\b(?!\s+courses?)",
    re.I,
)
# MTSU class standing by earned hours: sophomore 30, junior 60, senior 90
_STANDING_HOURS = {"sophomore": 30, "junior": 60, "upper division": 60, "upper-division": 60, "senior": 90}


def standing_hours(code: str) -> int:
    """Earned hours a course's prerequisite line asks for through class
    standing ("Junior standing" -> 60), or 0. A standing only "recommended"
    doesn't count."""
    text = (course(code) or {}).get("prereq_text") or ""
    hours = [
        _STANDING_HOURS[m.group(1).lower()]
        for m in _STANDING_PHRASE.finditer(text)
        if not re.match(r"[\s,]*(is\s+)?recommended", text[m.end():], re.I)
    ]
    return min(hours) if hours else 0


def catalog_entry(code: str, prefer: set | None = None) -> dict:
    """The planner's per-course dict: title, credits, prereqs, prereq_grades,
    coreqs, and min_hours (class standing the course asks for)."""
    info = course(code) or {}
    return {
        "title": info.get("title", ""),
        "credits": info.get("credits") or 0,
        "prereqs": resolve_prereqs(code, prefer),
        "prereq_grades": dict(info.get("min_grades", {})),
        "coreqs": set(info.get("coreqs", [])),
        "min_hours": standing_hours(code),
    }


def planner_catalog(prefixes=(), codes=(), prefer: set | None = None) -> dict:
    """Planner catalog for every course under *prefixes* plus *codes*."""
    wanted = set(codes)
    for prefix in prefixes:
        wanted.update(codes_for_prefix(prefix))
    return {code: catalog_entry(code, prefer) for code in wanted if code in courses()}


# ── Class schedule (which terms each course actually ran) ───────────────────

SCHEDULE_FILE = BASE_DIR / "catalog_data" / "schedule.json"
_SUFFIX = {"Spring": "10", "Summer": "50", "Fall": "80"}
_sched: dict = {"mtime": None, "data": None, "checked": 0.0}


def schedule() -> dict | None:
    """The exported class schedule, or None when there isn't one.

    Adds two derived keys: "complete" (term codes whose schedule looks fully
    posted -- at least 70% of that season's typical course count) and
    "by_season" ({season: [term dicts]} in date order).
    """
    now = time.monotonic()
    if _sched["data"] is not None and now - _sched["checked"] < _CHECK_EVERY:
        return _sched["data"] or None
    _sched["checked"] = now
    try:
        mtime = SCHEDULE_FILE.stat().st_mtime
    except OSError:
        _sched.update(mtime=None, data={})
        return None
    if mtime == _sched["mtime"]:
        return _sched["data"] or None
    try:
        with open(SCHEDULE_FILE, encoding="utf-8") as fh:
            data = json.load(fh)
        offered = {code: set(terms) for code, terms in data["offered"].items()}
        counts = {}
        for terms in offered.values():
            for term in terms:
                counts[term] = counts.get(term, 0) + 1
        by_season = {}
        for term in sorted(data["terms"], key=lambda t: t["code"]):
            by_season.setdefault(term["season"], []).append(term)
        complete = set()
        for season, terms in by_season.items():
            sizes = sorted(counts.get(t["code"], 0) for t in terms)
            typical = sizes[len(sizes) // 2] if sizes else 0
            complete |= {t["code"] for t in terms if counts.get(t["code"], 0) >= 0.7 * typical}
        parsed = {**data, "offered": offered, "complete": complete, "by_season": by_season}
    except (OSError, ValueError, KeyError, TypeError) as exc:
        log.error("Couldn't read class schedule %s: %s", SCHEDULE_FILE, exc)
        if _sched["data"]:
            return _sched["data"]
        parsed = {}
    _sched.update(mtime=mtime, data=parsed)
    return parsed or None


def offered_in(code: str, season: str, year: int):
    """True/False from MTSU's own class schedules, or None when they don't say.

    - A term whose schedule is posted: exactly whether the course has a section.
    - Otherwise its history in that season over the last few years: never run
      in that season -> False; only in odd (or even) years -> that parity;
      otherwise True.
    - A course with no history at all -> None (caller decides).
    """
    sched = schedule()
    if not sched:
        return None
    seen = sched["offered"].get(code)
    term = f"{year}{_SUFFIX.get(season, '')}"
    if term in sched["complete"]:
        return bool(seen) and term in seen
    if not seen:
        return None
    pattern = _season_pattern(sched, seen, season)
    if pattern is None:
        return None
    if pattern == "never":
        return False
    if pattern in ("odd", "even"):
        return (year % 2 == 1) == (pattern == "odd")
    return True


def _season_pattern(sched, seen, season):
    """'every' / 'odd' / 'even' / 'never' for a course in one season, or None
    when there isn't enough history to say. Only terms since the course first
    appeared count, so a brand-new course isn't judged on years before it
    existed; alternate-year and never-in-this-season need two observations."""
    first = min(seen)
    history = [t for t in sched["by_season"].get(season, [])
               if t["code"] in sched["complete"] and t["code"] >= first]
    hits = [t for t in history if t["code"] in seen]
    if not hits:
        return "never" if len(history) >= 2 else None
    parities = {t["year"] % 2 for t in hits}
    if len(parities) == 1 and len(hits) >= 2:
        misses = [t for t in history if t["year"] % 2 not in parities]
        if len(misses) >= 2:
            return "odd" if parities == {1} else "even"
    return "every"


def offering_summary(code: str) -> str:
    """Short label like "Fall only", "Spring & summer", "Odd-year springs"
    when a course isn't offered every fall and spring; "" otherwise."""
    sched = schedule()
    if not sched or not sched["offered"].get(code):
        return ""
    seasons = []
    for season in ("Fall", "Spring", "Summer"):
        pattern = _season_pattern(sched, sched["offered"][code], season)
        if pattern in ("odd", "even"):
            seasons.append(f"{pattern}-year {season.lower()}s")
        elif pattern == "every":
            seasons.append(season.lower())
    if not seasons or (len(seasons) >= 2 and {"fall", "spring"} <= set(seasons)):
        return ""
    text = " & ".join(seasons)
    text = text[0].upper() + text[1:]
    return text + (" only" if len(seasons) == 1 and "-year" not in text else "")


def summer_only(code: str) -> bool:
    """True when MTSU's schedules show a course runs in summers but never in
    fall or spring (e.g. a field course)."""
    sched = schedule()
    seen = sched["offered"].get(code) if sched else None
    if not seen:
        return False
    patterns = {season: _season_pattern(sched, seen, season) for season in ("Fall", "Spring", "Summer")}
    return patterns["Fall"] == "never" and patterns["Spring"] == "never" and patterns["Summer"] not in (None, "never")


def offered_seasons(code: str) -> list:
    """Seasons a course runs in, from MTSU's schedules ("Fall", "Spring",
    "Summer"). A course without enough history counts as fall and spring --
    the same assumption the planner makes."""
    sched = schedule()
    seen = sched["offered"].get(code) if sched else None
    if not seen:
        return ["Fall", "Spring"]
    out = []
    for season in ("Fall", "Spring", "Summer"):
        pattern = _season_pattern(sched, seen, season)
        if pattern in ("every", "odd", "even") or (pattern is None and season != "Summer"):
            out.append(season)
    return out or ["Fall", "Spring"]
