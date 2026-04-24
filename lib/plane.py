import time
from typing import Optional

import requests

from lib.config import PLANE_API_KEY, PLANE_BASE_URL, PLANE_WORKSPACE_SLUG


def plane_headers() -> dict:
    return {
        "X-API-Key": PLANE_API_KEY,
        "Content-Type": "application/json",
    }


def plane_get(path: str, params: Optional[dict] = None) -> dict:
    url = f"{PLANE_BASE_URL}{path}"
    for attempt in range(3):
        resp = requests.get(url, headers=plane_headers(), params=params, timeout=15)
        if resp.status_code == 429:
            wait = int(resp.headers.get("Retry-After", 5))
            time.sleep(wait)
            continue
        resp.raise_for_status()
        return resp.json()
    resp.raise_for_status()
    return resp.json()


def plane_patch(path: str, data: dict) -> dict:
    url = f"{PLANE_BASE_URL}{path}"
    for attempt in range(3):
        resp = requests.patch(url, headers=plane_headers(), json=data, timeout=15)
        if resp.status_code == 429:
            wait = int(resp.headers.get("Retry-After", 5))
            time.sleep(wait)
            continue
        resp.raise_for_status()
        return resp.json()
    resp.raise_for_status()
    return resp.json()


def get_me() -> dict:
    """Return the current authenticated user."""
    return plane_get("/users/me/")


def get_projects() -> list[dict]:
    """Return all projects in the workspace."""
    data = plane_get(f"/workspaces/{PLANE_WORKSPACE_SLUG}/projects/")
    return data.get("results", data) if isinstance(data, dict) else data


def get_states(project_id: str) -> dict[str, dict]:
    """Return a dict of state_id -> state object."""
    data = plane_get(f"/workspaces/{PLANE_WORKSPACE_SLUG}/projects/{project_id}/states/")
    states = data.get("results", data) if isinstance(data, dict) else data
    return {s["id"]: s for s in states}


def get_my_issues(project_id: str, member_id: str) -> list[dict]:
    """Fetch all issues assigned to member_id in a project.

    The API ignores the assignees filter param, so we paginate all issues
    and filter client-side by matching member_id in the assignees list.
    """
    all_issues = []
    page = 1
    while True:
        data = plane_get(
            f"/workspaces/{PLANE_WORKSPACE_SLUG}/projects/{project_id}/work-items/",
            params={"page": page},
        )
        if isinstance(data, list):
            all_issues.extend(data)
            break
        results = data.get("results", [])
        all_issues.extend(results)
        if not data.get("next"):
            break
        page += 1

    def is_assigned(issue: dict) -> bool:
        for a in issue.get("assignees", []):
            if isinstance(a, dict):
                if a.get("id") == member_id:
                    return True
            elif a == member_id:
                return True
        return False

    return [issue for issue in all_issues if is_assigned(issue)]


def get_issue_by_id(project_id: str, issue_id: str) -> dict:
    """Fetch a single issue by id."""
    return plane_get(f"/workspaces/{PLANE_WORKSPACE_SLUG}/projects/{project_id}/work-items/{issue_id}/")


def get_all_project_issues(project_id: str) -> list[dict]:
    """Fetch all issues in a project (paginated)."""
    all_issues = []
    page = 1
    while True:
        data = plane_get(
            f"/workspaces/{PLANE_WORKSPACE_SLUG}/projects/{project_id}/work-items/",
            params={"page": page},
        )
        if isinstance(data, list):
            all_issues.extend(data)
            break
        results = data.get("results", [])
        all_issues.extend(results)
        if not data.get("next"):
            break
        page += 1
    return all_issues


def get_workspace_members() -> dict[str, str]:
    """Return a dict of member_id -> display_name for all workspace members."""
    data = plane_get(f"/workspaces/{PLANE_WORKSPACE_SLUG}/members/")
    members = data.get("results", data) if isinstance(data, dict) else data
    return {m["id"]: m.get("display_name") or m.get("email", m["id"]) for m in members}


def get_cycles(project_id: str) -> list[dict]:
    """Return all cycles for a project."""
    data = plane_get(f"/workspaces/{PLANE_WORKSPACE_SLUG}/projects/{project_id}/cycles/")
    return data.get("results", data) if isinstance(data, dict) else data


def get_cycle_issues(project_id: str, cycle_id: str) -> list[dict]:
    """Return all issues in a cycle."""
    data = plane_get(
        f"/workspaces/{PLANE_WORKSPACE_SLUG}/projects/{project_id}/cycles/{cycle_id}/cycle-issues/",
    )
    if isinstance(data, list):
        return data
    return data.get("results", [])


def get_issue_by_sequence_id(project_id: str, sequence_id: int) -> dict | None:
    """Fetch a single issue by its sequence_id (e.g. 123 from DATA-123). Returns None if not found."""
    for issue in get_all_project_issues(project_id):
        if issue.get("sequence_id") == sequence_id:
            return issue
    return None


def get_issue_identifier(project: dict, issue: dict) -> str:
    """Build e.g. DATA-123 from project identifier + issue sequence."""
    identifier = project.get("identifier", project.get("name", "??"))
    seq = issue.get("sequence_id", issue.get("id", "?"))
    return f"{identifier}-{seq}"


def build_issue_url(project_id: str, issue_id: str) -> str:
    return f"https://app.plane.so/{PLANE_WORKSPACE_SLUG}/projects/{project_id}/issues/{issue_id}"


def build_browse_url(ticket: str) -> str:
    """Fallback URL for tickets not found in Plane, using the browse shortlink."""
    return f"https://app.plane.so/{PLANE_WORKSPACE_SLUG}/browse/{ticket}/"
