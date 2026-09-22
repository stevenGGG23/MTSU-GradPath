import json
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from html import unescape
from pathlib import Path

import requests
from bs4 import BeautifulSoup
from .config import (
    BASE_CATALOG_URL,
    CACHE_FILE,
    CATALOG_IDS,
    CATALOG_HTML_CATOID,
    CATALOG_HTML_NAVOID,
    PROGRAM_PREFIX,
)
from .db import SessionLocal
from .models import Course, CourseType, Prerequisite

PAGE_SIZE = 100  # Acalog widget API caps page-size at 100

# Per-course detail fetches are plain network GETs (no DB access), so they're
# safe to run concurrently. Kept modest -- fetch_json() already treats an AWS
# WAF challenge (HTTP 202) as an error, and firing too many requests at once
# raises the odds of tripping that, same as hammering it sequentially fast.
DETAIL_FETCH_WORKERS = 5
ROOT_URL = f"{BASE_CATALOG_URL}/"
INIT_REFERER = "https://www.google.com/"

# Reuses a request session for connection to persist
SESSION = requests.Session()

SESSION.headers.update({
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
    "Accept-Encoding": "gzip, deflate",
    "Connection": "keep-alive",
    "Accept-Language": "en-US,en;q=0.9",
    # Sec-Fetch / Sec-CH-UA headers intentionally omitted from session defaults —
    # they conflict with the JSON API calls.  They are only sent during the
    # homepage pre-flight in initialize_session().
})

SESSION_READY = False

# Function to open the main catalog page and API requests
def initialize_session():
    global SESSION_READY

    if SESSION_READY:
        return

    response = SESSION.get(
        ROOT_URL,
        headers={
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Referer": INIT_REFERER,
        },
        timeout=30,
    )

    if response.status_code not in (200, 301, 302):
        raise RuntimeError(
            f"Unable to initialize catalog session at {ROOT_URL}; got {response.status_code}"
        )

    SESSION_READY = True

# Minimal headers that the Acalog widget API accepts without authentication.
# Using a persistent Session object adds default headers (cookies, Accept-Encoding
# negotiation artifacts) that cause the server to return 400, so we make each
# request with a clean header set instead.
_API_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/128.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": ROOT_URL,
    "X-Requested-With": "XMLHttpRequest",
}


# Function that requests the JSON data from the catalog
def fetch_json(url):
    response = requests.get(url, headers=_API_HEADERS, timeout=60)

    if response.status_code == 202:
        raise RuntimeError(
            f"Received AWS WAF challenge from {url}. "
            "The catalog endpoint may require a browser session or API key."
        )

    response.raise_for_status()

    try:
        return response.json()
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Invalid JSON from {url}: {exc}") from exc

# Function that downloads a page of course summaries from the catalog
def fetch_course_page(catalog_id, page=1, page_size=PAGE_SIZE):
    url = (
        f"{BASE_CATALOG_URL}/widget-api/catalog/{catalog_id}/courses/"
        f"?page-size={page_size}&page={page}"
    )

    data = fetch_json(url)

    return data.get("course-list") or [], data.get("count", 0)

# Function that downloads every course summary page from the catalog
def fetch_all_courses(catalog_id):
    page = 1
    all_courses = []
    total_count = None

    while True:
        if page > 1:
            time.sleep(0.3)  # avoid WAF rate limiting
        courses, count = fetch_course_page(catalog_id, page)

        if total_count is None:
            total_count = count

        if not courses:
            break

        all_courses.extend(courses)

        if total_count is not None and len(all_courses) >= total_count:
            break

        page += 1

    return all_courses

# Function that converts catalog details into an API URL
def construct_detail_url(detail_path):
    if not detail_path:
        return None

    detail_path = detail_path.strip()

    # Convert older paths into widget format
    if detail_path.startswith("http://") or detail_path.startswith("https://"):
        return detail_path

    if detail_path.startswith('/api/mtsu/'):
        return f"{BASE_CATALOG_URL}{detail_path.replace('/api/mtsu/', '/widget-api/')}"

    if detail_path.startswith('/api/'):
        return f"{BASE_CATALOG_URL}{detail_path.replace('/api/', '/widget-api/api/')}"

    if detail_path.startswith('/'):
        return f"{BASE_CATALOG_URL}{detail_path}"

    return f"{BASE_CATALOG_URL}/{detail_path.lstrip('/')}"

