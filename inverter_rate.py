#!/usr/bin/env python3
"""Get / set pv_active_p_rate via the legacy ShineServer API.

This hits server.growatt.com — the same backend the web portal "Read"
and "Yes" buttons use — via session login with username + password.
It is independent of the V1 OpenAPI token used by the limiter.

Usage:
    python inverter_rate.py get
    python inverter_rate.py set 60

Required env (in .env):
    GROWATT_USERNAME       — your ShinePhone account
    GROWATT_PASSWORD       — its password
    GROWATT_INVERTER_SN    — the inverter device_sn
"""
import os
import sys

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

import growattServer

USERNAME = os.environ.get("GROWATT_USERNAME", "").strip()
PASSWORD = os.environ.get("GROWATT_PASSWORD", "").strip()
SN       = os.environ.get("GROWATT_INVERTER_SN", "").strip()

if not USERNAME or not PASSWORD or not SN:
    sys.exit("Set GROWATT_USERNAME, GROWATT_PASSWORD, GROWATT_INVERTER_SN in .env")


def login():
    api = growattServer.GrowattApi(add_random_user_id=True)
    res = api.login(USERNAME, PASSWORD)
    if not res or not res.get("success"):
        sys.exit(f"Login failed: {res}")
    return api


def get_rate(api):
    """Try to read the current pv_active_p_rate.

    The legacy API has no documented helper for MAX-class settings reads.
    inverter_detail returns live data only (output power etc.), not the
    commanded rate. The web UI's Read button POSTs to /newTcpsetAPI.do
    with op='spaGetApi' (mirror of spaSetApi used for writes). We call
    it directly via the same session.
    """
    r = api.session.post(
        api.get_url("newTcpsetAPI.do"),
        params={
            "op": "spaGetApi",
            "serialNum": SN,
            "type": "pv_active_p_rate",
        },
        timeout=30,
    )
    print(f"HTTP {r.status_code}  CT={r.headers.get('Content-Type')}")
    print("BODY:", r.text[:600])
    try:
        return r.json()
    except ValueError:
        return None


def set_rate(api, percent):
    if not 0 <= percent <= 100:
        sys.exit("percent must be 0-100")
    res = api.update_ac_inverter_setting(SN, "pv_active_p_rate", [str(percent)])
    print(res)
    return res


def main():
    if len(sys.argv) < 2 or sys.argv[1] not in ("get", "set"):
        sys.exit("usage: inverter_rate.py {get | set <percent>}")
    api = login()
    if sys.argv[1] == "get":
        get_rate(api)
    else:
        if len(sys.argv) < 3:
            sys.exit("usage: inverter_rate.py set <percent>")
        set_rate(api, int(sys.argv[2]))


if __name__ == "__main__":
    main()
