import base64
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

API_BASE = "https://api.dropboxapi.com/2"
OAUTH_TOKEN_URL = "https://api.dropboxapi.com/oauth2/token"

_cached_token: str | None = None


def _load_env():
    env_path = Path(__file__).resolve().parents[2] / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip())


def _refresh_access_token() -> str:
    """Exchange refresh_token for a new access_token."""
    _load_env()
    app_key = os.environ.get("DROPBOX_APP_KEY", "").strip()
    app_secret = os.environ.get("DROPBOX_APP_SECRET", "").strip()
    refresh = os.environ.get("DROPBOX_REFRESH_TOKEN", "").strip()
    if not (app_key and app_secret and refresh):
        raise RuntimeError(
            "Refresh flow requires DROPBOX_APP_KEY, DROPBOX_APP_SECRET, DROPBOX_REFRESH_TOKEN in .env. "
            "Run tasks/setup_dropbox_refresh.py to obtain refresh_token."
        )
    body = urllib.parse.urlencode(
        {"grant_type": "refresh_token", "refresh_token": refresh}
    ).encode("utf-8")
    basic = base64.b64encode(f"{app_key}:{app_secret}".encode()).decode()
    req = urllib.request.Request(
        OAUTH_TOKEN_URL,
        data=body,
        headers={
            "Authorization": f"Basic {basic}",
            "Content-Type": "application/x-www-form-urlencoded",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Dropbox oauth2/token HTTP {e.code}: {detail}") from e
    return data["access_token"]


def get_token() -> str:
    """Return cached access_token, refreshing if necessary.

    Prefers refresh flow (APP_KEY+APP_SECRET+REFRESH_TOKEN). Falls back to
    DROPBOX_ACCESS_TOKEN env var if refresh creds are incomplete.
    """
    global _cached_token
    if _cached_token:
        return _cached_token
    _load_env()
    has_refresh = all(
        os.environ.get(k, "").strip()
        for k in ("DROPBOX_APP_KEY", "DROPBOX_APP_SECRET", "DROPBOX_REFRESH_TOKEN")
    )
    if has_refresh:
        _cached_token = _refresh_access_token()
        return _cached_token
    # Fallback: static access token
    token = os.environ.get("DROPBOX_ACCESS_TOKEN", "").strip()
    if not token:
        raise RuntimeError(
            "No Dropbox credentials. Set DROPBOX_REFRESH_TOKEN (+APP_KEY/SECRET) "
            "or DROPBOX_ACCESS_TOKEN in .env"
        )
    _cached_token = token
    return _cached_token


def _invalidate_token() -> None:
    global _cached_token
    _cached_token = None


def get_team_member_id() -> str | None:
    _load_env()
    v = os.environ.get("DROPBOX_TEAM_MEMBER_ID", "").strip()
    return v or None


def rpc(endpoint: str, body: dict | None = None, path_root: dict | None = None) -> dict:
    """Call a Dropbox RPC endpoint. Auto-refreshes on 401 once."""
    url = f"{API_BASE}/{endpoint.lstrip('/')}"
    member_id = get_team_member_id()
    for attempt in range(2):
        token = get_token()
        headers = {"Authorization": f"Bearer {token}"}
        if body is None:
            data = None
        else:
            data = json.dumps(body, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json; charset=utf-8"
        if path_root is not None:
            headers["Dropbox-API-Path-Root"] = json.dumps(path_root)
        if member_id:
            headers["Dropbox-API-Select-User"] = member_id
        req = urllib.request.Request(url, data=data, method="POST", headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                raw = resp.read().decode("utf-8")
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", errors="replace")
            if e.code == 401 and attempt == 0 and "expired_access_token" in detail:
                _invalidate_token()
                continue
            raise RuntimeError(f"Dropbox {endpoint} HTTP {e.code}: {detail}") from e
    raise RuntimeError(f"Dropbox {endpoint} failed after refresh retry")


def healthcheck() -> dict:
    return rpc("users/get_current_account")


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    info = healthcheck()
    print(json.dumps(info, ensure_ascii=False, indent=2))
