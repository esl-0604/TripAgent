import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from connectors.dropbox.client import rpc

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

for p in ["/회의록", "/FDA", "/이승연’s files", "/00_선행기술팀_공유용"]:
    print(f"\n=== get_metadata {p} ===")
    try:
        meta = rpc("files/get_metadata", {"path": p, "include_has_explicit_shared_members": True})
        print(json.dumps(meta, ensure_ascii=False, indent=2))
    except Exception as e:
        print(f"ERROR: {e}")
