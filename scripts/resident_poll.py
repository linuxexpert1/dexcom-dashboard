#!/usr/bin/env python3
"""Resident Dexcom poller for an always-on machine (e.g. a Mac via launchd).

Unlike scripts/gh_poll.py (which logs in fresh every run and is meant for CI),
this process logs into Dexcom Share ONCE and reuses that session, polling every
POLL_SECONDS (default 300s = Dexcom's own update rate). Reusing the session is
what avoids Dexcom's login rate-limiting. Each cycle it merges new readings,
encrypts the rolling window with DATA_PASSPHRASE (same AES-256-GCM scheme the
browser decrypts), and force-pushes readings.enc.json to the repo's `data`
branch via git (using the machine's existing git/gh credentials).

Config comes from <repo>/.env (DEXCOM_USERNAME/PASSWORD/REGION, DATA_PASSPHRASE,
optional POLL_SECONDS). Run it under launchd with KeepAlive so it restarts if it
ever dies. Plaintext readings never leave this machine.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from gh_poll import MAX_POINTS, THRESHOLDS, decrypt, encrypt  # reuse crypto + constants

from pydexcom import Dexcom
from pydexcom.const import Region

REPO_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = REPO_DIR.parent / "dexcom-dashboard-data"  # local clone of the `data` branch
REPO_HTTPS = "https://github.com/linuxexpert1/dexcom-dashboard.git"


def log(msg: str) -> None:
    print(f"{datetime.now(timezone.utc):%FT%TZ} {msg}", flush=True)


def load_env() -> None:
    envf = REPO_DIR / ".env"
    if envf.exists():
        for line in envf.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())


def connect() -> Dexcom:
    region = Region(os.environ.get("DEXCOM_REGION", "us"))
    kwargs = {"password": os.environ["DEXCOM_PASSWORD"], "region": region}
    if os.environ.get("DEXCOM_ACCOUNT_ID"):
        kwargs["account_id"] = os.environ["DEXCOM_ACCOUNT_ID"]
    else:
        kwargs["username"] = os.environ["DEXCOM_USERNAME"]
    dex = Dexcom(**kwargs)
    log("logged in to Dexcom Share")
    return dex


def git(*args: str) -> None:
    subprocess.run(["git", *args], cwd=str(DATA_DIR), check=True,
                   capture_output=True, text=True)


def ensure_data_clone() -> None:
    if (DATA_DIR / ".git").exists():
        return
    log(f"cloning data branch into {DATA_DIR}")
    r = subprocess.run(["git", "clone", "--branch", "data", "--single-branch",
                        REPO_HTTPS, str(DATA_DIR)], capture_output=True, text=True)
    if (DATA_DIR / ".git").exists():
        return
    log(f"clone failed ({r.stderr.strip()}); initializing a fresh data branch")
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    git("init", "-q", "-b", "data")
    git("remote", "add", "origin", REPO_HTTPS)


def load_existing() -> dict[int, dict]:
    f = DATA_DIR / "readings.enc.json"
    if not f.exists():
        return {}
    try:
        blob = json.loads(f.read_text())
        if blob.get("readings") is not None:  # legacy plaintext
            prior = blob["readings"]
        else:
            prior = json.loads(decrypt(os.environ["DATA_PASSPHRASE"], blob).decode()).get("readings", [])
        return {int(r["ts"]): r for r in prior}
    except Exception as exc:  # noqa: BLE001
        log(f"could not read existing data ({exc}); starting fresh")
        return {}


def publish(existing: dict[int, dict]) -> int:
    readings = sorted(existing.values(), key=lambda x: x["ts"])[-MAX_POINTS:]
    payload = {"generated_at": datetime.now(timezone.utc).isoformat(),
               "thresholds": THRESHOLDS, "readings": readings}
    blob = encrypt(os.environ["DATA_PASSPHRASE"], json.dumps(payload, separators=(",", ":")).encode())
    (DATA_DIR / "readings.enc.json").write_text(json.dumps(blob, separators=(",", ":")))
    git("add", "readings.enc.json")
    git("-c", "user.name=dexcom-bot", "-c", "user.email=actions@users.noreply.github.com",
        "commit", "-q", "-m", f"Update readings {datetime.now(timezone.utc):%FT%TZ}")
    git("push", "-f", "-q", "origin", "data")
    return len(readings)


def fetch(dex: Dexcom):
    return dex.get_glucose_readings(minutes=1440, max_count=288)


def main() -> int:
    load_env()
    if not os.environ.get("DATA_PASSPHRASE") or not os.environ.get("DEXCOM_PASSWORD"):
        log("ERROR: .env missing DATA_PASSPHRASE or DEXCOM_PASSWORD")
        return 2
    interval = int(os.environ.get("POLL_SECONDS", "300"))
    ensure_data_clone()
    existing = load_existing()
    log(f"resident poller starting; interval={interval}s; history={len(existing)}")

    dex = None
    while True:
        try:
            if dex is None:
                dex = connect()
            try:
                readings = fetch(dex)
            except Exception as exc:  # session likely expired or a transient hiccup
                log(f"fetch failed ({exc}); re-connecting once")
                time.sleep(10)
                dex = connect()
                readings = fetch(dex)
            for r in readings:
                ts = r.datetime
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=timezone.utc)
                ts = int(ts.timestamp())
                existing[ts] = {"ts": ts, "mg_dl": int(r.mg_dl), "trend": int(r.trend),
                                "trend_arrow": r.trend_arrow or "", "trend_desc": r.trend_description or ""}
            n = publish(existing)
            newest = max(existing) if existing else None
            age = int(time.time() - newest) if newest else None
            log(f"published {n} readings; newest {age}s old")
        except Exception as exc:  # noqa: BLE001 - never let the loop die
            log(f"cycle error ({type(exc).__name__}: {exc}); will retry next interval")
            dex = None  # force a fresh login next cycle
        time.sleep(interval)


if __name__ == "__main__":
    sys.exit(main())
