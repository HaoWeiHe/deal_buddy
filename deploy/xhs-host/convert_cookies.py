#!/usr/bin/env python3
"""Turn cookies exported from a normal browser into the cookies.json that xiaohongshu-mcp loads.

Use it when the MCP's own login tool can't finish (小紅書 risk control often rejects its browser):
log in to xiaohongshu.com (or rednote.com, for an account made with a non-Chinese phone number) with the
dedicated account in your everyday Chrome, export the cookies with
the Cookie-Editor extension (Export -> JSON), then:

    python3 convert_cookies.py exported.json > cookies.json
    scp cookies.json root@<host>:deal_buddy/deploy/xhs-host/data/cookies.json

Needs only the Python standard library, so it runs on a Mac as is.
"""
import json
import sys
import time

DOMAINS = ("xiaohongshu.com", "rednote.com")
SAME_SITE = {"no_restriction": "None", "none": "None", "lax": "Lax", "strict": "Strict"}


def convert(exported: list[dict]) -> list[dict]:
    """Cookie-Editor / EditThisCookie entries -> Chrome DevTools Network.Cookie objects."""
    out = []
    for c in exported:
        domain = c.get("domain") or ""
        if not any(d in domain for d in DOMAINS):
            continue
        expires = c.get("expirationDate", c.get("expires"))
        if bool(c.get("session")) or expires in (None, -1):
            # Browser-session cookies would vanish when the MCP's browser restarts; keep them for 30 days.
            expires = time.time() + 30 * 86400
        same_site = SAME_SITE.get(str(c.get("sameSite") or "").lower())
        cookie = {
            "name": c["name"],
            "value": c.get("value", ""),
            "domain": domain,
            "path": c.get("path") or "/",
            "expires": float(expires),
            "size": len(c["name"]) + len(c.get("value", "")),
            "httpOnly": bool(c.get("httpOnly")),
            "secure": bool(c.get("secure")),
            "session": False,
            "priority": "Medium",
            "sameParty": False,
            "sourceScheme": "Secure",
            "sourcePort": 443,
        }
        if same_site:
            cookie["sameSite"] = same_site
        out.append(cookie)
    return out


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(__doc__, file=sys.stderr)
        return 2
    with open(argv[1], encoding="utf-8") as f:
        exported = json.load(f)
    if isinstance(exported, dict):  # some exporters wrap the list
        exported = exported.get("cookies") or []
    cookies = convert(exported)
    if not any(c["name"] == "web_session" for c in cookies):
        print("注意：沒看到 web_session cookie，匯出時小號可能還沒登入。", file=sys.stderr)
    if not cookies:
        print("沒有任何 xiaohongshu.com / rednote.com 的 cookie，請確認是在小紅書網頁上匯出的。", file=sys.stderr)
        return 1
    if any("rednote.com" in c["domain"] for c in cookies):
        print("這是 rednote.com 的 cookie：主機的 .env 要加 COMPOSE_FILE=docker-compose.yml:docker-compose.rednote.yml。",
              file=sys.stderr)
    json.dump(cookies, sys.stdout, ensure_ascii=False, indent=2)
    print(f"轉好了 {len(cookies)} 個 cookie。", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
