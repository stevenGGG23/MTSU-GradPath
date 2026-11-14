"""Using a student's Degree Works audit in the plan.

The planner page reads the audit PDF in the browser (static/degreeworks.js)
and sends, with the plan form, which course Degree Works used for which
requirement and whether each requirement block is complete -- no name, ID or
grades. This module applies that to GradPath's own requirement rows:

* Rows the catalog only describes in words ("Math elective (course for Math
  majors)", "Science (different prefix from year-long sequence)") can't be
  filled from a course list. The audit says which courses met them ("Math
  Elective: MATH 2010"), so those courses fill the row whose wording matches.
* A General Education block Degree Works marks complete means the True Blue
  Core rows are complete (whatever catalog year the courses were taken under).
* When Degree Works shows every block complete and nothing "Still needed",
  every requirement row is complete.

Only applied when the plan is for the major the audit was matched to.
"""
import json
import re

MAX_PAYLOAD = 48 * 1024
_CODE_RE = re.compile(r"[A-Z]{2,5} \d{4}[A-Z]?")
_DONE = {"COMPLETE"}
# IN-PROGRESS: complete once the classes in progress finish
_DONE_WITH_IP = {"COMPLETE", "IN-PROGRESS"}

# Words that don't tell requirements apart ("elective", "hours", ...)
_STOP = set("""
a an and or of in on the to for from with as at by be may can any all other others
course courses class classes hour hours hr hrs credit credits cr required requirement
requirements requires minimum grade c d option options elective electives choose select
take list complete completed level upper lower division total additional must one two three
four five six seven eight nine ten 1 2 3 4 5 6 7 8 9 10 12 15 18 30 bs ba bba bsn ma ms s
pre prerequisite prerequisites counted count area see note students student major majors
""".split())

_SYNONYMS = {
    "mathematics": "math", "maths": "math", "sciences": "science", "scientific": "science",
    "biology": "biol", "chemistry": "chem", "physics": "phys", "communication": "comm",
    "communications": "comm", "statistics": "stat", "statistical": "stat",
}


def _words(text):
    out = set()
    for word in re.findall(r"[a-z]+", (text or "").lower().replace("&", " and ")):
        word = _SYNONYMS.get(word, word)
        if len(word) > 3 and word.endswith("s") and not word.endswith("ss"):
            word = word[:-1]
        word = _SYNONYMS.get(word, word)
        if word not in _STOP and len(word) > 1:
            out.add(word)
    return out


def _plain(text):
    """A label reduced to its letters, for exact comparisons."""
    return re.sub(r"[^a-z]+", " ", (text or "").lower().replace("&", " and ")).strip().removesuffix("s")


def read_payload(raw):
    """The audit data sent with the form, checked and trimmed; None if absent
    or malformed (never an error page)."""
    if not raw or len(raw) > MAX_PAYLOAD:
        return None
    try:
        data = json.loads(raw)
    except (ValueError, RecursionError):
        return None
    if not isinstance(data, dict) or not isinstance(data.get("major"), str):
        return None

    def text(value, limit=200):
        return " ".join(str(value).split())[:limit] if isinstance(value, (str, int, float)) else ""

    blocks = []
    for block in (data.get("blocks") or [])[:40]:
        if isinstance(block, dict):
            blocks.append({
                "title": text(block.get("title")),
                "status": text(block.get("status"), 20).upper(),
                "still_needed": bool(block.get("still_needed")),
            })
    rows = []
    for row in (data.get("rows") or [])[:300]:
        if not isinstance(row, dict):
            continue
        codes = [c for c in (row.get("codes") or [])[:40] if isinstance(c, str) and _CODE_RE.fullmatch(c)]
        if codes:
            rows.append({
                "block": text(row.get("block")),
                "label": text(row.get("label")),
                "context": [text(c) for c in (row.get("context") or [])[:4]],
                "codes": codes,
            })
    in_progress = []
    for item in (data.get("in_progress") or [])[:40]:
        if isinstance(item, dict) and isinstance(item.get("code"), str) and _CODE_RE.fullmatch(item["code"]):
            in_progress.append({"code": item["code"], "term": text(item.get("term"), 20)})
    percent = data.get("percent")
    return {
        "major": data["major"][:200],
        "count_ip": bool(data.get("count_ip")),
        "percent": percent if isinstance(percent, (int, float)) and 0 <= percent <= 100 else None,
        "catalog_year": text(data.get("catalog_year"), 20),
        "blocks": blocks,
        "in_progress": in_progress,
        "rows": rows,
    }


def _met(block):
    """Done, or done once the classes in progress finish (IN-PROGRESS with
    nothing "Still needed") -- nothing new has to be planned for it."""
    return not block["still_needed"] and block["status"] in _DONE_WITH_IP


def gen_ed_complete(payload):
    """Degree Works marks the General Education / True Blue Core block done."""
    return any(
        re.search(r"general education|true blue core|\bgen(eral)? ed\b", b["title"], re.I) and _met(b)
        for b in payload["blocks"]
    )


def all_complete(payload):
    """Every block done (or done once the classes in progress finish) and
    nothing "Still needed" anywhere: nothing left to plan."""
    blocks = payload["blocks"]
    return bool(blocks) and all(_met(b) for b in blocks)


def pending_classes(payload):
    """The classes in progress still between the student and "everything
    met", when they chose not to count them as finished: (term, [codes])."""
    if payload["count_ip"] or not payload["in_progress"] or not all_complete(payload):
        return None
    terms = sorted({c["term"] for c in payload["in_progress"] if c["term"]}, key=_term_key)
    return (terms[-1] if terms else ""), [c["code"] for c in payload["in_progress"]]


