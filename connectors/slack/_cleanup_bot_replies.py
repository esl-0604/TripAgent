"""Delete all Trip Agent bot-authored thread replies in #출장일지.

Preserves the 4 parent messages (Tokyo Live / CMEF / DDW / ESGE) so the
!아카이브 listener remains functional for future trips.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from connectors.slack.client import auth_test, call, delete_message, get_channel_id


def fetch_thread_all(channel: str, parent_ts: str) -> list:
    msgs: list = []
    cursor = None
    while True:
        payload: dict = {"channel": channel, "ts": parent_ts, "limit": 200}
        if cursor:
            payload["cursor"] = cursor
        res = call("conversations.replies", payload)
        msgs.extend(res.get("messages", []))
        cursor = res.get("response_metadata", {}).get("next_cursor")
        if not cursor:
            break
    return msgs


def fetch_channel_parents(channel: str) -> list:
    parents: list = []
    cursor = None
    while True:
        payload: dict = {"channel": channel, "limit": 200}
        if cursor:
            payload["cursor"] = cursor
        res = call("conversations.history", payload)
        parents.extend(res.get("messages", []))
        cursor = res.get("response_metadata", {}).get("next_cursor")
        if not cursor:
            break
    return parents


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    channel = get_channel_id()
    bot_user_id = auth_test()["user_id"]
    parents = fetch_channel_parents(channel)
    print(f"Found {len(parents)} root-level messages in channel")

    total_deleted = 0
    for p in parents:
        parent_ts = p["ts"]
        if (p.get("reply_count") or 0) == 0:
            continue
        replies = fetch_thread_all(channel, parent_ts)
        bot_replies = [r for r in replies if r.get("ts") != parent_ts and r.get("user") == bot_user_id]
        if not bot_replies:
            continue
        print(f"\nThread parent_ts={parent_ts}  text={(p.get('text') or '')[:40]!r}  bot_replies={len(bot_replies)}")
        for r in bot_replies:
            try:
                delete_message(channel=channel, ts=r["ts"])
                total_deleted += 1
            except Exception as e:
                print(f"  [fail] {r['ts']}: {e}")

    print(f"\nTotal deleted: {total_deleted}")


if __name__ == "__main__":
    main()
