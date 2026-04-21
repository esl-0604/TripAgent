"""Dropbox File Request helpers.

File Requests let anyone (even without a Dropbox account) upload files
directly to a specified Dropbox folder via a public URL — ideal for mobile
uploads of videos > 1GB that exceed Slack's upload limit.
"""
from connectors.dropbox.client import rpc


def create_file_request(
    destination: str,
    title: str,
    path_root: dict | None = None,
    deadline_iso: str | None = None,
) -> dict:
    """Create an open file request pointing to a Dropbox folder.

    Args:
        destination: Full Dropbox path to target folder (must exist).
        title: Human-readable title shown on the upload page.
        path_root: Optional Dropbox-API-Path-Root header payload.
        deadline_iso: RFC3339/ISO8601 timestamp. If None, request stays
                      open indefinitely.

    Returns dict with keys: id, url, title, destination, created, is_open, ...
    """
    payload: dict = {
        "title": title,
        "destination": destination,
        "open": True,
    }
    if deadline_iso:
        payload["deadline"] = {
            "deadline": deadline_iso,
            "allow_late_uploads": "one_day",
        }
    return rpc("file_requests/create", payload, path_root=path_root)


def close_file_request(request_id: str, path_root: dict | None = None) -> dict:
    """Close (but don't delete) a file request — stops new uploads."""
    return rpc(
        "file_requests/update",
        {"id": request_id, "open": False},
        path_root=path_root,
    )


def delete_file_request(request_id: str, path_root: dict | None = None) -> dict:
    """Delete a closed file request. Must be closed first."""
    return rpc("file_requests/delete", {"ids": [request_id]}, path_root=path_root)
