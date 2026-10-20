"""Download raw catalog data from MTSU's public Acalog widget API.

The widget API (the same one behind catalog.mtsu.edu's embeddable widgets)
serves the current catalog without a token -- as long as you use the API's
*internal* catalog id, not the "catoid" seen in catalog.mtsu.edu URLs. The
2026-27 undergraduate catalog is catoid=49 on the website but id 12 here;
asking the API for 49 returns a 500/401, which is where the old "needs a
token" belief came from. ``find_current_catalog()`` resolves the right id
from /widget-api/catalogs/ so nothing has to be hardcoded.

Raw responses are cached one file per course/program under a raw directory,
so an interrupted export resumes where it left off and the normalizer
(catalog_build.py) can be re-run without touching the network.
"""

from __future__ import annotations

import json
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import requests

from .config import BASE_CATALOG_URL

API_ROOT = f"{BASE_CATALOG_URL}/widget-api"
PAGE_SIZE = 100
WORKERS = 2
# Pause per request per worker. The catalog sits behind an AWS WAF that
# starts answering HTTP 202 challenges when hit too fast.
REQUEST_PAUSE = 0.35

_HEADERS = {
    "User-Agent": "MTSU-GradPath catalog export (student project; github.com/stevenGGG23/MTSU-GradPath)",
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "X-Requested-With": "XMLHttpRequest",
    "Referer": f"{BASE_CATALOG_URL}/",
}


def get_json(url: str, retries: int = 6) -> dict:
    """GET JSON, backing off on throttling (HTTP 202 WAF challenge/429/5xx)."""
    delay = 5.0
    for attempt in range(retries + 1):
        try:
            resp = requests.get(url, headers=_HEADERS, timeout=60)
        except requests.RequestException:
            if attempt == retries:
                raise
            time.sleep(delay)
            delay *= 2
            continue
        if resp.status_code == 200:
            return resp.json()
        if resp.status_code in (202, 429, 500, 502, 503, 504) and attempt < retries:
            time.sleep(delay)
            delay *= 2
            continue
        resp.raise_for_status()
        raise RuntimeError(f"Unexpected HTTP {resp.status_code} from {url}")
    raise RuntimeError(f"Gave up on {url}")


def list_catalogs() -> list[dict]:
    return get_json(f"{API_ROOT}/catalogs/").get("catalog-list", [])


def find_current_catalog(catalog_type: str = "Undergraduate") -> dict:
    """Newest published, non-archived catalog of *catalog_type*."""
    candidates = [
        c for c in list_catalogs()
        if c.get("published")
        and not c.get("archived")
        and (c.get("catalog-type") or {}).get("name") == catalog_type
    ]
    if not candidates:
        raise RuntimeError(f"No published {catalog_type} catalog found")
    return max(candidates, key=lambda c: c.get("legacy-id") or 0)


def _list_all(catalog_id: int, kind: str) -> list[dict]:
    """Page through /catalog/<id>/<kind>/ (kind = courses or programs)."""
    key = "course-list" if kind == "courses" else "program-list"
    items, page = [], 1
    while True:
        data = get_json(f"{API_ROOT}/catalog/{catalog_id}/{kind}/?page-size={PAGE_SIZE}&page={page}")
        batch = data.get(key) or []
        items.extend(batch)
        if not batch or len(items) >= data.get("count", 0):
            return items
        page += 1


def _fetch_details(catalog_id: int, kind: str, briefs: list[dict], out_dir: Path, log) -> int:
    singular = "course" if kind == "courses" else "program"
    out_dir.mkdir(parents=True, exist_ok=True)
    todo = [b for b in briefs if not (out_dir / f"{b['id']}.json").exists()]
    log(f"{kind}: {len(briefs)} listed, {len(briefs) - len(todo)} cached, fetching {len(todo)}")

    def one(brief):
        time.sleep(REQUEST_PAUSE)
        detail = get_json(f"{API_ROOT}/catalog/{catalog_id}/{singular}/{brief['id']}/")
        tmp = out_dir / f"{brief['id']}.json.tmp"
        tmp.write_text(json.dumps(detail))
        tmp.replace(out_dir / f"{brief['id']}.json")

    done = failed = 0
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        for future in [pool.submit(one, b) for b in todo]:
            try:
                future.result()
                done += 1
            except Exception as exc:  # keep going; a re-run picks up the gaps
                failed += 1
                log(f"  failed: {exc}")
            if done and done % 250 == 0:
                log(f"  {kind}: {done}/{len(todo)}")
    if failed:
        log(f"{kind}: {failed} failed -- re-run the export to retry them")
    return failed


def fetch_raw(raw_dir: Path, catalog_id: int | None = None, log=print) -> dict:
    """Download every course and program detail for one catalog into *raw_dir*.

    Returns the catalog's metadata dict. Safe to re-run: cached files are
    skipped, so only missing items are fetched.
    """
    raw_dir = Path(raw_dir)
    raw_dir.mkdir(parents=True, exist_ok=True)

    if catalog_id is None:
        meta = find_current_catalog()
    else:
        meta = next((c for c in list_catalogs() if c["id"] == catalog_id), {"id": catalog_id})
    catalog_id = meta["id"]
    log(f"Catalog: {meta.get('name', catalog_id)} (api id {catalog_id}, catoid {meta.get('legacy-id')})")

    course_briefs = _list_all(catalog_id, "courses")
    program_briefs = _list_all(catalog_id, "programs")
    (raw_dir / "meta.json").write_text(json.dumps({
        "catalog": meta,
        "course_ids": [b["id"] for b in course_briefs],
        "program_ids": [b["id"] for b in program_briefs],
        "fetched_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }))

    failures = _fetch_details(catalog_id, "programs", program_briefs, raw_dir / "programs", log)
    failures += _fetch_details(catalog_id, "courses", course_briefs, raw_dir / "courses", log)
    if failures:
        raise RuntimeError(f"{failures} catalog items failed to download; re-run to retry")
    return meta
