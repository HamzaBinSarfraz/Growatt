#!/usr/bin/env python3
"""Get / set pv_active_p_rate via the legacy ShineServer endpoints.

Endpoints (confirmed by network capture — the same backend the web
portal Setting dialog uses):

    Read:  POST https://server.growatt.com/tcpSet.do
           data = {"action": "readAllMaxParam", "serialNum": <SN>}
           Returns a big JSON with every MAX parameter; the curtailment
           override lives in `msg.activeRate`.

    Set:   POST https://server.growatt.com/tcpSet.do
           data = {"action": "maxSet", "type": "pv_active_p_rate",
                   "serialNum": <SN>, "param1": "<pct>",
                   "param2": "0", "param3": "0"}

Auth: Growatt's WAF blocks the public login endpoint from non-browser
clients (403). Workaround — copy the JSESSIONID cookie from a logged-in
browser session into GROWATT_COOKIE in .env. Cookies expire after some
hours; refresh by re-copying when reads/writes start failing.

Usage:
    python inverter_rate.py get
    python inverter_rate.py set 60

How to grab the cookie:
    1. Open https://server.growatt.com in Chrome and log in.
    2. DevTools (Cmd+Opt+I) → Application → Cookies → server.growatt.com.
    3. Copy the *value* of the JSESSIONID row.
    4. In .env:  GROWATT_COOKIE=JSESSIONID=<that value>
       (or paste the entire `cookie:` header value if you prefer.)

Note on the read response — `activeRate` semantics:
    activeRate is the commanded percentage cap (0..100), matching what
    the dashboard's Set Active Power input shows. Verified by reading
    while the dashboard showed 100% (response: '100') — i.e. this is
    the cloud-side source of truth. Mirror it into local state if you
    need offline access.
"""
import json
import os
import sys

import requests

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

BASE = "https://server.growatt.com"
SN     = os.environ.get("GROWATT_INVERTER_SN", "").strip()
COOKIE = os.environ.get("GROWATT_COOKIE", "").strip()

if not SN:
    sys.exit("GROWATT_INVERTER_SN not set in .env")
if not COOKIE:
    sys.exit("GROWATT_COOKIE not set in .env. See module docstring for how "
             "to grab JSESSIONID from your browser.")

HEADERS = {
    "Cookie":     COOKIE,
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/124.0.0.0 Safari/537.36",
    "X-Requested-With": "XMLHttpRequest",
    "Accept":     "text/plain, */*; q=0.01",
    "Origin":     BASE,
    "Referer":    BASE + "/index",
}


def _post(data: dict) -> dict:
    r = requests.post(BASE + "/tcpSet.do", data=data, headers=HEADERS, timeout=30)
    if r.status_code != 200:
        sys.exit(f"HTTP {r.status_code}: {r.text[:200]}")
    try:
        return r.json()
    except ValueError:
        sys.exit(f"Response was not JSON (cookie may be expired):\n{r.text[:300]}")


def get_active() -> int | None:
    """Return the commanded active-power rate as a percentage (0..100)."""
    body = _post({"action": "readAllMaxParam", "serialNum": SN})
    if not body.get("success"):
        sys.exit(f"Read failed: {body}")
    msg = body.get("msg", {})
    raw = msg.get("activeRate")
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def set_active(percent: int) -> dict:
    if not 0 <= percent <= 100:
        sys.exit("percent must be 0-100")
    body = _post({
        "action":    "maxSet",
        "type":      "pv_active_p_rate",
        "serialNum": SN,
        "param1":    str(percent),
        "param2":    "0",
        "param3":    "0",
    })
    print(json.dumps(body, indent=2))
    return body


def main():
    if len(sys.argv) < 2 or sys.argv[1] not in ("get", "set"):
        sys.exit("usage: inverter_rate.py {get | set <percent>}")
    if sys.argv[1] == "get":
        rate = get_active()
        print(f"pv_active_p_rate = {rate}%")
    else:
        if len(sys.argv) < 3:
            sys.exit("usage: inverter_rate.py set <percent>")
        set_active(int(sys.argv[2]))


if __name__ == "__main__":
    main()
