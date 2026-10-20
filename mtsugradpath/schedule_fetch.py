"""Download which courses MTSU actually offers each term.

Source: MTSU's public class schedule ("Dynamic Schedule" on ssb.mtsu.edu,
no login), the same data RaiderNet's class lookup shows. One request returns
every section of every subject for a term, so a refresh is about a dozen
requests.

The result (catalog_data/schedule.json) records, for each course, the terms
it had at least one section in. The planner uses it to know a course is
fall-only, never offered in summer, offered in odd-year springs, or already
scheduled for a term that's posted (e.g. next spring).
"""

from __future__ import annotations

import html
import re
import time
from datetime import date

import requests

SCHEDULE_ROOT = "https://ssb.mtsu.edu/pls/PROD"
_HEADERS = {"User-Agent": "MTSU-GradPath schedule export (student project; github.com/stevenGGG23/MTSU-GradPath)"}
SEASON_BY_SUFFIX = {"10": "Spring", "50": "Summer", "80": "Fall"}
HISTORY_YEARS = 3
# A term with fewer courses than this is still being built (or failed to
# load); it's skipped rather than read as "nothing is offered"
MIN_COURSES_PER_TERM = 300

_TITLE_RE = re.compile(r'<th CLASS="ddtitle"[^>]*><a[^>]*>([^<]+)</a>', re.I)
_CODE_RE = re.compile(r" - ([A-Z]{2,4} \d{4}[A-Z]?) - ")


def _post(url, data, retries=4):
    delay = 5.0
    for attempt in range(retries + 1):
        try:
            resp = requests.post(url, data=data, headers=_HEADERS, timeout=300)
            if resp.status_code == 200:
                return resp.content.decode("latin-1")
            if attempt == retries:
                resp.raise_for_status()
        except requests.RequestException:
            if attempt == retries:
                raise
        time.sleep(delay)
        delay *= 2
    raise RuntimeError(f"Gave up on {url}")


def list_terms() -> list[dict]:
    """[{code, season, year, name}] for every term the schedule offers."""
    resp = requests.get(f"{SCHEDULE_ROOT}/bwckschd.p_disp_dyn_sched", headers=_HEADERS, timeout=60)
    resp.raise_for_status()
    page = resp.content.decode("latin-1")
    terms = []
    for code, name in re.findall(r'<OPTION VALUE="(\d{6})"[^>]*>([^<]*)', page, re.I):
        season = SEASON_BY_SUFFIX.get(code[4:])
        if season:
            terms.append({"code": code, "season": season, "year": int(code[:4]), "name": html.unescape(name).strip()})
    return terms


def _subjects(term_code) -> list[str]:
    page = _post(f"{SCHEDULE_ROOT}/bwckgens.p_proc_term_date",
                 {"p_calling_proc": "bwckschd.p_disp_dyn_sched", "p_term": term_code})
    return sorted(set(re.findall(r'<OPTION VALUE="([A-Z]{2,4})"', page)))


def term_courses(term_code) -> set[str]:
    """Every course code with at least one section in *term_code*."""
    subjects = _subjects(term_code)
    if not subjects:
        return set()
    fields = [("term_in", term_code)]
    for name in ("sel_subj", "sel_day", "sel_schd", "sel_insm", "sel_camp", "sel_levl", "sel_sess",
                 "sel_instr", "sel_ptrm", "sel_attr"):
        fields.append((name, "dummy"))
    fields += [("sel_subj", s) for s in subjects]
    fields += [("sel_crse", ""), ("sel_title", ""), ("sel_schd", "%"), ("sel_insm", "%"),
               ("sel_from_cred", ""), ("sel_to_cred", ""), ("sel_camp", "%"), ("sel_levl", "%"),
               ("sel_ptrm", "%"), ("sel_instr", "%"), ("sel_sess", "%"), ("sel_attr", "%"),
               ("begin_hh", "0"), ("begin_mi", "0"), ("begin_ap", "a"),
               ("end_hh", "0"), ("end_mi", "0"), ("end_ap", "a")]
    page = _post(f"{SCHEDULE_ROOT}/bwckschd.p_get_crse_unsec", fields)
    codes = set()
    for title in _TITLE_RE.findall(page):
        m = _CODE_RE.search(html.unescape(title))
        if m:
            codes.add(m.group(1))
    return codes


def fetch_schedule(history_years: int = HISTORY_YEARS, today: date | None = None, log=print) -> dict:
    """Course offerings for the last *history_years* years plus posted future terms."""
    today = today or date.today()
    oldest = today.year - history_years
    terms = [t for t in list_terms() if t["year"] >= oldest]
    terms.sort(key=lambda t: t["code"])
    kept, offered = [], {}
    for term in terms:
        codes = term_courses(term["code"])
        log(f"  {term['season']} {term['year']}: {len(codes)} courses")
        if len(codes) < MIN_COURSES_PER_TERM:
            log(f"    skipped (fewer than {MIN_COURSES_PER_TERM} courses -- not published yet?)")
            continue
        kept.append({k: term[k] for k in ("code", "season", "year")})
        for code in codes:
            offered.setdefault(code, []).append(term["code"])
        time.sleep(2)  # be gentle with the registrar's server
    if len(kept) < 3:
        raise RuntimeError(f"Only {len(kept)} usable terms found; not writing a schedule")
    return {
        "fetched_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "source": f"{SCHEDULE_ROOT}/bwckschd.p_disp_dyn_sched",
        "terms": kept,
        "offered": dict(sorted(offered.items())),
    }
