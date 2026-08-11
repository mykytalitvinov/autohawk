"""Vehicle dossier matching for AUTOHAWK.

This is the structured model/generation knowledge layer. It gives the dealer
engine a real car-specific dossier instead of generic brand/model rules.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from src.utils import norm, load_json_db, safe_int, safe_float

ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "data" / "vehicle_dossier_database.json"


@lru_cache(maxsize=1)
def load_db() -> dict[str, Any]:
    return load_json_db(DB_PATH, default={"dossiers": []})


def _contains(text: str, phrase: str) -> bool:
    p = norm(phrase)
    if not p:
        return False
    return p in text


def _match_dossier(brand: str, model: str, text: str) -> dict[str, Any] | None:
    b = norm(brand)
    m = norm(model)
    haystack = f"{b} {m} {text}"
    best: tuple[int, dict[str, Any]] | None = None
    for dossier in load_db().get("dossiers", []):
        score = 0
        db_brand = norm(dossier.get("brand"))
        db_model = norm(dossier.get("model"))
        if db_brand == b:
            score += 10
        if db_model == m or db_model in m or m in db_model:
            score += 15
        for alias in dossier.get("aliases", []):
            if _contains(haystack, alias):
                score += 12
                break
        if score >= 15 and (best is None or score > best[0]):
            best = (score, dossier)
    return best[1] if best else None


def _pick_segment(dossier: dict[str, Any], year: int, text: str) -> dict[str, Any] | None:
    best: tuple[int, dict[str, Any]] | None = None
    for segment in dossier.get("segments", []):
        years = segment.get("years") or [0, 9999]
        score = 0
        if year and int(years[0]) <= year <= int(years[1]):
            score += 30
        elif year:
            score -= min(abs(year - int(years[0])), abs(year - int(years[1])), 20)
        if _contains(text, segment.get("name", "")):
            score += 15
        if best is None or score > best[0]:
            best = (score, segment)
    return best[1] if best else None


def _pick_engine(segment: dict[str, Any], text: str) -> dict[str, Any] | None:
    best: tuple[int, dict[str, Any]] | None = None
    for engine in segment.get("engines", []):
        hits = 0
        for pattern in engine.get("patterns", []):
            if _contains(text, pattern):
                hits += 1
        if hits and (best is None or hits > best[0]):
            best = (hits, engine)
    return best[1] if best else None


def _buy_zone(segment: dict[str, Any], mileage: int) -> tuple[int | None, str]:
    zone = segment.get("buy_zone") or {}
    if not zone:
        return None, "unknown buy zone"
    if mileage and mileage <= 130000:
        return zone.get("low_km"), "low-km buy zone"
    if mileage and mileage >= 190000:
        return zone.get("high_km"), "high-km buy zone"
    return zone.get("normal"), "normal buy zone"


def evaluate_vehicle_dossier(listing: dict[str, Any]) -> dict[str, Any] | None:
    title = listing.get("title") or ""
    desc = listing.get("description") or ""
    brand = listing.get("brand") or ""
    model = listing.get("model") or ""
    engine_text = listing.get("engine") or ""
    gearbox = listing.get("gearbox") or ""
    fuel = listing.get("fuel") or ""
    text = norm(f"{brand} {model} {title} {desc} {engine_text} {gearbox} {fuel}")

    dossier = _match_dossier(brand, model, text)
    if not dossier:
        return None

    year = safe_int(listing.get("year"))
    mileage = safe_int(listing.get("mileage"))
    price = safe_float(listing.get("price"))

    segment = _pick_segment(dossier, year, text)
    if not segment:
        return None

    engine = _pick_engine(segment, text)
    buy_max, buy_label = _buy_zone(segment, mileage)
    retail_ceiling = (segment.get("buy_zone") or {}).get("retail_ceiling")

    score_delta = 0
    score_cap = None
    reserve = 0
    why: list[str] = []
    risks: list[str] = []
    checks: list[str] = []

    dossier_name = f"{dossier.get('brand')} {dossier.get('model')} {segment.get('name')}"
    why.append(f"Vehicle dossier: {dossier_name}.")
    why.append("Buyer exit: " + str(segment.get("buyer_exit") or "unknown"))
    why.append("Why it sells: " + str(segment.get("why_it_sells") or ""))
    checks.append("Mileage policy: " + str(segment.get("mileage_policy") or ""))

    if engine:
        score_delta += int(engine.get("score_delta") or 0)
        reserve += int(engine.get("reserve") or 0)
        engine_name = engine.get("name")
        risk = engine.get("risk")
        if str(risk).startswith("high"):
            risks.append(f"Engine dossier risk: {engine_name} ({risk}). {engine.get('notes')}")
        else:
            why.append(f"Engine dossier: {engine_name} ({risk}). {engine.get('notes')}")
        if engine.get("score_cap"):
            score_cap = min(score_cap or 100, int(engine.get("score_cap")))
    else:
        score_delta -= 6
        risks.append(f"Exact engine not resolved for {dossier_name}; do not trust score until engine is confirmed.")
        checks.append("Ask seller exact engine/displacement/gearbox from Fahrzeugschein.")

    if buy_max and price:
        if price <= buy_max:
            score_delta += 10
            why.append(f"Price dossier: asking is inside {buy_label} <= {int(buy_max)} EUR.")
        elif retail_ceiling and price > retail_ceiling:
            score_delta -= 18
            score_cap = min(score_cap or 100, 55)
            risks.append(f"Price dossier: asking {int(price)} EUR is above retail ceiling {int(retail_ceiling)} EUR for quick flip.")
        else:
            score_delta -= 4
            risks.append(f"Price dossier: asking {int(price)} EUR is above preferred {buy_label} ({int(buy_max)} EUR). Needs negotiation/proof.")

    for item in segment.get("body_risks", [])[:4]:
        risks.append("Body/common risk: " + str(item))
    for item in segment.get("must_check", [])[:8]:
        checks.append("Must check: " + str(item))

    red_hit = []
    for red in segment.get("red_flags", []):
        if norm(red).split()[0] in text or any(word in text for word in norm(red).split()[:2]):
            red_hit.append(str(red))
    if red_hit:
        score_delta -= min(20, 5 * len(red_hit))
        risks.append("Dossier red flags in text: " + ", ".join(red_hit[:5]) + ".")

    good_hit = []
    for good in segment.get("good_signals", []):
        words = norm(good).split()
        if words and any(word in text for word in words[:2]):
            good_hit.append(str(good))
    if good_hit:
        score_delta += min(12, 3 * len(good_hit))
        why.append("Dossier good signals: " + ", ".join(good_hit[:6]) + ".")

    report = (
        f"{dossier_name}: buy_max={int(buy_max) if buy_max else '?'} EUR ({buy_label}), "
        f"retail_ceiling={int(retail_ceiling) if retail_ceiling else '?'} EUR, "
        f"engine={engine.get('name') if engine else '?'}, "
        f"risk={engine.get('risk') if engine else 'unknown'}."
    )

    return {
        "dossier": dossier_name,
        "segment": segment.get("name"),
        "engine_profile": engine.get("name") if engine else None,
        "engine_risk": engine.get("risk") if engine else None,
        "buy_max": int(buy_max) if buy_max else None,
        "retail_ceiling": int(retail_ceiling) if retail_ceiling else None,
        "score_delta": max(-45, min(35, int(score_delta))),
        "score_cap": score_cap,
        "repair_reserve": reserve,
        "why": why[:10],
        "risks": risks[:12],
        "checks": checks[:14],
        "report": report,
    }
