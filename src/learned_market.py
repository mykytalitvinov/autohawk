"""
AUTOHAWK learned market database.

This is not magic AI. It is a local statistical memory built from the listings
AUTOHAWK has already observed. It helps avoid fantasy market prices by learning
real asking-price bands for brand/model/year/mileage buckets.
"""

from __future__ import annotations

import csv
import json
import os
import re
from collections import Counter
from datetime import datetime
from statistics import median
from typing import Any

from src.utils import norm, CONSERVATIVE_MARKET_MODELS


BAD_MARKET_TERMS = [
    "motorschaden", "motor schaden", "motor defekt", "motor kaputt",
    "motor laeuft nicht", "motor startet nicht",
    "motor unruhig", "motorkontrollleuchte", "kontrollleuchte",
    "check engine", "getriebeschaden", "getriebe schaden",
    "getriebe defekt", "getriebe kaputt", "kupplung defekt",
    "startet nicht", "springt nicht an", "faehrt nicht", "nicht fahrbereit",
    "bastler", "bastlerfahrzeug", "bastler fahrzeug", "projektfahrzeug",
    "projekt fahrzeug", "schlachtfest", "ersatzteile", "ersatzteiltraeger",
    "export", "nur export", "exportfahrzeug", "export fahrzeug",
    "nur tausch", "leasinguebernahme", "leasingÃ¼bernahme", "kein brief",
    "kein kfz brief", "ohne brief", "brief fehlt", "keine papiere",
    "ohne papiere", "kein tuev", "kein tuv", "ohne tuev", "ohne tuv",
    "tuev abgelaufen", "tuv abgelaufen", "ohne hu", "hu abgelaufen",
    "rostloch", "durchrostung", "starker rost", "schweller durch",
    "airbag leuchtet", "abs leuchtet", "esp leuchtet",
    "wir kaufen", "fahrzeugankauf", "ankauf",
]

SPECIAL_VARIANT_TERMS = [
    # These cars must not inflate the normal Golf/Polo/Astra/Fabia market.
    "gti", "r32", "r 32", "gtd", "vr6", "edition 30", "edition 35",
    "cabrio", "cabriolet", "roadster", "coupe", "individual",
    "gt sport", "s-line", "s line", "sline", "amg", "m paket", "m-paket",
    "m sport", "quattro", "4motion", "v6", "v8", "oldtimer", "youngtimer",
    "tuning", "umbau", "swap", "kompressor", "turbo umbau", "projekt",
    "sondermodell", "opc", "focus st", "fiesta st",
]


def to_float(value: Any) -> float | None:
    try:
        if value in (None, ""):
            return None
        return float(str(value).replace(",", "."))
    except Exception:
        return None


def to_int(value: Any) -> int | None:
    number = to_float(value)
    if number is None:
        return None
    return int(number)


def year_bucket(year: int | None) -> str:
    if not year:
        return "unknown"
    start = (int(year) // 3) * 3
    return f"{start}-{start + 2}"


def mileage_bucket(mileage: int | None) -> str:
    if not mileage:
        return "unknown"
    km = int(mileage)
    if km < 50_000:
        return "0-50k"
    if km < 100_000:
        return "50-100k"
    if km < 150_000:
        return "100-150k"
    if km < 200_000:
        return "150-200k"
    if km < 250_000:
        return "200-250k"
    if km < 300_000:
        return "250-300k"
    return "300k+"


def bucket_key(brand: str, model: str, year: int | None, mileage: int | None) -> str:
    return "|".join([norm(brand), norm(model), year_bucket(year), mileage_bucket(mileage)])


def model_key(brand: str, model: str) -> str:
    return "|".join([norm(brand), norm(model)])


def _stats(prices: list[float]) -> dict[str, Any]:
    clean = sorted(p for p in prices if 700 <= p <= 80_000)
    if not clean:
        return {}
    # Trim obvious outliers when enough data exists.
    trimmed = clean
    if len(clean) >= 8:
        cut = max(1, int(len(clean) * 0.12))
        trimmed = clean[cut:-cut] or clean
    mid = float(median(trimmed))
    q1 = trimmed[max(0, int(len(trimmed) * 0.25) - 1)]
    q3 = trimmed[min(len(trimmed) - 1, int(len(trimmed) * 0.75))]
    return {
        "count": len(trimmed),
        "raw_count": len(clean),
        "median": round(mid, -2),
        "q1": round(float(q1), -2),
        "q3": round(float(q3), -2),
        "min": round(float(trimmed[0]), -2),
        "max": round(float(trimmed[-1]), -2),
    }


def _is_special_variant(title: str) -> bool:
    text = norm(title)
    return any(term in text for term in SPECIAL_VARIANT_TERMS)


def _has_bad_market_terms(title: str) -> bool:
    text = norm(title)
    if any(term in text for term in BAD_MARKET_TERMS):
        return True
    # "Unfallfrei" is good. "Unfallwagen", "Unfallschaden" and "nach Unfall"
    # are not clean market comparables and must not teach the price database.
    return bool(re.search(r"\bunfall(?!frei)", text))


def _is_conservative_market(brand: str, model: str, year: int | None) -> bool:
    if not year or int(year) > 2012:
        return False
    b = norm(brand)
    m = norm(model)
    return any(b == cb and (m == cm or cm in m or m in cm) for cb, cm in CONSERVATIVE_MARKET_MODELS)


def _ensure_parent(path: str) -> None:
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)


