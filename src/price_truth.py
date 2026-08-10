"""Conservative price-truth layer for AUTOHAWK.

This answers a different question than a normal market estimate:
    "Is this price good enough for profit?"

market_mid = rough asking-market reference.
flip_buy_max/watch_max = flipper buy discipline.
"""

from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "data" / "price_truth_database.json"


def norm(value: Any) -> str:
    text = str(value or "").lower().strip()
    for old, new in {
        "Ã¤": "ae", "Ã¶": "oe", "Ã¼": "ue", "ÃŸ": "ss",
        "Ã„": "ae", "Ã–": "oe", "Ãœ": "ue",
    }.items():
        text = text.replace(old, new)
    text = re.sub(r"\s+", " ", text)
    if text == "vw":
        return "volkswagen"
    return text


@lru_cache(maxsize=1)
def load_price_truth() -> dict[str, Any]:
    if not DB_PATH.exists():
        return {"cards": []}
    try:
        return json.loads(DB_PATH.read_text(encoding="utf-8-sig"))
    except Exception:
        return {"cards": []}


def _match_card(brand: str, model: str, text: str) -> dict[str, Any] | None:
    b = norm(brand)
    m = norm(model)
    haystack = f"{b} {m} {norm(text)}"
    best: tuple[int, dict[str, Any]] | None = None
    for card in load_price_truth().get("cards", []):
        score = 0
        cb = norm(card.get("brand"))
        cm = norm(card.get("model"))
        aliases = [norm(x) for x in card.get("aliases", [])]
        if cb and cb == b:
            score += 6
        elif cb and cb in haystack:
            score += 3
        if cm and (cm == m or cm in m or m in cm):
            score += 8
        for alias in aliases:
            if alias and alias in haystack:
                score += 10
                break
        if score >= 9 and (best is None or score > best[0]):
            best = (score, card)
    return best[1] if best else None


def _choose_band(card: dict[str, Any], year: int, mileage: int) -> dict[str, Any] | None:
    bands = card.get("bands") or []
    if not bands:
        return None
    best: tuple[int, dict[str, Any]] | None = None
    for band in bands:
        years = band.get("years") or [0, 9999]
        kms = band.get("mileage") or [0, 999999]
        score = 0
        if year and int(years[0]) <= year <= int(years[1]):
            score += 20
        elif year:
            score -= min(12, abs(year - int(years[0])), abs(year - int(years[1])))
        if mileage and int(kms[0]) <= mileage <= int(kms[1]):
            score += 20
        elif mileage:
            if mileage < int(kms[0]):
                score -= min(10, int((int(kms[0]) - mileage) / 25000))
            else:
                score -= min(14, int((mileage - int(kms[1])) / 25000))
        if best is None or score > best[0]:
            best = (score, band)
    return best[1] if best else None


def evaluate_price_truth(listing: dict[str, Any]) -> dict[str, Any] | None:
    brand = listing.get("brand") or ""
    model = listing.get("model") or ""
    title = listing.get("title") or ""
    description = listing.get("description") or ""
    text = f"{title} {description}"
    card = _match_card(brand, model, text)
    if not card:
        return None

    try:
        price = float(listing.get("price") or 0)
    except Exception:
        price = 0.0
    try:
        year = int(listing.get("year") or 0)
    except Exception:
        year = 0
    try:
        mileage = int(listing.get("mileage") or 0)
    except Exception:
        mileage = 0

    band = _choose_band(card, year, mileage)
    if not band or not price:
        return None

    market_low = float(band.get("market_low") or 0)
    market_mid = float(band.get("market_mid") or market_low or 0)
    flip_buy_max = float(band.get("flip_buy_max") or 0)
    watch_max = float(band.get("watch_max") or flip_buy_max or 0)
    over_retail = float(band.get("over_retail") or market_mid * 1.1)

    score_delta = 0
    score_cap = None
    risks: list[str] = []
    why: list[str] = []
    checks: list[str] = []

    if price <= flip_buy_max:
        verdict = "FLIP_BUY_ZONE"
        score_delta += 14
        why.append(f"Price-truth DB: inside flipper buy zone (<= {int(flip_buy_max)} EUR).")
    elif price <= watch_max:
        verdict = "WATCH_ZONE"
        score_delta += 5
        why.append(f"Price-truth DB: watch zone (<= {int(watch_max)} EUR), needs proof/negotiation.")
    elif price <= over_retail:
        verdict = "RETAIL_OR_BUYER_SERVICE"
        score_delta -= 6
        score_cap = 68
        risks.append("Price-truth DB: probably normal retail/buyer-service price, not a clear quick-flip price.")
    else:
        verdict = "OVER_RETAIL_FOR_FLIP"
        score_delta -= 18
        score_cap = 52
        risks.append(f"Price-truth DB: above over-retail zone for this strategy (> {int(over_retail)} EUR).")

    if market_low and price > market_low * 1.08 and verdict != "FLIP_BUY_ZONE":
        risks.append("Price-truth DB: price is above lower market band; margin must come from negotiation or exceptional condition.")
    if market_mid and price <= market_mid * 0.8:
        checks.append("Price-truth DB: low price needs reason check: HU/TUV, accident, rust, engine/gearbox, documents.")

    discount_vs_mid = round(((market_mid - price) / market_mid) * 100, 1) if market_mid else None
    estimated_profit_band = int(max(0, market_low - price - 450)) if market_low else None

    return {
        "brand": card.get("brand"),
        "model": card.get("model"),
        "verdict": verdict,
        "market_low": int(market_low) if market_low else None,
        "market_mid": int(market_mid) if market_mid else None,
        "market_high": int(float(band.get("market_high") or 0)) if band.get("market_high") else None,
        "flip_buy_max": int(flip_buy_max) if flip_buy_max else None,
        "watch_max": int(watch_max) if watch_max else None,
        "over_retail": int(over_retail) if over_retail else None,
        "discount_vs_mid_pct": discount_vs_mid,
        "estimated_profit_band": estimated_profit_band,
        "score_delta": int(score_delta),
        "score_cap": score_cap,
        "why": why,
        "risks": risks,
        "checks": checks,
        "summary": (
            f"{card.get('brand')} {card.get('model')}: {verdict}; "
            f"price {int(price)} EUR, flip_buy <= {int(flip_buy_max)} EUR, "
            f"watch <= {int(watch_max)} EUR, market_low/mid {int(market_low)}/{int(market_mid)} EUR. "
            f"{band.get('notes') or ''}"
        ).strip(),
        "notes": band.get("notes") or "",
    }
