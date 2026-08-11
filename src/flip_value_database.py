"""Flip-value knowledge layer for AUTOHAWK.

This database answers a different question than model_knowledge:
"Is this a good buy zone for fast resale to young/first-car buyers?"
"""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path
from typing import Any

from src.utils import norm as norm, load_json_db

ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "data" / "flip_value_database.json"


@lru_cache(maxsize=1)
def load_flip_database() -> dict[str, Any]:
    return load_json_db(DB_PATH, default={"cards": []})


def find_flip_value_card(brand: str | None, model: str | None, year: int | None = None, text: str = "") -> dict[str, Any] | None:
    brand_n = norm(brand)
    model_n = norm(model)
    haystack = f"{brand_n} {model_n} {norm(text)}".strip()
    if not haystack:
        return None

    best: tuple[int, dict[str, Any]] | None = None
    for card in load_flip_database().get("cards", []):
        score = 0
        card_brand = norm(card.get("brand"))
        card_model = norm(card.get("model"))
        aliases = [norm(x) for x in card.get("aliases", [])]

        if card_brand and card_brand == brand_n:
            score += 5
        elif card_brand and card_brand in haystack:
            score += 2

        if card_model and (card_model == model_n or card_model in model_n or model_n in card_model):
            score += 6

        for alias in aliases:
            if alias and alias in haystack:
                score += 8
                break

        years = card.get("target_years") or []
        if year and len(years) == 2 and int(years[0]) <= int(year) <= int(years[1]):
            score += 2

        if score >= 7 and (best is None or score > best[0]):
            best = (score, card)

    return best[1] if best else None


def _has_any(text: str, values: list[str]) -> list[str]:
    text_n = norm(text)
    hits = []
    for raw in values or []:
        item = norm(raw)
        if item and item in text_n:
            hits.append(str(raw))
    return hits


def _has_tuv(text: str) -> bool:
    text_n = norm(text)
    if re.search(r"\b(kein|keine|ohne|abgelaufen)\s*(tuev|tuv|hu)\b|\b(tuev|tuv|hu)\s*(abgelaufen)\b", text_n):
        return False
    return bool(re.search(r"\b(tuev|tuv|hu)\s*(neu|bis|[0-9]{1,2}[./-]?[0-9]{2,4})\b|\b[0-9]{1,2}[./-]?(2[6-9]|202[6-9])\b.{0,20}\b(tuev|tuv|hu)\b", text_n))


def _door_points(text: str) -> tuple[int, str | None]:
    text_n = norm(text)
    if re.search(r"\b(5|fuenf|funf)\s*[- ]?(tuer|tueren|tuerer|tuerig|door|doors)\b|\b5-trg\b|\b5trg\b", text_n):
        return 8, "flip DB: 5-door demand boost"
    if re.search(r"\b(3|drei)\s*[- ]?(tuer|tueren|tuerer|tuerig|door|doors)\b|\b3-trg\b|\b3trg\b|\bcoupe\b|\bcabrio\b", text_n):
        return -10, "flip DB: 3-door/coupe reduces buyer pool"
    return 0, None


