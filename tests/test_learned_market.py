from __future__ import annotations

import csv
from pathlib import Path

from src.learned_market import build_learned_market, bucket_key, estimate_from_learned_market


def write_observations(path: Path, rows: list[dict]) -> None:
    fieldnames = ["time", "scan", "source", "title", "price", "brand", "model", "year", "mileage", "fuel", "gearbox", "age_minutes", "url"]
    with path.open("w", newline="", encoding="utf-8-sig") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({name: row.get(name, "") for name in fieldnames})


def test_build_learned_market_stats_and_conservative_bucket(tmp_path: Path) -> None:
    observations = tmp_path / "market_observations.csv"
    rows = [
        {"title": "VW Golf 1.6 TUV", "price": price, "brand": "Volkswagen", "model": "Golf", "year": 2004, "mileage": 175000}
        for price in [1000, 1500, 2000, 2500, 3000]
    ]
    rows.append({"title": "VW Golf GTI S-line Sondermodell", "price": 12000, "brand": "Volkswagen", "model": "Golf", "year": 2004, "mileage": 175000})
    write_observations(observations, rows)

    learned = build_learned_market(
        str(observations),
        output_json=str(tmp_path / "learned_market.json"),
        summary_csv=str(tmp_path / "learned_market_summary.csv"),
        quality_report_csv=str(tmp_path / "learned_market_quality_report.csv"),
    )

    key = bucket_key("Volkswagen", "Golf", 2004, 175000)
    stats = learned["groups"][key]
    assert learned["source_rows"] == 6
    assert learned["usable_rows"] == 5
    assert learned["rejected_rows"] == 1
    assert learned["reject_reasons"]["special_variant_not_normal_market"] == 1
    assert stats == {
        "count": 5,
        "raw_count": 5,
        "median": 2000.0,
        "q1": 1000.0,
        "q3": 2500.0,
        "min": 1000.0,
        "max": 3000.0,
    }


def test_estimate_from_learned_market_uses_q1_for_old_conservative_models(tmp_path: Path) -> None:
    observations = tmp_path / "market_observations.csv"
    rows = [
        {"title": "Skoda Fabia 1.4", "price": price, "brand": "Skoda", "model": "Fabia", "year": 2008, "mileage": 160000}
        for price in [1200, 1500, 1800, 2100]
    ]
    write_observations(observations, rows)
    learned = build_learned_market(
        str(observations),
        str(tmp_path / "market.json"),
        str(tmp_path / "summary.csv"),
        str(tmp_path / "quality.csv"),
    )

    estimate, comps, key = estimate_from_learned_market(
        {"brand": "Skoda", "model": "Fabia", "year": 2008, "mileage": 160000},
        learned,
        min_bucket_count=3,
    )

    assert estimate == 1200.0
    assert comps == 4
    assert key == bucket_key("Skoda", "Fabia", 2008, 160000)


def test_estimate_from_learned_market_rejects_broad_model_fallback(tmp_path: Path) -> None:
    observations = tmp_path / "market_observations.csv"
    write_observations(
        observations,
        [
            {"title": "Toyota Yaris", "price": 2600, "brand": "Toyota", "model": "Yaris", "year": 2008, "mileage": 160000},
            {"title": "Toyota Yaris", "price": 2800, "brand": "Toyota", "model": "Yaris", "year": 2009, "mileage": 170000},
        ],
    )
    learned = build_learned_market(
        str(observations),
        str(tmp_path / "market.json"),
        str(tmp_path / "summary.csv"),
        str(tmp_path / "quality.csv"),
    )

    estimate, comps, reason = estimate_from_learned_market(
        {"brand": "Toyota", "model": "Yaris", "year": 2015, "mileage": 90000},
        learned,
        min_bucket_count=3,
    )

    assert estimate is None
    assert comps == 0
    assert reason == "no_learned_bucket_match"


def test_build_learned_market_rejects_problem_noise_and_bad_mileage(tmp_path: Path) -> None:
    observations = tmp_path / "market_observations.csv"
    write_observations(
        observations,
        [
            {"title": "VW Golf 1.6 TUV", "price": 1200, "brand": "Volkswagen", "model": "Golf", "year": 2004, "mileage": 180000},
            {"title": "VW Golf 1.6 gepflegt", "price": 1600, "brand": "Volkswagen", "model": "Golf", "year": 2004, "mileage": 185000},
            {"title": "VW Golf 1.6 Klima", "price": 1900, "brand": "Volkswagen", "model": "Golf", "year": 2004, "mileage": 175000},
            {"title": "VW Golf Motorschaden", "price": 900, "brand": "Volkswagen", "model": "Golf", "year": 2004, "mileage": 180000},
            {"title": "VW Golf Unfallwagen", "price": 1000, "brand": "Volkswagen", "model": "Golf", "year": 2004, "mileage": 180000},
            {"title": "VW Golf Unfallfrei gepflegt", "price": 1700, "brand": "Volkswagen", "model": "Golf", "year": 2004, "mileage": 181000},
            {"title": "VW Golf GTI 400PS Projekt", "price": 12000, "brand": "Volkswagen", "model": "Golf", "year": 2004, "mileage": 150000},
            {"title": "VW Golf service at 25.700 km", "price": 2200, "brand": "Volkswagen", "model": "Golf", "year": 2004, "mileage": 25700},
            {"title": "VW Golf future", "price": 2200, "brand": "Volkswagen", "model": "Golf", "year": 2099, "mileage": 100000},
            {"title": "Unknown car", "price": 2200, "brand": "", "model": "", "year": 2008, "mileage": 100000},
        ],
    )

    quality_report = tmp_path / "learned_market_quality_report.csv"
    learned = build_learned_market(
        str(observations),
        output_json=str(tmp_path / "learned_market.json"),
        summary_csv=str(tmp_path / "learned_market_summary.csv"),
        quality_report_csv=str(quality_report),
    )

    key = bucket_key("Volkswagen", "Golf", 2004, 180000)
    assert learned["source_rows"] == 10
    assert learned["usable_rows"] == 4
    assert learned["rejected_rows"] == 6
    assert learned["groups"][key]["count"] == 4
    assert learned["reject_reasons"]["problem_listing_terms"] == 2
    assert learned["reject_reasons"]["special_variant_not_normal_market"] == 1
    assert learned["reject_reasons"]["implausibly_low_mileage_for_age"] == 1
    assert learned["reject_reasons"]["invalid_year"] == 1
    assert learned["reject_reasons"]["missing_brand_or_model"] == 1
    assert quality_report.exists()
