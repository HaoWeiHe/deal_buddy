"""Log the dedicated 小紅書 account into xiaohongshu-mcp on a headless host.

    python -m dealbuddy.xhs_login [--out data/xhs-login.png]

Saves the login QR code as a PNG, then waits until the account is logged in. Scan it with the
小紅書 app of the dedicated account (copy the file to your computer first, e.g. with scp).
The MCP keeps the cookies in its data volume, so this is only needed again when they expire.
"""
import argparse
import base64
import sys
import time

from .sources import XiaohongshuSource


def save_qr(src: XiaohongshuSource, out: str) -> int:
    """Write the QR PNG to `out`. Returns the MCP's timeout in seconds."""
    data = src._data(src.http.get("/api/v1/login/qrcode"))
    img = data.get("img") or ""
    if "," in img:
        img = img.split(",", 1)[1]
    with open(out, "wb") as f:
        f.write(base64.b64decode(img))
    timeout = data.get("timeout") or 240
    if isinstance(timeout, str):
        digits = "".join(ch for ch in timeout if ch.isdigit())
        timeout = int(digits) if digits else 240
        if timeout > 10000:  # some versions report milliseconds
            timeout //= 1000
    return int(timeout)


def main(argv: list[str] | None = None, *, src: XiaohongshuSource | None = None, poll_seconds: float = 3) -> int:
    parser = argparse.ArgumentParser(description="小紅書小號掃碼登入")
    parser.add_argument("--out", default="data/xhs-login.png")
    args = parser.parse_args(argv)
    src = src or XiaohongshuSource()
    if not src.enabled:
        print("請先設定 XHS_MCP_URL")
        return 2
    if src.logged_in():
        print("已經登入了，不用再掃。")
        return 0
    try:
        timeout = save_qr(src, args.out)
    except Exception as e:
        print(f"連不上 xiaohongshu-mcp（{src.base_url}）：{e}\n先確認 `docker compose ps` 裡它有在跑。")
        return 2
    print(f"QR code 已存到 {args.out}。用小號的小紅書 app 掃描（{timeout} 秒內有效），這裡會等你掃完。")
    deadline = time.time() + timeout
    while time.time() < deadline:
        time.sleep(poll_seconds)
        if src.logged_in():
            print("登入成功！worker 下一輪就會開始抓。")
            return 0
    print("逾時了，再執行一次拿新的 QR code。")
    return 1


if __name__ == "__main__":
    sys.exit(main())
