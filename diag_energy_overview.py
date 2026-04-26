#!/usr/bin/env python3
"""Diagnostic: dump every field returned by plant_energy_overview.

Useful for finding the right power signal to drive dynamic control. The
top-level fields vary by region/firmware — this prints the flattened
key-value pairs so you can see which one tracks live AC output.

    python diag_energy_overview.py
"""
import os
import sys

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


def flatten(d, parent=""):
    items = []
    if isinstance(d, dict):
        for k, v in d.items():
            new_key = f"{parent}.{k}" if parent else k
            items.extend(flatten(v, new_key))
    elif isinstance(d, list):
        for i, v in enumerate(d):
            new_key = f"{parent}[{i}]"
            items.extend(flatten(v, new_key))
    else:
        items.append((parent, d))
    return items


plants = api.plant_list()
if isinstance(plants, dict):
    plants = plants.get("plants") or plants.get("data", [])
if not plants:
    sys.exit("No plant data")

plant_id = plants[0].get("plant_id") or plants[0].get("id")
print(f"Plant ID: {plant_id}")

res = api.plant_energy_overview(int(plant_id))
data = res.get("data", res) if isinstance(res, dict) else res
flat_data = flatten(data)

power = None
for k, v in flat_data:
    if k.lower().endswith(("current_power", "currentpower", "pac", "power")):
        try:
            power = float(v)
            break
        except (TypeError, ValueError):
            pass
if power is not None and power < 20:
    power *= 1000.0

print("\n================= GROWATT DATA TABLE =================\n")
print(f"{'KEY':60} | VALUE")
print("-" * 90)
for k, v in flat_data:
    print(f"{k:60} | {v}")

print("\n================= SUMMARY =================")
print(f"Power (W): {int(power) if power is not None else 'N/A'}")
