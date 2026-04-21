from connectors.slack.client import call


def fetch_thread_replies(channel: str, thread_ts: str, oldest: str | None = None) -> list:
    """Return all messages in a thread (including parent).

    Slack returns parent as element [0], replies follow.
    If oldest provided, filters by ts > oldest. (Client-side filter since
    conversations.replies' oldest param is inclusive.)
    """
    out: list = []
    cursor: str | None = None
    while True:
        payload: dict = {"channel": channel, "ts": thread_ts, "limit": 200}
        if cursor:
            payload["cursor"] = cursor
        res = call("conversations.replies", payload)
        out.extend(res.get("messages", []))
        cursor = res.get("response_metadata", {}).get("next_cursor")
        if not cursor:
            break
    if oldest is not None:
        out = [m for m in out if float(m.get("ts", "0")) > float(oldest)]
    return out


def find_parent_messages(channel: str, bot_user_id: str) -> list:
    """Return channel root-level messages posted by the bot."""
    from connectors.slack.client import list_channel_messages
    msgs = list_channel_messages(channel)
    return [m for m in msgs if m.get("user") == bot_user_id and m.get("thread_ts") in (None, m.get("ts"))]
