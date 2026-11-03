"""University-wide checks a plan has to pass besides its program's courses
(Degree Works' "Bachelor's Degree" block) and clear elective wording.

Everything comes from the catalog: the bachelor's rules from its "Undergraduate
Degree Requirements" page (catalog_store.degree_rules), prerequisites from its
course descriptions.
"""

from __future__ import annotations

import re

from . import catalog_store

UPPER_DIVISION_ELECTIVE_LABEL = "Upper-division elective (any subject, 3000–4000 level)"
_PREFIX_IN_LABEL = re.compile(r"\b([A-Z]{2,4})\b(?=\s+(?:UD|upper[- ]division)\b)")


def _number(code: str) -> int:
    digits = re.sub(r"\D", "", code.split()[-1])[:4]
    return int(digits) if digits else 0


def is_upper_division(code: str) -> bool:
    return _number(code) >= 3000


def clarify_label(label: str) -> str:
    """Elective labels that say exactly what counts: "ACTG UD electives" ->
    "ACTG upper-division electives (3000–4000-level ACTG courses)", a bare
    "Upper-division electives" -> "... (3000–4000 level)", "Free electives" ->
    "Free electives (any course)". A subject-specific elective must be in that
    subject; a plain upper-division one only has to be 3000-4000 level."""
    if not label:
        return label
    text = re.sub(r"\bUD\b", "upper-division", label)
    if re.search(r"upper[- ]division", text, re.I) and not re.search(r"3000|4000|any subject", text):
        prefix = _PREFIX_IN_LABEL.search(re.sub(r"\bupper-division\b", "UD", text))
        text += f" (3000–4000-level {prefix.group(1)} courses)" if prefix else " (3000–4000 level)"
    elif re.fullmatch(r"(free|general) electives?", text.strip(), re.I):
        text += " (any course)"
    return text


def _chunk_is_upper_division(chunk: dict, cfg: dict) -> bool:
    if chunk.get("id") == "ud_elective_shortfall":
        return True
    if re.search(r"upper[- ]division|\bUD\b|3000|4000", chunk.get("label") or ""):
        return True
    options = (cfg.get("generic_options") or {}).get(chunk.get("id")) or []
    return bool(options) and all(is_upper_division(code) for code in options)


def _could_be_upper_division(chunk: dict, cfg: dict) -> bool:
    options = (cfg.get("generic_options") or {}).get(chunk.get("id")) or []
    return not options or any(is_upper_division(code) for code in options)


def upper_division_hours(plan: dict, completed: set, cfg: dict) -> float:
    """Upper-division (3000-4000 level) hours in completed courses plus the plan."""
    hours = sum(catalog_store.credits(c, default=3) or 0 for c in completed if is_upper_division(c))
    for items in plan.values():
        for item in items:
            if item.get("kind") == "course" and is_upper_division(item["code"]):
                hours += item.get("hours") or 0
            elif item.get("kind") == "requirement" and _chunk_is_upper_division(item, cfg):
                hours += item.get("hours") or 0
    return hours


