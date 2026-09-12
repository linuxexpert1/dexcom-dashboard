#!/usr/bin/env python3
"""GitHub Actions poller: fetch recent Dexcom Share readings, merge, and publish ENCRYPTED.

Usage: python scripts/gh_poll.py <path-to-readings.enc.json>

Reads existing (encrypted) readings from the file if present, pulls the last 24h
from Dexcom Share, merges (dedup on timestamp), keeps the most recent 7 days, and
writes the file back as an AES-256-GCM encrypted blob. The key is derived from
DATA_PASSPHRASE via PBKDF2-HMAC-SHA256 -- the same scheme the browser uses to
decrypt (WebCrypto). Plaintext readings never leave the runner, so the file
published to the public `data` branch is unreadable without the passphrase.

Output JSON: {"v":1,"iter":<n>,"salt":b64,"iv":b64,"ct":b64}  (ct = ciphertext||tag)
"""
from __future__ import annotations

import base64
import json
import os
import sys
from datetime import datetime, timezone

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from pydexcom import Dexcom
from pydexcom.const import Region

MAX_POINTS = 7 * 288  # 7 days at one reading / 5 min
PBKDF2_ITER = 200_000
THRESHOLDS = {"target_low": 70, "target_high": 180, "urgent_low": 55, "urgent_high": 250}


def _b64(b: bytes) -> str:
    return base64.b64encode(b).decode()


def _derive_key(passphrase: str, salt: bytes) -> bytes:
    kdf = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=salt, iterations=PBKDF2_ITER)
    return kdf.derive(passphrase.encode("utf-8"))


def encrypt(passphrase: str, plaintext: bytes) -> dict:
    salt, iv = os.urandom(16), os.urandom(12)
    key = _derive_key(passphrase, salt)
    ct = AESGCM(key).encrypt(iv, plaintext, None)
    return {"v": 1, "iter": PBKDF2_ITER, "salt": _b64(salt), "iv": _b64(iv), "ct": _b64(ct)}


def decrypt(passphrase: str, blob: dict) -> bytes:
    salt = base64.b64decode(blob["salt"])
    iv = base64.b64decode(blob["iv"])
    ct = base64.b64decode(blob["ct"])
    key = _derive_key(passphrase, salt)
    return AESGCM(key).decrypt(iv, ct, None)


def main() -> int:
    path = sys.argv[1] if len(sys.argv) > 1 else "readings.enc.json"
    passphrase = os.environ.get("DATA_PASSPHRASE")
    if not passphrase:
        print("ERROR: DATA_PASSPHRASE is not set", file=sys.stderr)
        return 2

    # Load and decrypt any existing history so it accumulates beyond Dexcom's 24h window.
    existing: dict[int, dict] = {}
    try:
        with open(path) as fh:
            blob = json.load(fh)
        if blob.get("readings") is not None:  # legacy plaintext file
            prior = blob["readings"]
        else:
            prior = json.loads(decrypt(passphrase, blob).decode("utf-8")).get("readings", [])
        for r in prior:
            existing[int(r["ts"])] = r
    except FileNotFoundError:
        pass
    except Exception as exc:  # noqa: BLE001 - bad/foreign existing file: start fresh rather than fail
        print(f"WARN: could not read existing data ({exc}); starting fresh", file=sys.stderr)

    region = Region(os.environ.get("DEXCOM_REGION", "us"))
    kwargs = {"password": os.environ["DEXCOM_PASSWORD"], "region": region}
    if os.environ.get("DEXCOM_ACCOUNT_ID"):
        kwargs["account_id"] = os.environ["DEXCOM_ACCOUNT_ID"]
    else:
        kwargs["username"] = os.environ["DEXCOM_USERNAME"]

    dex = Dexcom(**kwargs)
    fetched = dex.get_glucose_readings(minutes=1440, max_count=288)
    for r in fetched:
        ts = r.datetime
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        ts = int(ts.timestamp())
        existing[ts] = {
            "ts": ts,
            "mg_dl": int(r.mg_dl),
            "trend": int(r.trend),
            "trend_arrow": r.trend_arrow or "",
            "trend_desc": r.trend_description or "",
        }

    readings = sorted(existing.values(), key=lambda x: x["ts"])[-MAX_POINTS:]
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "thresholds": THRESHOLDS,
        "readings": readings,
    }
    blob = encrypt(passphrase, json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    with open(path, "w") as fh:
        json.dump(blob, fh, separators=(",", ":"))

    newest = readings[-1] if readings else None
    print(f"Fetched {len(fetched)}; total {len(readings)}; newest={newest}; encrypted -> {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
