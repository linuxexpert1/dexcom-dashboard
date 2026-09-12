#!/usr/bin/env python3
"""GitHub Actions poller: fetch recent Dexcom Share readings, merge into a JSON file.

Usage: python scripts/gh_poll.py <path-to-readings.json>

Reads existing readings from the file (if present), pulls the last 24h from
Dexcom Share, merges (dedup on timestamp), keeps the most recent 7 days, and
writes the file back. History grows across runs since Share only returns ~24h.

The output file contains NO personal identifiers -- just timestamps and values.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone

from pydexcom import Dexcom
from pydexcom.const import Region

MAX_POINTS = 7 * 288  # 7 days at one reading / 5 min
THRESHOLDS = {"target_low": 70, "target_high": 180, "urgent_low": 55, "urgent_high": 250}


def main() -> int:
    path = sys.argv[1] if len(sys.argv) > 1 else "readings.json"

    existing: dict[int, dict] = {}
    try:
        with open(path) as fh:
            for r in json.load(fh).get("readings", []):
                existing[int(r["ts"])] = r
    except (FileNotFoundError, ValueError):
        pass

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
    out = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "thresholds": THRESHOLDS,
        "readings": readings,
    }
    with open(path, "w") as fh:
        json.dump(out, fh, separators=(",", ":"))

    newest = readings[-1] if readings else None
    print(f"Fetched {len(fetched)}; total {len(readings)}; newest={newest}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
