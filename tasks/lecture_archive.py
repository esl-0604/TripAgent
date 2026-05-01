"""Lecture archive: !시작 / !끝 orchestrator for #학회강의 channel.

Each attendee has a dedicated thread per trip. Example root titles:
  "260501-07, 미국, DDW 2026 @권태빈"
  "260512-17, 이탈리아, ESGE 2026 @최지영"

Attendees press !시작 at the start of a lecture, post notes / PPT photos
in the thread, and press !끝 to finalize. Between the two triggers, any
content (regular replies, in-Slack file uploads, and !대용량 large-file
uploads routed to the lecture folder) ends up in a single Dropbox folder:

  {trip_folder}/학회강의/{attendee}_강의{N}/

Counter N is per-attendee, per-trip (reset via a fresh thread).
"""
import json
import re
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from connectors.dropbox.upload import (
    create_folder,
    list_folder_file_names,
    list_folder_subfolders,
    upload_file,
)
from connectors.slack.client import call
from connectors.slack.files import download_file
from connectors.slack.threads import fetch_thread_replies
from tasks.daily_trip_archive import (
    DROPBOX_BASE,
    TEAM_ROOT,
    resolve_user,
    slack_ts_to_local,
    stable_attachment_name,
)
from tasks.trip_parser import parse_parent
from tasks.trip_timezones import get_timezone

START_TRIGGER = "!시작"
END_TRIGGER = "!끝"
LECTURE_ROOT = "학회강의"  # sub-folder inside the trip folder

STATE_PATH = Path(__file__).resolve().parents[1] / "state" / "lecture_sessions.json"

# "{trip_title} @{attendee}"
_TITLE_RE = re.compile(r"^(.+?)\s+@\s*(\S+)\s*$")

# "{attendee}_강의{N}"  →  capture N
_LECTURE_FOLDER_RE = re.compile(r"_강의(\d+)$")


def _next_lecture_number(trip_folder: str, attendee: str) -> int:
    """Return next lecture number for an attendee based on actual Dropbox
    folders under {trip_folder}/학회강의/. If no matching folder exists,
    counter resets to 1 (so deleting Dropbox folders re-initializes N)."""
    lecture_root_path = f"{trip_folder}/{LECTURE_ROOT}"
    folders = list_folder_subfolders(lecture_root_path, path_root=TEAM_ROOT)
    prefix = f"{attendee}_강의"
    nums: list[int] = []
    for name in folders:
        if not name.startswith(prefix):
            continue
        m = _LECTURE_FOLDER_RE.search(name)
        if m:
            try:
                nums.append(int(m.group(1)))
            except ValueError:
                pass
    return (max(nums) + 1) if nums else 1


def load_state() -> dict:
    if STATE_PATH.exists():
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    return {"threads": {}}


