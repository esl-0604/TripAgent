import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from connectors.dropbox.client import rpc

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


def list_root():
    entries = []
    cursor = None
    while True:
        if cursor is None:
            res = rpc(
                "files/list_folder",
                {
                    "path": "",
                    "recursive": False,
                    "limit": 500,
                    "include_has_explicit_shared_members": True,
                },
            )
        else:
            res = rpc("files/list_folder/continue", {"cursor": cursor})
        entries.extend(res.get("entries", []))
        if not res.get("has_more"):
            break
        cursor = res.get("cursor")
    return entries


def list_shared_folders():
    """Returns dict: shared_folder_id -> metadata"""
    result = {}
    cursor = None
    while True:
        if cursor is None:
            res = rpc("sharing/list_folders", {"limit": 100})
        else:
            res = rpc("sharing/list_folders/continue", {"cursor": cursor})
        for f in res.get("entries", []):
            result[f["shared_folder_id"]] = f
        cursor = res.get("cursor")
        if not cursor:
            break
    return result


entries = list_root()
shared_meta = list_shared_folders()

print(f"=== Root entries: {len(entries)}  |  Shared folder memberships: {len(shared_meta)} ===\n")

team_shared = []
personal = []
files_list = []

for e in entries:
    if e[".tag"] == "file":
        files_list.append(e)
        continue
    si = e.get("sharing_info") or {}
    sf_id = si.get("shared_folder_id")
    meta = shared_meta.get(sf_id) if sf_id else None
    if sf_id:
        team_shared.append((e, meta))
    else:
        personal.append(e)


def fmt_policy(meta):
    if not meta:
        return "(shared, meta n/a)"
    policy = meta.get("policy", {})
    member_policy = policy.get("member_policy", {}).get(".tag", "?")
    acl_policy = policy.get("acl_update_policy", {}).get(".tag", "?")
    access = meta.get("access_type", {}).get(".tag", "?")
    return f"access={access}  member_policy={member_policy}  acl={acl_policy}"


print(f"[TEAM-SHARED FOLDERS]  {len(team_shared)}")
print("-" * 70)
for e, meta in team_shared:
    print(f"  {e['path_display']}")
    print(f"    {fmt_policy(meta)}")
print()

print(f"[PERSONAL / NOT-SHARED FOLDERS]  {len(personal)}")
print("-" * 70)
for e in personal:
    print(f"  {e['path_display']}")
print()

print(f"[FILES AT ROOT]  {len(files_list)}")
print("-" * 70)
for e in files_list:
    print(f"  {e['path_display']}")
