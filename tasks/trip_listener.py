"""Socket Mode listener for #출장일지 and #학회강의 commands.

#출장일지 (TRIP_CHANNEL_ID):
  !아카이브  — archives new thread replies + attachments into Day N folder
  !대용량    — shared folder link into today's Day N folder

#학회강의 (LECTURE_CHANNEL_ID):
  !시작      — open a lecture session; create 학회강의/{attendee}_강의{N}/ folder
  !끝        — finalize session; archive replies + attachments into the folder
  !대용량    — shared folder link into the active lecture folder

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
    get_lecture_channel_id,
    get_token,
    post_message,
)
from tasks.daily_trip_archive import (
    DROPBOX_BASE,
    PRE_TRIP_FOLDER_NAME,
    TEAM_ROOT,
    archive_parent_by_ts,
)
from tasks.dropbox_upload_watcher import start_background_thread as start_watcher
from tasks.lecture_archive import (
    END_TRIGGER,
    START_TRIGGER,
    end_session,
    get_lecture_folder_for_upload,
    start_session,
)
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
    Pre-trip uploads are routed into a single "출장전" folder (matches the
    !아카이브 behavior).
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
    return (
        f"{trip_folder}/{PRE_TRIP_FOLDER_NAME}",
        PRE_TRIP_FOLDER_NAME,
        {"title": title, "tz": trip_tz},
    )


def _try_delete_trigger(channel: str, ts: str, label: str) -> None:
    try:
        delete_message(channel=channel, ts=ts, token_kind="user")
    except Exception as e:
        print(f"[warn] failed to delete {label} trigger: {e}")


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

        _try_delete_trigger(channel_id, trigger_ts, ARCHIVE_TRIGGER)


def handle_upload_trip(channel_id: str, parent_ts: str, trigger_ts: str, user_id: str) -> None:
    print(f"[trigger] {UPLOAD_TRIGGER} (trip) from {user_id} on parent_ts={parent_ts}")
    with _lock:
        try:
            res = call("conversations.replies", {"channel": channel_id, "ts": parent_ts, "limit": 1})
            msgs = res.get("messages") or []
            if not msgs:
                raise RuntimeError("parent 메시지를 찾을 수 없음")
            parent = msgs[0]

            dest, label, _info = _resolve_day_destination(parent)
            create_folder(dest, path_root=TEAM_ROOT)
            folder_url = get_or_create_folder_link(dest, path_root=TEAM_ROOT)
            text = (
                f"*{label}*\n"
                f"<{folder_url}|드랍박스 앱 열기>\n\n"
                f"_앱에서 폴더 열린 뒤 `+` 버튼으로 업로드_\n"
                f"_화면 잠가도·앱 전환해도 이어집니다 (OS 백그라운드)_"
            )
            blocks = _blocks("📤", text)
        except Exception as e:
            print(f"[error upload trip] {e}")
            blocks = _blocks("❌", f"업로드 링크 생성 실패: `{e}`")

        try:
            post_message(channel=channel_id, text="upload link", blocks=blocks, thread_ts=parent_ts)
        except Exception as e:
            print(f"[warn] failed to post upload link: {e}")

        _try_delete_trigger(channel_id, trigger_ts, UPLOAD_TRIGGER)


def handle_upload_lecture(channel_id: str, parent_ts: str, trigger_ts: str, user_id: str) -> None:
    print(f"[trigger] {UPLOAD_TRIGGER} (lecture) from {user_id} on parent_ts={parent_ts}")
    with _lock:
        try:
            dest = get_lecture_folder_for_upload(parent_ts)
            if not dest:
                blocks = _blocks(
                    "⚠️",
                    "진행 중인 강의가 없습니다. `!시작`을 먼저 입력해주세요.",
                )
            else:
                create_folder(dest, path_root=TEAM_ROOT)
                folder_url = get_or_create_folder_link(dest, path_root=TEAM_ROOT)
                label = dest.rsplit("/", 1)[-1]
                text = (
                    f"*{label}*\n"
                    f"<{folder_url}|드랍박스 앱 열기>\n\n"
                    f"_앱에서 폴더 열린 뒤 `+` 버튼으로 업로드_\n"
                    f"_화면 잠가도·앱 전환해도 이어집니다 (OS 백그라운드)_"
                )
                blocks = _blocks("📤", text)
        except Exception as e:
            print(f"[error upload lecture] {e}")
            blocks = _blocks("❌", f"업로드 링크 생성 실패: `{e}`")

        try:
            post_message(channel=channel_id, text="upload link", blocks=blocks, thread_ts=parent_ts)
        except Exception as e:
            print(f"[warn] failed to post upload link: {e}")

        _try_delete_trigger(channel_id, trigger_ts, UPLOAD_TRIGGER)


def handle_start(channel_id: str, parent_ts: str, trigger_ts: str, user_id: str) -> None:
    print(f"[trigger] {START_TRIGGER} from {user_id} on parent_ts={parent_ts}")
    with _lock:
        try:
            result = start_session(channel_id, parent_ts, trigger_ts)
            status = result.get("status")
            if status == "ok":
                text = (
                    f"*{result['attendee']} · 강의{result['n']} 기록 시작*\n"
                    f"폴더: `{result['folder']}`\n\n"
                    f"_이 쓰레드 내의 모든 댓글·첨부파일이 `!끝` 입력 시점에 위 폴더로 아카이브됩니다._\n"
                    f"_대용량 파일은 `!대용량`을 입력하면 같은 폴더 업로드 링크가 나옵니다._"
                )
                blocks = _blocks("🎙️", text)
            elif status == "already_active":
                blocks = _blocks("⚠️", result.get("message", "이미 진행 중인 강의가 있습니다."))
            else:
                blocks = _blocks("❌", result.get("message", "시작 실패"))
        except Exception as e:
            print(f"[error start] {e}")
            blocks = _blocks("❌", f"시작 실패: `{e}`")

        try:
            post_message(channel=channel_id, text="lecture start", blocks=blocks, thread_ts=parent_ts)
        except Exception as e:
            print(f"[warn] failed to post start response: {e}")

        _try_delete_trigger(channel_id, trigger_ts, START_TRIGGER)


def handle_end(channel_id: str, parent_ts: str, trigger_ts: str, user_id: str) -> None:
    print(f"[trigger] {END_TRIGGER} from {user_id} on parent_ts={parent_ts}")
    with _lock:
        try:
            result = end_session(channel_id, parent_ts, trigger_ts)
            status = result.get("status")
            if status == "ok":
                text = (
                    f"*{result['attendee']} · 강의{result['n']} 아카이브 완료*\n"
                    f"폴더: `{result['folder']}`\n"
                    f"메시지 {result['replies']}개, 첨부 +{result['uploaded']} 업로드 "
                    f"/ {result['skipped']} 스킵"
                )
                blocks = _blocks("✅", text)
            elif status == "no_active":
                blocks = _blocks("⚠️", result.get("message", "진행 중인 강의가 없습니다."))
            else:
                blocks = _blocks("❌", result.get("message", "종료 실패"))
        except Exception as e:
            print(f"[error end] {e}")
            blocks = _blocks("❌", f"종료 실패: `{e}`")

        try:
            post_message(channel=channel_id, text="lecture end", blocks=blocks, thread_ts=parent_ts)
        except Exception as e:
            print(f"[warn] failed to post end response: {e}")

        _try_delete_trigger(channel_id, trigger_ts, END_TRIGGER)


def handle_event(event: dict, trip_channel: str, lecture_channel: str) -> None:
    channel = event.get("channel")
    if channel not in (trip_channel, lecture_channel):
        return
    if event.get("subtype"):
        return

    text = (event.get("text") or "").strip()
    ts = event.get("ts")
    thread_ts = event.get("thread_ts")
    user_id = event.get("user") or "?"

    if not thread_ts or thread_ts == ts:
        # Commands are only valid as thread replies.
        return

    if channel == trip_channel:
        if text == ARCHIVE_TRIGGER:
            handle_archive(channel, thread_ts, ts, user_id)
        elif text == UPLOAD_TRIGGER:
            handle_upload_trip(channel, thread_ts, ts, user_id)
    elif channel == lecture_channel:
        if text == START_TRIGGER:
            handle_start(channel, thread_ts, ts, user_id)
        elif text == END_TRIGGER:
            handle_end(channel, thread_ts, ts, user_id)
        elif text == UPLOAD_TRIGGER:
            handle_upload_lecture(channel, thread_ts, ts, user_id)


def _make_on_request(trip_channel: str, lecture_channel: str):
    def on_request(client: SocketModeClient, req: SocketModeRequest) -> None:
        client.send_socket_mode_response(SocketModeResponse(envelope_id=req.envelope_id))
        if req.type != "events_api":
            return
        event = (req.payload or {}).get("event") or {}
        if event.get("type") != "message":
            return
        try:
            handle_event(event, trip_channel, lecture_channel)
        except Exception as e:
            print(f"[error] handle_event crashed: {e}")

    return on_request


def main() -> None:
    _load_env()
    app_token = os.environ.get("TRIP_APP_TOKEN", "")
    if not app_token:
        raise RuntimeError("TRIP_APP_TOKEN is empty. Add xapp-... to .env")
    bot_token = get_token("bot")
    trip_channel = get_channel_id()
    try:
        lecture_channel = get_lecture_channel_id()
    except RuntimeError as e:
        print(f"[warn] {e} — 학회강의 commands disabled.")
        lecture_channel = ""

    web = WebClient(token=bot_token)
    sm = SocketModeClient(app_token=app_token, web_client=web)
    sm.socket_mode_request_listeners.append(_make_on_request(trip_channel, lecture_channel))

    triggers_trip = f"{ARCHIVE_TRIGGER!r}/{UPLOAD_TRIGGER!r}"
    triggers_lec = f"{START_TRIGGER!r}/{END_TRIGGER!r}/{UPLOAD_TRIGGER!r}"
    print(
        f"[listener] connecting...\n"
        f"  trip_channel   = {trip_channel}  (triggers: {triggers_trip})\n"
        f"  lecture_channel= {lecture_channel or '(disabled)'}  (triggers: {triggers_lec})"
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
