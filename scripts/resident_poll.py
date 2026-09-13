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


# ---- alerting (email / carrier email-to-SMS) ----------------------------
import smtplib
import ssl
from email.message import EmailMessage

ALERT_STATE_FILE = REPO_DIR.parent / "alert_state.json"


def _alert_cfg() -> dict:
    return {
        "high": int(os.environ.get("ALERT_HIGH", "255")),
        "repeat": int(os.environ.get("ALERT_REPEAT_MINUTES", "30")) * 60,
        "smtp_host": os.environ.get("SMTP_HOST", "smtp.gmail.com"),
        "smtp_port": int(os.environ.get("SMTP_PORT", "587")),
        "smtp_user": os.environ.get("SMTP_USER", ""),
        "smtp_pass": os.environ.get("SMTP_PASS", ""),
        "sender": os.environ.get("ALERT_FROM") or os.environ.get("SMTP_USER", ""),
        "to": [x.strip() for x in os.environ.get("ALERT_TO", "").split(",") if x.strip()],
    }


def _load_alert_state() -> dict:
    try:
        return json.loads(ALERT_STATE_FILE.read_text())
    except Exception:
        return {"high_active": False, "last_sent": 0}


def _save_alert_state(st: dict) -> None:
    try:
        ALERT_STATE_FILE.write_text(json.dumps(st))
    except Exception as exc:  # noqa: BLE001
        log(f"alert state save failed: {exc}")


def send_email(cfg: dict, subject: str, body: str) -> bool:
    if not (cfg["smtp_user"] and cfg["smtp_pass"] and cfg["to"]):
        return False
    msg = EmailMessage()
    msg["From"] = cfg["sender"]
    msg["To"] = ", ".join(cfg["to"])
    msg["Subject"] = subject
    msg.set_content(body)
    ctx = ssl.create_default_context()
    with smtplib.SMTP(cfg["smtp_host"], cfg["smtp_port"], timeout=30) as s:
        s.starttls(context=ctx)
        s.login(cfg["smtp_user"], cfg["smtp_pass"])
        s.send_message(msg)
    return True


def maybe_alert(existing: dict) -> None:
    """Fire on crossing ALERT_HIGH, remind every ALERT_REPEAT_MINUTES while high, all-clear on recovery."""
    cfg = _alert_cfg()
    if not cfg["to"] or not existing:
        return  # alerts not configured, or no data yet
    ts = max(existing)
    r = existing[ts]
    v = r["mg_dl"]
    now = time.time()
    st = _load_alert_state()
    arrow = r.get("trend_arrow", "")
    when = datetime.fromtimestamp(ts, timezone.utc).astimezone().strftime("%-I:%M %p %Z")
    url = "https://linuxexpert1.github.io/dexcom-dashboard/"
    if v >= cfg["high"]:
        if not st.get("high_active"):
            if send_email(cfg, f"Rahim HIGH {v} mg/dL",
                          f"HIGH: Rahim {v} mg/dL {arrow} at {when} (>= {cfg['high']}). {url}"):
                log(f"ALERT sent: high {v}")
                st = {"high_active": True, "last_sent": now}
        elif now - st.get("last_sent", 0) >= cfg["repeat"]:
            if send_email(cfg, f"Rahim STILL HIGH {v} mg/dL",
                          f"STILL HIGH: Rahim {v} mg/dL {arrow} at {when} (>= {cfg['high']}). {url}"):
                log(f"ALERT repeat: high {v}")
                st["last_sent"] = now
    else:
        if st.get("high_active"):
            if send_email(cfg, f"Rahim back below {cfg['high']}",
                          f"Back below {cfg['high']}: Rahim {v} mg/dL {arrow} at {when}. {url}"):
                log(f"ALERT all-clear: {v}")
            st = {"high_active": False, "last_sent": 0}
    _save_alert_state(st)


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
            try:
                maybe_alert(existing)
            except Exception as exc:  # noqa: BLE001 - alerting must never break the poll loop
                log(f"alert check failed: {exc}")
        except Exception as exc:  # noqa: BLE001 - never let the loop die
            log(f"cycle error ({type(exc).__name__}: {exc}); will retry next interval")
            dex = None  # force a fresh login next cycle
        time.sleep(interval)


if __name__ == "__main__":
    sys.exit(main())