def _term_key(term):
    m = re.match(r"(Spring|Summer|Fall|Winter) (\d{4})$", term or "")
    return (int(m.group(2)), ["Spring", "Summer", "Fall", "Winter"].index(m.group(1))) if m else (0, 0)


_GEN_ED_BLOCK = re.compile(r"general education|true blue core|\bgen(eral)? ed\b", re.I)
# Blocks whose courses never fill the major's rows
_OTHER_BLOCK = re.compile(r"^\s*(minor|undergraduate prerequisite|prerequisite|certificate)", re.I)


def match_rows(rows, payload, completed, used, have, credits, gen_ed_ids=(), used_gen_ed=()):
    """Fill described-in-words requirement rows from the audit.

    rows: [(gid, label, hours)] that may still need hours; completed: course
    codes counted as done; used: codes GradPath already counts toward the
    major (named courses, major rows filled from course lists); used_gen_ed:
    codes already filling True Blue Core rows; have: {gid: hours so far};
    credits(code) -> hours; gen_ed_ids: which rows are True Blue Core rows.
    Like Degree Works, a course may count once toward gen-ed and once toward
    the major ("may be counted in the True Blue Core"), never twice in either.
    Returns ({gid: added hours}, {gid: [codes]}).

    A requirement from the audit's General Education block only fills a gen-ed
    row and one from a major block only a major row; minor and prerequisite
    blocks fill nothing here. It goes to the row its own label shares the
    most meaningful words with; headings above it, then how well its hours
    fit, break ties. Generic words ("elective", "hours") never match.
    """
    gen_ed_ids = set(gen_ed_ids)
    need = {gid: max(float(hours) - float(have.get(gid) or 0), 0.0) for gid, _, hours in rows}
    label_words = {gid: _words(label) for gid, label, _ in rows}
    plain_labels = {gid: _plain(label) for gid, label, _ in rows}
    taken = {False: set(used), True: set(used_gen_ed)}
    added, applied = {}, {}

    reqs = []
    for req in payload["rows"]:
        # Count rules ("30 Credits in Humanities and Social Sciences", "9
        # Upper-Division CSCI Elective Hours") reuse courses already placed
        if re.match(r"\s*\d+\s+(semester\s+)?(credit|hour|upper|lower|percent)", req["label"], re.I):
            continue
        if _OTHER_BLOCK.search(req["block"]):
            continue
        own = _words(req["label"])
        around = set().union(*(_words(c) for c in req["context"])) if req["context"] else set()
        reqs.append({**req, "gen_ed": bool(_GEN_ED_BLOCK.search(req["block"])), "own": own,
                     "around": around - own, "plain": _plain(req["label"])})

    def key_for(req, gid, hours):
        shared = len(req["own"] & label_words[gid])
        # Only generic words on both sides ("Electives"): the same label
        same = req["plain"] == plain_labels[gid]
        if not shared and not same:
            return None
        # More shared words, then the closer label overall ("Business
        # Elective" over "College of Business Core"), then headings, then
        # how well the hours fit
        union = len(req["own"] | label_words[gid]) or 1
        return (shared + same, 1.0 if same else shared / union,
                len(req["around"] & label_words[gid]), -abs(need[gid] - hours))

    # Strongest requirement-to-row pair first, so a requirement that fits a
    # row better isn't beaten to it by one that merely shares a word
    while reqs:
        best = None
        for req in list(reqs):
            codes = [c for c in req["codes"] if c in completed and c not in taken[req["gen_ed"]]]
            if not codes:
                reqs.remove(req)
                continue
            hours = sum(credits(c) for c in codes)
            for gid, _, _ in rows:
                if need[gid] <= 0 or (gid in gen_ed_ids) != req["gen_ed"]:
                    continue
                key = key_for(req, gid, hours)
                if key is not None and (best is None or key > best[0]):
                    best = (key, req, gid, codes)
        if best is None:
            break
        _, req, gid, codes = best
        reqs.remove(req)
        # Courses go in until the row is full; extras stay free for electives
        for code in codes:
            if need[gid] <= 0:
                break
            hrs = credits(code)
            need[gid] -= hrs
            added[gid] = added.get(gid, 0.0) + hrs
            applied.setdefault(gid, []).append(code)
            taken[req["gen_ed"]].add(code)
    return added, applied


def minor_complete(payload, minor_name):
    """Degree Works shows the "Minor - <name>" block done."""
    def base(name):
        # "Art Minor (for Media and Entertainment Majors)" -> art
        return _words(re.sub(r"\([^)]*\)|\bminor\b", " ", name, flags=re.I))

    want = base(minor_name)
    for block in payload["blocks"]:
        m = re.match(r"\s*minor\s*[-–:]\s*(.+)", block["title"], re.I)
        if m and base(m.group(1)) == want and _met(block):
            return True
    return False


def mark_complete(audit):
    """A requirement audit (dict with entries) shown as fully met."""
    audit = dict(audit)
    audit["completed_hours"] = audit.get("required_hours", audit.get("completed_hours"))
    audit["percent"] = 100
    audit["entries"] = [{**entry, "done": True} for entry in audit.get("entries", [])]
    audit["from_degree_works"] = True
    return audit


def mark_degree_complete(audit):
    """The whole degree audit shown as met (Degree Works shows every
    requirement done)."""
    if not audit:
        return audit
    audit = dict(audit)
    audit["groups"] = [
        {**mark_complete(group), "completed_hours": group.get("required_hours")}
        for group in audit.get("groups", [])
    ]
    audit["total_completed"] = audit.get("total_required")
    audit["percent"] = 100
    return audit