def _reject_reason(row: dict[str, Any], current_year: int | None = None) -> str | None:
    current_year = current_year or datetime.now().year
    title = norm(row.get("title"))
    brand = norm(row.get("brand"))
    model = norm(row.get("model"))
    price = to_float(row.get("price"))
    year = to_int(row.get("year"))
    mileage = to_int(row.get("mileage"))

    if not brand or not model:
        return "missing_brand_or_model"
    if price is None:
        return "missing_price"
    if price < 700:
        return "price_too_low_for_market_learning"
    if price > 80_000:
        return "price_too_high_for_market_learning"
    if year and (year < 1995 or year > current_year):
        return "invalid_year"
    if mileage and mileage > 450_000:
        return "mileage_too_high_for_market_learning"
    if year and mileage:
        age = current_year - year
        if age >= 15 and mileage < 50_000:
            return "implausibly_low_mileage_for_age"
        if age >= 8 and mileage < 20_000:
            return "implausibly_low_mileage_for_age"
    if _has_bad_market_terms(title):
        return "problem_listing_terms"
    if _is_conservative_market(brand, model, year) and _is_special_variant(title):
        return "special_variant_not_normal_market"
    return None


def _write_quality_report(path: str, reject_reasons: Counter[str], samples: dict[str, dict[str, Any]]) -> None:
    if not path:
        return
    _ensure_parent(path)
    with open(path, "w", newline="", encoding="utf-8-sig") as fh:
        fieldnames = [
            "reason", "count", "sample_title", "sample_price", "sample_brand",
            "sample_model", "sample_year", "sample_mileage", "sample_url",
        ]
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        for reason, count in reject_reasons.most_common():
            sample = samples.get(reason, {})
            writer.writerow({
                "reason": reason,
                "count": count,
                "sample_title": sample.get("title", ""),
                "sample_price": sample.get("price", ""),
                "sample_brand": sample.get("brand", ""),
                "sample_model": sample.get("model", ""),
                "sample_year": sample.get("year", ""),
                "sample_mileage": sample.get("mileage", ""),
                "sample_url": sample.get("url", ""),
            })


def build_learned_market(
    observations_csv: str,
    output_json: str = "output/learned_market.json",
    summary_csv: str = "output/learned_market_summary.csv",
    quality_report_csv: str = "output/learned_market_quality_report.csv",
) -> dict[str, Any]:
    grouped: dict[str, list[float]] = {}
    model_grouped: dict[str, list[float]] = {}
    total_rows = 0
    usable_rows = 0
    reject_reasons: Counter[str] = Counter()
    reject_samples: dict[str, dict[str, Any]] = {}

    if not os.path.exists(observations_csv):
        data = {
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "cleaning_version": "market_clean_v1",
            "source_rows": 0,
            "usable_rows": 0,
            "rejected_rows": 0,
            "reject_reasons": {},
            "groups": {},
            "models": {},
        }
        return data

    with open(observations_csv, "r", newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        current_year = datetime.now().year
        for row in reader:
            total_rows += 1
            reason = _reject_reason(row, current_year=current_year)
            if reason:
                reject_reasons[reason] += 1
                reject_samples.setdefault(reason, dict(row))
                continue
            brand = norm(row.get("brand"))
            model = norm(row.get("model"))
            price = to_float(row.get("price"))
            year = to_int(row.get("year"))
            mileage = to_int(row.get("mileage"))

            usable_rows += 1
            grouped.setdefault(bucket_key(brand, model, year, mileage), []).append(price)
            model_grouped.setdefault(model_key(brand, model), []).append(price)

    groups = {key: value for key, prices in grouped.items() if (value := _stats(prices))}
    models = {key: value for key, prices in model_grouped.items() if (value := _stats(prices))}
    data = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "cleaning_version": "market_clean_v1",
        "source_rows": total_rows,
        "usable_rows": usable_rows,
        "rejected_rows": sum(reject_reasons.values()),
        "reject_reasons": dict(sorted(reject_reasons.items())),
        "groups": groups,
        "models": models,
    }

    _ensure_parent(output_json)
    with open(output_json, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)

    if summary_csv:
        _ensure_parent(summary_csv)
        with open(summary_csv, "w", newline="", encoding="utf-8-sig") as fh:
            fieldnames = ["key", "count", "median", "q1", "q3", "min", "max"]
            writer = csv.DictWriter(fh, fieldnames=fieldnames)
            writer.writeheader()
            for key, stats in sorted(groups.items(), key=lambda item: (-item[1].get("count", 0), item[0])):
                writer.writerow({"key": key, **{name: stats.get(name, "") for name in fieldnames if name != "key"}})

    _write_quality_report(quality_report_csv, reject_reasons, reject_samples)

    return data


def estimate_from_learned_market(
    listing: dict[str, Any],
    learned: dict[str, Any],
    min_bucket_count: int = 3,
) -> tuple[float | None, int, str]:
    brand = norm(listing.get("brand"))
    model = norm(listing.get("model"))
    year = to_int(listing.get("year"))
    mileage = to_int(listing.get("mileage"))
    if not brand or not model:
        return None, 0, "missing_brand_model"

    key = bucket_key(brand, model, year, mileage)
    bucket = (learned.get("groups") or {}).get(key)
    if bucket and int(bucket.get("count") or 0) >= min_bucket_count:
        price_key = "q1" if _is_conservative_market(brand, model, year) and bucket.get("q1") else "median"
        return float(bucket[price_key]), int(bucket["count"]), key

    # Do not use broad model-wide median as price proof: Golf 2018 and Golf 2006
    # are different markets. Model stats stay in the summary as context only.
    return None, 0, "no_learned_bucket_match"