def bachelors_checks(plan: dict, completed: set, cfg: dict, programs=()) -> list:
    """Rows for the plan page's "Bachelor's degree requirements" card.

    Mutates *plan*: when the plan falls short of the upper-division minimum,
    free-elective rows in the latest terms become "Upper-division elective
    (any subject, 3000-4000 level)" until it's met.

    *programs*: (label, cfg, minimum upper-division hours at MTSU) for the
    major, any second major, and each minor.
    """
    rules = catalog_store.degree_rules()
    rows = []

    # 36 upper-division hours -- turn late free electives into upper-division ones if needed
    needed = rules["upper_division_hours"]
    have = upper_division_hours(plan, completed, cfg)
    if have < needed:
        for term in reversed(list(plan)):
            for item in plan[term]:
                if have >= needed:
                    break
                if item.get("kind") == "requirement" and item.get("id") == "general_electives":
                    item["label"] = UPPER_DIVISION_ELECTIVE_LABEL
                    item["suggestion"] = "Any 3000- or 4000-level course"
                    have += item.get("hours") or 0
    # Choice rows that may or may not be upper-division (a minor, a pick-list
    # with options at both levels) -- the student's picks decide
    open_hours = sum(
        item.get("hours") or 0 for items in plan.values() for item in items
        if item.get("kind") == "requirement" and item.get("id") != "general_electives"
        and not str(item.get("id", "")).startswith("tbc_") and not _chunk_is_upper_division(item, cfg)
        and _could_be_upper_division(item, cfg)
    )
    if have >= needed:
        status, extra = "ok", ""
    elif have + open_hours >= needed:
        status, extra = "info", (f" Pick upper-division (3000–4000) courses for at least {_nice(needed - have)} of the "
                                 "hours where the plan lets you choose (minor, pick-lists).")
    else:
        status, extra = "warn", (f" {_nice(needed - have)} more needed — choose 3000- or 4000-level courses wherever "
                                 "the plan lets you pick.")
    rows.append({
        "status": status,
        "title": f"{needed} upper-division hours (3000–4000 level, any subject)",
        "detail": f"{_nice(have)} upper-division hours in completed courses and this plan.{extra}",
    })

    # Hours through MTSU
    planned = sum(item.get("hours") or 0 for items in plan.values() for item in items
                  if item.get("kind") in ("course", "requirement"))
    degree_hours = max(cfg.get("total_hours") or 0, rules["min_hours"])
    residence = max(rules["residence_hours"], rules["residence_percent"] / 100 * degree_hours)
    rows.append({
        "status": "ok" if planned >= residence else "info",
        "title": f"{_nice(residence)} hours earned through MTSU ({rules['residence_percent']}% of the degree)",
        "detail": (f"This plan has {_nice(planned)} hours still to take at MTSU."
                   + ("" if planned >= residence else
                      " Courses you already took at MTSU count too; transfer, exam and CLEP credit don't.")),
    })

    # Upper-division hours at MTSU in each major / minor
    for label, program_cfg, minimum in programs:
        if not minimum:
            continue
        codes = {c for key in ("core_courses", "concentration_courses", "supporting_courses", "required_courses")
                 for c, *_ in program_cfg.get(key, [])}
        ids = {gid for gid, _, _, _ in program_cfg.get("supporting_generic", [])}
        planned_ud = 0
        for items in plan.values():
            for item in items:
                if item.get("kind") == "course" and item["code"] in codes and is_upper_division(item["code"]):
                    planned_ud += item.get("hours") or 0
                elif item.get("kind") == "requirement" and item.get("id") in ids and _chunk_is_upper_division(item, program_cfg):
                    planned_ud += item.get("hours") or 0
        rows.append({
            "status": "ok" if planned_ud >= minimum else "info",
            "title": f"{minimum} upper-division hours at MTSU in {label}",
            "detail": (f"This plan has {_nice(planned_ud)} upper-division hours in it still to take at MTSU."
                       + ("" if planned_ud >= minimum else
                          " Upper-division courses you pick for its choice lists, and ones you already took at "
                          "MTSU, count too.")),
        })

    rows.append({
        "status": "info",
        "title": f"Last {rules['last_hours']} hours at MTSU",
        "detail": (f"Plan to take your last {rules['last_hours']} hours here — up to {rules['last_hours_elsewhere']} "
                   "of them may be elsewhere (or CLEP) with your college dean's approval."),
    })
    rows.append({
        "status": "info",
        "title": f"{rules['senior_college_hours']} hours from a four-year school (transfer students)",
        "detail": ("If you transferred from a community college, at least "
                   f"{rules['senior_college_hours']} of your hours must come from four-year schools such as MTSU."),
    })
    return rows


def _nice(value):
    value = float(value)
    return int(value) if value.is_integer() else round(value, 1)
