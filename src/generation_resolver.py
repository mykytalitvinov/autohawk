"""Generation resolver for AUTOHAWK.

This is the first layer that separates broad model names into real market
objects such as Golf 5 1.6 MPI vs Golf 6 1.4 TSI DSG.
"""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path
from typing import Any

from src.utils import norm, load_json_db, safe_int, safe_float


ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "data" / "generation_knowledge_database.json"


@lru_cache(maxsize=1)
def load_generation_db() -> dict[str, Any]:
    return load_json_db(DB_PATH, default={"cards": []})


def _contains_phrase(text: str, phrase: str) -> bool:
    p = norm(phrase)
    if not p:
        return False
    if p in text:
        return True
    return re.search(r"\b" + re.escape(p).replace(r"\ ", r"\s+") + r"\b", text, re.IGNORECASE) is not None


def _match_card(brand: str, model: str, text: str) -> dict[str, Any] | None:
    b = norm(brand)
    m = norm(model)
    haystack = f"{b} {m} {text}"
    best = None
    for card in load_generation_db().get("cards", []):
        cb = norm(card.get("brand"))
        cm = norm(card.get("model"))
        score = 0
        if cb == b:
            score += 6
        elif cb and cb in haystack:
            score += 3
        if cm == m or cm in m or m in cm:
            score += 8
        for alias in card.get("aliases", []):
            if _contains_phrase(haystack, alias):
                score += 6
                break
        if score >= 8 and (best is None or score > best[0]):
            best = (score, card)
    return best[1] if best else None


def _pick_generation(card: dict[str, Any], year: int, text: str) -> dict[str, Any] | None:
    best = None
    for gen in card.get("generations", []):
        years = gen.get("years") or [0, 9999]
        score = 0
        if year and int(years[0]) <= year <= int(years[1]):
            score += 20
        elif year:
            score -= min(abs(year - int(years[0])), abs(year - int(years[1])), 10)
        for alias in gen.get("aliases", []):
            if _contains_phrase(text, alias):
                score += 20
                break
        if best is None or score > best[0]:
            best = (score, gen)
    return best[1] if best else None


def _pick_mileage_band(gen: dict[str, Any], mileage: int) -> dict[str, Any] | None:
    bands = gen.get("mileage_bands") or []
    if not bands:
        return None
    for band in bands:
        low, high = band.get("mileage") or [0, 999999]
        if not mileage or int(low) <= mileage <= int(high):
            return band
    return bands[-1]


def _pick_engine(gen: dict[str, Any], text: str) -> dict[str, Any] | None:
    best = None
    for engine in gen.get("engines", []):
        hits = sum(1 for p in engine.get("patterns", []) if _contains_phrase(text, p))
        if hits and (best is None or hits > best[0]):
            best = (hits, engine)
    return best[1] if best else None


def evaluate_generation_truth(listing: dict[str, Any]) -> dict[str, Any] | None:
    brand = listing.get("brand") or ""
    model = listing.get("model") or ""
    title = listing.get("title") or ""
    desc = listing.get("description") or ""
    engine_text = listing.get("engine") or ""
    gearbox = listing.get("gearbox") or ""
    fuel = listing.get("fuel") or ""
    text = norm(f"{brand} {model} {title} {desc} {engine_text} {gearbox} {fuel}")
    card = _match_card(brand, model, text)
    if not card:
        return None

    year = safe_int(listing.get("year"))
    mileage = safe_int(listing.get("mileage"))
    price = safe_float(listing.get("price"))

    gen = _pick_generation(card, year, text)
    if not gen:
        return None
    band = _pick_mileage_band(gen, mileage)
    engine = _pick_engine(gen, text)

    why: list[str] = []
    risks: list[str] = []
    checks: list[str] = []
    score_delta = 0
    score_cap = None
    reserve = 0

    gen_name = str(gen.get("name") or "")
    why.append(f"Generation DB: resolved as {card.get('brand')} {card.get('model')} / {gen_name}.")
    if gen.get("notes"):
        why.append("Generation note: " + str(gen.get("notes")))

    if engine:
        label = str(engine.get("label") or "unknown engine")
        risk = str(engine.get("risk") or "unknown")
        delta = int(engine.get("score_delta") or 0)
        reserve += int(engine.get("reserve") or 0)
        score_delta += delta
        if delta >= 4:
            why.append(f"Engine profile: {label} ({risk}). {engine.get('notes') or ''}".strip())
        else:
            risks.append(f"Engine profile risk: {label} ({risk}). {engine.get('notes') or ''}".strip())
        checks.append(f"Generation/engine check: confirm exact engine in Fahrzeugschein; app inferred {label}.")
        if risk == "high":
            score_cap = min(score_cap or 100, 62)
    else:
        risks.append(f"Engine profile unknown for {gen_name}; do not treat market price as precise.")
        checks.append("Ask seller exact engine code/displacement and gearbox before visit.")
        score_delta -= 3

    if band:
        market_low = float(band.get("market_low") or 0)
        market_mid = float(band.get("market_mid") or 0)
        flip_buy_max = float(band.get("flip_buy_max") or 0)
        watch_max = float(band.get("watch_max") or 0)
        over_retail = float(band.get("over_retail") or 0)
        if price:
            if flip_buy_max and price <= flip_buy_max:
                score_delta += 10
                why.append(f"Generation price: inside flip-buy zone <= {int(flip_buy_max)} EUR.")
            elif watch_max and price <= watch_max:
                score_delta += 3
                why.append(f"Generation price: watch zone <= {int(watch_max)} EUR; needs proof.")
            elif over_retail and price > over_retail:
                score_delta -= 16
                score_cap = min(score_cap or 100, 54)
                risks.append(f"Generation price: over retail/too expensive for this generation (> {int(over_retail)} EUR).")
            elif market_low and price > market_low * 1.1:
                score_delta -= 5
                risks.append("Generation price: around/above lower market band, weak flip edge unless seller is motivated.")
        checks.append(
            f"Generation price band: flip <= {int(flip_buy_max) if flip_buy_max else '?'} EUR, "
            f"watch <= {int(watch_max) if watch_max else '?'} EUR, "
            f"market low/mid {int(market_low) if market_low else '?'}/{int(market_mid) if market_mid else '?'} EUR."
        )
    else:
        risks.append(f"No generation mileage price band for {gen_name}; price confidence lower.")
        market_low = market_mid = None
        flip_buy_max = watch_max = over_retail = None

    return {
        "brand": card.get("brand"),
        "model": card.get("model"),
        "generation": gen_name,
        "generation_key": f"{norm(card.get('brand'))}|{norm(card.get('model'))}|{norm(gen_name)}",
        "engine_profile": engine.get("label") if engine else None,
        "engine_risk": engine.get("risk") if engine else None,
        "market_low": int(market_low) if band and market_low else None,
        "market_mid": int(market_mid) if band and market_mid else None,
        "flip_buy_max": int(flip_buy_max) if band and flip_buy_max else None,
        "watch_max": int(watch_max) if band and watch_max else None,
        "over_retail": int(over_retail) if band and over_retail else None,
        "score_delta": max(-35, min(25, int(score_delta))),
        "score_cap": score_cap,
        "repair_reserve": reserve,
        "why": why[:6],
        "risks": risks[:6],
        "checks": checks[:6],
        "summary": f"{card.get('brand')} {card.get('model')} {gen_name}; engine={engine.get('label') if engine else '?'}; price={int(price) if price else '?'} EUR",
    }