# Function that removes the HTML tags and cleans spacing from catalog text
def normalize_text(html_text):
    if html_text is None:
        return None

    text = html_text.replace("\r", " ").replace("\n", " ")

    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)

    text = re.sub(r"<[^>]+>", "", text)

    text = unescape(text).strip()

    return re.sub(r"\s+", " ", text)

# Function that checks the catalog course titles match the program prefix
def brief_matches_program(title, prefix=PROGRAM_PREFIX):
    if not prefix:
        return True

    if not title:
        return False

    return title.replace(" ", " ").strip().upper().startswith(prefix.upper())

# Function that separates a course's title prefix, number, and name
def parse_title(title):
    if not title:
        return None, None, None

    title = title.replace("\u00a0", " ")

    match = re.match(r"^([A-Z]{2,4})\s*(\d{4})\s*-\s*(.+)$", title)

    if match:
        return match.group(1), match.group(2), match.group(3).strip()

    # Catalog title may skip over a hyphen after course number
    match = re.match(r"^([A-Z]{2,4})\s*(\d{4})\s*(.+)$", title)

    if match:
        return match.group(1), match.group(2), match.group(3).strip("- ")

    return None, None, title.strip()

# Function that extracts the number of credit hours from a course description
def extract_credits(body_text):
    if not body_text:
        return None

    match = re.search(r"(\d+(?:\.\d+)?)\s*credit hours", body_text, re.IGNORECASE)

    if match:
        return float(match.group(1))

    return None

# Function that extracts prerequisite wording from a course description
def extract_prerequisites(body_text):
    if not body_text:
        return []

    match = re.search(r"Prerequisites?:\s*(.+?)(?:\.|$)", body_text, re.IGNORECASE)

    if not match:
        return []

    prereq = match.group(1).strip()
    prereq = re.sub(r"\s+", " ", prereq)

    return [prereq]

# Function that fetches full course detail JSON for a batch of course briefs
# concurrently. Network-only (no DB session involved), so it's thread-safe;
# the caller still does all DB writes sequentially on one session afterward.
def _fetch_course_details(course_briefs):
    def _fetch_one(course_brief):
        detail_path = course_brief.get("url", "")
        if not detail_path:
            return course_brief, None

        detail_url = construct_detail_url(detail_path)
        if not detail_url:
            return course_brief, None

        try:
            return course_brief, fetch_json(detail_url)
        except Exception as exc:
            print(f"Skipping course {detail_url}: {exc}")
            return course_brief, None

    results = []
    with ThreadPoolExecutor(max_workers=DETAIL_FETCH_WORKERS) as pool:
        futures = [pool.submit(_fetch_one, brief) for brief in course_briefs]
        for future in as_completed(futures):
            results.append(future.result())

    return results


# Function that creates a new course record or updates an existing one
def upsert_course(session, detail):
    title = detail.get("title") or detail.get("name")

    prefix, number, title_name = parse_title(title)

    body = normalize_text(detail.get("body"))

    credits = extract_credits(body)

    course = session.get(Course, detail["id"])

    if course is None:
        course = Course(id=detail["id"])

    course.legacy_id = detail["legacy-id"]
    course.catalog_id = detail["catalog-id"]
    course.prefix = prefix
    course.number = number
    course.title = title_name
    course.credits = credits
    course.body = body
    course.url = detail.get("url")
    course.updated_at = detail.get("modified")

    session.add(course)

    # Flush so a course can be used again
    session.flush()

    return course

# Function that exports all synced courses from the DB to a JSON cache file
def _export_cache(prefix=None):
    prefix = prefix or PROGRAM_PREFIX
    try:
        courses_data = []
        with SessionLocal() as session:
            for row in session.query(Course).filter(Course.prefix == prefix).all():
                courses_data.append({
                    "id": row.id,
                    "legacy_id": row.legacy_id,
                    "catalog_id": row.catalog_id,
                    "prefix": row.prefix,
                    "number": row.number,
                    "title": row.title,
                    "credits": row.credits,
                    "body": row.body,
                    "url": row.url,
                    "updated_at": row.updated_at,
                    "prerequisites": [p.prerequisite_text for p in row.prerequisites],
                })
        payload = {
            "synced_at": datetime.now().isoformat(),
            "prefix": prefix,
            "courses": courses_data,
        }
        with open(CACHE_FILE, "w") as f:
            json.dump(payload, f, indent=2)
    except Exception as exc:
        print(f"Cache export failed (non-fatal): {exc}")


