#!/usr/bin/env python3
"""Create task snapshot from raw Notion data (tasks + comments).

With versioning and metadata watermark.

Usage:
    uv run create_snapshot.py                                   # use latest snapshots/YYYY-MM-DD_HHMMSS/
    uv run create_snapshot.py --dir snapshots/2026-05-07_demo   # create snapshot in specific dir
    uv run create_snapshot.py --date 2026-05-06                 # override snapshot date (default: today)

Typical workflow:
    mkdir snapshots/2026-05-07_demo
    uv run notion_tasks.py --dir snapshots/2026-05-07_demo
    uv run notion_comments.py --dir snapshots/2026-05-07_demo page_id1 page_id2
    uv run create_snapshot.py --dir snapshots/2026-05-07_demo
"""
import argparse
import glob
import json
import os
import re
import sys
from datetime import date, datetime
from pathlib import Path


def parse_url_id(url: str) -> str:
    """Extract UUID from Notion URL (without dashes)."""
    m = re.search(r'notion\.so/([a-f0-9]{32})', url.lower())
    return m.group(1) if m else None


def normalize_uuid(uuid: str) -> str:
    """Normalize UUID by removing dashes."""
    return uuid.replace("-", "").lower() if uuid else None


def extract_parent_id(parent_task_str: str) -> int:
    """Extract parent task ID from Parent task field.

    Parent task field contains JSON like: ["https://www.notion.so/..."]
    """
    if not parent_task_str or parent_task_str == "None":
        return None
    try:
        urls = json.loads(parent_task_str)
        if urls and isinstance(urls, list) and urls[0]:
            # Will be resolved later after building ID map
            return urls[0]
    except:
        pass
    return None


def get_state_group(state: str) -> str:
    """Classify state into group."""
    state = (state or "").lower().strip()
    if state == "done" or state == "completed":
        return "done"
    elif "review" in state:
        return "review"
    elif state == "backlog":
        return "backlog"
    elif state == "cancelled":
        return "cancelled"
    elif state.startswith("in"):  # "in progress", "in review", etc
        return "started"
    else:
        return "skipped"


def parse_xml_comments(xml_content: str) -> dict:
    """Parse XML comments into dict keyed by page UUID (uses regex, not XML parser).

    Returns: {page_uuid: [{"text": "...", "datetime": "..."}]}
    """
    comments_by_page = {}

    # Extract XML from backticks if wrapped
    m = re.search(r'```(?:xml)?\n(.*?)\n```', xml_content, re.DOTALL)
    if m:
        xml_content = m.group(1)

    # Find all comment tags
    for comment_match in re.finditer(r'<comment([^>]*)>(.*?)</comment>', xml_content, re.DOTALL):
        attrs = comment_match.group(1)
        text = comment_match.group(2).strip()

        if not text:
            continue

        # Extract datetime from attributes
        dt_match = re.search(r'datetime="([^"]*)"', attrs)
        datetime_str = dt_match.group(1) if dt_match else ""

        # Find which discussion this comment belongs to by looking backward
        # Extract page UUID from the nearest preceding <discussion...id="discussion://uuid/...">
        before_comment = xml_content[:comment_match.start()]
        disc_matches = list(re.finditer(r'<discussion[^>]*id="([^"]*)"', before_comment))
        if not disc_matches:
            continue

        discussion_id = disc_matches[-1].group(1)
        m = re.search(r'discussion://([a-f0-9\-]+?)/', discussion_id)
        if not m:
            continue

        page_uuid = normalize_uuid(m.group(1))
        if page_uuid not in comments_by_page:
            comments_by_page[page_uuid] = []

        comments_by_page[page_uuid].append({
            "text": text,
            "datetime": datetime_str
        })

    return comments_by_page


