#!/usr/bin/env python3
"""Print the current pv_active_p_rate the limiter has commanded.

Reads from .limiter_state.json — the local source of truth, written
after every successful write by growatt_export_limiter.py. The Growatt
V1 OpenAPI does not expose a public read counterpart of /v1/max/setting/write
(the path returns 404), so the local state file is the simplest way to
know what rate is currently in effect.

If the rate has been changed manually via ShinePhone since the limiter's
last write, the state file will be stale.

    python read_active_power_rate.py
"""
import json
import sys
from datetime import datetime

try:
    with open(".limiter_state.json") as f:
        state = json.load(f)
except FileNotFoundError:
    sys.exit("No .limiter_state.json yet. Run growatt_export_limiter.py "
             "at least once with --live so it persists a value, or assume "
             "the inverter is at its default 100%.")

pct = state.get("current_pct")
ts = state.get("ts")
when = datetime.fromtimestamp(ts).isoformat(timespec="seconds") if ts else "unknown"
print(f"pv_active_p_rate = {pct}%  (set at {when})")
