# Growatt Export Limiter

Cloud-based curtailment for a Growatt **MOD 15KTL3-X** inverter (SN `HBJPE5R038`).
Polls the Growatt OpenAPI v1, and when live AC power exceeds the configured grid-export
ceiling (default 8500 W), drops the inverter's Active Power Rate from 100 % to 50 %.
When output drops back below the limit (with hysteresis), it restores 100 %.

## Device-type warning

This script targets **MAX-class** inverters and writes to `/v1/max/setting/write`
with parameter `pv_active_p_rate`. The MOD 15KTL3-X is reported by the Growatt cloud
as MAX-class — but **verify yours first**. On startup the script logs the device
type the cloud reports for your serial; if it comes back as `min` / `tlx` / `mix` /
`sph` / `spa`, the MAX endpoint will not work and you'll need a different write path.
Don't ignore that diagnostic line.

## Important: cloud latency caveat

This is a **soft** export limiter. The Growatt OpenAPI has multi-minute end-to-end
latency (poll period + cloud propagation + inverter ack), so this script is **not**
a substitute for a hard regulatory export limit. If your DNO/grid operator requires
a strict export cap, use the inverter's **built-in export limitation** with a
**Growatt smart meter** wired into the RS485 port. That runs locally on the inverter
and reacts in seconds, not minutes.

Use this script for opportunistic curtailment (e.g. shaving export to stay below a
billing threshold) where slow correction is acceptable.

## Getting a token

The OpenAPI v1 token must come from the **ShinePhone mobile app**, not the web
portal — web-portal tokens are often rejected by the V1 endpoints.

1. Open ShinePhone → Me → Settings → API token (or similar; menu varies by version).
2. Generate / copy the token.
3. Put it in `.env` (see below) or export `GROWATT_TOKEN` in your shell.

## Install

```sh
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# edit .env and paste your token after GROWATT_TOKEN=
```

## Usage

The script defaults to **dry-run mode** for safety. You must pass `--live` to
actually issue writes to the inverter.

```sh
# Dry-run, persistent loop (default). Logs what it would do, never writes.
python growatt_export_limiter.py

# Single cycle, dry-run — good for cron and for testing reads.
python growatt_export_limiter.py --once

# Single cycle, live — writes for real. Use after dry-run looks correct.
python growatt_export_limiter.py --live --once

# Persistent live loop (the original behaviour).
python growatt_export_limiter.py --live

# Non-EU account: pick your region (eu | us | au | cn).
python growatt_export_limiter.py --live --region us
```

### Cron example

```cron
*/5 * * * * cd /opt/growatt && .venv/bin/python growatt_export_limiter.py --live --once >> limiter.log 2>&1
```

State is persisted to `.limiter_state.json`, so `--once` cron runs remember the
current rate across invocations.

## Tests

```sh
pip install pytest
pytest tests/ -v
```

Tests cover the pure decision branch (`decide_next_rate`) — when to curtail and when
to release. Network code is intentionally not tested.

## Files

- `growatt_export_limiter.py` — main script
- `requirements.txt` — pinned deps
- `.env.example` — template for `GROWATT_TOKEN`
- `.limiter_state.json` — persisted current rate (gitignored)
- `tests/` — pytest tests for the decision logic

## Disclaimer

Writing inverter parameters can affect production, warranty, and grid-code
compliance. Test during a low-stakes window first. No liability for damage or
lost generation.
