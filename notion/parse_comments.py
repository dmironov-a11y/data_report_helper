#!/usr/bin/env python3
"""Parse raw Notion comment XML (already fetched via MCP) into recent_comments.

Reads:
  $DIR/notion_tasks.json   — task rows
  $DIR/raw_comments.json   — { task_id: raw_xml_string } from MCP notion-get-comments

Writes:
  $DIR/notion_tasks_with_comments.json — task rows with `recent_comments` added

Usage:
    uv run notion/parse_comments.py --dir snapshots/2026-05-22_100422
"""
import argparse
import json
import os
import re
import sys


def strip_tags(text: str) -> str:
    text = re.sub(r'<mention-user\b[^>]*/>', '@user', text)
    text = re.sub(r'<mention-user\b[^>]*>.*?</mention-user>', '@user', text)
    text = re.sub(r'<br\s*/?>', '\n', text)
    text = re.sub(r'<[^>]+>', '', text)
    return text.strip()


def parse_xml(xml: str) -> list[dict]:
    out = []
    for m in re.finditer(r'<comment\b[^>]*datetime="([^"]*)"[^>]*>(.*?)</comment>', xml, re.DOTALL):
        text = strip_tags(m.group(2))
        if text:
            out.append({"text": text, "datetime": m.group(1)})
    out.sort(key=lambda c: c["datetime"], reverse=True)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", required=True, help="snapshot directory")
    args = ap.parse_args()

    tasks_path = os.path.join(args.dir, "notion_tasks.json")
    raw_path = os.path.join(args.dir, "raw_comments.json")

    if not os.path.exists(tasks_path):
        print(f"Not found: {tasks_path}", file=sys.stderr)
        sys.exit(1)
    if not os.path.exists(raw_path):
        print(f"Not found: {raw_path}", file=sys.stderr)
        sys.exit(1)

    with open(tasks_path) as f:
        tasks = json.load(f)
    with open(raw_path) as f:
        raw = json.load(f)

    total = 0
    for task in tasks:
        tid = task.get("id", "")
        xml = raw.get(tid, "")
        task["recent_comments"] = parse_xml(xml) if xml else []
        total += len(task["recent_comments"])

    out_path = os.path.join(args.dir, "notion_tasks_with_comments.json")
    with open(out_path, "w") as f:
        json.dump(tasks, f, indent=2, ensure_ascii=False)

    print(f"✓ {len(tasks)} tasks, {total} comments → {out_path}", file=sys.stderr)


if __name__ == "__main__":
    main()
