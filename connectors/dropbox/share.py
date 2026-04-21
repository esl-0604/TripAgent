"""Dropbox sharing helpers — generate folder links that open in the
native Dropbox mobile app (via Universal Links).
"""
from connectors.dropbox.client import rpc


def get_or_create_folder_link(
    path: str,
    path_root: dict | None = None,
    audience: str = "team",
) -> str:
    """Return a shared link URL for a Dropbox folder.

    If no link exists: creates one (viewer access, team-only visibility).
    If one already exists: reuses the existing URL (idempotent).

    On mobile with Dropbox app installed, tapping this URL opens the native
    app navigated to the folder — enabling true background uploads.
    """
    settings = {"access": "viewer", "audience": audience, "allow_download": True}
    try:
        res = rpc(
            "sharing/create_shared_link_with_settings",
            {"path": path, "settings": settings},
            path_root=path_root,
        )
        return res.get("url", "")
    except RuntimeError as e:
        if "shared_link_already_exists" not in str(e):
            raise
        res = rpc(
            "sharing/list_shared_links",
            {"path": path, "direct_only": True},
            path_root=path_root,
        )
        links = res.get("links") or []
        if not links:
            raise RuntimeError(f"shared_link_already_exists but list empty for {path}")
        return links[0].get("url", "")
