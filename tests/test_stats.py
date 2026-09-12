from app.stats import classify, summarize
from app.poller import demo_readings


def test_summarize_basic():
    s = summarize([50, 60, 100, 150, 200, 300], 70, 180, 55, 250)
    assert s["count"] == 6
    assert s["pct_very_low"] == round(100 / 6, 1)
    assert s["pct_low"] == round(100 / 6, 1)
    assert s["pct_in_range"] == round(200 / 6, 1)
    assert s["pct_high"] == round(100 / 6, 1)
    assert s["pct_very_high"] == round(100 / 6, 1)
    assert s["min"] == 50 and s["max"] == 300
    assert abs(s["gmi"] - (3.31 + 0.02392 * s["mean"])) < 0.1


def test_summarize_empty():
    assert summarize([], 70, 180, 55, 250)["count"] == 0


def test_classify():
    assert classify(54, 70, 180, 55, 250) == "urgent_low"
    assert classify(65, 70, 180, 55, 250) == "low"
    assert classify(120, 70, 180, 55, 250) == "in_range"
    assert classify(200, 70, 180, 55, 250) == "high"
    assert classify(260, 70, 180, 55, 250) == "urgent_high"


def test_demo_readings_shape():
    rows = demo_readings(hours=2)
    assert len(rows) == 25
    assert all(rows[i]["ts"] < rows[i + 1]["ts"] for i in range(len(rows) - 1))
    assert all(45 <= r["mg_dl"] <= 320 for r in rows)
