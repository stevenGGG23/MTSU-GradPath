"""A Degree Works audit applied to the plan (mtsugradpath/degreeworks.py).

The planner page reads the audit PDF in the browser and sends, with the plan
form, which course Degree Works used for which requirement and each block's
status. These tests use a made-up student.
"""
import json
import re

import pytest

from app import app
from mtsugradpath import catalog_store, degreeworks
from mtsugradpath.catalog_programs import auto_fill_rules
from mtsugradpath.degree_configs import find_degree_config, picker_majors
from mtsugradpath.minors import picker_minors

CS = find_degree_config("cs")

# A CS (Professional) student near the end, under an older catalog year
COMPLETED = [
    "ENGL 1010", "ENGL 1020", "COMM 2200", "HIST 2010", "HIST 2020", "ART 1030", "ENGL 2030", "MUS 1030",
    "PSY 1410", "SOC 1010", "BIOL 1110", "BIOL 1120", "CHEM 1110",
    "CSCI 1010", "CSCI 1170", "CSCI 2170", "CSCI 3033", "CSCI 3080", "CSCI 3110", "CSCI 3130", "CSCI 3210",
    "CSCI 3240", "CSCI 4160", "CSCI 4700", "CSCI 3180", "CSCI 3200", "CSCI 4250",
    "MATH 1910", "MATH 1920", "MATH 2050", "MATH 2010", "PHIL 3170",
]


def block(title, status="COMPLETE", still_needed=False):
    return {"title": title, "status": status, "still_needed": still_needed}


def req(block_title, label, *codes, context=()):
    return {"block": block_title, "label": label, "context": list(context), "codes": list(codes)}


MAJOR_BLOCK = "Major - Computer Science, BS"
AUX_BLOCK = "Auxiliary_3 - College of Basic & Applied Sciences"


def payload(blocks=None, rows=None, major=CS["key"], count_ip=True):
    return {
        "major": major,
        "count_ip": count_ip,
        "percent": 90,
        "catalog_year": "2023-2024",
        "blocks": blocks if blocks is not None else [
            block("BS - Bachelor of Science", "IN-PROGRESS", still_needed=True),
            block("General Education"),
            block(MAJOR_BLOCK, "INCOMPLETE", still_needed=True),
            block(AUX_BLOCK),
        ],
        "rows": rows if rows is not None else [
            req("General Education", "MATHEMATICS", "MATH 1910"),
            req(MAJOR_BLOCK, "9 Upper-Division CSCI Elective Hours", "CSCI 3180", "CSCI 3200", "CSCI 4250"),
            req(AUX_BLOCK, "Math Elective", "MATH 2010",
                context=["MATHEMATICS CORE/SCIENCE OPTION/", "ADDITIONAL COURSE REQUIREMENTS"]),
            req(AUX_BLOCK, "BIOLOGY SEQUENCE", "BIOL 1110", "BIOL 1120",
                context=["NATURAL SCIENCE OPTIONS", "SCIENCE OPTION 1"]),
            req(AUX_BLOCK, "SCIENCE COURSE", "CHEM 1110", context=["NATURAL SCIENCE OPTIONS", "SCIENCE OPTION 1"]),
            req(AUX_BLOCK, "30 Credits in Humanities and Social Sciences", "PSY 1410", "SOC 1010"),
        ],
    }


def post(dw=None, major=None, completed=COMPLETED, minors=(), raw=None):
    data = {
        "major": major or CS["key"],
        "completed_courses": "\n".join(completed),
        "start_season": "Spring", "start_year": "2027", "target_semesters": "4",
        "minor": list(minors),
    }
    if raw is not None:
        data["degree_works"] = raw
    elif dw is not None:
        data["degree_works"] = json.dumps(dw)
    resp = app.test_client().post("/", data=data)
    assert resp.status_code == 200
    return resp.get_data(as_text=True)


def percent(html):
    return int(re.search(r'progress-ring-value">(\d+)%', html).group(1))


def group_hours(html, name):
    return re.search(re.escape(name) + r'</span>\s*<span class="text-muted">([^<]+)<', html).group(1)


def counted(html):
    """Requirement label -> the courses its "Counted:" line lists."""
    out = {}
    for label, applied in re.findall(
            r'audit-item-label">([^<]+)</span>.*?(?:audit-item-applied">Counted: ([^<]+)<|</div>\s*</div>)', html, re.S):
        if applied:
            out[label.strip()] = applied.split(", ")
    return out


