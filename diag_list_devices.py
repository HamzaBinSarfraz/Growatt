#!/usr/bin/env python3
"""Diagnostic: dump everything the Growatt cloud knows about your account.

Run once when device_list isn't returning what you expect. Prints the raw
plants list and, for each plant, the raw device_list response. Use the
output to find the actual SN and which plant owns it.

    python diag_list_devices.py
"""
import json
import os
import sys
import time

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

import growattServer

token = os.environ.get("GROWATT_TOKEN")
if not token:
    sys.exit("GROWATT_TOKEN not set in env or .env")

api = growattServer.OpenApiV1(token=token)

print("=" * 60)
print("PLANT LIST")
print("=" * 60)
plants = api.plant_list()
print(json.dumps(plants, indent=2, default=str))

# Normalise to a list of plant records
if isinstance(plants, dict):
    plants_list = plants.get("plants") or plants.get("data", {}).get("plants", [])
else:
    plants_list = plants

for p in plants_list:
    pid = p.get("plant_id") or p.get("id")
    print()
    print("=" * 60)
    print(f"DEVICE LIST FOR plant_id={pid}  (name={p.get('name')})")
    print("=" * 60)
    try:
        # Sleep a beat between calls to avoid rate limiting.
        time.sleep(2)
        devices = api.device_list(int(pid))
        print(json.dumps(devices, indent=2, default=str))
    except Exception as e:
        print(f"device_list failed: {type(e).__name__}: {e}")
