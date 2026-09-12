"""Pure functions for glucose statistics (unit-tested, no I/O)."""
from __future__ import annotations

from statistics import mean, pstdev


def summarize(values: list[int], low: int, high: int, urgent_low: int, urgent_high: int) -> dict:
    """Time-in-range and summary stats for a list of mg/dL values.

    GMI (Glucose Management Indicator) uses the Bergenstal 2018 formula:
        GMI(%) = 3.31 + 0.02392 * mean_mg_dl
    It is an estimate, not a lab A1c.
    """
    n = len(values)
    if n == 0:
        return {
            "count": 0, "mean": None, "median": None, "sd": None, "cv": None, "gmi": None,
            "min": None, "max": None,
            "pct_very_low": None, "pct_low": None, "pct_in_range": None,
            "pct_high": None, "pct_very_high": None,
        }
    avg = mean(values)
    sd = pstdev(values) if n > 1 else 0.0
    sorted_vals = sorted(values)
    median = sorted_vals[n // 2] if n % 2 else (sorted_vals[n // 2 - 1] + sorted_vals[n // 2]) / 2

    def pct(pred) -> float:
        return round(100.0 * sum(1 for v in values if pred(v)) / n, 1)

    return {
        "count": n,
        "mean": round(avg, 1),
        "median": median,
        "sd": round(sd, 1),
        "cv": round(100.0 * sd / avg, 1) if avg else None,
        "gmi": round(3.31 + 0.02392 * avg, 1),
        "min": sorted_vals[0],
        "max": sorted_vals[-1],
        "pct_very_low": pct(lambda v: v < urgent_low),
        "pct_low": pct(lambda v: urgent_low <= v < low),
        "pct_in_range": pct(lambda v: low <= v <= high),
        "pct_high": pct(lambda v: high < v <= urgent_high),
        "pct_very_high": pct(lambda v: v > urgent_high),
    }


def classify(mg_dl: int, low: int, high: int, urgent_low: int, urgent_high: int) -> str:
    if mg_dl < urgent_low:
        return "urgent_low"
    if mg_dl < low:
        return "low"
    if mg_dl <= high:
        return "in_range"
    if mg_dl <= urgent_high:
        return "high"
    return "urgent_high"