def _row_ids(cfg, word):
    return [gid for gid, label, _, _ in cfg["supporting_generic"] if word in label.lower()]


# ── Requirement rows the catalog only describes in words ──────────────────────


def test_described_rows_fill_the_way_degree_works_applied_them():
    without = post()
    html = post(payload())
    assert group_hours(without, "Supporting Courses") != group_hours(html, "Supporting Courses")
    assert group_hours(html, "Supporting Courses") == "32 / 32 cr hrs"
    rows = counted(html)
    math = next(v for k, v in rows.items() if k.startswith("Math elective"))
    sequence = next(v for k, v in rows.items() if "year-long sequence in the True Blue Core" in k)
    other = next(v for k, v in rows.items() if "Different prefix" in k)
    assert math == ["MATH 2010"]
    # The sequence also counts in the True Blue Core, as the catalog allows
    assert sequence == ["BIOL 1110", "BIOL 1120"]
    assert other == ["CHEM 1110"]
    assert "Using your Degree Works audit (90% there, 2023-2024 catalog)" in html


def test_matching_is_by_wording_not_order():
    # The same requirements listed the other way round land the same way
    shuffled = payload()
    shuffled["rows"] = list(reversed(shuffled["rows"]))
    assert counted(post(shuffled)) == counted(post(payload()))


def test_gen_ed_and_count_rules_dont_fill_major_rows():
    # Only the gen-ed MATHEMATICS row and a "30 credits in ..." count rule:
    # neither may fill the major's Math elective or science rows
    rows = [req("General Education", "MATHEMATICS", "MATH 2010"),
            req(AUX_BLOCK, "30 Credits in Mathematics and Sciences", "BIOL 1110", "BIOL 1120", "CHEM 1110"),
            req("Minor - Data Science", "Introduction Data Science", "CHEM 1110")]
    html = post(payload(rows=rows))
    assert group_hours(html, "Supporting Courses") == group_hours(post(), "Supporting Courses")


def test_label_matching_finds_the_right_row_in_every_major():
    """For every major, a Degree Works requirement worded like one of its
    described-in-words rows fills that row and not a neighbour."""
    checked = 0
    for _, cfg in picker_majors():
        _, options, _ = auto_fill_rules(cfg)
        rows = [(gid, label, hours) for gid, label, hours, _ in cfg["supporting_generic"] + cfg["tbc_generic"]]
        labels = {gid: label for gid, label, _ in rows}
        gen_ed = {gid for gid, _, _, _ in cfg["tbc_generic"]}
        for gid, label, hours, _ in cfg["supporting_generic"]:
            if options.get(gid) or not hours:
                continue
            data = degreeworks.read_payload(json.dumps({"major": cfg["key"], "rows": [req("Major - X", label, "ZZZ 9999")]}))
            _, applied = degreeworks.match_rows(rows, data, {"ZZZ 9999"}, set(), {}, lambda _c: 3, gen_ed_ids=gen_ed)
            got = next(iter(applied), None)
            assert got is not None, (cfg["key"], label)
            assert got == gid or degreeworks._plain(labels[got]) == degreeworks._plain(label), (cfg["key"], label, labels[got])
            checked += 1
    assert checked > 200


# ── Blocks Degree Works shows complete ────────────────────────────────────────


def test_every_major_is_complete_when_degree_works_says_so():
    """Whatever catalog year the student is under, an audit with every block
    met and nothing "Still needed" means nothing left to plan."""
    for key, cfg in picker_majors():
        dw = payload(major=cfg["key"], rows=[], blocks=[block("Major - X"), block("General Education"),
                                                         block("BS", "IN-PROGRESS")])
        html = post(dw, major=key, completed=[])
        assert percent(html) == 100, key
        assert not re.search(r'term-card-title">', html), key
        assert "Nothing left to plan." in html, key


def test_in_progress_blocks_count_only_when_the_student_counts_those_classes():
    blocks = [block("Major - X", "IN-PROGRESS"), block("General Education")]
    assert degreeworks.all_complete(degreeworks.read_payload(json.dumps(payload(blocks=blocks, count_ip=True))))
    assert not degreeworks.all_complete(degreeworks.read_payload(json.dumps(payload(blocks=blocks, count_ip=False))))
    # "Still needed" anywhere means not done, whatever the status says
    blocks = [block("Major - X", "COMPLETE", still_needed=True), block("General Education")]
    assert not degreeworks.all_complete(degreeworks.read_payload(json.dumps(payload(blocks=blocks))))