def evaluate_flip_value(
    card: dict[str, Any] | None,
    listing: dict[str, Any],
    text: str,
    price: float,
    year: int,
    mileage: int,
    fuel: str,
    gearbox: str,
    discount_pct: float | None,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "score_delta": 0,
        "score_cap": None,
        "why": [],
        "risks": [],
        "checks": [],
        "max_buy_price": None,
        "fast_sell_zone": None,
        "summary": "",
    }
    if not card:
        return result

    points = 0
    risks: list[str] = []
    why: list[str] = []
    checks: list[str] = []

    buy = card.get("buy_zone") or {}
    ideal_max = float(buy.get("ideal_max") or 0)
    max_good = float(buy.get("max_good") or 0)
    max_ok = float(buy.get("max_ok") or 0)
    avoid_over = float(buy.get("avoid_over") or 0)

    result["max_buy_price"] = int(max_good) if max_good else None
    result["fast_sell_zone"] = card.get("fast_sell_zone")

    why.append(f"Flip DB card: {card.get('brand')} {card.get('model')} tier {card.get('tier')} liquidity {card.get('liquidity')}/100.")

    if price and ideal_max and price <= ideal_max:
        points += 14
        why.append(f"Flip DB: asking price is inside ideal buy zone (<= {int(ideal_max)} EUR).")
    elif price and max_good and price <= max_good:
        points += 8
        why.append(f"Flip DB: asking price is inside good buy zone (<= {int(max_good)} EUR).")
    elif price and max_ok and price <= max_ok:
        points += 1
        risks.append(f"Flip DB: price is only OK, not a clear bargain (max OK {int(max_ok)} EUR).")
    elif price and avoid_over and price > avoid_over:
        points -= 22
        result["score_cap"] = 45
        risks.append(f"Flip DB: price is above avoid zone ({int(avoid_over)} EUR) for this strategy.")
    elif price and max_ok and price > max_ok:
        points -= 12
        result["score_cap"] = 62
        risks.append(f"Flip DB: above normal buy zone ({int(max_ok)} EUR); not a strong flip unless condition is exceptional.")

    years = card.get("target_years") or []
    if year and len(years) == 2:
        if int(years[0]) <= int(year) <= int(years[1]):
            points += 3
        else:
            points -= 6
            risks.append(f"Flip DB: year {year} is outside preferred range {years[0]}-{years[1]}.")

    ideal_mileage = int(card.get("ideal_mileage_max") or 0)
    max_mileage = int(card.get("max_mileage") or 0)
    if mileage and ideal_mileage and mileage <= ideal_mileage:
        points += 6
        why.append(f"Flip DB: mileage is in preferred range (<= {ideal_mileage} km).")
    elif mileage and max_mileage and mileage <= max_mileage:
        points += 1
    elif mileage and max_mileage and mileage > max_mileage:
        points -= 12
        result["score_cap"] = min(result["score_cap"] or 100, 58)
        risks.append(f"Flip DB: mileage above preferred maximum ({max_mileage} km).")

    tuv_required = any("tuv" in norm(x) or "hu" in norm(x) for x in card.get("must_have", []))
    if tuv_required:
        if _has_tuv(text):
            points += 10
            why.append("Flip DB: HU/TUV proof found, important for fast resale.")
        else:
            points -= 18
            result["score_cap"] = min(result["score_cap"] or 100, 62)
            risks.append("Flip DB: no clear HU/TUV proof found; keep as CHECK unless very cheap.")

    door_score, door_reason = _door_points(text)
    points += door_score
    if door_reason:
        (why if door_score > 0 else risks).append(door_reason)
        if door_score < 0:
            result["score_cap"] = min(result["score_cap"] or 100, 66)

    boosts = _has_any(text, card.get("boost_terms", []))
    if boosts:
        add = min(14, 2 * len(boosts))
        points += add
        why.append("Flip DB boosts: " + ", ".join(boosts[:6]))

    risk_hits = _has_any(text, card.get("risk_terms", []))
    if risk_hits:
        penalty = min(30, 6 * len(risk_hits))
        points -= penalty
        risks.append("Flip DB risks: " + ", ".join(risk_hits[:6]))
        if penalty >= 18:
            result["score_cap"] = min(result["score_cap"] or 100, 60)

    bad_engine_hits = _has_any(text, card.get("bad_engines", []))
    if bad_engine_hits:
        points -= min(24, 8 * len(bad_engine_hits))
        risks.append("Flip DB bad engine/gearbox match: " + ", ".join(bad_engine_hits[:4]))
        result["score_cap"] = min(result["score_cap"] or 100, 62)

    good_engine_hits = _has_any(text, card.get("good_engines", []))
    if good_engine_hits:
        points += min(8, 3 * len(good_engine_hits))
        why.append("Flip DB good engine match: " + ", ".join(good_engine_hits[:4]))

    if discount_pct is not None:
        if discount_pct >= 20:
            points += 8
        elif discount_pct >= 10:
            points += 4
        elif discount_pct < 3 and price and max_good and price > max_good:
            points -= 8
            risks.append("Flip DB: no meaningful discount and above buy zone.")

    checks.extend([f"Flip DB check: {item}" for item in card.get("must_have", [])[:5]])
    if card.get("notes"):
        why.append("Flip DB note: " + str(card.get("notes")))

    result["score_delta"] = int(max(-35, min(35, points)))
    result["why"] = why
    result["risks"] = risks
    result["checks"] = checks
    sell = card.get("fast_sell_zone")
    if sell and len(sell) == 2:
        result["summary"] = f"Buy-zone max {int(max_good) if max_good else '?'} EUR; fast-sell zone about {sell[0]}-{sell[1]} EUR if clean."
    return result
