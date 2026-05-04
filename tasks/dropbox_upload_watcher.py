"""Watch Dropbox trip folders for new files and notify matching Slack threads.

Uses Dropbox `files/list_folder/continue` cursor API: efficient polling that
only returns deltas since last check.

On first run, establishes a cursor at current state (no backfill). Subsequent
runs post a Slack notification for each new file (excluding files uploaded
by the archive task itself — messages.md and file-id-prefixed attachments).
"""
import json
import re
import sys
import threading
import time
from pathlib import Path

from connectors.dropbox.client import rpc
from connectors.slack.client import call, get_channel_id, post_message
from tasks.daily_trip_archive import DROPBOX_BASE, TEAM_ROOT

_user_name_cache: dict[str, str] = {}

STATE_PATH = Path(__file__).resolve().parents[1] / "state" / "dropbox_watcher_state.json"
POLL_INTERVAL_SEC = 60

# Files written by our own archive task — skip to avoid feedback spam.
_ARCHIVE_FILE_PATTERN = re.compile(r"^[A-Z0-9]{8}_")


def _load_state() -> dict:
    if STATE_PATH.exists():
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    return {}


def _save_state(state: dict) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def _format_size(n: int | None) -> str:
    if not n:
        return "?"
    if n < 1024:
        return f"{n} B"
    if n < 1024**2:
        return f"{n/1024:.1f} KB"
    if n < 1024**3:
        return f"{n/1024**2:.1f} MB"
    return f"{n/1024**3:.2f} GB"


def _is_archive_written_file(name: str) -> bool:
    if name == "messages.md":
        return True
    if _ARCHIVE_FILE_PATTERN.match(name):
        return True
    return False


def _resolve_uploader(entry: dict) -> str:
    """Try to get uploader's display name from file metadata.

    Dropbox team folders record the last modifier's account_id in
    sharing_info.modified_by. Fall back to '(업로더 미상)' if not available.
    """
    sharing = entry.get("sharing_info") or {}
    account_id = sharing.get("modified_by")
    if not account_id:
        return "(업로더 미상)"
    if account_id in _user_name_cache:
        return _user_name_cache[account_id]
    try:
        res = rpc("users/get_account", {"account_id": account_id})
        name = (res.get("name") or {}).get("display_name") or res.get("email") or account_id
    except Exception:
        name = "(업로더 미상)"
    _user_name_cache[account_id] = name
    return name


def _find_parent_ts(channel: str, title: str, cache: dict) -> str | None:
    if title in cache:
        return cache[title]
    res = call("conversations.history", {"channel": channel, "limit": 200})
    for m in res.get("messages", []):
        t = (m.get("text") or "").strip()
        if t == title:
            cache[title] = m["ts"]
            return m["ts"]
    return None


def _establish_cursor() -> str:
    """Initial recursive list to get a cursor without returning entries."""
    res = rpc(
        "files/list_folder",
        {"path": DROPBOX_BASE, "recursive": True, "limit": 2000},
        path_root=TEAM_ROOT,
    )
    while res.get("has_more"):
        res = rpc("files/list_folder/continue", {"cursor": res["cursor"]}, path_root=TEAM_ROOT)
    return res["cursor"]


def _fetch_changes(cursor: str) -> tuple[list, str]:
    """Return (new_entries, new_cursor). Handles pagination."""
    all_entries: list = []
    current = cursor
    while True:
        try:
            res = rpc("files/list_folder/continue", {"cursor": current}, path_root=TEAM_ROOT)
        except RuntimeError as e:
            if "reset" in str(e):
                # Cursor invalidated — re-establish
                print("[watcher] cursor reset by server, re-establishing")
                new_cursor = _establish_cursor()
                return [], new_cursor
            raise
        all_entries.extend(res.get("entries", []))
        current = res.get("cursor", current)
        if not res.get("has_more"):
            break
    return all_entries, current


def _handle_file_entry(entry: dict, channel: str, parent_cache: dict) -> None:
    path = entry.get("path_display") or ""
    name = entry.get("name") or ""

    # Path: /전략기획/400 시장분석/000 학회 자료/<trip>/<day?>/<file>
    prefix = DROPBOX_BASE + "/"
    if not path.startswith(prefix):
        return
    rel = path[len(prefix):]
    parts = rel.split("/")
    if len(parts) < 2:
        return  # file must be inside at least a trip folder
    trip_title = parts[0]
    if len(parts) == 2:
        day_label = "(출장 폴더 root)"
    else:
        day_label = parts[1]

    # Lecture files (under {trip}/학회강의/...) are intentionally not
    # announced — !끝 produces a single summary message in the lecture thread.
    if len(parts) >= 2 and parts[1] == "학회강의":
        print(f"[watcher] skip lecture upload: {name}")
        return

    if _is_archive_written_file(name):
        print(f"[watcher] skip archive-written: {name}")
        return

    parent_ts = _find_parent_ts(channel, trip_title, parent_cache)
    if not parent_ts:
        print(f"[watcher] no Slack parent for trip {trip_title!r}, skipping {name}")
        return

    uploader = _resolve_uploader(entry)
    text = f"*{uploader}* 님이 *{day_label}*에 `{name}` 업로드 완료"
    blocks = [
        {"type": "section", "text": {"type": "mrkdwn", "text": f"📥 {text}"}},
        {"type": "divider"},
    ]
    try:
        post_message(channel=channel, text=f"{uploader} {name} 업로드", blocks=blocks, thread_ts=parent_ts)
        print(f"[watcher] notified: {uploader} → {trip_title}/{day_label} ({name})")
    except Exception as e:
        print(f"[watcher] failed to post notification for {name}: {e}")


def poll_once(state: dict, channel: str, parent_cache: dict) -> None:
    cursor = state.get("cursor")
    if not cursor:
        cursor = _establish_cursor()
        state["cursor"] = cursor
        _save_state(state)
        print(f"[watcher] cursor established, watching {DROPBOX_BASE} recursively")
        return

    entries, new_cursor = _fetch_changes(cursor)
    for e in entries:
        if e.get(".tag") != "file":
            continue
        _handle_file_entry(e, channel, parent_cache)
    if new_cursor != cursor:
        state["cursor"] = new_cursor
        _save_state(state)


def run_forever() -> None:
    """Main loop — intended to run as a background thread or standalone script."""
    channel = get_channel_id()
    state = _load_state()
    parent_cache: dict = {}
    print(f"[watcher] starting (poll every {POLL_INTERVAL_SEC}s)")
    while True:
        try:
            poll_once(state, channel, parent_cache)
        except Exception as e:
            print(f"[watcher] error: {e}")
        time.sleep(POLL_INTERVAL_SEC)


def start_background_thread() -> threading.Thread:
    t = threading.Thread(target=run_forever, name="dropbox_watcher", daemon=True)
    t.start()
    return t


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    run_forever()
