# Growatt Export Limiter

Cloud-based curtailment for a Growatt **MOD 15KTL3-X** (or other MAX-class) inverter.
Polls the Growatt OpenAPI v1, and when live AC power exceeds the configured grid-export
ceiling (default 8500 W), drops the inverter's Active Power Rate from 100 % to 50 %.
When output drops back below the limit (with hysteresis), it restores 100 %.

The token and the inverter serial number are read from environment variables
(`GROWATT_TOKEN`, `GROWATT_INVERTER_SN`) — typically loaded from a local `.env`
file via [python-dotenv](https://pypi.org/project/python-dotenv/).

---

## Quick start (step by step)

### 1. Clone the repo

```sh
git clone https://github.com/HamzaBinSarfraz/Growatt.git
cd Growatt
```

### 2. Create a virtual environment

```sh
python3 -m venv .venv
source .venv/bin/activate
```

(On Windows: `.venv\Scripts\activate`.)

### 3. Install dependencies

```sh
pip install -r requirements.txt
```

### 4. Get a Growatt OpenAPI token

The token **must** come from the **ShinePhone mobile app** — web-portal tokens are
often rejected by the V1 endpoints.

1. Open ShinePhone → Me → Settings → API token (menu varies by version).
2. Generate / copy the token.

### 5. Configure `.env`

Copy the template:

```sh
cp .env.example .env
```

Then edit `.env` and fill in both values:

```
GROWATT_TOKEN=<your token from step 4>
GROWATT_INVERTER_SN=<your inverter's device_sn>
```

`.env` is gitignored, so neither value will be committed.

If you don't know your inverter's serial, run the diagnostic — it dumps every
plant and device on your account so you can copy the right SN:

```sh
python diag_list_devices.py
```

### 6. Dry-run a single cycle

This reads from the cloud and **logs** what it would write — but never actually
writes. Use this to confirm everything is wired up.

```sh
python growatt_export_limiter.py --once
```

You should see:

- `Mode: DRY-RUN`
- `Using only plant on account: id=...`
- `Device <SN> reports type=... (max ...)` — type must be MAX-class (codes 1 or 4).
- `Live AC power: N W` — the current reading. Will be 0 W at night or if the
  inverter is offline.

### 7. Dry-run the persistent loop

Same as above but loops every 5 minutes. Stop with `Ctrl-C`.

```sh
python growatt_export_limiter.py
```

### 8. First live write (when inverter is online and producing)

Only do this when you've seen a non-zero `Live AC power` in dry-run. Single live
cycle:

```sh
python growatt_export_limiter.py --live --once
```

A successful write logs `Active power rate set to 50%` (or `100%`) and saves the
new rate to `.limiter_state.json`.

### 9. Run continuously

Two options:

**Persistent loop:**

```sh
python growatt_export_limiter.py --live
```

**Cron (recommended for production):**

```cron
*/5 * * * * cd /path/to/Growatt && .venv/bin/python growatt_export_limiter.py --live --once >> limiter.log 2>&1
```

Cron uses fewer API calls than the persistent loop and survives reboots without a
supervisor. State is persisted to `.limiter_state.json` between runs.

---

## CLI flags

| Flag | Purpose |
|------|---------|
| *(none)* | Dry-run, persistent 5-minute loop. Default. |
| `--once` | Run a single cycle and exit (cron-friendly). |
| `--live` | Actually issue writes. Required to leave dry-run. |
| `--region {eu,us,au,cn}` | Pick the OpenAPI host. Default `eu`. |

Combine freely — e.g. `--live --once --region us`.

---

## Device-type warning

This script targets **MAX-class** inverters and writes to `/v1/max/setting/write`
with parameter `pv_active_p_rate`. The MOD 15KTL3-X is reported by the Growatt cloud
as MAX-class — but **verify yours first**. On startup the script logs the device
type the cloud reports for your serial; if it comes back as `min` / `tlx` / `mix` /
`sph` / `spa`, the MAX endpoint will not work and you'll need a different write path.
Don't ignore that diagnostic line.

## Cloud latency caveat

This is a **soft** export limiter. The Growatt OpenAPI has multi-minute end-to-end
latency (poll period + cloud propagation + inverter ack), so this script is **not**
a substitute for a hard regulatory export limit. If your DNO/grid operator requires
a strict export cap, use the inverter's **built-in export limitation** with a
**Growatt smart meter** wired into the RS485 port. That runs locally on the inverter
and reacts in seconds, not minutes.

Use this script for opportunistic curtailment (e.g. shaving export to stay below a
billing threshold) where slow correction is acceptable.

## Rate limits

Growatt's V1 OpenAPI returns `error_code 10012` (`error_frequently_access`) if you
call too often. The script spaces consecutive calls 3 s apart and backs off 30 s on
error. If you trip this manually (e.g. running the script several times in quick
succession), wait ~60 s before retrying.

## Tests

```sh
pip install pytest
pytest tests/ -v
```

Tests cover the pure decision branch (`decide_next_rate`) — when to curtail and when
to release. Network code is intentionally not tested.

## Files

- `growatt_export_limiter.py` — main script
- `diag_list_devices.py` — diagnostic dump of plants/devices
- `requirements.txt` — pinned deps
- `.env.example` — template for `GROWATT_TOKEN`
- `.limiter_state.json` — persisted current rate (gitignored)
- `tests/` — pytest tests for the decision logic

## Troubleshooting

| Symptom | Likely cause / fix |
|---------|--------------------|
| `GROWATT_INVERTER_SN not set` | Add it to `.env`. |
| `<SN> not in device_list for plant <id>` | Wrong `GROWATT_INVERTER_SN`. Run `diag_list_devices.py` to see the actual SN. |
| `code=10012 msg=error_frequently_access` | Rate-limited. Wait 60 s. |
| `code=10003` / `permission denied` | Token invalid or wrong scope. Regenerate from ShinePhone. |
| `Device ... is currently OFFLINE` | Inverter not reporting. Wait for sunrise / check Wi-Fi. |
| `code=10006` on write | Wrong parameter type for this device class — verify it's MAX-class. |

## Disclaimer

Writing inverter parameters can affect production, warranty, and grid-code
compliance. Test during a low-stakes window first. No liability for damage or
lost generation.
