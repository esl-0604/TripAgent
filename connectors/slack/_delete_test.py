import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from connectors.slack.client import delete_message, get_channel_id

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

channel = get_channel_id()
res = delete_message(channel=channel, ts="1776673922.267899")
print({"ok": res.get("ok"), "ts": res.get("ts")})
