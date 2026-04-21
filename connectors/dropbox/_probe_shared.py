import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from connectors.dropbox.client import rpc

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


def list_shared_folders():
    out = []
    cursor = None
    while True:
        if cursor is None:
            res = rpc("sharing/list_folders", {"limit": 100})
        else:
            res = rpc("sharing/list_folders/continue", {"cursor": cursor})
        out.extend(res.get("entries", []))
        cursor = res.get("cursor")
        if not cursor:
            break
    return out


folders = list_shared_folders()
print(f"Total shared-folder memberships: {len(folders)}\n")

rows = []
for f in folders:
    access = f.get("access_type", {}).get(".tag", "?")
    writable = access in ("owner", "editor")
    path_lower = f.get("path_lower") or "(not mounted at a path)"
    rows.append((writable, access, f.get("name", ""), path_lower, f.get("shared_folder_id", "")))

# Sort: writable first, then by path
rows.sort(key=lambda r: (not r[0], r[3]))

print(f"{'W':>1} {'ACCESS':10} {'NAME':40} {'PATH':60}")
print("-" * 120)
for writable, access, name, path, sfid in rows:
    marker = "✓" if writable else "·"
    print(f"{marker:>1} {access:10} {name[:40]:40} {path[:60]:60}")
