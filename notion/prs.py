#!/usr/bin/env python3
"""Fetch GitHub PR pages from Notion PR DB, keyed by task URL.

Reads notion_tasks.json from the snapshot dir to discover which tasks have
PR relations, then queries the PR DB for those pages.

Output: notion_prs.json — dict keyed by task Notion URL, value is list of PRs:
  {
    "https://www.notion.so/<task-id>": [
      {"number": 154, "merged": true, "title": "[STAGE] ...", "env": "STAGE",
       "url": "https://www.notion.so/<pr-id>"}
    ]
  }

Usage:
    uv run notion/prs.py --dir snapshots/2026-05-20_125010
    uv run notion/prs.py --dir snapshots/... --model sonnet
"""
import argparse
import json
import os
import re
import subprocess
import sys

PR_DB = "collection://36650979-0d9a-805f-80b4-000ba2669c0d"

_ENV_TAG_RE = re.compile(r'^\[([^\]]+)\]')


def run_claude(prompt: str, model: str) -> str:
    cmd = [
        "claude", "--print", "--model", model,
        "--allowedTools", "mcp__claude_ai_Notion__notion-query-data-sources,Read",
    ]
    result = subprocess.run(cmd, input=prompt, capture_output=True, text=True, timeout=120)
    if result.returncode != 0:
        print(f"Claude error:\n{result.stderr}", file=sys.stderr)
        sys.exit(1)
    return result.stdout.strip()


def strip_code_fence(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r'^```[^\n]*\n', '', text)
        text = re.sub(r'\n```$', '', text.rstrip())
    return text.strip()


def extract_file_if_saved(text: str) -> str:
    m = re.search(r'(/Users/.+?/tool-results/.+?\.txt)', text)
    if m:
        try:
            with open(m.group(1)) as f:
                return f.read().strip()
        except FileNotFoundError:
            pass
    return text


def load_pr_urls(tasks_path: str) -> dict[str, list[str]]:
    """Return {task_url: [pr_page_url, ...]} for tasks that have PR relations."""
    with open(tasks_path) as f:
        data = json.load(f)
    rows = data.get("results", data) if isinstance(data, dict) else data
    result: dict[str, list[str]] = {}
    for row in rows:
        pr_raw = row.get("GitHub Pull Requests") or ""
        try:
            pr_urls = json.loads(pr_raw) if pr_raw else []
        except (json.JSONDecodeError, ValueError):
            pr_urls = []
        if pr_urls:
            task_url = row.get("url", "")
            if task_url:
                result[task_url] = pr_urls
    return result


def fetch_prs(pr_urls: list[str], model: str) -> list[dict]:
    """Query PR DB for the given PR page URLs, return raw result rows."""
    url_list = ", ".join(f"'{u}'" for u in pr_urls)
    query = (
        f'SELECT url, "Title", "PR Number", '
        f'"date:Merged At:start", "date:Closed At:start", '
        f'"Related to Paynext Data Tasks (GitHub Pull Requests)" '
        f'FROM "{PR_DB}" WHERE url IN ({url_list})'
    )
    prompt = (
        'Use the notion-query-data-sources MCP tool with these exact parameters:\n'
        f'- mode: "sql"\n'
        f'- data_source_urls: ["{PR_DB}"]\n'
        f'- query: {query}\n\n'
        'Output ONLY the raw JSON response from the MCP tool, nothing else.\n'
        'No preamble, no explanation, no code fences, no markdown.\n'
        'Every character must come from the MCP tool output.\n'
        'Do not truncate, abbreviate, or modify any data.'
    )
    text = run_claude(prompt, model)
    text = strip_code_fence(extract_file_if_saved(text))
    try:
        data = json.loads(text)
        return data.get("results", data) if isinstance(data, dict) else data
    except json.JSONDecodeError:
        print(f"Failed to parse PR JSON:\n{text[:500]}", file=sys.stderr)
        sys.exit(1)


def parse_pr_row(row: dict) -> dict:
    title = row.get("Title", "") or ""
    m = _ENV_TAG_RE.match(title)
    env = m.group(1) if m else ""
    number = row.get("PR Number")
    merged = bool(row.get("date:Merged At:start") or row.get("date:Closed At:start"))
    return {
        "number": int(number) if number is not None else None,
        "merged": merged,
        "title": title,
        "env": env,
        "url": row.get("url", ""),
    }


def build_pr_index(
    task_pr_urls: dict[str, list[str]],
    pr_rows: list[dict],
) -> dict[str, list[dict]]:
    """Map pr page url → parsed PR info."""
    pr_by_url = {row.get("url", ""): parse_pr_row(row) for row in pr_rows}
    result: dict[str, list[dict]] = {}
    for task_url, urls in task_pr_urls.items():
        prs = [pr_by_url[u] for u in urls if u in pr_by_url]
        if prs:
            result[task_url] = sorted(prs, key=lambda p: p["number"] or 0)
    return result


def main() -> None:
    ap = argparse.ArgumentParser(description="Fetch Notion PR pages for current sprint tasks.")
    ap.add_argument("--dir", required=True, help="snapshot directory containing notion_tasks.json")
    ap.add_argument("--model", default="haiku", choices=["haiku", "sonnet", "opus"])
    args = ap.parse_args()

    tasks_path = os.path.join(args.dir, "notion_tasks.json")
    if not os.path.exists(tasks_path):
        print(f"[ERROR] Not found: {tasks_path}", file=sys.stderr)
        sys.exit(1)

    task_pr_urls = load_pr_urls(tasks_path)
    if not task_pr_urls:
        print("No tasks with PR relations found — writing empty notion_prs.json", file=sys.stderr)
        out: dict = {}
    else:
        all_pr_urls = list({u for urls in task_pr_urls.values() for u in urls})
        print(f"Fetching {len(all_pr_urls)} PR page(s) for {len(task_pr_urls)} task(s)...", file=sys.stderr)
        pr_rows = fetch_prs(all_pr_urls, args.model)
        out = build_pr_index(task_pr_urls, pr_rows)
        print(f"✓ {sum(len(v) for v in out.values())} PR(s) across {len(out)} task(s)", file=sys.stderr)
        for task_url, prs in out.items():
            for p in prs:
                status = "✓ merged" if p["merged"] else "open"
                print(f"  PR #{p['number']} {p['env']} ({status}) — {p['title'][:60]}", file=sys.stderr)

    out_path = os.path.join(args.dir, "notion_prs.json")
    with open(out_path, "w") as f:
        json.dump(out, f, indent=2, ensure_ascii=False)
    print(f"→ {out_path}", file=sys.stderr)


if __name__ == "__main__":
    main()
