import time
from typing import Optional

import requests

from lib.config import NOTION_API_KEY, NOTION_DATABASE_ID

NOTION_BASE_URL = "https://api.notion.com/v1"
NOTION_VERSION = "2022-06-28"


def _headers() -> dict:
    return {
        "Authorization": f"Bearer {NOTION_API_KEY}",
        "Content-Type": "application/json",
        "Notion-Version": NOTION_VERSION,
    }


def notion_get(path: str, params: Optional[dict] = None) -> dict:
    url = f"{NOTION_BASE_URL}{path}"
    for attempt in range(3):
        resp = requests.get(url, headers=_headers(), params=params, timeout=15)
        if resp.status_code == 429:
            time.sleep(int(resp.headers.get("Retry-After", 5)))
            continue
        resp.raise_for_status()
        return resp.json()
    resp.raise_for_status()
    return {}


def notion_post(path: str, data: dict) -> dict:
    url = f"{NOTION_BASE_URL}{path}"
    for attempt in range(3):
        resp = requests.post(url, headers=_headers(), json=data, timeout=15)
        if resp.status_code == 429:
            time.sleep(int(resp.headers.get("Retry-After", 5)))
            continue
        resp.raise_for_status()
        return resp.json()
    resp.raise_for_status()
    return {}


def notion_patch(path: str, data: dict) -> dict:
    url = f"{NOTION_BASE_URL}{path}"
    for attempt in range(3):
        resp = requests.patch(url, headers=_headers(), json=data, timeout=15)
        if resp.status_code == 429:
            time.sleep(int(resp.headers.get("Retry-After", 5)))
            continue
        resp.raise_for_status()
        return resp.json()
    resp.raise_for_status()
    return {}


def get_database(database_id: str = "") -> dict:
    """Return the database object (includes full property schema)."""
    return notion_get(f"/databases/{database_id or NOTION_DATABASE_ID}")


def get_title_prop_name(database_id: str = "") -> str:
    """Find the name of the title-type property in a database schema."""
    db = get_database(database_id)
    for name, prop in db.get("properties", {}).items():
        if prop.get("type") == "title":
            return name
    return "Name"


def query_database(
    filter_body: Optional[dict] = None,
    sorts: Optional[list] = None,
    database_id: str = "",
) -> list[dict]:
    """Return all pages from a database, handling cursor pagination."""
    db_id = database_id or NOTION_DATABASE_ID
    body: dict = {}
    if filter_body:
        body["filter"] = filter_body
    if sorts:
        body["sorts"] = sorts

    pages = []
    while True:
        data = notion_post(f"/databases/{db_id}/query", body)
        pages.extend(data.get("results", []))
        if not data.get("has_more"):
            break
        body["start_cursor"] = data["next_cursor"]
    return pages


def get_page(page_id: str) -> dict:
    """Fetch a single page by ID."""
    return notion_get(f"/pages/{page_id}")


def get_page_title(page: dict, title_prop: str = "") -> str:
    """Extract plain text from the title property of a page.

    Tries title_prop first, then scans all properties for type=title.
    """
    props = page.get("properties", {})
    candidates = ([props[title_prop]] if title_prop and title_prop in props else []) + list(props.values())
    for prop in candidates:
        if prop.get("type") == "title":
            return "".join(t.get("plain_text", "") for t in prop.get("title", []))
    return ""


def get_page_select(page: dict, prop_name: str) -> str:
    """Return selected option name for a Select property, or ''."""
    prop = page.get("properties", {}).get(prop_name, {})
    return (prop.get("select") or {}).get("name", "")


def get_page_status(page: dict, prop_name: str) -> str:
    """Return status name for a Status property, or ''."""
    prop = page.get("properties", {}).get(prop_name, {})
    return (prop.get("status") or {}).get("name", "")


def get_page_relation_ids(page: dict, prop_name: str) -> list[str]:
    """Return related page IDs for a Relation property."""
    prop = page.get("properties", {}).get(prop_name, {})
    return [r["id"] for r in prop.get("relation", [])]


def get_page_people(page: dict, prop_name: str) -> list[str]:
    """Return display names from a People property."""
    prop = page.get("properties", {}).get(prop_name, {})
    return [p.get("name", p.get("id", "?")) for p in prop.get("people", [])]


def update_page_title(page_id: str, title_prop_name: str, new_title: str) -> dict:
    """PATCH the title property of a page."""
    return notion_patch(
        f"/pages/{page_id}",
        {
            "properties": {
                title_prop_name: {
                    "title": [{"text": {"content": new_title}}]
                }
            }
        },
    )
