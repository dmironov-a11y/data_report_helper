import argparse
import os
import sys
from datetime import date

from dotenv import load_dotenv

load_dotenv()

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

PLANE_API_KEY = os.environ.get("PLANE_API_KEY", "")
PLANE_BASE_URL = "https://api.plane.so/api/v1"
PLANE_WORKSPACE_SLUG = os.environ.get("PLANE_WORKSPACE_SLUG", "")
PLANE_PROJECT_ID = os.environ.get("PLANE_PROJECT_ID", "")

GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "")
GITHUB_ORG = os.environ.get("GITHUB_ORG", "")
GITHUB_USERNAME = os.environ.get("GITHUB_USERNAME", "")
GITHUB_EXTRA_REPOS = os.environ.get("GITHUB_EXTRA_REPOS", "")  # comma-separated "org/repo" entries

SLACK_BOT_TOKEN = os.environ.get("SLACK_BOT_TOKEN", "")
SLACK_USER_ID = os.environ.get("SLACK_USER_ID", "")


def validate_config() -> bool:
    errors = []
    if not PLANE_API_KEY:
        errors.append("PLANE_API_KEY is not set")
    if not PLANE_WORKSPACE_SLUG:
        errors.append("PLANE_WORKSPACE_SLUG is not set")
    if errors:
        for e in errors:
            print(f"[ERROR] {e}", file=sys.stderr)
        print("\nSet the required environment variables and re-run.", file=sys.stderr)
        return False
    return True


def parse_date_arg(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"Invalid date '{value}'. Expected YYYY-MM-DD.")
