#!/usr/bin/env python3
from mtsugradpath.db import init_db
from mtsugradpath.scraper import sync_course_search_index, sync_courses


def main():
    init_db()
    indexed = abs(sync_course_search_index())
    count = sync_courses()
    print(f"Search index ready: {indexed} catalog courses.")
    print(f"Scrape complete: {count} courses synced.")


if __name__ == "__main__":
    main()