# Function that imports courses from the JSON cache file into the DB
def _import_cache(prefix=None):
    prefix = prefix or PROGRAM_PREFIX
    if not Path(CACHE_FILE).exists():
        return 0
    try:
        with open(CACHE_FILE) as f:
            payload = json.load(f)
        if payload.get("prefix") != prefix:
            return 0
        imported = 0
        with SessionLocal() as session:
            for cd in payload.get("courses", []):
                course = session.get(Course, cd["id"])
                if course is None:
                    course = Course(id=cd["id"])
                course.legacy_id = cd["legacy_id"]
                course.catalog_id = cd["catalog_id"]
                course.prefix = cd["prefix"]
                course.number = cd["number"]
                course.title = cd["title"]
                course.credits = cd["credits"]
                course.body = cd["body"]
                course.url = cd["url"]
                course.updated_at = cd["updated_at"]
                session.add(course)
                session.flush()
                session.query(Prerequisite).filter_by(course_id=course.id).delete()
                for prereq_text in cd.get("prerequisites", []):
                    session.add(Prerequisite(course_id=course.id, prerequisite_text=prereq_text))
                session.commit()
                imported += 1
        return imported
    except Exception as exc:
        print(f"Cache import failed: {exc}")
        return 0


# Function that returns the number of courses in the DB for a given prefix
def _db_course_count(prefix=None):
    prefix = prefix or PROGRAM_PREFIX
    try:
        with SessionLocal() as session:
            return session.query(Course).filter(Course.prefix == prefix).count()
    except Exception:
        return 0


# ─── HTML scraper (fallback when widget API returns 401) ─────────────────────

# Acalog's content.php with expand=1 returns an HTML page where each course
# entry looks like:
#
#   <td class="ntdefault">
#     <a href="preview_course.php?catoid=33&coid=195143">
#       CSCI 1170 - Computer Science I (4 credit hours)
#     </a>
#     <br/>
#     Course description text...
#     <br/><strong>Prerequisites:</strong> CSCI XXXX ...
#   </td>
#
# We paginate using filter[cpage]=N until no new course links appear.

_HTML_COURSE_URL = (
    "{base}/content.php?catoid={catoid}&navoid={navoid}"
    "&filter%5Bitem_type%5D=3&filter%5Bonly_active%5D=1"
    "&filter%5B3%5D=1&expand=1&filter%5Bcpage%5D={page}"
)

# Regex: "CSCI 1170 - Computer Science I (4 credit hours)"
_TITLE_RE = re.compile(
    r"^([A-Z]{2,5})\s*(\d{3,4})\s*[-\u2013]\s*(.+?)(?:\s*\((\d+(?:\.\d+)?)\s*credit hours?\))?$",
    re.IGNORECASE,
)


def _html_fetch_page(catoid: int, navoid: int, page: int) -> bytes:
    """Fetch one page of the Acalog HTML course list."""
    url = _HTML_COURSE_URL.format(
        base=BASE_CATALOG_URL,
        catoid=catoid,
        navoid=navoid,
        page=page,
    )
    initialize_session()
    resp = SESSION.get(
        url,
        headers={
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Referer": BASE_CATALOG_URL + "/",
        },
        timeout=45,
    )
    resp.raise_for_status()
    return resp.content


