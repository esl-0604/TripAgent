import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

API_BASE = "https://slack.com/api"


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


def get_token(kind: str = "bot") -> str:
    _load_env()
    var = {"bot": "TRIP_BOT_TOKEN", "user": "TRIP_USER_TOKEN"}[kind]
    token = os.environ.get(var, "")
    if not token:
        raise RuntimeError(f"{var} is empty. Fill it in .env")
    return token


def get_channel_id() -> str:
    _load_env()
    cid = os.environ.get("TRIP_CHANNEL_ID", "")
    if not cid:
        raise RuntimeError("TRIP_CHANNEL_ID is empty. Fill it in .env")
    return cid


def call(method: str, payload: dict | None = None, token_kind: str = "bot") -> dict:
    """Call Slack Web API method using form-urlencoded POST.

    Complex values (dict/list) are JSON-stringified per Slack's convention.
    This form works for both read (conversations.*) and write (chat.postMessage) methods.
    """
    token = get_token(token_kind)
    url = f"{API_BASE}/{method}"
    params: dict[str, str] = {}
    for k, v in (payload or {}).items():
        if v is None:
            continue
        if isinstance(v, (dict, list)):
            params[k] = json.dumps(v, ensure_ascii=False)
        elif isinstance(v, bool):
            params[k] = "true" if v else "false"
        else:
            params[k] = str(v)
    data = urllib.parse.urlencode(params).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        method="POST",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/x-www-form-urlencoded; charset=utf-8",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Slack {method} HTTP {e.code}: {detail}") from e
    if not body.get("ok"):
        raise RuntimeError(f"Slack {method} error: {body}")
    return body


def auth_test() -> dict:
    return call("auth.test")


def post_message(
    channel: str,
    text: str | None = None,
    blocks: list | None = None,
    thread_ts: str | None = None,
) -> dict:
    payload: dict = {"channel": channel}
    if text is not None:
        payload["text"] = text
    if blocks is not None:
        payload["blocks"] = blocks
    if thread_ts is not None:
        payload["thread_ts"] = thread_ts
    return call("chat.postMessage", payload)


def delete_message(channel: str, ts: str, token_kind: str = "bot") -> dict:
    return call("chat.delete", {"channel": channel, "ts": ts}, token_kind=token_kind)


def list_channel_messages(channel: str, limit: int = 200) -> list:
    out = []
    cursor = None
    while True:
        payload = {"channel": channel, "limit": limit}
        if cursor:
            payload["cursor"] = cursor
        res = call("conversations.history", payload)
        out.extend(res.get("messages", []))
        cursor = res.get("response_metadata", {}).get("next_cursor")
        if not cursor:
            break
    return out


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    info = auth_test()
    print(json.dumps(info, ensure_ascii=False, indent=2))
