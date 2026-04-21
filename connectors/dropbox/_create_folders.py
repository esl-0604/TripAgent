import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from connectors.dropbox.client import rpc

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

acc = rpc("users/get_current_account")
root_ns = acc["root_info"]["root_namespace_id"]
team_root = {".tag": "root", "root": root_ns}

PARENT = "/전략기획/400 시장분석/000 학회 자료"

print(f"=== Existing entries under {PARENT} ===")
try:
    res = rpc(
        "files/list_folder",
        {"path": PARENT, "recursive": False, "limit": 500},
        path_root=team_root,
    )
    existing = [e["name"] for e in res.get("entries", []) if e[".tag"] == "folder"]
    for name in sorted(existing):
        print(f"  {name}")
except Exception as e:
    print(f"ERROR listing parent: {e}")
    sys.exit(1)

print()

NEW_FOLDERS = [
    "260501-07, 미국, DDW 2026",
    "260512-17, 이탈리아, ESGE 2026",
]

print("=== Creating folders ===")
for name in NEW_FOLDERS:
    if name in existing:
        print(f"  SKIP (already exists): {name}")
        continue
    target = f"{PARENT}/{name}"
    try:
        res = rpc(
            "files/create_folder_v2",
            {"path": target, "autorename": False},
            path_root=team_root,
        )
        md = res.get("metadata", {})
        print(f"  CREATED: {md.get('path_display')}  id={md.get('id')}")
    except Exception as e:
        print(f"  FAILED: {name} -> {e}")
