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
import os
import threading
import time
from collections import defaultdict
from pathlib import Path

from .config import BASE_DIR, DATA_DIR

BUNDLED_FILE = BASE_DIR / "catalog_data" / "catalog.json"
RUNTIME_FILE = DATA_DIR / "catalog.json"

_lock = threading.Lock()
_state: dict = {"path": None, "mtime": None, "data": None, "by_prefix": None, "search": None, "checked": 0.0}


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
            with open(path) as fh:
                head = fh.read(4096)
            marker = '"fetched_at": "'
            i = head.find(marker)
            return head[i + len(marker): i + len(marker) + 19] if i >= 0 else ""
        except OSError:
            return ""

    return max(files, key=fetched)


def _empty() -> dict:
    return {"catalog": {}, "courses": {}, "programs": []}


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
        data = _empty()
        if path and path.exists():
            with open(path) as fh:
                data = json.load(fh)
        by_prefix = defaultdict(list)
        for code in data["courses"]:
            by_prefix[code.split()[0]].append(code)
        _state.update(path=path, mtime=mtime, data=data, by_prefix=dict(by_prefix), search=None)
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


def resolve_prereqs(code: str, prefer: set | None = None) -> set:
    """A course's prerequisites as one set: every required code, plus one
    pick from each "A or B" group -- the option in *prefer* if there is one
    (e.g. one the student completed or the program requires), else the first."""
    info = course(code) or {}
    result = set(info.get("prereqs", []))
    prefer = prefer or set()
    for group in info.get("prereq_any", []):
        picked = next((c for c in group if c in prefer), None)
        result.add(picked or group[0])
    return result


def catalog_entry(code: str, prefer: set | None = None) -> dict:
    """The planner's per-course dict: title, credits, prereqs, prereq_grades."""
    info = course(code) or {}
    return {
        "title": info.get("title", ""),
        "credits": info.get("credits") or 0,
        "prereqs": resolve_prereqs(code, prefer),
        "prereq_grades": dict(info.get("min_grades", {})),
        "coreqs": set(info.get("coreqs", [])),
    }


def planner_catalog(prefixes=(), codes=(), prefer: set | None = None) -> dict:
    """Planner catalog for every course under *prefixes* plus *codes*."""
    wanted = set(codes)
    for prefix in prefixes:
        wanted.update(codes_for_prefix(prefix))
    return {code: catalog_entry(code, prefer) for code in wanted if code in courses()}