def find_latest_snapshot_dir() -> str:
    """Find the latest snapshots/YYYY-MM-DD_HHMMSS directory."""
    dirs = glob.glob("snapshots/????-??-??_??????")
    if not dirs:
        return None
    dirs.sort()
    return dirs[-1]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default=None, help="snapshot directory (default: latest snapshots/YYYY-MM-DD_HHMMSS)")
    ap.add_argument("--date", default=None, help="snapshot date (YYYY-MM-DD, default: today)")
    ap.add_argument("--tasks", default=None, help="raw tasks file (override --dir)")
    ap.add_argument("--comments", default=None, help="raw comments file (override --dir)")
    args = ap.parse_args()

    # Determine snapshot directory
    snapshot_dir = args.dir or find_latest_snapshot_dir()
    if not snapshot_dir:
        print("No snapshot directory found. Run notion_tasks.py first.", file=sys.stderr)
        sys.exit(1)

    # Determine input files
    tasks_file = args.tasks or os.path.join(snapshot_dir, "notion_tasks.json")
    comments_file = args.comments or os.path.join(snapshot_dir, "notion_comments.json")
    output_file = os.path.join(snapshot_dir, "snapshot.json")

    # Parse date
    if args.date:
        try:
            snapshot_date = datetime.strptime(args.date, "%Y-%m-%d").date()
        except ValueError:
            print(f"Invalid date: {args.date}", file=sys.stderr)
            sys.exit(1)
    else:
        snapshot_date = date.today()

    # Load raw tasks
    if not os.path.exists(tasks_file):
        print(f"Tasks file not found: {tasks_file}", file=sys.stderr)
        sys.exit(1)

    try:
        with open(tasks_file) as f:
            tasks_data = json.load(f)
    except json.JSONDecodeError as e:
        print(f"Could not parse {tasks_file}: {e}", file=sys.stderr)
        sys.exit(1)

    # Extract results array
    results = tasks_data.get("results", []) if isinstance(tasks_data, dict) else tasks_data

    # Load comments if available
    comments_by_page = {}
    if os.path.exists(comments_file):
        try:
            with open(comments_file) as f:
                comments_content = f.read()

            # Try JSON first
            try:
                comments_data = json.loads(comments_content)
                if isinstance(comments_data, dict):
                    # Already in the right format
                    for page_id, comments_list in comments_data.items():
                        if isinstance(comments_list, list):
                            comments_by_page[page_id] = comments_list
            except json.JSONDecodeError:
                # Try XML format
                comments_by_page = parse_xml_comments(comments_content)
        except Exception as e:
            print(f"Warning: could not load comments from {comments_file}: {e}", file=sys.stderr)
    else:
        print(f"Note: no comments file at {comments_file}", file=sys.stderr)

    # Build ID map for resolving parent IDs
    id_to_row = {}  # userDefined:ID → raw row
    url_to_id = {}  # page UUID → userDefined:ID

    for row in results:
        user_id = row.get("userDefined:ID")
        url = row.get("url")
        if user_id is not None:
            id_to_row[user_id] = row
        if url:
            page_uuid = parse_url_id(url)
            if page_uuid and user_id is not None:
                url_to_id[page_uuid] = user_id

    # Build tasks array
    tasks = []
    for row in results:
        user_id = row.get("userDefined:ID")
        url = row.get("url")
        page_uuid = parse_url_id(url) if url else None

        # Resolve parent ID
        parent_task_str = row.get("Parent task")
        parent_id = None
        if parent_task_str and parent_task_str != "None":
            try:
                urls = json.loads(parent_task_str)
                if urls and isinstance(urls, list):
                    parent_url = urls[0]
                    parent_uuid = parse_url_id(parent_url)
                    if parent_uuid in url_to_id:
                        parent_id = url_to_id[parent_uuid]
            except:
                pass

        # Get comments for this page
        recent_comments = []
        if page_uuid in comments_by_page:
            # Sort by datetime descending (newest first)
            page_comments = sorted(
                comments_by_page[page_uuid],
                key=lambda c: c.get("datetime", ""),
                reverse=True
            )
            recent_comments = page_comments

        task = {
            "id": user_id,
            "name": row.get("Task name", ""),
            "url": url or "",
            "state": row.get("State", ""),
            "group": get_state_group(row.get("State")),
            "parent_id": parent_id,
            "status_summary": None,
            "recent_comments": recent_comments,
            "action_items": [],
            "blocker": {
                "is_blocker": False,
                "description": None
            },
            "released": False
        }
        tasks.append(task)

    # Build sprint info (from first task or generic)
    sprint = {
        "name": "Unknown Sprint",
        "start": snapshot_date.isoformat(),
        "end": None
    }

    # Check if any task has sprint info
    for row in results:
        sprint_str = row.get("Sprint")
        if sprint_str and sprint_str != "None":
            try:
                sprint_urls = json.loads(sprint_str)
                if sprint_urls and isinstance(sprint_urls, list):
                    # Extract sprint name from URL or use generic
                    sprint["name"] = f"Sprint {snapshot_date.strftime('%Y-W%U')}"
                    break
            except:
                pass

    # Build snapshot with metadata
    snapshot = {
        "metadata": {
            "created_at": datetime.now().isoformat(),
            "snapshot_dir": snapshot_dir,
            "sources": {
                "tasks": os.path.relpath(tasks_file, snapshot_dir),
                "comments": os.path.relpath(comments_file, snapshot_dir) if os.path.exists(comments_file) else None
            }
        },
        "date": snapshot_date.isoformat(),
        "sprint": sprint,
        "tasks": tasks
    }

    # Save snapshot in the snapshot directory
    with open(output_file, "w") as f:
        json.dump(snapshot, f, indent=2, ensure_ascii=False)

    print(f"✓ {len(tasks)} tasks → {output_file}", file=sys.stderr)


if __name__ == "__main__":
    import sys
    main()
