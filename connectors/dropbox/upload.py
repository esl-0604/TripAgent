import json
import urllib.error
import urllib.request
from pathlib import Path

from connectors.dropbox.client import _invalidate_token, get_team_member_id, get_token, rpc

CONTENT_BASE = "https://content.dropboxapi.com/2"
MAX_SINGLE_UPLOAD = 140 * 1024 * 1024  # 140 MB — keep under Dropbox's 150 MB single-shot limit


def _escape_non_ascii_for_header(s: str) -> str:
    """Dropbox-API-Arg header requires ASCII. Escape non-ASCII as \\uXXXX."""
    out = []
    for c in s:
        o = ord(c)
        if o < 128:
            out.append(c)
        else:
            out.append(f"\\u{o:04x}")
    return "".join(out)


def _api_arg_header(args: dict) -> str:
    return _escape_non_ascii_for_header(json.dumps(args, ensure_ascii=False))


def create_folder(path: str, path_root: dict | None = None, autorename: bool = False) -> dict:
    try:
        return rpc(
            "files/create_folder_v2",
            {"path": path, "autorename": autorename},
            path_root=path_root,
        )
    except RuntimeError as e:
        # already-exists is fine
        if "path/conflict/folder" in str(e) or "path_conflict" in str(e):
            return {"metadata": {"path_display": path, "existed": True}}
        raise


def folder_exists(path: str, path_root: dict | None = None) -> bool:
    try:
        rpc("files/get_metadata", {"path": path}, path_root=path_root)
        return True
    except RuntimeError as e:
        if "not_found" in str(e):
            return False
        raise


def list_folder_file_names(path: str, path_root: dict | None = None) -> set[str]:
    """Return set of file names (case-preserved) directly inside a folder.
    Returns empty set if folder doesn't exist.
    """
    names: set[str] = set()
    try:
        res = rpc("files/list_folder", {"path": path, "limit": 2000}, path_root=path_root)
    except RuntimeError as e:
        if "not_found" in str(e):
            return names
        raise
    for e in res.get("entries", []):
        if e.get(".tag") == "file":
            names.add(e.get("name", ""))
    # paginate if needed
    while res.get("has_more"):
        cursor = res.get("cursor")
        res = rpc("files/list_folder/continue", {"cursor": cursor}, path_root=path_root)
        for e in res.get("entries", []):
            if e.get(".tag") == "file":
                names.add(e.get("name", ""))
    return names


def list_folder_subfolders(path: str, path_root: dict | None = None) -> set[str]:
    """Return set of subfolder names directly inside `path`.
    Returns empty set if folder doesn't exist.
    """
    names: set[str] = set()
    try:
        res = rpc("files/list_folder", {"path": path, "limit": 2000}, path_root=path_root)
    except RuntimeError as e:
        if "not_found" in str(e):
            return names
        raise
    for e in res.get("entries", []):
        if e.get(".tag") == "folder":
            names.add(e.get("name", ""))
    while res.get("has_more"):
        cursor = res.get("cursor")
        res = rpc("files/list_folder/continue", {"cursor": cursor}, path_root=path_root)
        for e in res.get("entries", []):
            if e.get(".tag") == "folder":
                names.add(e.get("name", ""))
    return names


def upload_file(
    local_path: Path,
    dropbox_path: str,
    path_root: dict | None = None,
    mode: str = "add",
) -> dict:
    """Upload a local file to Dropbox (single-shot, files up to ~140MB).

    mode: 'add' (auto-rename if conflict) or 'overwrite'.
    """
    size = local_path.stat().st_size
    if size > MAX_SINGLE_UPLOAD:
        return _upload_session(local_path, dropbox_path, path_root=path_root, mode=mode)

    args = {"path": dropbox_path, "mode": mode, "autorename": True, "mute": True}
    data = local_path.read_bytes()
    url = f"{CONTENT_BASE}/files/upload"

    member_id = get_team_member_id()
    for attempt in range(2):
        token = get_token()
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/octet-stream",
            "Dropbox-API-Arg": _api_arg_header(args),
        }
        if path_root is not None:
            headers["Dropbox-API-Path-Root"] = json.dumps(path_root)
        if member_id:
            headers["Dropbox-API-Select-User"] = member_id
        req = urllib.request.Request(url, data=data, method="POST", headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=300) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", errors="replace")
            if e.code == 401 and attempt == 0 and "expired_access_token" in detail:
                _invalidate_token()
                continue
            raise RuntimeError(f"Dropbox upload HTTP {e.code}: {detail}") from e
    raise RuntimeError("Dropbox upload failed after refresh retry")


def _upload_session(local_path: Path, dropbox_path: str, path_root: dict | None, mode: str) -> dict:
    """Chunked upload for files > 140 MB."""
    token = get_token()
    chunk_size = 8 * 1024 * 1024
    session_id = None
    offset = 0
    base_headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/octet-stream"}
    if path_root is not None:
        base_headers["Dropbox-API-Path-Root"] = json.dumps(path_root)
    member_id = get_team_member_id()
    if member_id:
        base_headers["Dropbox-API-Select-User"] = member_id

    total = local_path.stat().st_size
    with local_path.open("rb") as f:
        while True:
            chunk = f.read(chunk_size)
            last = not chunk or (offset + len(chunk) >= total)
            if session_id is None:
                # start
                args = {"close": last}
                headers = {**base_headers, "Dropbox-API-Arg": _api_arg_header(args)}
                url = f"{CONTENT_BASE}/files/upload_session/start"
                req = urllib.request.Request(url, data=chunk, method="POST", headers=headers)
                with urllib.request.urlopen(req, timeout=300) as resp:
                    resp_json = json.loads(resp.read().decode("utf-8"))
                session_id = resp_json["session_id"]
                offset += len(chunk)
                if last:
                    break
                continue
            if last:
                args = {
                    "cursor": {"session_id": session_id, "offset": offset},
                    "commit": {"path": dropbox_path, "mode": mode, "autorename": True, "mute": True},
                }
                headers = {**base_headers, "Dropbox-API-Arg": _api_arg_header(args)}
                url = f"{CONTENT_BASE}/files/upload_session/finish"
                req = urllib.request.Request(url, data=chunk, method="POST", headers=headers)
                with urllib.request.urlopen(req, timeout=600) as resp:
                    return json.loads(resp.read().decode("utf-8"))
            else:
                args = {"cursor": {"session_id": session_id, "offset": offset}, "close": False}
                headers = {**base_headers, "Dropbox-API-Arg": _api_arg_header(args)}
                url = f"{CONTENT_BASE}/files/upload_session/append_v2"
                req = urllib.request.Request(url, data=chunk, method="POST", headers=headers)
                with urllib.request.urlopen(req, timeout=300) as resp:
                    resp.read()
                offset += len(chunk)
    return {"path_display": dropbox_path}
