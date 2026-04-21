import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from connectors.dropbox.client import rpc

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


acc = rpc("users/get_current_account")
root_ns = acc["root_info"]["root_namespace_id"]
home_ns = acc["root_info"]["home_namespace_id"]
print(f"root_namespace = {root_ns} (team space)")
print(f"home_namespace = {home_ns} (personal home)")
print()


def list_all(path_root):
    entries = []
    cursor = None
    while True:
        if cursor is None:
            res = rpc(
                "files/list_folder",
                {"path": "", "recursive": False, "limit": 500, "include_mounted_folders": True},
                path_root=path_root,
            )
        else:
            res = rpc("files/list_folder/continue", {"cursor": cursor}, path_root=path_root)
        entries.extend(res.get("entries", []))
        if not res.get("has_more"):
            break
        cursor = res["cursor"]
    return entries


team_root = {".tag": "root", "root": root_ns}
team_entries = list_all(team_root)
print(f"=== TEAM SPACE ROOT (ns={root_ns}): {len(team_entries)} entries ===")
for e in sorted(team_entries, key=lambda x: (x[".tag"] != "folder", x["name"])):
    tag = e[".tag"]
    si = e.get("sharing_info") or {}
    sfid = si.get("shared_folder_id", "")
    marker = "[SHARED]" if sfid else "       "
    print(f"  [{tag:6}] {marker} {e['path_display']}")

print()

home_root = {".tag": "namespace_id", "namespace_id": home_ns}
home_entries = list_all(home_root)
print(f"=== HOME NAMESPACE (ns={home_ns}): {len(home_entries)} entries ===")
for e in sorted(home_entries, key=lambda x: (x[".tag"] != "folder", x["name"])):
    tag = e[".tag"]
    si = e.get("sharing_info") or {}
    sfid = si.get("shared_folder_id", "")
    marker = "[SHARED]" if sfid else "       "
    print(f"  [{tag:6}] {marker} {e['path_display']}")
