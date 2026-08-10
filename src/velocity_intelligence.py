from __future__ import annotations

import json
import re
import unicodedata
from functools import lru_cache
from pathlib import Path
from typing import Any
from src.replacement import replacements

def _norm(value: Any) -> str:
    text = str(value or "").lower()

    for old, new in replacements.items():
        text = text.replace(old, new)
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"\s+", " ", text).strip()


@lru_cache(maxsize=1)
def _db() -> dict:
    path = Path(__file__).resolve().parents[1] / "data" / "velocity_database.json"
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _contains_model(model: str, candidates: list[str]) -> bool:
    m = _norm(model)
    return any(_norm(candidate) in m or m in _norm(candidate) for candidate in candidates)


def _cap_for(brand: str, model: str, year: int) -> tuple[float | None, str]:
    brand_n = _norm(brand)
    model_n = _norm(model)
    for row in _db().get("psychological_caps", []):
        if _norm(row.get("brand")) not in {brand_n, "volkswagen" if brand_n == "vw" else brand_n}:
            continue
        if _norm(row.get("model")) not in model_n:
            continue
        year_min = int(row.get("year_min") or 0)
        year_max = int(row.get("year_max") or 9999)
        if year and not (year_min <= year <= year_max):
            continue
        return float(row["max_price"]), f"{row.get('brand')} {row.get('model')} psychological cap"
    return None, "no cap"


def evaluate_velocity(listing, observed_discount_pct: float | None, observed_comps: int, config: dict | None = None) -> dict:
    config = config or {}
    brand = _norm(getattr(listing, "brand", "") or "")
    model = _norm(getattr(listing, "model", "") or "")
    title = getattr(listing, "title", "") or ""
    desc = getattr(listing, "description", "") or ""
    text = _norm(f"{title} {desc}")
    price = float(getattr(listing, "price", 0) or 0)
    year = int(getattr(listing, "year", 0) or 0)
    mileage = int(getattr(listing, "mileage", 0) or 0)
    seller = _norm(getattr(listing, "seller_type", "") or "")
    score = 50
    good: list[str] = []
    bad: list[str] = []

    db = _db()
    fast_models = db.get("fast_core_models", {}).get(brand, [])
    slow_models = db.get("slow_or_only_with_discount", {}).get(brand, [])

    if _contains_model(model, fast_models):
        score += 18
        good.append("core fast-buyer model")
    if _contains_model(model, slow_models):
        score -= 18
        bad.append("slow/only-with-discount model")

    cap, cap_reason = _cap_for(brand, model, year)
    if cap is not None and price:
        if price <= cap * 0.88:
            score += 14
            good.append(f"below fast-sale price cap ({price:.0f} <= {cap:.0f})")
        elif price <= cap:
            score += 5
            good.append(f"inside fast-sale price cap ({price:.0f} <= {cap:.0f})")
        else:
            score -= 16
            bad.append(f"above fast-sale price cap ({price:.0f} > {cap:.0f})")

    has_tuv = any(token in text for token in ["tuev", "tuv", "hu neu", "hu bis", "hauptuntersuchung"])
    if has_tuv:
        score += 9
        good.append("TUV/HU signal")
    elif price and price > 1500:
        score -= 12
        bad.append("no TUV/HU signal over 1500 EUR")

    if any(token in text for token in ["1.hand", "erste hand", "scheckheft", "service neu", "rechnungen"]):
        score += 8
        good.append("trust/service signal")
    if any(token in text for token in ["klima", "sitzheizung", "8-fach", "winterreifen", "sommerreifen"]):
        score += 5
        good.append("easy resale equipment")
    if any(token in text for token in ["dringend", "muss weg", "wegen neuanschaffung", "umzug", "vb", "vhb"]):
        score += 7
        good.append("seller motivation/negotiation signal")

    if seller == "dealer":
        score -= 10
        bad.append("dealer/autohaus usually not first-hour bargain")

    negative_hits = [token for token in db.get("negative_text", []) if token in text]
    if negative_hits:
        score -= min(35, 10 + 5 * len(negative_hits))
        bad.append("red-flag wording: " + ", ".join(negative_hits[:4]))

    is_diesel = any(token in text for token in ["diesel", "tdi", "tdci", "hdi", "crdi", "cdi", "dci"])
    if is_diesel and mileage >= 180000:
        score -= 14
        bad.append("high-mileage diesel slows normal buyers")
    if year and year <= 2007 and price > 2200:
        score -= 12
        bad.append("old car above fast-sale comfort price")
    if mileage >= 220000 and not any(x in brand for x in ["toyota", "honda"]):
        score -= 10
        bad.append("very high mileage for broad buyer pool")

    if observed_discount_pct is not None:
        if observed_discount_pct >= 25 and observed_comps >= 5:
            score += 18
            good.append(f"verified strong market discount ({observed_discount_pct:.0f}%)")
        elif observed_discount_pct >= 12 and observed_comps >= 3:
            score += 7
            good.append(f"some market discount ({observed_discount_pct:.0f}%)")
        elif observed_discount_pct < 5:
            score -= 10
            bad.append("weak/no verified market discount")
    else:
        score -= 4
        bad.append("no observed discount proof")

    score = max(0, min(100, int(score)))
    min_score = int(config.get("velocity_min_export_score", db.get("rules", {}).get("fast_sale_min_score_for_export", 62)))
    if score >= 82:
        label = "FAST_BUYER_STRONG"
    elif score >= min_score:
        label = "FAST_BUYER_POSSIBLE"
    else:
        label = "SLOW_OR_NORMAL_MARKET"

    return {
        "score": score,
        "label": label,
        "export_ok": score >= min_score,
        "good": good[:6],
        "bad": bad[:6],
    }
