"""FastAPI app: serves the dashboard (docs/index.html) and a small JSON API."""
from __future__ import annotations

import logging
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .config import settings
from .poller import Poller
from .stats import classify, summarize
from .store import Store

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("dexcom.api")

ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / "docs"

store: Store | None = None
poller: Poller | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global store, poller
    store = Store(settings.db_path)
    if not settings.demo_mode and not settings.has_dexcom_credentials:
        log.error("No Dexcom credentials in .env and DEMO_MODE is off. Set DEXCOM_USERNAME/DEXCOM_PASSWORD or DEMO_MODE=1.")
    poller = Poller(settings, store)
    poller.start()
    log.info("Dashboard on http://%s:%d  (demo_mode=%s)", settings.host, settings.port, settings.demo_mode)
    try:
        yield
    finally:
        poller.stop()
        store.close()


app = FastAPI(title="Dexcom Dashboard", version="1.0.0", lifespan=lifespan)


# ---- auth ---------------------------------------------------------------
def require_token(request: Request) -> None:
    if not settings.api_token:
        return
    header = request.headers.get("authorization", "")
    token = header[7:] if header.lower().startswith("bearer ") else request.query_params.get("token")
    if token != settings.api_token:
        raise HTTPException(status_code=401, detail="invalid or missing token")


def _thresholds() -> dict:
    return {
        "target_low": settings.target_low,
        "target_high": settings.target_high,
        "urgent_low": settings.urgent_low,
        "urgent_high": settings.urgent_high,
    }


def _decorate(row: dict) -> dict:
    return {
        **row,
        "time": datetime.fromtimestamp(row["ts"], tz=timezone.utc).isoformat(),
        "mmol_l": round(row["mg_dl"] / 18.0, 1),
        "status": classify(row["mg_dl"], settings.target_low, settings.target_high,
                           settings.urgent_low, settings.urgent_high),
    }


# ---- API ---------------------------------------------------------------
@app.get("/api/health")
def health():
    latest = store.latest() if store else None
    age = int(time.time() - latest["ts"]) if latest else None
    return {
        "ok": True,
        "demo_mode": settings.demo_mode,
        "readings_stored": store.count() if store else 0,
        "latest_reading_age_seconds": age,
        "stale": age is None or age > 15 * 60,
        "last_poll_at": poller.last_poll_at.isoformat() if poller and poller.last_poll_at else None,
        "last_error": poller.last_error if poller else None,
        "consecutive_failures": poller.consecutive_failures if poller else 0,
        "server_time": datetime.now(timezone.utc).isoformat(),
        "thresholds": _thresholds(),
    }


@app.get("/api/current", dependencies=[Depends(require_token)])
def current():
    latest = store.latest()
    if not latest:
        return JSONResponse({"reading": None, "previous": None, "thresholds": _thresholds()})
    recent = store.last_hours(0.5)
    previous = recent[-2] if len(recent) >= 2 else None
    out = {
        "reading": _decorate(latest),
        "previous": _decorate(previous) if previous else None,
        "delta": (latest["mg_dl"] - previous["mg_dl"]) if previous else None,
        "age_seconds": int(time.time() - latest["ts"]),
        "thresholds": _thresholds(),
    }
    return out


@app.get("/api/readings", dependencies=[Depends(require_token)])
def readings(hours: float = Query(24, gt=0, le=24 * 90)):
    rows = store.last_hours(hours)
    return {"hours": hours, "count": len(rows), "thresholds": _thresholds(),
            "readings": [_decorate(r) for r in rows]}


@app.get("/api/stats", dependencies=[Depends(require_token)])
def stats(hours: float = Query(24, gt=0, le=24 * 90)):
    rows = store.last_hours(hours)
    values = [r["mg_dl"] for r in rows]
    return {"hours": hours, "thresholds": _thresholds(),
            **summarize(values, settings.target_low, settings.target_high,
                        settings.urgent_low, settings.urgent_high)}


@app.post("/api/poll", dependencies=[Depends(require_token)])
def poll_now():
    """Force an immediate poll (handy for testing)."""
    inserted = poller.poll_once()
    return {"inserted": inserted, "last_error": poller.last_error}


# ---- static dashboard --------------------------------------------------
@app.get("/", include_in_schema=False)
def index():
    return FileResponse(DOCS / "index.html")


app.mount("/", StaticFiles(directory=str(DOCS)), name="static")


def run() -> None:
    import uvicorn

    uvicorn.run("app.main:app", host=settings.host, port=settings.port, log_level="info")


if __name__ == "__main__":
    run()
