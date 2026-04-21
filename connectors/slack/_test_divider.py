import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from connectors.slack.client import get_channel_id, post_message

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

channel = get_channel_id()

blocks = [
    {
        "type": "section",
        "text": {
            "type": "mrkdwn",
            "text": "✈️ *260501-07, 미국, DDW 2026* _(Block Kit divider 테스트)_",
        },
    },
    {"type": "divider"},
]

res = post_message(
    channel=channel,
    text="260501-07, 미국, DDW 2026 (Block Kit divider 테스트)",
    blocks=blocks,
)
print(json.dumps({"ok": res.get("ok"), "ts": res.get("ts"), "channel": res.get("channel")}, ensure_ascii=False, indent=2))
