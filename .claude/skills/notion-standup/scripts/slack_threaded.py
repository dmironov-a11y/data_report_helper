#!/usr/bin/env python3
"""Post a Slack DM with the standup summary, then attach details as thread replies.

Usage:
    uv run python slack_threaded.py \
        --main /tmp/standup_summary.txt \
        --thread /tmp/standup_report.txt \
        --thread /tmp/standup_followup.txt

The --thread flag can be repeated; each non-empty file becomes a separate
reply under the main message. Empty / missing files are skipped silently.

Reads SLACK_BOT_TOKEN and SLACK_USER_ID from .env at the repo root.
Prints the parent message ts on success.
"""
import argparse
import os
import sys
from pathlib import Path

import requests
from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parents[4]
load_dotenv(REPO_ROOT / ".env")

API_URL = "https://slack.com/api/chat.postMessage"


def post(token: str, channel: str, text: str, thread_ts: str | None = None) -> str:
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json; charset=utf-8",
    }
    payload = {"channel": channel, "text": text}
    if thread_ts:
        payload["thread_ts"] = thread_ts
    r = requests.post(API_URL, headers=headers, json=payload, timeout=15)
    r.raise_for_status()
    data = r.json()
    if not data.get("ok"):
        raise RuntimeError(f"chat.postMessage failed: {data.get('error')}")
    return data["ts"]


def read_text(path: str) -> str:
    p = Path(path)
    if not p.exists():
        return ""
    return p.read_text().strip()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--main", required=True)
    ap.add_argument("--thread", action="append", default=[])
    args = ap.parse_args()

    token = os.environ.get("SLACK_BOT_TOKEN", "")
    channel = os.environ.get("SLACK_USER_ID", "")
    if not token or not channel:
        print("SLACK_BOT_TOKEN and SLACK_USER_ID must be set", file=sys.stderr)
        sys.exit(1)

    main_text = read_text(args.main)
    if not main_text:
        print("main text is empty — nothing to send", file=sys.stderr)
        sys.exit(1)

    parent_ts = post(token, channel, main_text)
    for path in args.thread:
        body = read_text(path)
        if body:
            post(token, channel, body, thread_ts=parent_ts)

    print(parent_ts)


if __name__ == "__main__":
    main()
