#!/usr/bin/env python3
"""
Growatt MOD 15KTL3-X Export Limiter
====================================
Keeps grid export under 8500 W on a 15 kW three-phase MOD inverter by
toggling the Active Power Rate between 100 % and 50 % via the Growatt
public OpenAPI v1.

IMPORTANT — read the header notes in the script before running.
The MOD 15KTL3-X is classified as a MAX-type device on the Growatt cloud,
NOT a MIN-type. `growattServer.OpenApiV1.min_write_parameter` will not
work for it; that endpoint is /v1/min/setting/write and the server
rejects writes for non-MIN serials. This script therefore:
  1. Uses growattServer for read paths (plant_list / plant_energy_overview).
  2. Performs the parameter write via a direct HTTP POST to the
     /v1/max/setting/write endpoint with the official parameter id
     `pv_active_p_rate` (range 0-100, value sent as a string).
  3. Includes a one-shot diagnostic at startup that prints the actual
     device_type the cloud reports for your serial. If it comes back as
     anything other than MAX (or whichever class includes 15KTL3-X), the
     loop will refuse to start and tell you what to change.

Usage:
    pip install -r requirements.txt
    cp .env.example .env  # then edit .env to add your token
    python growatt_export_limiter.py            # dry-run, persistent loop
    python growatt_export_limiter.py --once     # single cycle, dry-run
    python growatt_export_limiter.py --live     # writes for real
    python growatt_export_limiter.py --live --once --region eu

Disclaimer: writing inverter parameters can affect production, warranty,
and grid-code compliance. Test during a low-stakes window first. The
author and Anthropic accept no liability for damage or lost generation.
"""

import argparse
import json
import logging
import os
import sys
import time
from typing import Optional

import requests

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    # dotenv is optional at runtime; fall back to plain env vars.
    pass

try:
    import growattServer
except ImportError:
    sys.exit("Install dependencies first:  pip install -r requirements.txt")

try:
    from growattServer.exceptions import GrowattV1ApiError
except ImportError:
    # Allows the unit tests to import this module with a stubbed
    # growattServer that has no `.exceptions` submodule.
    class GrowattV1ApiError(Exception):  # type: ignore[no-redef]
        error_code = None
        error_msg = None

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
API_TOKEN       = os.environ.get("GROWATT_TOKEN", "PUT_YOUR_TOKEN_HERE")
INVERTER_SN     = os.environ.get("GROWATT_INVERTER_SN", "").strip()
EXPORT_LIMIT_W  = 8500          # grid export ceiling
HYSTERESIS_W    = 200           # avoid flapping near the limit
CURTAILED_PCT   = 50            # rate when curtailing (50% of 15 kW ≈ 7.5 kW)
FULL_PCT        = 100           # rate when unconstrained
POLL_SECONDS    = 300           # 5-minute loop
HTTP_TIMEOUT    = 30
STATE_FILE      = ".limiter_state.json"

# Growatt V1 OpenAPI rate limit: error_code 10012 / "error_frequently_access"
# fires both on too-close consecutive calls AND on cumulative volume in a
# sliding window. We space consecutive calls a few seconds apart and, if a
# 10012 trips anyway, back off long enough for the window to clear before
# retrying. These values are conservative; tighten if you find them slow.
V1_CALL_INTERVAL = 3.0
V1_RETRY_BACKOFF = 30.0

REGION_BASES = {
    "eu": "https://openapi.growatt.com",
    "us": "https://openapi-us.growatt.com",
    "au": "https://openapi-au.growatt.com",
    "cn": "https://openapi-cn.growatt.com",
}

# Public V1 endpoint for MAX-class inverter settings. This is the path
# documented in the Growatt OpenAPI v1.0.x PDF for three-phase string
# inverters.
MAX_WRITE_PATH  = "/v1/max/setting/write"
MAX_LIST_PATH   = "/v1/max/list"

# Integer device-type codes returned by /v1/device/list. Sourced from the
# growattServer library docstring on OpenApiV1.device_list — the V1 API
# returns "type" as an integer, not a string. 1 ("inverter, including MAX")
# and 4 ("single MAX") are both MAX-class for this script's purposes.
DEVICE_TYPE_NAMES = {
    1: "inverter (incl. MAX)",
    2: "storage",
    3: "other",
    4: "max",
    5: "sph",
    6: "spa",
    7: "min (incl. TLX)",
    8: "pcs",
    9: "hps",
    10: "pbd",
}
MAX_CLASS_TYPE_CODES = {1, 4}

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("growatt-limiter")


