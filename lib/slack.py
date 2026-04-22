import requests


def send_to_slack(text: str, bot_token: str, user_id: str) -> None:
    """Send a DM to user_id via Slack Web API (user ID used directly as channel)."""
    headers = {
        "Authorization": f"Bearer {bot_token}",
        "Content-Type": "application/json",
    }
    resp = requests.post(
        "https://slack.com/api/chat.postMessage",
        headers=headers,
        json={"channel": user_id, "text": text},
        timeout=15,
    )
    resp.raise_for_status()
    data = resp.json()
    if not data.get("ok"):
        raise RuntimeError(f"chat.postMessage failed: {data.get('error')}")
