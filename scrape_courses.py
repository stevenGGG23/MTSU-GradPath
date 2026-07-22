#!/usr/bin/env python3
from mtsugradpath.db import init_db
from mtsugradpath.scraper import sync_courses


def main():
    init_db()
    count = sync_courses()
    print(f"Scrape complete: {count} courses synced.")


if __name__ == "__main__":
    main()
