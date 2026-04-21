import urllib.request
from pathlib import Path

from connectors.slack.client import get_token


def download_file(url_private: str, dest: Path) -> int:
    """Download a Slack-hosted file (url_private) using bot token auth.

    Returns bytes written.
    """
    token = get_token("bot")
    req = urllib.request.Request(
        url_private,
        headers={"Authorization": f"Bearer {token}"},
    )
    dest.parent.mkdir(parents=True, exist_ok=True)
    with urllib.request.urlopen(req, timeout=120) as resp:
        data = resp.read()
    dest.write_bytes(data)
    return len(data)
