from __future__ import annotations

import csv
from pathlib import Path

from src import sold_memory


def _write_csv(path: Path, rows: list[dict[str, object]]) -> None:
    fields = [
        "status",
        "title",
        "brand",
        "model",
        "year",
        "mileage",
        "price",
        "market_price",
        "minutes_to_sold",
        "minutes_since_found",
        "url",
        "seller_type",
        "tuv_months_left",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in fields})


def test_build_sold_memory_writes_fast_sold_records(tmp_path: Path, monkeypatch) -> None:
    tracker_csv = tmp_path / "sold_tracker.csv"
    manual_csv = tmp_path / "manual_sold_feedback.csv"
    memory_json = tmp_path / "sold_velocity_memory.json"
    summary_csv = tmp_path / "sold_velocity_memory_summary.csv"
    fast_csv = tmp_path / "fast_sold_records.csv"
    report_txt = tmp_path / "sold_velocity_report.txt"

    _write_csv(
        tracker_csv,
        [
            {
                "status": "active",
                "title": "Ford Focus Kombi",
                "brand": "Ford",
                "model": "Focus",
                "year": 2009,
                "mileage": 186000,
                "price": 1500,
                "minutes_since_found": 90,
                "url": "https://example.test/focus",
            },
        ],
    )
    _write_csv(
        manual_csv,
        [
            {
                "status": "verkauft",
                "title": "VW Polo 1.2 TUV bis 05/2028",
                "brand": "VW",
                "model": "Polo",
                "year": 2009,
                "mileage": 170000,
                "price": 1000,
                "minutes_to_sold": 120,
                "url": "https://example.test/polo",
                "seller_type": "private",
            },
        ],
    )

    monkeypatch.setattr(sold_memory, "DATABASE_PATH", tmp_path / "missing.db")
    monkeypatch.setattr(sold_memory, "SOLD_TRACKER_CSV", tracker_csv)
    monkeypatch.setattr(sold_memory, "MANUAL_FEEDBACK_CSV", manual_csv)
    monkeypatch.setattr(sold_memory, "MEMORY_JSON", memory_json)
    monkeypatch.setattr(sold_memory, "MEMORY_SUMMARY_CSV", summary_csv)
    monkeypatch.setattr(sold_memory, "FAST_SOLD_CSV", fast_csv)
    monkeypatch.setattr(sold_memory, "REPORT_TXT", report_txt)

    memory = sold_memory.build_sold_memory()

    assert memory["totals"]["events"] == 2
    assert memory["totals"]["sold_events"] == 1
    assert memory["totals"]["active_events"] == 1
    assert memory["totals"]["fast_sold_records"] == 1
    assert memory["fast_sold_records"][0]["brand"] == "volkswagen"
    assert memory["fast_sold_records"][0]["model"] == "polo"
    assert memory["fast_sold_records"][0]["seller_type"] == "private"
    assert memory["fast_sold_records"][0]["tuv_status"] == "fresh_18m_plus"
    assert fast_csv.exists()
    assert summary_csv.exists()
    assert report_txt.exists()