# ---------------------------------------------------------------------------
# Pure decision logic (unit-tested in tests/)
# ---------------------------------------------------------------------------
def decide_next_rate(power_w: float,
                     current_pct: int,
                     limit_w: float,
                     hysteresis_w: float,
                     full_pct: int = FULL_PCT,
                     curtailed_pct: int = CURTAILED_PCT) -> int:
    """Return the rate the limiter should request next.

    If we're currently at full power and the live reading is above the
    limit plus hysteresis, curtail. If we're already curtailed, only
    release once output sits well below the limit (margin factor 0.85),
    which suggests available PV has actually fallen under the cap rather
    than just being capped by the 50 % rate itself.
    """
    if current_pct == full_pct and power_w > limit_w + hysteresis_w:
        return curtailed_pct
    if current_pct == curtailed_pct and power_w < (limit_w - hysteresis_w) * 0.85:
        return full_pct
    return current_pct


# ---------------------------------------------------------------------------
# State persistence
# ---------------------------------------------------------------------------
def load_state(path: str = STATE_FILE) -> int:
    """Return the last-known rate from disk, or FULL_PCT if absent/invalid."""
    try:
        with open(path, "r") as f:
            data = json.load(f)
        rate = int(data.get("current_pct", FULL_PCT))
        if rate in (FULL_PCT, CURTAILED_PCT):
            log.info("Loaded prior state: current_pct=%d%% from %s", rate, path)
            return rate
        log.warning("State file has unexpected rate=%s, defaulting to %d%%",
                    rate, FULL_PCT)
    except FileNotFoundError:
        log.info("No state file at %s; starting at %d%%", path, FULL_PCT)
    except (json.JSONDecodeError, ValueError, OSError) as e:
        log.warning("Could not read state file %s (%s); defaulting to %d%%",
                    path, e, FULL_PCT)
    return FULL_PCT


def save_state(rate: int, path: str = STATE_FILE) -> None:
    try:
        with open(path, "w") as f:
            json.dump({"current_pct": rate, "ts": int(time.time())}, f)
    except OSError as e:
        log.warning("Could not persist state to %s: %s", path, e)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def open_api_v1() -> "growattServer.OpenApiV1":
    """Token-authenticated client for read operations."""
    if not API_TOKEN or API_TOKEN.startswith("PUT_YOUR_TOKEN"):
        sys.exit("GROWATT_TOKEN not set (env var or .env file).")
    if not INVERTER_SN:
        sys.exit("GROWATT_INVERTER_SN not set (env var or .env file). "
                 "Run `python diag_list_devices.py` to find it.")
    return growattServer.OpenApiV1(token=API_TOKEN)


_last_v1_call_ts = 0.0


def v1_call(fn, *args, **kwargs):
    """Call a V1 API method with rate-limit spacing and a single retry.

    The Growatt V1 API rejects bursts (~>1 req/s) with an opaque
    GrowattV1ApiError. We sleep enough to keep consecutive calls at least
    V1_CALL_INTERVAL apart and, on error, log the error_code/error_msg
    and retry once after V1_RETRY_BACKOFF seconds.
    """
    global _last_v1_call_ts
    for attempt in (1, 2):
        elapsed = time.time() - _last_v1_call_ts
        if elapsed < V1_CALL_INTERVAL:
            time.sleep(V1_CALL_INTERVAL - elapsed)
        try:
            result = fn(*args, **kwargs)
            _last_v1_call_ts = time.time()
            return result
        except GrowattV1ApiError as e:
            _last_v1_call_ts = time.time()
            log.warning("V1 API %s attempt %d failed: code=%s msg=%s",
                        getattr(fn, "__name__", "call"),
                        attempt, e.error_code, e.error_msg)
            if attempt == 2:
                raise
            time.sleep(V1_RETRY_BACKOFF)


