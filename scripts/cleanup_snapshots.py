#!/usr/bin/env python3
"""
Delete snapshot directories older than N days (default: 10).
Skips directories whose names don't match the YYYY-MM-DD_HHMMSS pattern.

Usage:
    uv run scripts/cleanup_snapshots.py
    uv run scripts/cleanup_snapshots.py --days 14
    uv run scripts/cleanup_snapshots.py --dry-run
"""

import argparse
import re
import shutil
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

SNAPSHOT_RE = re.compile(r"^\d{4}-\d{2}-\d{2}_\d{6}$")
SNAPSHOTS_DIR = Path(__file__).parent.parent / "snapshots"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=10, help="Delete snapshots older than this many days")
    parser.add_argument("--dry-run", action="store_true", help="Print what would be deleted without deleting")
    args = parser.parse_args()

    cutoff = datetime.now(tz=timezone.utc) - timedelta(days=args.days)
    deleted = 0
    skipped = 0

    for entry in sorted(SNAPSHOTS_DIR.iterdir()):
        if not entry.is_dir() or not SNAPSHOT_RE.match(entry.name):
            continue
        try:
            ts = datetime.strptime(entry.name, "%Y-%m-%d_%H%M%S").replace(tzinfo=timezone.utc)
        except ValueError:
            continue
        if ts < cutoff:
            if args.dry_run:
                print(f"[dry-run] would delete {entry.name}")
            else:
                shutil.rmtree(entry)
                print(f"deleted {entry.name}")
            deleted += 1
        else:
            skipped += 1

    action = "would delete" if args.dry_run else "deleted"
    print(f"{action} {deleted} snapshot(s), kept {skipped}", file=sys.stderr)


if __name__ == "__main__":
    main()
