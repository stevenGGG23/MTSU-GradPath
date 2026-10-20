#!/usr/bin/env python3
"""Export MTSU's catalog and class schedule for GradPath.

    python scrape_courses.py                 # catalog: download what's missing, then build
    python scrape_courses.py --refresh       # catalog: re-download everything (new catalog year)
    python scrape_courses.py --build-only    # catalog: rebuild from already-downloaded data
    python scrape_courses.py --schedule      # class schedule only (which terms courses run)
    python scrape_courses.py --refresh --schedule-too   # both
    python scrape_courses.py --catalog-id 12 # a specific catalog (widget-API id, not catoid)

Outputs: catalog_data/catalog.json (courses + programs) and
catalog_data/schedule.json (terms each course was offered).

Downloads are cached in data/raw_catalog/ (one file per course/program), so an
interrupted run resumes where it stopped. The finished JSON is what the app
loads -- commit it so deploys never have to touch the catalog website.
"""

import argparse
import json
import shutil
import sys
from pathlib import Path

from mtsugradpath.catalog_build import build_catalog
from mtsugradpath.catalog_fetch import fetch_raw
from mtsugradpath.schedule_fetch import fetch_schedule
from mtsugradpath.config import BASE_DIR, DATA_DIR

RAW_DIR = DATA_DIR / "raw_catalog"
OUT_FILE = BASE_DIR / "catalog_data" / "catalog.json"
SCHEDULE_FILE = BASE_DIR / "catalog_data" / "schedule.json"
# Sanity floor for a full undergraduate catalog (2026-27 has 3,475 courses / 397 programs)
MIN_COURSES = 2000
MIN_PROGRAMS = 200
# A refresh that loses more than this share of the current file's courses or
# programs is almost certainly a broken download/parse, not a real catalog
# change -- refuse it and keep serving the current data
MAX_SHRINK = 0.2


def _current_counts(path):
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        return len(data.get("courses", {})), len(data.get("programs", []))
    except (OSError, ValueError):
        return 0, 0


def _write_lines(tmp, head, sections):
    """JSON with one entry per line, so git diffs show exactly what changed."""
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(head)
        for i, (name, items, is_dict) in enumerate(sections):
            fh.write(f'"{name}": ' + ("{\n" if is_dict else "[\n"))
            for j, item in enumerate(items):
                text = (json.dumps(item[0]) + ": " + json.dumps(item[1], ensure_ascii=False)) if is_dict \
                    else json.dumps(item, ensure_ascii=False)
                fh.write(text + (",\n" if j < len(items) - 1 else "\n"))
            fh.write(("}" if is_dict else "]") + (",\n" if i < len(sections) - 1 else "\n"))
        fh.write("}\n")


def export_schedule(out_file=SCHEDULE_FILE, log=print):
    log("Class schedule:")
    data = fetch_schedule(log=log)
    old = 0
    try:
        with open(out_file, encoding="utf-8") as fh:
            old = len(json.load(fh).get("offered", {}))
    except (OSError, ValueError):
        pass
    if old and len(data["offered"]) < old * (1 - MAX_SHRINK):
        raise RuntimeError(f"Schedule shrank from {old} to {len(data['offered'])} courses; not writing {out_file}")
    out_file = Path(out_file)
    out_file.parent.mkdir(parents=True, exist_ok=True)
    tmp = out_file.with_suffix(".tmp")
    head = "{" + ", ".join(f'"{k}": {json.dumps(data[k])}' for k in ("fetched_at", "source", "terms")) + ",\n"
    _write_lines(tmp, head, [("offered", list(data["offered"].items()), True)])
    tmp.replace(out_file)
    log(f"Wrote {out_file}: {len(data['offered'])} courses across {len(data['terms'])} terms")
    return data


def export(out_file=OUT_FILE, raw_dir=RAW_DIR, fetch=True, refresh=False, catalog_id=None, log=print):
    raw_dir = Path(raw_dir)
    if refresh and raw_dir.exists():
        shutil.rmtree(raw_dir)
    if fetch:
        fetch_raw(raw_dir, catalog_id=catalog_id, log=log)
    data = build_catalog(raw_dir)
    # Never replace a good catalog with a broken export
    if len(data["courses"]) < MIN_COURSES or len(data["programs"]) < MIN_PROGRAMS:
        raise RuntimeError(
            f"Export looks incomplete ({len(data['courses'])} courses, {len(data['programs'])} programs); "
            f"not writing {out_file}"
        )
    old_courses, old_programs = _current_counts(out_file)
    if (old_courses and len(data["courses"]) < old_courses * (1 - MAX_SHRINK)) or (
        old_programs and len(data["programs"]) < old_programs * (1 - MAX_SHRINK)
    ):
        raise RuntimeError(
            f"Export shrank from {old_courses} courses / {old_programs} programs to "
            f"{len(data['courses'])} / {len(data['programs'])}; not writing {out_file}"
        )
    out_file = Path(out_file)
    out_file.parent.mkdir(parents=True, exist_ok=True)
    tmp = out_file.with_suffix(".tmp")
    # One course/program per line keeps git diffs between catalog years readable
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write('{"catalog": ' + json.dumps(data["catalog"]) + ',\n"courses": {\n')
        items = list(data["courses"].items())
        for i, (code, course) in enumerate(items):
            fh.write(json.dumps(code) + ": " + json.dumps(course, ensure_ascii=False) + (",\n" if i < len(items) - 1 else "\n"))
        fh.write('},\n"programs": [\n')
        for i, program in enumerate(data["programs"]):
            fh.write(json.dumps(program, ensure_ascii=False) + (",\n" if i < len(data["programs"]) - 1 else "\n"))
        fh.write("]}\n")
    tmp.replace(out_file)
    log(f"Wrote {out_file}: {len(data['courses'])} courses, {len(data['programs'])} programs "
        f"({data['catalog'].get('name')})")
    return data


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--build-only", action="store_true", help="don't download; rebuild from data/raw_catalog")
    parser.add_argument("--refresh", action="store_true", help="discard downloaded data and fetch everything again")
    parser.add_argument("--catalog-id", type=int, help="widget-API catalog id (default: newest undergraduate catalog)")
    parser.add_argument("--out", default=str(OUT_FILE), help="output path")
    parser.add_argument("--schedule", action="store_true", help="refresh only the class schedule")
    parser.add_argument("--schedule-too", action="store_true", help="refresh the catalog and the class schedule")
    args = parser.parse_args(argv)
    try:
        if not args.schedule:
            export(args.out, fetch=not args.build_only, refresh=args.refresh, catalog_id=args.catalog_id)
        if args.schedule or args.schedule_too:
            export_schedule()
    except (RuntimeError, OSError) as exc:
        print(f"Export incomplete: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
