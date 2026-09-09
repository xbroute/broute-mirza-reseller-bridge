#!/usr/bin/env python3
from __future__ import annotations

import re
import sys
import tempfile
import urllib.request
from pathlib import Path

from mirza_patch import PatchError, transform


UA = "broute-bridge-upstream-watch/0.1"


def fetch(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "text/plain"})
    with urllib.request.urlopen(req, timeout=30) as response:  # nosec B310 - fixed HTTPS GitHub hosts below
        return response.read().decode("utf-8")


def require(text: str, patterns: list[str], label: str) -> None:
    missing = [pattern for pattern in patterns if re.search(pattern, text, re.MULTILINE) is None]
    if missing:
        raise RuntimeError(f"{label}: missing contract pattern(s): {missing}")


def check_mirza() -> None:
    base = "https://raw.githubusercontent.com/mahdiMGF2/mirzabot/main/"
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        for name in ("request.php", "mirza_agent.php", "panels.php"):
            (root / name).write_text(fetch(base + name), encoding="utf-8")
        try:
            result = transform(root)
        except PatchError as exc:
            raise RuntimeError(f"MirzaBot no longer matches the safe patch contract: {exc}") from exc
        changed = [p.name for p, (_, did) in result.items() if did]
        print(f"MirzaBot: compatible ({'patch required: ' + ', '.join(changed) if changed else 'already compatible'})")


def check_reseller() -> None:
    base = "https://raw.githubusercontent.com/AMasoudKaveh/x-ui-reseller-panel/main/backend/"
    api = fetch(base + "api_v1.py")
    users = fetch(base + "reseller_users.py")
    actions = fetch(base + "reseller_user_actions.py")
    create = fetch(base + "reseller_create_user.py")

    require(api, [
        r'prefix\s*=\s*["\']?/api/v1',
        r'@router\.get\(["\']/users["\']\)',
        r'@router\.post\(["\']/users["\']\)',
        r'@router\.get\(["\']/users/\{client_id\}["\']\)',
        r'@router\.patch\(["\']/users/\{client_id\}["\']\)',
        r'/reset-usage',
        r'/revoke-subscription',
        r'/access',
    ], "x-ui-reseller-panel api_v1.py")
    require(users, [r'"traffic_limit_bytes"', r'"used_bytes"', r'"expire_at_ms"', r'"status_code"'], "x-ui-reseller-panel reseller_users.py")
    require(actions, [r'def\s+user_details\s*\(', r'def\s+reset_usage\s*\(', r'def\s+revoke_subscription\s*\('], "x-ui-reseller-panel reseller_user_actions.py")
    require(create, [r'class\s+CreateUserBody', r'traffic_gb', r'expiry_date', r'inbound_ids'], "x-ui-reseller-panel reseller_create_user.py")
    print("x-ui-reseller-panel: compatible")


def check_3xui() -> None:
    url = "https://raw.githubusercontent.com/MHSanaei/3x-ui/main/internal/web/controller/client.go"
    client = fetch(url)
    require(client, [
        r'g\.GET\("/get/:email"',
        r'g\.GET\("/links/:email"',
        r'g\.POST\("/add"',
        r'g\.POST\("/update/:email"',
        r'g\.POST\("/del/:email"',
        r'g\.POST\("/:email/attach"',
        r'g\.POST\("/:email/detach"',
        r'g\.POST\("/resetTraffic/:email"',
        r'"inboundIds"',
        r'"usedTraffic"',
    ], "3x-ui client controller")
    print("3x-ui: compatible")


def main() -> int:
    try:
        check_mirza()
        check_reseller()
        check_3xui()
    except Exception as exc:
        print(f"UPSTREAM COMPATIBILITY FAILURE: {exc}", file=sys.stderr)
        return 1
    print("All upstream contracts are compatible.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
