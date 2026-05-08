#!/usr/bin/env python3
"""Fetch Notion comments per task via Claude + MCP, enrich notion_tasks.json in place.

Calls Claude CLI once per task (parallel workers), parses XML comments,
and adds `recent_comments` field to each task in notion_tasks.json.

Usage:
    uv run notion_comments.py --dir snapshots/2026-05-07_demo
    uv run notion_comments.py --dir snapshots/... --model sonnet
    uv run notion_comments.py --dir snapshots/... --workers 10
    uv run notion_comments.py --dir snapshots/... --include-resolved
"""
import argparse
import json
import os
import re
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime


def run_claude(prompt: str, model: str) -> str:
    cmd = ["claude", "--print", "--model", model, prompt]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip())
    return result.stdout.strip()


def extract_xml(text: str) -> str:
    """Extract XML from Claude output — strips JSON wrapper or code fences."""
    # {"text": "<xml...>"} wrapper
    try:
        data = json.loads(text)
        if isinstance(data, dict) and "text" in data:
            return data["text"]
    except (json.JSONDecodeError, ValueError):
        pass
    # ```xml or ```json fence
    m = re.search(r'```(?:xml|json)?\n(.*?)\n```', text, re.DOTALL)
    if m:
        return m.group(1).strip()
    # raw XML
    idx = text.find('<')
    if idx != -1:
        return text[idx:].strip()
    return text.strip()


def parse_comments(xml: str) -> list[dict]:
    """Parse <comment datetime="...">text</comment> tags from XML."""
    comments = []
    for m in re.finditer(r'<comment[^>]*datetime="([^"]*)"[^>]*>(.*?)</comment>', xml, re.DOTALL):
        text = m.group(2).strip()
        if text:
            comments.append({"text": text, "datetime": m.group(1)})
    # Sort newest first
    comments.sort(key=lambda c: c.get("datetime", ""), reverse=True)
    return comments


def fetch_comments_for_task(url: str, model: str, include_resolved: bool) -> list[dict]:
    prompt = (
        f'Use the notion-get-comments MCP tool with these parameters:\n'
        f'- page_id: "{url}"\n'
        f'- include_all_blocks: true\n'
        f'- include_resolved: {str(include_resolved).lower()}\n\n'
        'Output ONLY the raw XML response from the MCP tool, nothing else.\n'
        'No preamble, no explanation, no code fences, no markdown.\n'
        'Every character must come from the MCP tool output.\n'
        'Do not truncate, abbreviate, or modify any data.'
    )
    try:
        text = run_claude(prompt, model)
        xml = extract_xml(text)
        return parse_comments(xml)
    except Exception as e:
        print(f"  warn: {url} — {e}", file=sys.stderr)
        return []


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", required=True, help="snapshot directory containing notion_tasks.json")
    ap.add_argument("--model", default="haiku", choices=["haiku", "sonnet", "opus"])
    ap.add_argument("--workers", type=int, default=5, help="parallel workers (default: 5)")
    ap.add_argument("--include-resolved", action="store_true", help="include resolved discussions")
    args = ap.parse_args()

    tasks_path = os.path.join(args.dir, "notion_tasks.json")
    if not os.path.exists(tasks_path):
        print(f"Not found: {tasks_path}", file=sys.stderr)
        sys.exit(1)

    with open(tasks_path) as f:
        data = json.load(f)
    rows = data.get("results", data) if isinstance(data, dict) else data

    urls = [(i, r["url"]) for i, r in enumerate(rows) if r.get("url")]
    print(f"Fetching comments for {len(urls)} tasks (workers={args.workers})...", file=sys.stderr)

    results = {}

    def fetch(i_url):
        i, url = i_url
        comments = fetch_comments_for_task(url, args.model, args.include_resolved)
        return i, comments

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(fetch, iu): iu for iu in urls}
        done = 0
        for future in as_completed(futures):
            i, comments = future.result()
            results[i] = comments
            done += 1
            if comments:
                print(f"  [{done}/{len(urls)}] #{i} — {len(comments)} comment(s)", file=sys.stderr)
            else:
                print(f"  [{done}/{len(urls)}] #{i} — no comments", file=sys.stderr)

    # Attach comments to each task row
    total = 0
    for i, row in enumerate(rows):
        row["recent_comments"] = results.get(i, [])
        total += len(row["recent_comments"])

    # Save to notion_tasks_with_comments.json (notion_tasks.json unchanged)
    if isinstance(data, dict) and "results" in data:
        data["results"] = rows
        out = data
    else:
        out = rows

    out_path = os.path.join(args.dir, "notion_tasks_with_comments.json")
    with open(out_path, "w") as f:
        json.dump(out, f, indent=2, ensure_ascii=False)

    print(f"✓ {total} total comments → {out_path}", file=sys.stderr)


if __name__ == "__main__":
    main()