def save_state(state: dict) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(
        json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def parse_lecture_title(title: str) -> tuple[str, str]:
    """Split '{trip_title} @{attendee}' -> (trip_title, attendee)."""
    m = _TITLE_RE.match((title or "").strip())
    if not m:
        raise ValueError(f"Cannot parse lecture thread title: {title!r}")
    return m.group(1).strip(), m.group(2).strip()


def get_lecture_folder_for_upload(thread_ts: str) -> str | None:
    """Return the Dropbox path of the currently active lecture folder for a
    given #학회강의 thread, or None if no active session."""
    state = load_state()
    tstate = state["threads"].get(thread_ts)
    if not tstate:
        return None
    active = tstate.get("active")
    if not active:
        return None
    trip_folder = tstate.get("trip_folder")
    if not trip_folder:
        return None
    return f"{trip_folder}/{active['folder']}"


def _ensure_thread_state(state: dict, thread_ts: str, title: str) -> dict:
    trip_title, attendee = parse_lecture_title(title)
    parse_parent(trip_title)  # validate trip format; raises ValueError on mismatch
    tstate = state["threads"].setdefault(
        thread_ts,
        {
            "trip": trip_title,
            "attendee": attendee,
            "trip_folder": f"{DROPBOX_BASE}/{trip_title}",
            "counter": 0,
            "active": None,
        },
    )
    # Auto-heal in case the root message was edited or state drifted
    tstate["trip"] = trip_title
    tstate["attendee"] = attendee
    tstate["trip_folder"] = f"{DROPBOX_BASE}/{trip_title}"
    return tstate


def start_session(channel: str, thread_ts: str, trigger_ts: str) -> dict:
    """Open a new lecture session in a thread.

    Returns a dict with keys: status, message, n, attendee, trip, folder.
    status:
      "ok"             — new session opened
      "already_active" — an earlier session is still open; caller should warn
      "error"          — parent missing / title unparseable
    """
    res = call("conversations.replies", {"channel": channel, "ts": thread_ts, "limit": 1})
    msgs = res.get("messages") or []
    if not msgs:
        return {"status": "error", "message": "부모 메시지를 찾을 수 없음"}
    parent = msgs[0]
    title = (parent.get("text") or "").strip()

    state = load_state()
    try:
        tstate = _ensure_thread_state(state, thread_ts, title)
    except ValueError as e:
        return {"status": "error", "message": f"쓰레드 제목 파싱 실패: {e}"}

    if tstate.get("active"):
        act = tstate["active"]
        return {
            "status": "already_active",
            "n": act["n"],
            "attendee": tstate["attendee"],
            "trip": tstate["trip"],
            "folder": act["folder"],
            "message": (
                f"이미 강의{act['n']} 진행 중입니다. "
                f"먼저 `!끝` 입력 후 다시 `!시작`을 눌러주세요."
            ),
        }

    # Derive N from actual Dropbox folders under {trip_folder}/학회강의/.
    # This way, deleting the Dropbox folder resets the counter (the in-memory
    # `counter` field in state is no longer the source of truth, just bookkeeping).
    n = _next_lecture_number(tstate["trip_folder"], tstate["attendee"])
    folder_rel = f"{LECTURE_ROOT}/{tstate['attendee']}_강의{n}"
    tstate["counter"] = n
    tstate["active"] = {
        "n": n,
        "folder": folder_rel,
        "start_trigger_ts": trigger_ts,
    }
    save_state(state)

    create_folder(f"{tstate['trip_folder']}/{LECTURE_ROOT}", path_root=TEAM_ROOT)
    create_folder(f"{tstate['trip_folder']}/{folder_rel}", path_root=TEAM_ROOT)

    return {
        "status": "ok",
        "n": n,
        "attendee": tstate["attendee"],
        "trip": tstate["trip"],
        "folder": folder_rel,
        "message": f"강의{n} 기록 시작 — 폴더 `{folder_rel}` 생성",
    }


def end_session(channel: str, thread_ts: str, trigger_ts: str) -> dict:
    """Finalize the active lecture session.

    Archives every non-trigger reply between start_trigger_ts and trigger_ts
    into messages.md + attachment uploads in the lecture folder.

    Returns: {status, message, n, attendee, folder, replies, uploaded, skipped}.
    status:
      "ok"         — archive written, counter retained, active cleared
      "no_active"  — nothing to end
      "error"      — runtime failure
    """
    state = load_state()
    tstate = state["threads"].get(thread_ts)
    if not tstate or not tstate.get("active"):
        return {
            "status": "no_active",
            "message": "진행 중인 강의가 없습니다. `!시작`을 먼저 입력해주세요.",
        }

    active = tstate["active"]
    start_ts = active["start_trigger_ts"]
    attendee = tstate["attendee"]
    trip_title = tstate["trip"]
    trip_folder = tstate["trip_folder"]
    lecture_folder = f"{trip_folder}/{active['folder']}"

    try:
        trip_tz = get_timezone(parse_parent(trip_title).country, trip_title=trip_title)
    except Exception:
        trip_tz = "UTC"

    all_replies = fetch_thread_replies(channel, thread_ts, oldest=None)
    in_range: list = []
    for r in all_replies:
        ts = r.get("ts")
        if not ts or ts == thread_ts:
            continue
        if float(ts) <= float(start_ts) or float(ts) >= float(trigger_ts):
            continue
        if (r.get("text") or "").strip() in (START_TRIGGER, END_TRIGGER):
            continue
        in_range.append(r)

    user_cache: dict = {}
    md_content = _format_lecture_md(
        trip_title, attendee, active["n"], in_range, user_cache, trip_tz
    )

    create_folder(lecture_folder, path_root=TEAM_ROOT)
    existing = list_folder_file_names(lecture_folder, path_root=TEAM_ROOT)

    uploaded = 0
    skipped = 0
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        md_file = tmp / "messages.md"
        md_file.write_text(md_content, encoding="utf-8")
        upload_file(
            md_file,
            f"{lecture_folder}/messages.md",
            path_root=TEAM_ROOT,
            mode="overwrite",
        )

        for r in in_range:
            for f in r.get("files") or []:
                url = f.get("url_private")
                if not url:
                    continue
                name = stable_attachment_name(f)
                if name in existing:
                    skipped += 1
                    continue
                local_path = tmp / name
                try:
                    download_file(url, local_path)
                    upload_file(
                        local_path,
                        f"{lecture_folder}/{name}",
                        path_root=TEAM_ROOT,
                        mode="overwrite",
                    )
                    uploaded += 1
                except Exception as e:
                    print(f"  [fail] {name}: {e}")

    tstate["active"] = None
    save_state(state)

    return {
        "status": "ok",
        "n": active["n"],
        "attendee": attendee,
        "folder": active["folder"],
        "replies": len(in_range),
        "uploaded": uploaded,
        "skipped": skipped,
        "message": (
            f"강의{active['n']} 아카이브 완료 — 메시지 {len(in_range)}개, "
            f"첨부 +{uploaded} 업로드 / {skipped} 스킵"
        ),
    }


def _format_lecture_md(
    trip_title: str,
    attendee: str,
    n: int,
    replies: list,
    user_cache: dict,
    tz_name: str,
) -> str:
    lines = [
        f"# {trip_title} — {attendee} 강의{n}",
        f"타임존: {tz_name}",
        "",
    ]
    for m in sorted(replies, key=lambda x: float(x.get("ts", "0"))):
        name = resolve_user(m.get("user", ""), user_cache)
        local_dt = slack_ts_to_local(m["ts"], tz_name)
        t_str = local_dt.strftime("%Y-%m-%d %H:%M:%S %Z")
        lines.append(f"## {name} — {t_str}")
        lines.append("")
        text = (m.get("text") or "").strip()
        if text:
            lines.append(text)
            lines.append("")
        files = m.get("files") or []
        if files:
            lines.append("**첨부파일:**")
            for f in files:
                lines.append(f"- {stable_attachment_name(f)}")
            lines.append("")
    return "\n".join(lines)
