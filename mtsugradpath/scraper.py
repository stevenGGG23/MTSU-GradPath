import json
import re
from html import unescape

import requests
from .config import BASE_CATALOG_URL, CATALOG_IDS, PROGRAM_PREFIX
from .db import SessionLocal
from .models import Course, CourseType, Prerequisite

PAGE_SIZE = 20
ROOT_URL = f"{BASE_CATALOG_URL}/"
INIT_REFERER = "https://www.google.com/"

SESSION = requests.Session()
SESSION.headers.update({
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
    "Accept-Encoding": "gzip, deflate",
    "Connection": "keep-alive",
    "Accept-Language": "en-US,en;q=0.9",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
    "Sec-CH-UA": '"Chromium";v="128", "Google Chrome";v="128", "Not A(Brand)";v="99"',
    "Sec-CH-UA-Mobile": "?0",
    "Sec-CH-UA-Platform": '"Windows"',
})
SESSION_READY = False


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


def fetch_json(url, retry=True):
    global SESSION_READY
    initialize_session()
    response = SESSION.get(
        url,
        headers={
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "Accept-Language": "en-US,en;q=0.9",
            "Referer": ROOT_URL,
            "X-Requested-With": "XMLHttpRequest",
            "Sec-Fetch-Dest": "empty",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Site": "same-origin",
        },
        timeout=30,
    )
    if response.status_code == 202:
        if retry:
            SESSION_READY = False
            initialize_session()
            return fetch_json(url, retry=False)
        raise RuntimeError(
            f"Received AWS WAF challenge or empty response from {url}. "
            "This catalog endpoint may require a browser token or different network access."
        )
    response.raise_for_status()
    try:
        return response.json()
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Invalid JSON from {url}: {exc}") from exc


def fetch_course_page(catalog_id, page=1, page_size=PAGE_SIZE):
    url = (
        f"{BASE_CATALOG_URL}/widget-api/catalog/{catalog_id}/courses/"
        f"?page-size={page_size}&page={page}"
    )
    data = fetch_json(url)
    return data.get("course-list") or [], data.get("count", 0)


def fetch_all_courses(catalog_id):
    page = 1
    all_courses = []
    total_count = None
    while True:
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


def construct_detail_url(detail_path):
    if not detail_path:
        return None
    detail_path = detail_path.strip()
    if detail_path.startswith("http://") or detail_path.startswith("https://"):
        return detail_path
    if detail_path.startswith('/api/mtsu/'):
        return f"{BASE_CATALOG_URL}{detail_path.replace('/api/mtsu/', '/widget-api/')}"
    if detail_path.startswith('/api/'):
        return f"{BASE_CATALOG_URL}{detail_path.replace('/api/', '/widget-api/api/')}"
    if detail_path.startswith('/'):
        return f"{BASE_CATALOG_URL}{detail_path}"
    return f"{BASE_CATALOG_URL}/{detail_path.lstrip('/')}"


def normalize_text(html_text):
    if html_text is None:
        return None
    text = html_text.replace("\r", " ").replace("\n", " ")
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", "", text)
    text = unescape(text).strip()
    return re.sub(r"\s+", " ", text)


def brief_matches_program(title, prefix=PROGRAM_PREFIX):
    if not prefix:
        return True
    if not title:
        return False
    return title.replace(" ", " ").strip().upper().startswith(prefix.upper())


def parse_title(title):
    if not title:
        return None, None, None
    title = title.replace("\u00a0", " ")
    match = re.match(r"^([A-Z]{2,4})\s*(\d{4})\s*-\s*(.+)$", title)
    if match:
        return match.group(1), match.group(2), match.group(3).strip()
    match = re.match(r"^([A-Z]{2,4})\s*(\d{4})\s*(.+)$", title)
    if match:
        return match.group(1), match.group(2), match.group(3).strip("- ")
    return None, None, title.strip()


def extract_credits(body_text):
    if not body_text:
        return None
    match = re.search(r"(\d+(?:\.\d+)?)\s*credit hours", body_text, re.IGNORECASE)
    if match:
        return float(match.group(1))
    return None


def extract_prerequisites(body_text):
    if not body_text:
        return []
    match = re.search(r"Prerequisites?:\s*(.+?)(?:\.|$)", body_text, re.IGNORECASE)
    if not match:
        return []
    prereq = match.group(1).strip()
    prereq = re.sub(r"\s+", " ", prereq)
    return [prereq]


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
    session.flush()
    return course


def sync_courses():
    courses = []
    for catalog_id in CATALOG_IDS:
        catalog_courses = fetch_all_courses(catalog_id)
        courses.extend(c for c in catalog_courses if brief_matches_program(c.get("title")))

    if not courses:
        raise RuntimeError(
            f"No '{PROGRAM_PREFIX}' courses found in catalogs {CATALOG_IDS}"
        )

    synced = 0
    with SessionLocal() as session:
        for course_brief in courses:
            detail_path = course_brief.get('url', '')
            if not detail_path:
                continue
            detail_url = construct_detail_url(detail_path)
            if not detail_url:
                continue
            try:
                detail = fetch_json(detail_url)
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
        raise RuntimeError("Catalog sync ran but no courses were saved")
    return synced