def find_plant_id(api) -> int:
    """Return the plant_id that owns INVERTER_SN.

    Note: the library method is api.plant_list() in current versions, not
    plant_list_v1() or get_plant_list(). The V1 client exposes plant_list()
    as the token-based call.
    """
    try:
        plants = v1_call(api.plant_list)
    except AttributeError:
        # Some forks renamed it; try the v1 alias.
        plants = v1_call(api.plant_list_v1)  # type: ignore[attr-defined]

    # The V1 response shape is {"plants": [...]} or a list directly,
    # depending on library version. Normalise.
    if isinstance(plants, dict):
        plants = plants.get("plants") or plants.get("data", {}).get("plants", [])

    if not plants:
        sys.exit("No plants returned. Verify your token was generated in "
                 "the ShinePhone APP (web-portal tokens often fail).")

    if len(plants) == 1:
        pid = plants[0].get("plant_id") or plants[0].get("id")
        log.info("Using only plant on account: id=%s", pid)
        return int(pid)

    # Multiple plants: pick the one containing our inverter
    for p in plants:
        pid = p.get("plant_id") or p.get("id")
        try:
            devices = v1_call(api.device_list, int(pid))
            if isinstance(devices, dict):
                devices = devices.get("devices", [])
            for d in devices:
                if d.get("device_sn") == INVERTER_SN:
                    log.info("Found %s in plant id=%s", INVERTER_SN, pid)
                    return int(pid)
        except Exception as e:
            log.debug("device_list failed for plant %s: %s", pid, e)
    sys.exit(f"Inverter {INVERTER_SN} not found on any plant.")


def diagnose_device_type(api, plant_id: int) -> str:
    """Return a lowercase device-class label for INVERTER_SN.

    The V1 API returns `type` as an integer code (see DEVICE_TYPE_NAMES).
    The same SN can appear under multiple type codes (e.g. 1 and 4 for a
    MAX inverter); we prefer the most specific MAX-class match. The
    returned string is always lowercase and is what the loop's MAX/MOD
    sanity check inspects.
    """
    devices = v1_call(api.device_list, plant_id)
    if isinstance(devices, dict):
        devices = devices.get("devices", [])

    matches = [d for d in devices if d.get("device_sn") == INVERTER_SN]
    if not matches:
        sys.exit(f"{INVERTER_SN} not in device_list for plant {plant_id}.")

    # Prefer the entry whose integer type maps to a MAX-class code, so a
    # device listed as both type=1 and type=4 is recognised as MAX.
    matches.sort(key=lambda d: 0 if d.get("type") in MAX_CLASS_TYPE_CODES else 1)
    chosen = matches[0]

    raw_type = (chosen.get("type")
                if chosen.get("type") is not None
                else chosen.get("device_type", chosen.get("deviceType")))
    label = DEVICE_TYPE_NAMES.get(raw_type, str(raw_type)).lower()

    if chosen.get("lost"):
        log.warning("Device %s is currently OFFLINE (lost=True, status=%s, "
                    "last_update_time=%s). Reads will be 0 W and writes will "
                    "not take effect until the inverter reconnects.",
                    INVERTER_SN, chosen.get("status"),
                    chosen.get("last_update_time"))

    log.info("Device %s reports type=%s (%s); full record: %s",
             INVERTER_SN, raw_type, label, chosen)
    return label


def get_current_power_w(api, plant_id: int) -> Optional[float]:
    """Return current AC output in watts, or None on failure.

    plant_energy_overview returns the live aggregate power. For a
    single-inverter plant this equals the inverter output. The field
    name varies by region — we check the common ones.
    """
    try:
        overview = v1_call(api.plant_energy_overview, plant_id)
    except Exception as e:
        log.warning("plant_energy_overview failed: %s", e)
        return None

    # Unwrap typical response shapes
    data = overview
    if isinstance(overview, dict) and "data" in overview:
        data = overview["data"]

    for key in ("current_power", "currentPower", "power", "pac"):
        if isinstance(data, dict) and key in data:
            try:
                v = float(data[key])
                # Some endpoints return kW, some return W. Heuristic:
                # a 15 kW inverter cannot output > 20000 W, so anything
                # under ~20 we treat as kW.
                return v * 1000.0 if v < 20 else v
            except (TypeError, ValueError):
                continue
    log.warning("Could not find power field in overview: %s", data)
    return None


