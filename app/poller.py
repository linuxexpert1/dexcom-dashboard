"""Background poller: pulls readings from Dexcom Share (via pydexcom) or generates demo data."""
from __future__ import annotations

import logging
import math
import random
import threading
import time
from datetime import datetime, timedelta, timezone

from .config import Settings
from .store import Store

log = logging.getLogger("dexcom.poller")

# Dexcom trend codes -> arrow / description (mirrors pydexcom's mapping)
TRENDS = {
    0: ("", "none"),
    1: ("↑↑", "rising quickly"),
    2: ("↑", "rising"),
    3: ("↗", "rising slightly"),
    4: ("→", "steady"),
    5: ("↘", "falling slightly"),
    6: ("↓", "falling"),
    7: ("↓↓", "falling quickly"),
    8: ("?", "unable to determine trend"),
    9: ("-", "trend unavailable"),
}


def _trend_from_slope(delta_per_5min: float) -> int:
    if delta_per_5min > 15:
        return 1
    if delta_per_5min > 8:
        return 2
    if delta_per_5min > 3:
        return 3
    if delta_per_5min < -15:
        return 7
    if delta_per_5min < -8:
        return 6
    if delta_per_5min < -3:
        return 5
    return 4


def demo_readings(hours: float = 24 * 7, end: datetime | None = None, seed: int = 42) -> list[dict]:
    """Generate a plausible week of CGM data: baseline + meal bumps + noise, 5-minute cadence."""
    rng = random.Random(seed)
    end = end or datetime.now(timezone.utc)
    end = end.replace(second=0, microsecond=0)
    end -= timedelta(minutes=end.minute % 5)
    n = int(hours * 12)
    rows: list[dict] = []
    prev = None
    for i in range(n, -1, -1):
        ts = end - timedelta(minutes=5 * i)
        local_hour = (ts.hour + ts.minute / 60.0 - 4) % 24  # rough US-Eastern-ish daily rhythm
        base = 120 + 12 * math.sin((local_hour - 6) / 24 * 2 * math.pi)
        meals = 0.0
        for meal_hour, amp in ((7.5, 55), (12.5, 60), (18.5, 70)):
            dt = local_hour - meal_hour
            if 0 <= dt <= 3:
                meals += amp * math.exp(-((dt - 1.0) ** 2) / 0.6)
        day_factor = 1 + 0.15 * math.sin(ts.timetuple().tm_yday * 1.7)
        noise = rng.gauss(0, 6)
        value = int(round((base + meals * day_factor + noise)))
        value = max(45, min(320, value))
        slope = (value - prev) if prev is not None else 0
        trend = _trend_from_slope(slope)
        arrow, desc = TRENDS[trend]
        rows.append({"ts": int(ts.timestamp()), "mg_dl": value, "trend": trend,
                     "trend_arrow": arrow, "trend_desc": desc})
        prev = value
    return rows


class Poller(threading.Thread):
    def __init__(self, settings: Settings, store: Store):
        super().__init__(name="dexcom-poller", daemon=True)
        self.settings = settings
        self.store = store
        self._stop = threading.Event()
        self.last_poll_at: datetime | None = None
        self.last_error: str | None = None
        self.consecutive_failures = 0
        self._client = None

    # ---- data sources -------------------------------------------------
    def _connect(self):
        from pydexcom import Dexcom
        from pydexcom.const import Region

        region = Region(self.settings.dexcom_region)
        kwargs = {"password": self.settings.dexcom_password, "region": region}
        if self.settings.dexcom_account_id:
            kwargs["account_id"] = self.settings.dexcom_account_id
        else:
            kwargs["username"] = self.settings.dexcom_username
        self._client = Dexcom(**kwargs)
        log.info("Connected to Dexcom Share (region=%s)", region.value)

    def _fetch_dexcom(self, minutes: int) -> list[dict]:
        if self._client is None:
            self._connect()
        readings = self._client.get_glucose_readings(minutes=minutes, max_count=min(288, max(1, minutes // 5 + 1)))
        rows = []
        for r in readings:
            ts = r.datetime
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            rows.append({
                "ts": int(ts.timestamp()),
                "mg_dl": int(r.mg_dl),
                "trend": int(r.trend),
                "trend_arrow": r.trend_arrow or "",
                "trend_desc": r.trend_description or "",
            })
        return rows

    def poll_once(self, backfill_minutes: int = 30) -> int:
        """One poll. Returns number of new readings stored."""
        try:
            if self.settings.demo_mode:
                rows = demo_readings(hours=1)
            else:
                rows = self._fetch_dexcom(backfill_minutes)
            inserted = self.store.upsert(rows)
            self.last_poll_at = datetime.now(timezone.utc)
            self.last_error = None
            self.consecutive_failures = 0
            if inserted:
                log.info("Stored %d new reading(s); latest %s", inserted, rows[0] if rows else None)
            return inserted
        except Exception as exc:  # noqa: BLE001 - we want to keep polling no matter what
            self.consecutive_failures += 1
            self.last_error = f"{type(exc).__name__}: {exc}"
            self._client = None  # force re-login next time
            log.warning("Poll failed (%d in a row): %s", self.consecutive_failures, self.last_error)
            return 0

    # ---- thread loop --------------------------------------------------
    def run(self) -> None:
        # First run: backfill up to 24h so the chart is useful immediately.
        if self.settings.demo_mode:
            self.store.upsert(demo_readings(hours=24 * 7))
            self.last_poll_at = datetime.now(timezone.utc)
            log.info("Demo mode: seeded 7 days of sample data")
        else:
            self.poll_once(backfill_minutes=24 * 60)
        while not self._stop.is_set():
            # Sleep until just after the next expected reading, with backoff on repeated failures.
            wait = self._seconds_until_next()
            if self._stop.wait(wait):
                break
            self.poll_once()

    def _seconds_until_next(self) -> float:
        base = self.settings.poll_seconds
        if self.consecutive_failures:
            return min(base * 4, base * (1.5 ** self.consecutive_failures))
        latest = self.store.latest()
        if latest and not self.settings.demo_mode:
            # Dexcom publishes every 5 min; aim for ~30s after the next expected timestamp.
            next_expected = latest["ts"] + 300 + 30
            delta = next_expected - time.time()
            if delta > 0:
                return min(delta, base)
            return 60  # we're already late; retry each minute until it shows up
        return base

    def stop(self) -> None:
        self._stop.set()
