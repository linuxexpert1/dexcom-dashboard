"""Configuration loaded from environment / .env."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


def _int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, "") or default)
    except ValueError:
        return default


@dataclass(frozen=True)
class Settings:
    dexcom_username: str | None = os.getenv("DEXCOM_USERNAME") or None
    dexcom_account_id: str | None = os.getenv("DEXCOM_ACCOUNT_ID") or None
    dexcom_password: str | None = os.getenv("DEXCOM_PASSWORD") or None
    dexcom_region: str = (os.getenv("DEXCOM_REGION") or "us").lower()
    demo_mode: bool = (os.getenv("DEMO_MODE") or "0").lower() in ("1", "true", "yes")
    api_token: str | None = os.getenv("API_TOKEN") or None
    target_low: int = _int("TARGET_LOW", 70)
    target_high: int = _int("TARGET_HIGH", 180)
    urgent_low: int = _int("URGENT_LOW", 55)
    urgent_high: int = _int("URGENT_HIGH", 250)
    poll_seconds: int = _int("POLL_SECONDS", 300)
    db_path: Path = Path(os.getenv("DB_PATH") or "./data/glucose.db")
    host: str = os.getenv("HOST") or "0.0.0.0"
    port: int = _int("PORT", 8080)

    @property
    def has_dexcom_credentials(self) -> bool:
        return bool(self.dexcom_password and (self.dexcom_username or self.dexcom_account_id))


settings = Settings()
