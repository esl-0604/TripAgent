import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from connectors.slack.client import (
    call,
    delete_message,
    get_channel_id,
    list_channel_messages,
)

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

channel = get_channel_id()
me = call("auth.test", token_kind="user")
my_user_id = me["user_id"]
print(f"User token belongs to: {me['user']} ({my_user_id})")

msgs = list_channel_messages(channel)
print(f"Total messages in channel: {len(msgs)}\n")

targets = []
keep = []
for m in msgs:
    ts = m.get("ts")
    uid = m.get("user") or m.get("bot_id") or ""
    text_preview = (m.get("text") or "")[:60].replace("\n", " ")
    if m.get("user") == my_user_id:
        targets.append((ts, text_preview))
    else:
        keep.append((ts, uid, text_preview))

print(f"[DELETE TARGETS — owned by {my_user_id}]  {len(targets)}")
for ts, tx in targets:
    print(f"  {ts}  {tx}")

print(f"\n[KEEP]  {len(keep)}")
for ts, uid, tx in keep:
    print(f"  {ts}  user={uid}  {tx}")

if "--dry-run" in sys.argv:
    print("\n(dry-run) no deletions performed")
    sys.exit(0)

print("\n=== Deleting ===")
for ts, _ in targets:
    try:
        res = delete_message(channel=channel, ts=ts, token_kind="user")
        print(f"  deleted {ts}: ok={res.get('ok')}")
    except Exception as e:
        print(f"  FAIL  {ts}: {e}")