def test_complete_gen_ed_block_fills_the_true_blue_core():
    # Only a few gen-eds checked off, but Degree Works says the block is done
    html = post(payload(rows=[]), completed=["CSCI 1170", "ENGL 1010"])
    assert group_hours(html, "True Blue Core (general education)") == "35 / 35 cr hrs"
    blocks = [block("General Education", "INCOMPLETE", still_needed=True), block(MAJOR_BLOCK, "INCOMPLETE")]
    html = post(payload(rows=[], blocks=blocks), completed=["CSCI 1170", "ENGL 1010"])
    assert group_hours(html, "True Blue Core (general education)") != "35 / 35 cr hrs"


def test_every_minor_matches_its_degree_works_block():
    names = [m["name"] for _, m in picker_minors()]

    def base(name):
        return " ".join(re.sub(r"\([^)]*\)|\bMinor\b", " ", name).split())

    for name in names:
        # Degree Works titles a block "Minor - Data Science"
        data = degreeworks.read_payload(json.dumps(payload(blocks=[block("Minor - " + base(name))])))
        assert degreeworks.minor_complete(data, name), name
        # ...and no differently named minor matches it
        assert {base(n) for n in names if degreeworks.minor_complete(data, n)} == {base(name)}, name


def test_complete_minor_block_shows_the_minor_done():
    key, minor = next((k, m) for k, m in picker_minors() if m["name"] == "Data Science Minor")
    blocks = [block(MAJOR_BLOCK, "INCOMPLETE", still_needed=True), block("Minor - Data Science", "IN-PROGRESS")]
    html = post(payload(rows=[], blocks=blocks), minors=[key], completed=COMPLETED + ["DATA 1500"])
    card = re.search(r'extra-progress-name">Data Science Minor<.*?extra-progress-hours">([^<]+)<', html, re.S)
    hours = card.group(1).split(" / ")
    assert hours[0] == hours[1].replace(" cr hrs", "")


# ── Only for the right major, and never an error ──────────────────────────────


def test_audit_for_another_major_is_ignored():
    other = payload(major="accounting-bba")
    assert percent(post(other)) == percent(post())
    assert "Using your Degree Works audit" not in post(other)


@pytest.mark.parametrize("raw", [
    "", "not json", "[]", "null", json.dumps({"major": 5}), json.dumps({"major": CS["key"], "rows": "x"}),
    json.dumps({"major": CS["key"], "rows": [{"codes": ["DROP TABLE"], "label": {"a": 1}}], "blocks": [7]}),
    "x" * 60000,
])
def test_malformed_audit_data_is_ignored(raw):
    assert percent(post(raw=raw)) == percent(post())


def test_payload_never_carries_more_than_it_needs():
    data = degreeworks.read_payload(json.dumps({**payload(), "name": "Raider, Test", "student_id": "M00000000"}))
    assert set(data) == {"major", "count_ip", "percent", "catalog_year", "blocks", "rows"}


# ── Step 3's live preview ──────────────────────────────────────────────────────


def preview(dw, major=None, completed=COMPLETED):
    resp = app.test_client().post("/degreeworks/preview", data={
        "major": major or CS["key"], "completed_courses": "\n".join(completed),
        "degree_works": json.dumps(dw),
    })
    assert resp.status_code == 200
    return resp.get_json()


def test_step_3_preview_matches_the_plan():
    rows = preview(payload())
    by_label = {label: gid for gid, label, _, _ in CS["supporting_generic"]}
    math = next(gid for label, gid in by_label.items() if label.startswith("Math elective"))
    assert rows[math] == {"codes": ["MATH 2010"], "full": False, "hours": 3.0}
    sequence = next(gid for label, gid in by_label.items() if "year-long sequence in the True Blue Core" in label)
    assert rows[sequence]["codes"] == ["BIOL 1110", "BIOL 1120"]


def test_step_3_preview_ignores_an_audit_for_another_major():
    assert preview(payload(major="accounting-bba")) == {}
    assert app.test_client().post("/degreeworks/preview", data={"major": CS["key"]}).get_json() == {}
