import argparse
import os
from datetime import date

from dotenv import load_dotenv

load_dotenv()

GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "")
GITHUB_ORG = os.environ.get("GITHUB_ORG", "")
GITHUB_USERNAME = os.environ.get("GITHUB_USERNAME", "")
GITHUB_EXTRA_REPOS = os.environ.get("GITHUB_EXTRA_REPOS", "")  # comma-separated "org/repo" entries

SLACK_BOT_TOKEN = os.environ.get("SLACK_BOT_TOKEN", "")
SLACK_USER_ID = os.environ.get("SLACK_USER_ID", "")

NOTION_USER_ID = os.environ.get("NOTION_USER_ID", "")  # e.g. user://UUID


def parse_date_arg(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"Invalid date '{value}'. Expected YYYY-MM-DD.")
