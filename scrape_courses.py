#!/usr/bin/env python3
"""Export the current MTSU undergraduate catalog to catalog_data/catalog.json.

    python scrape_courses.py                 # download what's missing, then build
    python scrape_courses.py --build-only    # rebuild the JSON from already-downloaded data
    python scrape_courses.py --refresh       # re-download everything (new catalog year)
    python scrape_courses.py --catalog-id 12 # a specific catalog (widget-API id, not catoid)

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
from mtsugradpath.config import BASE_DIR, DATA_DIR

RAW_DIR = DATA_DIR / "raw_catalog"
OUT_FILE = BASE_DIR / "catalog_data" / "catalog.json"
# Sanity floor for a full undergraduate catalog (2026-27 has 3,475 courses / 397 programs)
MIN_COURSES = 2000
MIN_PROGRAMS = 200


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
    args = parser.parse_args(argv)
    try:
        export(args.out, fetch=not args.build_only, refresh=args.refresh, catalog_id=args.catalog_id)
    except RuntimeError as exc:
        print(f"Export incomplete: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
