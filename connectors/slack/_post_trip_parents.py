import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from connectors.slack.client import get_channel_id, post_message

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

TRIPS = [
    "260319-23, 일본, Tokyo Live 2026",
    "260409-12, 중국, CMEF 2026, CACA 2026",
    "260501-07, 미국, DDW 2026",
    "260512-17, 이탈리아, ESGE 2026",
]

channel = get_channel_id()
results = []
for title in TRIPS:
    blocks = [
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"✈️ *{title}*"},
        },
        {"type": "divider"},
    ]
    res = post_message(channel=channel, text=title, blocks=blocks)
    results.append({"title": title, "ts": res.get("ts")})

print(json.dumps(results, ensure_ascii=False, indent=2))
