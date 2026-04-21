"""Daily orchestrator: Slack #출장일지 threads → Dropbox trip folders.

Run with --dry-run to see what would happen without writing anything.
"""
import argparse
import json
import sys
import tempfile
from datetime import date, datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from connectors.dropbox.upload import create_folder, list_folder_file_names, upload_file
from connectors.slack.client import auth_test, call, get_channel_id
from connectors.slack.files import download_file
from connectors.slack.threads import fetch_thread_replies, find_parent_messages
from tasks.trip_parser import day_folder_name, parse_parent
from tasks.trip_timezones import get_timezone

# Team space root (엔도로보틱스)
TEAM_ROOT_NS = "4745864929"
TEAM_ROOT = {".tag": "root", "root": TEAM_ROOT_NS}
DROPBOX_BASE = "/전략기획/400 시장분석/000 학회 자료"

STATE_PATH = Path(__file__).resolve().parents[1] / "state" / "trip_archive_state.json"


def load_state() -> dict:
    if STATE_PATH.exists():
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    return {"threads": {}, "last_run": None}


def save_state(state: dict) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def slack_ts_to_local(ts: str, tz_name: str) -> datetime:
    dt = datetime.fromtimestamp(float(ts), tz=timezone.utc)
    return dt.astimezone(ZoneInfo(tz_name))


def resolve_user(user_id: str, cache: dict) -> str:
    if not user_id:
        return "(unknown)"
    if user_id in cache:
        return cache[user_id]
    try:
        res = call("users.info", {"user": user_id})
        u = res.get("user") or {}
        name = (
            u.get("profile", {}).get("display_name")
            or u.get("real_name")
            or u.get("name")
            or user_id
        )
    except Exception:
        name = user_id
    cache[user_id] = name
    return name


def stable_attachment_name(slack_file: dict) -> str:
    """File-id-prefixed name so re-uploads are idempotent."""
    file_id = slack_file.get("id") or "xxxxxxxx"
    original = slack_file.get("name") or file_id or "file"
    safe_original = original.replace("/", "_")
    return f"{file_id[:8]}_{safe_original}"


def format_messages_md(
    trip_title: str,
    day_label: str,
    replies: list,
    user_cache: dict,
    tz_name: str,
) -> str:
    lines = [f"# {trip_title} — {day_label}", f"타임존: {tz_name}", ""]
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