def _parse_html_course_page(html: bytes, catoid: int, prefix_filter: str):
    """Parse one Acalog content.php page and return a list of raw course dicts.

    Each dict has keys: id (int coid), prefix, number, title, credits, body, url, prereq_texts.
    Returns an empty list when no courses are found (signals last page).
    """
    soup = BeautifulSoup(html, "html.parser")
    results = []

    # Course links have href containing "preview_course" and a coid param
    for anchor in soup.find_all("a", href=re.compile(r"preview_course.*coid=\d+")):
        href = anchor.get("href", "")
        coid_match = re.search(r"coid=(\d+)", href)
        if not coid_match:
            continue
        coid = int(coid_match.group(1))

        raw_title = anchor.get_text(" ", strip=True)
        m = _TITLE_RE.match(raw_title)
        if not m:
            continue

        pfx, number, course_title, credits_str = m.groups()

        # Filter by prefix if requested
        if prefix_filter and pfx.upper() != prefix_filter.upper():
            continue

        credits = float(credits_str) if credits_str else None

        # The body and prerequisites live in the nearest parent <td> or <div>
        container = anchor.find_parent("td") or anchor.find_parent("div")
        body_text = ""
        prereq_texts = []

        if container:
            # Remove the anchor's own text to get just the description
            full_text = container.get_text("\n", strip=True)
            # Strip the title line from the top
            body_text = full_text[len(raw_title):].strip(" \n-–")

            # Try to extract credits from body if not in title
            if credits is None:
                cm = re.search(r"(\d+(?:\.\d+)?)\s*credit hours?", body_text, re.I)
                if cm:
                    credits = float(cm.group(1))

            # Extract prerequisite sentence
            prereq_match = re.search(
                r"Prerequisites?[:\s]+(.+?)(?:\.|$)", body_text, re.I
            )
            if prereq_match:
                prereq_texts = [prereq_match.group(1).strip()]

        course_url = f"{BASE_CATALOG_URL}/preview_course_nopop.php?catoid={catoid}&coid={coid}"

        results.append({
            "id": coid,
            "legacy_id": coid,
            "catalog_id": catoid,
            "prefix": pfx.upper(),
            "number": number,
            "title": course_title.strip(),
            "credits": credits,
            "body": body_text or None,
            "url": course_url,
            "updated_at": None,
            "prereq_texts": prereq_texts,
        })

    return results


def html_sync_courses(
    prefix: str = None,
    catoid: int = None,
    navoid: int = None,
) -> int:
    """Sync courses by scraping the Acalog HTML catalog pages.

    Used as a fallback when the widget API returns 401.  Returns the number of
    courses saved.  Raises RuntimeError if nothing could be retrieved.
    """
    prefix = (prefix or PROGRAM_PREFIX).upper()
    catoid = catoid or CATALOG_HTML_CATOID
    navoid = navoid or CATALOG_HTML_NAVOID

    all_courses = []
    page = 1
    seen_coids: set = set()

    while True:
        try:
            html = _html_fetch_page(catoid, navoid, page)
        except Exception as exc:
            if page == 1:
                raise RuntimeError(
                    f"HTML catalog scrape failed on page {page}: {exc}"
                ) from exc
            # If later pages fail, stop gracefully with what we have
            print(f"HTML scrape stopped at page {page}: {exc}")
            break

        courses = _parse_html_course_page(html, catoid, prefix)

        if not courses:
            break  # No more results

        new_courses = [c for c in courses if c["id"] not in seen_coids]
        if not new_courses:
            break  # All coids already seen — avoid infinite loop

        all_courses.extend(new_courses)
        seen_coids.update(c["id"] for c in new_courses)
        page += 1

    if not all_courses:
        raise RuntimeError(
            f"HTML scrape returned no '{prefix}' courses from catoid={catoid} navoid={navoid}. "
            "Check that MTSU_HTML_CATOID and MTSU_HTML_NAVOID are set correctly."
        )

    saved = 0
    with SessionLocal() as db_session:
        for cd in all_courses:
            try:
                course = db_session.get(Course, cd["id"])
                if course is None:
                    course = Course(id=cd["id"])

                course.legacy_id = cd["legacy_id"]
                course.catalog_id = cd["catalog_id"]
                course.prefix = cd["prefix"]
                course.number = cd["number"]
                course.title = cd["title"]
                course.credits = cd["credits"]
                course.body = cd["body"]
                course.url = cd["url"]
                course.updated_at = cd["updated_at"]

                db_session.add(course)
                db_session.flush()

                db_session.query(Prerequisite).filter_by(course_id=course.id).delete()
                for prereq_text in cd["prereq_texts"]:
                    db_session.add(
                        Prerequisite(course_id=course.id, prerequisite_text=prereq_text)
                    )

                db_session.commit()
                saved += 1
            except Exception as exc:
                db_session.rollback()
                print(f"Skipping coid {cd['id']}: {exc}")

    if saved == 0:
        raise RuntimeError("HTML scrape ran but no courses were saved.")

    _export_cache(prefix)
    return saved


