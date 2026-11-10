"""Decide whether this run of the data-refresh workflow should go ahead,
and whether it re-downloads the full catalog.

Prints "go=true|false" and "catalog=true|false" (the workflow appends them
to $GITHUB_OUTPUT).

- Manual run: always goes; the catalog only if asked for.
- Monday main run: class schedule. On the 1st it skips, since the monthly
  run (catalog + schedule) is running too.
- 1st-of-month main run: catalog + schedule.
- Backup runs (a day later): GitHub sometimes fails a scheduled run before
  it even starts, so a day later each backup checks the data on main and
  refreshes only if the main run didn't.
"""
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

WEEKLY = "17 3 * * 1"
MONTHLY = "17 3 1 * *"
WEEKLY_BACKUP = "17 3 * * 2"
MONTHLY_BACKUP = "17 3 2 * *"
SCHEDULES = (WEEKLY, MONTHLY, WEEKLY_BACKUP, MONTHLY_BACKUP)

# Fresher than this means the main run already did the work. (A main run
# refreshes ~24 hours before its backup; the last one before that was a
# week or a month ago.)
FRESH = timedelta(days=3)


def fetched_at(path: Path) -> datetime | None:
    """When a data file was downloaded (UTC), or None if unknown."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        stamp = data.get("fetched_at") or data["catalog"]["fetched_at"]
        return datetime.fromisoformat(stamp)
    except (OSError, ValueError, KeyError, TypeError):
        return None


def decide(schedule: str, manual_catalog: bool, data_dir: Path, now_utc: datetime) -> tuple[bool, bool, str]:
    if not schedule:
        return True, manual_catalog, "Manual run."
    if schedule == WEEKLY:
        if now_utc.replace(tzinfo=timezone.utc).astimezone(ZoneInfo("America/Chicago")).day == 1:
            return False, False, "The 1st: the monthly run refreshes the schedule along with the catalog."
        return True, False, "Weekly class schedule refresh."
    if schedule == MONTHLY:
        return True, True, "Monthly catalog + schedule refresh."
    if schedule in (WEEKLY_BACKUP, MONTHLY_BACKUP):
        catalog = schedule == MONTHLY_BACKUP
        when = fetched_at(data_dir / ("catalog.json" if catalog else "schedule.json"))
        if when and now_utc - when < FRESH:
            return False, catalog, f"Backup: data already refreshed at {when:%Y-%m-%d %H:%M} UTC."
        return True, catalog, f"Backup: the main run didn't refresh (last refresh {when or 'unknown'}); refreshing now."
    # A schedule this script doesn't know: refresh rather than silently skip
    return True, False, f"Unknown schedule {schedule!r}; refreshing the class schedule."


def main() -> None:
    go, catalog, why = decide(
        os.environ.get("SCHEDULE", ""),
        os.environ.get("MANUAL_CATALOG", "") == "true",
        Path(sys.argv[1] if len(sys.argv) > 1 else "catalog_data"),
        datetime.now(timezone.utc).replace(tzinfo=None),
    )
    print(why, file=sys.stderr)
    print(f"go={str(go).lower()}")
    print(f"catalog={str(catalog).lower()}")


if __name__ == "__main__":
    main()