def process_parent(
    parent: dict,
    channel: str,
    state: dict,
    user_cache: dict,
    dry_run: bool,
) -> dict:
    """Archive new replies for a single parent. Returns summary dict:
      {"status": "ok"|"nop"|"skipped", "title": str, "message": str,
       "days_archived": int, "replies_archived": int, "attachments": int}
    """
    title = (parent.get("text") or "").strip()
    parent_ts = parent["ts"]

    try:
        trip = parse_parent(title)
    except ValueError as e:
        print(f"[skip] unparseable: {title!r}")
        return {"status": "skipped", "title": title, "message": f"parse 실패: {e}"}
    try:
        trip_tz = get_timezone(trip.country, trip_title=title)
    except KeyError as e:
        print(f"[skip] unknown country for {title!r}: {e}")
        return {"status": "skipped", "title": title, "message": f"타임존 없음: {e}"}

    thread_state = state["threads"].get(parent_ts, {})
    last_ts = thread_state.get("last_archived_ts", parent_ts)

    # Fetch ALL replies (not filtered by oldest) so each day's messages.md
    # can be regenerated in full on every run (merge semantics).
    all_replies = fetch_thread_replies(channel, parent_ts, oldest=None)
    all_replies = [r for r in all_replies if r.get("ts") != parent_ts]
    # Exclude the trigger command itself (legacy '!archive' and current '!아카이브')
    all_replies = [r for r in all_replies if (r.get("text") or "").strip() not in ("!archive", "!아카이브")]

    if not all_replies:
        print(f"[nop]  {title}  (tz={trip_tz}, no replies)")
        return {"status": "nop", "title": title, "message": "새 메시지 없음"}

    # Detect "is there anything new since last run?"
    has_new = any(float(r.get("ts", "0")) > float(last_ts) for r in all_replies)
    if not has_new:
        print(f"[nop]  {title}  (tz={trip_tz}, no new replies since last run)")
        return {"status": "nop", "title": title, "message": "마지막 아카이브 이후 새 메시지 없음"}

    # Group ALL replies by trip-local day
    by_day: dict[int, tuple[date, list]] = {}
    for r in all_replies:
        local_dt = slack_ts_to_local(r["ts"], trip_tz)
        d = local_dt.date()
        day_num = (d - trip.start_date).days + 1
        if day_num < 1:
            continue
        bucket = by_day.setdefault(day_num, (d, []))
        bucket[1].append(r)

    if not by_day:
        print(f"[nop]  {title}  (tz={trip_tz}, no in-range replies)")
        return {"status": "nop", "title": title, "message": "출장 시작일 이전 메시지만 있어 스킵"}

    # Only regenerate days that got new content
    affected_days = set()
    for r in all_replies:
        if float(r.get("ts", "0")) <= float(last_ts):
            continue
        local_dt = slack_ts_to_local(r["ts"], trip_tz)
        d = local_dt.date()
        day_num = (d - trip.start_date).days + 1
        if day_num >= 1:
            affected_days.add(day_num)

    if not affected_days:
        print(f"[nop]  {title}  (tz={trip_tz}, new replies all before trip start)")
        return {"status": "nop", "title": title, "message": "새 메시지가 모두 출장 시작일 이전"}

    total_replies = 0
    total_attachments_uploaded = 0
    total_attachments_skipped = 0
    print(f"[proc] {title}  (tz={trip_tz}, regenerating {len(affected_days)} day(s))")

    max_ts = last_ts

    for day_num in sorted(affected_days):
        d, day_replies = by_day[day_num]
        folder_name = day_folder_name(d, day_num)
        trip_folder = f"{DROPBOX_BASE}/{title}"
        day_folder = f"{trip_folder}/{folder_name}"

        md_content = format_messages_md(title, folder_name, day_replies, user_cache, trip_tz)

        # Build unique name for each attachment using stable_attachment_name()
        # so same file on re-run maps to same Dropbox name (idempotent).
        attachments = []
        for r in day_replies:
            for f in (r.get("files") or []):
                url = f.get("url_private")
                if not url:
                    continue
                attachments.append({"url": url, "name": stable_attachment_name(f), "size": f.get("size")})

        total_replies += len(day_replies)

        if dry_run:
            print(f"  [dry] folder: {day_folder}")
            print(f"  [dry] messages.md (overwrite): {len(md_content)} chars, {len(day_replies)} msgs")
            for a in attachments:
                print(f"  [dry] attach (skip-if-exists): {a['name']}")
            continue

        create_folder(day_folder, path_root=TEAM_ROOT)

        # Snapshot existing file names in day folder so we can skip duplicates
        existing = list_folder_file_names(day_folder, path_root=TEAM_ROOT)

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            md_file = tmp / "messages.md"
            md_file.write_text(md_content, encoding="utf-8")
            upload_file(md_file, f"{day_folder}/messages.md", path_root=TEAM_ROOT, mode="overwrite")

            for a in attachments:
                name = a["name"]
                if name in existing:
                    total_attachments_skipped += 1
                    print(f"  [skip] {name} (already uploaded)")
                    continue
                local_path = tmp / name
                try:
                    n = download_file(a["url"], local_path)
                    upload_file(local_path, f"{day_folder}/{name}", path_root=TEAM_ROOT, mode="overwrite")
                    total_attachments_uploaded += 1
                    print(f"  [ok]  {name} ({n} B)")
                except Exception as ex:
                    print(f"  [fail] {name}: {ex}")

        for r in day_replies:
            if float(r["ts"]) > float(max_ts):
                max_ts = r["ts"]

    if not dry_run:
        state["threads"][parent_ts] = {"title": title, "last_archived_ts": max_ts}

    msg = (
        f"{len(affected_days)}개 Day 폴더에 총 {total_replies}개 메시지 재생성 "
        f"(첨부 +{total_attachments_uploaded} 업로드 / {total_attachments_skipped} 스킵)"
    )
    return {
        "status": "ok",
        "title": title,
        "message": msg,
        "days_archived": len(affected_days),
        "replies_archived": total_replies,
        "attachments_uploaded": total_attachments_uploaded,
        "attachments_skipped": total_attachments_skipped,
    }


def archive_parent_by_ts(parent_ts: str, dry_run: bool = False) -> dict:
    """Archive one specific parent thread. Used by the Socket Mode listener."""
    state = load_state()
    channel = get_channel_id()
    res = call("conversations.replies", {"channel": channel, "ts": parent_ts, "limit": 1})
    msgs = res.get("messages", [])
    if not msgs:
        return {"status": "skipped", "title": "", "message": f"parent 메시지 찾을 수 없음: {parent_ts}"}
    parent = msgs[0]
    user_cache: dict = {}
    result = process_parent(parent, channel, state, user_cache, dry_run)
    if not dry_run and result.get("status") == "ok":
        state["last_run"] = datetime.now(timezone.utc).isoformat()
        save_state(state)
    return result


def run(dry_run: bool = False) -> None:
    state = load_state()
    auth = auth_test()
    bot_user_id = auth["user_id"]
    channel = get_channel_id()

    parents = find_parent_messages(channel, bot_user_id)
    print(f"[init] channel={channel}  bot={auth['user']}({bot_user_id})  parents={len(parents)}")

    user_cache: dict = {}
    for p in parents:
        process_parent(p, channel, state, user_cache, dry_run)

    state["last_run"] = datetime.now(timezone.utc).isoformat()
    if not dry_run:
        save_state(state)
        print("[done] state saved")
    else:
        print("[done] dry-run, state NOT saved")


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="Do not write to Dropbox or state file")
    args = ap.parse_args()
    run(dry_run=args.dry_run)
