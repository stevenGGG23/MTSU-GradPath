"""Regression tests for the catalog-sync stuck-lock bug.

sync_status.running was found stuck at True in the shared DB with no sync
actually in progress -- the background thread that should have cleared it
had died mid-sync (e.g. a worker restart), and since nothing else could ever
clear a running=True row, every future "Sync Catalog" click just reported
"still running" without ever starting a new sync.
"""
import time

from app import _sync_is_stale, STALE_SYNC_SECONDS


def test_missing_started_at_is_treated_as_stale():
    # A row written before this fix has no started_at at all.
    assert _sync_is_stale({"running": True, "started_at": None}) is True


def test_recent_start_is_not_stale():
    state = {"running": True, "started_at": time.time()}
    assert _sync_is_stale(state) is False


def test_old_start_is_stale():
    state = {"running": True, "started_at": time.time() - STALE_SYNC_SECONDS - 1}
    assert _sync_is_stale(state) is True
