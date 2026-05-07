#!/usr/bin/env python3
"""Compute report date, previous workday, and comment window for a standup run.

Usage:
    python3 window.py [--date YYYY-MM-DD]

Prints JSON with report_date, prev_date, window_start, window_end,
snapshot_path, prev_snapshot_path, prev_snapshot_exists.

window_start = mtime of previous snapshot (UTC) if it exists, else start of
the previous working day. This narrows the comment window so that comments
already accounted for in yesterday's standup don't leak into today.
"""
import argparse
import json
import sys
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[4]
STANDUPS_DIR = REPO_ROOT / "standups"


def shift_to_friday_if_weekend(d: date) -> date:
    if d.weekday() == 5:
        return d - timedelta(days=1)
    if d.weekday() == 6:
        return d - timedelta(days=2)
    return d


def prev_workday(d: date) -> date:
    if d.weekday() == 0:
        return d - timedelta(days=3)
    return d - timedelta(days=1)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=None)
    args = ap.parse_args()

    today = date.fromisoformat(args.date) if args.date else date.today()
    today = shift_to_friday_if_weekend(today)
    prev = prev_workday(today)

    snapshot_path = STANDUPS_DIR / f"standup_{today.isoformat()}.json"
    prev_snapshot_path = STANDUPS_DIR / f"standup_{prev.isoformat()}.json"
    prev_exists = prev_snapshot_path.exists()

    if prev_exists:
        ts = datetime.fromtimestamp(prev_snapshot_path.stat().st_mtime, tz=timezone.utc)
        window_start = ts.isoformat()
    else:
        window_start = datetime.combine(prev, time.min, tzinfo=timezone.utc).isoformat()

    window_end = datetime.combine(today, time.max, tzinfo=timezone.utc).isoformat()

    json.dump(
        {
            "report_date": today.isoformat(),
            "prev_date": prev.isoformat(),
            "window_start": window_start,
            "window_end": window_end,
            "snapshot_path": str(snapshot_path),
            "prev_snapshot_path": str(prev_snapshot_path),
            "prev_snapshot_exists": prev_exists,
        },
        sys.stdout,
        indent=2,
    )
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()