# Function to download matching courses and saves to SQLlite database
def sync_courses(prefix=None, force=False):
    effective_prefix = (prefix or PROGRAM_PREFIX).upper()

    # Skip network calls if DB already has data and we're not forcing a refresh.
    # Return a negative number to signal "already loaded" vs a positive fresh count.
    if not force and _db_course_count(prefix=effective_prefix) > 0:
        return -_db_course_count(prefix=effective_prefix)

    courses = []
    waf_partial = False  # set True when WAF interrupts mid-sync

    try:
        for catalog_id in CATALOG_IDS:
            catalog_courses = fetch_all_courses(catalog_id)
            courses.extend(c for c in catalog_courses if brief_matches_program(c.get("title"), prefix=effective_prefix))
    except requests.HTTPError as exc:
        status = exc.response.status_code if exc.response is not None else None
        if status == 401:
            print("Widget API returned 401. Falling back to HTML catalog scraper...")
            try:
                count = html_sync_courses(prefix=effective_prefix)
                return count
            except Exception as html_exc:
                imported = _import_cache(prefix=effective_prefix)
                if imported > 0:
                    raise RuntimeError(
                        f"Widget API (401) and HTML scrape both failed. "
                        f"Restored {imported} courses from local cache. "
                        f"HTML error: {html_exc}"
                    )
                existing = _db_course_count(prefix=effective_prefix)
                if existing > 0:
                    raise RuntimeError(
                        f"Widget API (401) and HTML scrape both failed ({html_exc}). "
                        f"Using {existing} existing courses in the database."
                    )
                raise RuntimeError(
                    f"Widget API returned 401 and HTML scrape failed: {html_exc}. "
                    "No cached data available."
                )
        raise
    except RuntimeError as exc:
        # WAF challenge or other runtime error mid-fetch — use what we collected so far
        if courses:
            print(f"Sync interrupted for {effective_prefix} after {len(courses)} course summaries: {exc}")
            waf_partial = True
        else:
            raise

    if not courses:
        existing = _db_course_count(prefix=effective_prefix)
        if existing > 0:
            return existing  # Keep existing data, don't raise
        raise RuntimeError(
            f"No '{effective_prefix}' courses found in catalogs {CATALOG_IDS}"
        )

    synced = 0

    # Detail fetches (one HTTP round trip per course) run concurrently since
    # they're pure network I/O; the resulting DB writes below stay on one
    # session/thread, since SQLAlchemy sessions aren't safe to share across threads.
    detailed_courses = _fetch_course_details(courses)

    with SessionLocal() as session:
        for course_brief, detail in detailed_courses:
            if detail is None:
                continue

            detail_url = construct_detail_url(course_brief.get("url", ""))

            try:
                course = upsert_course(session, detail)

                for ct in detail.get("course_types", []):
                    course_type = session.get(CourseType, ct["id"])

                    if course_type is None:
                        course_type = CourseType(id=ct["id"])

                    course_type.legacy_id = ct["legacy-id"]
                    course_type.catalog_id = ct["catalog-id"]
                    course_type.name = ct.get("name")
                    course_type.category = ct.get("category")
                    course_type.visible = bool(ct.get("status", {}).get("visible", True))

                    session.add(course_type)
                    session.flush()

                    if course_type not in course.course_types:
                        course.course_types.append(course_type)

                session.query(Prerequisite).filter_by(course_id=course.id).delete()

                for prereq_text in extract_prerequisites(course.body):
                    prerequisite = Prerequisite(course_id=course.id, prerequisite_text=prereq_text)
                    session.add(prerequisite)

                session.commit()
                synced += 1

            except Exception as exc:
                session.rollback()
                print(f"Skipping course {detail_url}: {exc}")
                continue

    if synced == 0:
        existing = _db_course_count(prefix=effective_prefix)
        if existing > 0:
            return existing
        raise RuntimeError("Catalog sync ran but no courses were saved")

    _export_cache(prefix=effective_prefix)

    return synced
