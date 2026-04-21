"""Socket Mode listener for #출장일지 commands.

Supports two triggers posted as thread replies:
  !아카이브  — archives new thread replies + Slack attachments into Dropbox
  !대용량    — creates a Dropbox File Request URL for mobile large-file upload

Keeps a persistent WebSocket connection to Slack. No public endpoint needed.
Run with: python tasks/trip_listener.py
"""
import os
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from slack_sdk import WebClient
from slack_sdk.socket_mode import SocketModeClient
from slack_sdk.socket_mode.request import SocketModeRequest
from slack_sdk.socket_mode.response import SocketModeResponse

from connectors.dropbox.share import get_or_create_folder_link
from connectors.dropbox.upload import create_folder
from connectors.slack.client import (
    _load_env,
    call,
    delete_message,
    get_channel_id,
    get_token,
    post_message,
)
from tasks.daily_trip_archive import (
    DROPBOX_BASE,
    TEAM_ROOT,
    archive_parent_by_ts,
)
from tasks.dropbox_upload_watcher import start_background_thread as start_watcher
from tasks.trip_parser import day_folder_name, parse_parent
from tasks.trip_timezones import get_timezone

ARCHIVE_TRIGGER = "!아카이브"
UPLOAD_TRIGGER = "!대용량"
_lock = threading.Lock()


def _blocks(emoji: str, text: str) -> list:
    return [
        {"type": "section", "text": {"type": "mrkdwn", "text": f"{emoji} {text}"}},
        {"type": "divider"},
    ]


def _resolve_day_destination(parent: dict) -> tuple[str, str, dict]:
    """From the parent message, compute target Dropbox folder + human label.

    Returns (dropbox_path, label, trip_info).
    If today is before trip start, uses trip root folder.
    """
    title = (parent.get("text") or "").strip()
    trip = parse_parent(title)
    trip_tz = get_timezone(trip.country, trip_title=title)
    today_local = datetime.now(ZoneInfo(trip_tz)).date()
    day_num = (today_local - trip.start_date).days + 1
    trip_folder = f"{DROPBOX_BASE}/{title}"
    if day_num >= 1:
        folder = day_folder_name(today_local, day_num)
        return f"{trip_folder}/{folder}", folder, {"title": title, "tz": trip_tz}
    return trip_folder, "(출장 시작 전 — 출장 폴더 root)", {"title": title, "tz": trip_tz}


def handle_archive(channel_id: str, parent_ts: str, trigger_ts: str, user_id: str) -> None:
    print(f"[trigger] {ARCHIVE_TRIGGER} from {user_id} on parent_ts={parent_ts}")
    with _lock:
        try:
            result = archive_parent_by_ts(parent_ts, dry_run=False)
            status = result.get("status", "unknown")
            msg = result.get("message", "")
            title = result.get("title", "")
            if status == "ok":
                blocks = _blocks("✅", f"*아카이브 완료* — _{title}_\n{msg}")
            elif status == "nop":
                blocks = _blocks("ℹ️", f"_{title}_ — {msg}")
            elif status == "skipped":
                blocks = _blocks("⚠️", f"스킵: {msg}")
            else:
                blocks = _blocks("⚠️", f"알 수 없는 상태: {status}")
        except Exception as e:
            print(f"[error archive] {e}")
            blocks = _blocks("❌", f"오류 발생: `{e}`")

        try:
            post_message(channel=channel_id, text="archive result", blocks=blocks, thread_ts=parent_ts)
        except Exception as e:
            print(f"[warn] failed to post archive response: {e}")

        try:
            delete_message(channel=channel_id, ts=trigger_ts, token_kind="user")
        except Exception as e:
            print(f"[warn] failed to delete trigger: {e}")


def handle_upload(channel_id: str, parent_ts: str, trigger_ts: str, user_id: str) -> None:
    print(f"[trigger] {UPLOAD_TRIGGER} from {user_id} on parent_ts={parent_ts}")
    with _lock:
        try:
            # Fetch parent to parse trip title
            res = call("conversations.replies", {"channel": channel_id, "ts": parent_ts, "limit": 1})
            msgs = res.get("messages") or []
            if not msgs:
                raise RuntimeError("parent 메시지를 찾을 수 없음")
            parent = msgs[0]

            dest, label, info = _resolve_day_destination(parent)
            title = info["title"]

            # Ensure destination folder exists
            create_folder(dest, path_root=TEAM_ROOT)

            # Shared folder link — opens native Dropbox app on mobile
            folder_url = get_or_create_folder_link(dest, path_root=TEAM_ROOT)
            text = (
                f"*{label}*\n"
                f"<{folder_url}|드랍박스 앱 열기>\n\n"
                f"_앱에서 폴더 열린 뒤 `+` 버튼으로 업로드_\n"
                f"_화면 잠가도·앱 전환해도 이어집니다 (OS 백그라운드)_"
            )
            blocks = _blocks("📤", text)
        except Exception as e:
            print(f"[error upload] {e}")
            blocks = _blocks("❌", f"업로드 링크 생성 실패: `{e}`")

        try:
            post_message(channel=channel_id, text="upload link", blocks=blocks, thread_ts=parent_ts)
        except Exception as e:
            print(f"[warn] failed to post upload link: {e}")

        try:
            delete_message(channel=channel_id, ts=trigger_ts, token_kind="user")
        except Exception as e:
            print(f"[warn] failed to delete trigger: {e}")


def handle_event(event: dict) -> None:
    channel_id = get_channel_id()
    if event.get("channel") != channel_id:
        return
    if event.get("subtype"):
        return

    text = (event.get("text") or "").strip()
    if text not in (ARCHIVE_TRIGGER, UPLOAD_TRIGGER):
        return

    ts = event.get("ts")
    thread_ts = event.get("thread_ts")
    user_id = event.get("user") or "?"

    if not thread_ts or thread_ts == ts:
        print(f"[ignore] '{text}' at root level (not inside a trip thread) from {user_id}")
        return

    if text == ARCHIVE_TRIGGER:
        handle_archive(channel_id, thread_ts, ts, user_id)
    elif text == UPLOAD_TRIGGER:
        handle_upload(channel_id, thread_ts, ts, user_id)


def on_request(client: SocketModeClient, req: SocketModeRequest) -> None:
    client.send_socket_mode_response(SocketModeResponse(envelope_id=req.envelope_id))

    if req.type != "events_api":
        return
    event = (req.payload or {}).get("event") or {}
    if event.get("type") != "message":
        return
    try:
        handle_event(event)
    except Exception as e:
        print(f"[error] handle_event crashed: {e}")


def main() -> None:
    _load_env()
    app_token = os.environ.get("TRIP_APP_TOKEN", "")
    if not app_token:
        raise RuntimeError("TRIP_APP_TOKEN is empty. Add xapp-... to .env")
    bot_token = get_token("bot")
    channel_id = get_channel_id()

    web = WebClient(token=bot_token)
    sm = SocketModeClient(app_token=app_token, web_client=web)
    sm.socket_mode_request_listeners.append(on_request)

    print(
        f"[listener] connecting (channel={channel_id}, triggers={ARCHIVE_TRIGGER!r} / {UPLOAD_TRIGGER!r})..."
    )
    sm.connect()
    print("[listener] connected. Starting Dropbox upload watcher thread.")
    start_watcher()
    print("[listener] Ctrl+C to stop.")

    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        print("\n[listener] shutting down...")
        try:
            sm.disconnect()
            sm.close()
        except Exception:
            pass


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    main()