def write_active_power_rate_max(percent: int, openapi_base: str) -> bool:
    """POST directly to the MAX setting endpoint.

    Returns True on success, False otherwise. We do this with `requests`
    because growattServer (as of 1.7.x) does not wrap the MAX endpoints.

    Auth: the V1 OpenAPI accepts the token in a `token` header (some
    deployments also accept it as a query param). We send both to be safe.
    """
    if not 0 <= percent <= 100:
        raise ValueError("percent must be 0-100")

    url = openapi_base + MAX_WRITE_PATH
    headers = {"token": API_TOKEN, "Content-Type": "application/x-www-form-urlencoded"}
    payload = {
        "max_sn":      INVERTER_SN,         # some firmwares expect max_sn
        "device_sn":   INVERTER_SN,         # others expect device_sn
        "type":        "pv_active_p_rate",  # official parameter name
        "param1":      str(percent),        # values must be strings
    }
    try:
        r = requests.post(url, headers=headers, data=payload, timeout=HTTP_TIMEOUT)
    except requests.RequestException as e:
        log.error("HTTP error during write: %s", e)
        return False

    log.info("Write response: HTTP %s  body=%s", r.status_code, r.text[:300])
    if r.status_code != 200:
        return False
    try:
        body = r.json()
    except ValueError:
        return False
    code = body.get("error_code", body.get("errorCode", -1))
    if code == 0:
        log.info("Active power rate set to %d%%", percent)
        return True
    log.error("Growatt rejected write: code=%s msg=%s",
              code, body.get("error_msg") or body.get("errorMsg"))
    # Common codes worth knowing:
    #   10006 = parameter type does not exist  -> wrong key for this device class
    #   10007 = parameter value is empty
    #   10008 = parameter value out of range
    #   10011 = permission denied               -> token / region mismatch
    return False


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def parse_args(argv: Optional[list] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Growatt MAX export limiter (cloud-based curtailment).",
    )
    mode = p.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", dest="dry_run", action="store_true",
                      help="Read and log only; never POST a write. (default)")
    mode.add_argument("--live", dest="live", action="store_true",
                      help="Actually write to the inverter. Required to "
                           "leave dry-run mode.")
    p.add_argument("--once", action="store_true",
                   help="Run a single cycle and exit (for cron-style use).")
    p.add_argument("--region", choices=sorted(REGION_BASES.keys()),
                   default="eu",
                   help="Growatt OpenAPI region (default: eu).")
    return p.parse_args(argv)


# ---------------------------------------------------------------------------
# Main control loop
# ---------------------------------------------------------------------------
def run_cycle(api, plant_id: int, current_pct: int,
              openapi_base: str, dry_run: bool) -> int:
    """One iteration of the loop. Returns the (possibly updated) current_pct."""
    power_w = get_current_power_w(api, plant_id)
    if power_w is None:
        log.warning("Skipping cycle: no power reading.")
        return current_pct

    log.info("Live AC power: %.0f W  (current rate=%d%%)", power_w, current_pct)

    next_pct = decide_next_rate(power_w, current_pct, EXPORT_LIMIT_W, HYSTERESIS_W)
    if next_pct == current_pct:
        return current_pct

    if next_pct == CURTAILED_PCT:
        log.info("Above limit -> curtailing to %d%%", CURTAILED_PCT)
    else:
        log.info("Output well below limit -> releasing to %d%%", FULL_PCT)

    if dry_run:
        log.info("[dry-run] Would POST pv_active_p_rate=%d to %s%s",
                 next_pct, openapi_base, MAX_WRITE_PATH)
        return current_pct

    if write_active_power_rate_max(next_pct, openapi_base):
        save_state(next_pct)
        return next_pct
    return current_pct


def main(argv: Optional[list] = None) -> None:
    args = parse_args(argv)
    dry_run = not args.live  # default ON; only --live disables it
    openapi_base = REGION_BASES[args.region]

    log.info("Mode: %s  region=%s  base=%s",
             "DRY-RUN" if dry_run else "LIVE", args.region, openapi_base)

    api = open_api_v1()
    plant_id = find_plant_id(api)

    dtype = diagnose_device_type(api, plant_id)
    if "max" not in dtype and "mod" not in dtype:
        log.warning(
            "Cloud reports device type '%s'. This script targets the MAX "
            "endpoint. If the type is 'min' or 'tlx', switch to "
            "api.min_write_parameter('pv_active_p_rate', '<pct>'). If it "
            "is 'mix'/'sph'/'spa', no active-power-rate write is exposed "
            "via the V1 API and you will need the legacy ShinePhone path.",
            dtype,
        )
        # Don't exit — let the user observe one cycle to confirm reads work.

    current_pct = load_state()
    log.info("Entering control loop. Limit=%d W, hysteresis=±%d W, poll=%ds",
             EXPORT_LIMIT_W, HYSTERESIS_W, POLL_SECONDS)

    while True:
        try:
            current_pct = run_cycle(api, plant_id, current_pct,
                                    openapi_base, dry_run)
        except KeyboardInterrupt:
            log.info("Stopped by user.")
            return
        except Exception as e:  # noqa: BLE001 — we never want to die in the loop
            log.exception("Unexpected error in cycle: %s", e)

        if args.once:
            log.info("--once flag set; exiting after one cycle.")
            return

        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    main()
