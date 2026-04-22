"""Seed the #학회강의 channel with one thread-root message per attendee.

Each posted message looks like:
    🎙️ *260501-07, 미국, DDW 2026 @권태빈*

Attendees click into their own thread and press !시작 / !끝 inside it.

Run with:
    python tasks/create_lecture_threads.py \
        --trip "260501-07, 미국, DDW 2026" \
        --attendees "권태빈,최지영"
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from connectors.slack.client import _load_env, get_lecture_channel_id, post_message
from tasks.trip_parser import parse_parent


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--trip", required=True, help='e.g. "260501-07, 미국, DDW 2026"')
    ap.add_argument(
        "--attendees",
        required=True,
        help="Comma-separated Korean names (e.g. '권태빈,최지영')",
    )
    ap.add_argument(
        "--channel",
        default=None,
        help="Override channel ID (default: LECTURE_CHANNEL_ID from .env)",
    )
    args = ap.parse_args()

    _load_env()
    parse_parent(args.trip)  # fail fast on malformed trip title

    channel = args.channel or get_lecture_channel_id()
    attendees = [a.strip() for a in args.attendees.split(",") if a.strip()]
    if not attendees:
        raise SystemExit("No attendees parsed from --attendees")

    results = []
    for attendee in attendees:
        title = f"{args.trip} @{attendee}"
        blocks = [
            {"type": "section", "text": {"type": "mrkdwn", "text": f"🎙️ *{title}*"}},
            {"type": "divider"},
        ]
        res = post_message(channel=channel, text=title, blocks=blocks)
        results.append({"title": title, "ts": res.get("ts")})
        print(f"[ok] {title}  ts={res.get('ts')}")

    print(json.dumps(results, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    main()
