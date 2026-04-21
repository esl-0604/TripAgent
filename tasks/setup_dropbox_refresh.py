"""One-time Dropbox OAuth setup to obtain a long-lived refresh_token.

Prereq: DROPBOX_APP_KEY and DROPBOX_APP_SECRET must already be in .env.

Flow:
  1. Prints authorization URL — open in browser
  2. Click "Allow" in Dropbox → page shows an access code
  3. Paste the code back to this script
  4. Script exchanges code for refresh_token
  5. Copy the refresh_token into .env as DROPBOX_REFRESH_TOKEN
"""
import base64
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from connectors.dropbox.client import _load_env


def main() -> None:
    _load_env()
    app_key = os.environ.get("DROPBOX_APP_KEY", "").strip()
    app_secret = os.environ.get("DROPBOX_APP_SECRET", "").strip()
    if not app_key or not app_secret:
        print("ERROR: DROPBOX_APP_KEY and DROPBOX_APP_SECRET must be set in .env first.")
        print("Get them from https://www.dropbox.com/developers/apps → MaestroAgent → Settings")
        sys.exit(1)

    auth_url = (
        "https://www.dropbox.com/oauth2/authorize"
        f"?client_id={urllib.parse.quote(app_key)}"
        "&response_type=code"
        "&token_access_type=offline"
    )
    print("1. Open this URL in your browser:")
    print()
    print(f"   {auth_url}")
    print()
    print("2. Click 'Allow' and Dropbox will show you an authorization code.")
    print("3. Paste the code below and press Enter.")
    print()
    code = input("Authorization code: ").strip()
    if not code:
        print("ERROR: No code entered.")
        sys.exit(1)

    token_url = "https://api.dropboxapi.com/oauth2/token"
    body = urllib.parse.urlencode(
        {"code": code, "grant_type": "authorization_code"}
    ).encode("utf-8")
    basic = base64.b64encode(f"{app_key}:{app_secret}".encode()).decode()
    req = urllib.request.Request(
        token_url,
        data=body,
        headers={
            "Authorization": f"Basic {basic}",
            "Content-Type": "application/x-www-form-urlencoded",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        print(f"ERROR HTTP {e.code}: {e.read().decode('utf-8', errors='replace')}")
        sys.exit(1)

    print()
    print("=" * 70)
    print("SUCCESS. Paste the following line into .env (replacing existing entry):")
    print()
    print(f"DROPBOX_REFRESH_TOKEN={data['refresh_token']}")
    print()
    print("Also set (optional — will be auto-refreshed at runtime):")
    print(f"DROPBOX_ACCESS_TOKEN={data['access_token']}")
    print("=" * 70)


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    main()
