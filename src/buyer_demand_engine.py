from __future__ import annotations

import json
import os
import re
import unicodedata
from functools import lru_cache
from pathlib import Path
from typing import Any


def _norm(value: Any) -> str:
    text = str(value or "").lower()
    replacements = {
        "Ã¤": "ae", "Ã¶": "oe", "Ã¼": "ue", "ÃŸ": "ss",
        "ÃƒÂ¤": "ae", "ÃƒÂ¶": "oe", "ÃƒÂ¼": "ue", "ÃƒÅ¸": "ss",
        "ÃƒÆ’Ã‚Â¤": "ae", "ÃƒÆ’Ã‚Â¶": "oe", "ÃƒÆ’Ã‚Â¼": "ue", "ÃƒÆ’Ã…Â¸": "ss",
    }
    for old, new in replacements.items():
        text = text.replace(old, new)
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"\s+", " ", text).strip()


@lru_cache(maxsize=1)
def _db() -> dict:
    path = Path(__file__).resolve().parents[1] / "data" / "buyer_demand_database.json"
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _model_match(model: str, candidates: list[str]) -> bool:
    m = _norm(model)
    for candidate in candidates:
        c = _norm(candidate)
        if c and (c in m or m in c):
            return True
    return False


def _price_zone(brand: str, model: str, year: int) -> tuple[float | None, float | None, str]:
    b = _norm(brand)
    if b == "vw":
        b = "volkswagen"
    m = _norm(model)
    for row in _db().get("psychological_price_zones", []):
        rb = _norm(row.get("brand"))
        rm = _norm(row.get("model"))
        if rb != b:
            continue
        if rm not in m and m not in rm:
            continue
        ymin = int(row.get("year_min") or 0)
        ymax = int(row.get("year_max") or 9999)
        if year and not (ymin <= year <= ymax):
            continue
        return float(row.get("fast")), float(row.get("safe")), f"{row.get('brand')} {row.get('model')} zone"
    return None, None, "no zone"


def classify_buyer_demand(listing, observed_discount_pct: float | None, observed_comps: int, config: dict | None = None) -> dict:
    config = config or {}
    db = _db()

    brand = _norm(getattr(listing, "brand", "") or "")
    model = _norm(getattr(listing, "model", "") or "")
    title = getattr(listing, "title", "") or ""
    desc = getattr(listing, "description", "") or ""
    text = _norm(f"{title} {desc}")
    price = float(getattr(listing, "price", 0) or 0)
    year = int(getattr(listing, "year", 0) or 0)
    mileage = int(getattr(listing, "mileage", 0) or 0)
    seller = _norm(getattr(listing, "seller_type", "") or "")
    final_score = float(getattr(listing, "final_score", 0) or 0)

    score = 50
    reasons: list[str] = []
    warnings: list[str] = []
    blockers: list[str] = []

    hard_hits = [w for w in db.get("hard_reject_words", []) if _norm(w) in text]
    if hard_hits:
        return {
            "category": "REJECT",
            "score": 0,
            "summary": "hard reject wording",
            "reasons": [],
            "warnings": [],
            "blockers": hard_hits[:5],
        }

    fast_models = db.get("fast_buyer_models", {}).get(brand, [])
    safe_models = db.get("safe_but_not_urgent", {}).get(brand, [])
    youth_models = db.get("youth_demand_models", {}).get(brand, [])
    is_fast_model = _model_match(model, fast_models)
    is_safe_model = _model_match(model, safe_models)
    is_youth_model = _model_match(model, youth_models) or any(alias in text for alias in youth_models)

    if is_fast_model:
        score += 24
        reasons.append("fast-buyer model")
    elif is_youth_model:
        score += int(config.get("youth_demand_base_bonus", 6))
        reasons.append("youth-demand side-lane model; condition is decisive")
    elif is_safe_model:
        score += 7
        reasons.append("safe buyer-service model")
        warnings.append("not usually first-hour demand unless cheap")
    else:
        score -= 6
        warnings.append("buyer pool is not core fast-car segment")

    fast_price, safe_price, zone_label = _price_zone(brand, model, year)
    if fast_price and price:
        if price <= fast_price:
            score += 22
            reasons.append(f"inside FAST price zone ({price:.0f} <= {fast_price:.0f})")
        elif safe_price and price <= safe_price:
            score += 6
            warnings.append(f"only SAFE price zone ({price:.0f} > fast {fast_price:.0f})")
        else:
            score -= 20
            blockers.append(f"above buyer comfort price ({price:.0f} > {safe_price or fast_price:.0f})")
    elif price:
        warnings.append("no psychological price zone yet")

    has_tuv = any(x in text for x in ["tuev", "tuv", "hu neu", "hu bis", "hauptuntersuchung"])
    if has_tuv:
        score += 14
        reasons.append("TUV/HU signal")
    elif price > 1500:
        score -= 14
        warnings.append("no TUV/HU proof in text")

    if any(w in text for w in db.get("trust_words", [])):
        score += 10
        reasons.append("trust/service proof")
    if any(w in text for w in db.get("equipment_words", [])):
        score += 5
        reasons.append("resale equipment")
    if any(w in text for w in db.get("motivation_words", [])):
        score += 8
        reasons.append("seller motivation / negotiation wording")

    is_petrol = any(x in text for x in ["benzin", "petrol", "mpi", "vvt", "vvt-i"]) or getattr(listing, "fuel", "") == "benzin"
    is_diesel = any(x in text for x in ["diesel", "tdi", "tdci", "hdi", "crdi", "cdi", "dci"]) or getattr(listing, "fuel", "") == "diesel"
    is_auto = any(x in text for x in ["automatik", "automatic", "dsg", "powershift", "multitronic"])

    sold_dna_hit = False

    sold_dna_hit = False

    strong_discount = observed_discount_pct is not None and observed_discount_pct >= 25 and observed_comps >= 5
    sold_dna_hit = False

    youth_condition_hits = [w for w in db.get("youth_condition_words", []) if _norm(w) in text]
    youth_risk_hits = [w for w in db.get("youth_risk_words", []) if _norm(w) in text]
    youth_quality_ok = False
    youth_fast_exception = False
    if is_youth_model:
        min_condition_hits = int(config.get("youth_demand_min_condition_hits", 1))
        max_youth_price = float(config.get("youth_demand_max_price", 4500))
        max_youth_mileage = int(config.get("youth_demand_max_mileage", 210000))
        ideal_youth_mileage = int(config.get("youth_demand_ideal_mileage", 175000))

        if len(youth_condition_hits) >= min_condition_hits:
            score += 8
            reasons.append("youth car has condition/trust wording")
        else:
            score -= 18
            blockers.append("youth car needs clear condition proof")
        if not has_tuv:
            score -= 28
            blockers.append("youth-demand car without TUV/HU proof")
        if youth_risk_hits:
            score -= 24
            blockers.append("youth car has risk wording: " + ", ".join(youth_risk_hits[:3]))
        if price and price > max_youth_price and not strong_discount:
            score -= 18
            blockers.append(f"youth car price too high for side lane ({price:.0f} EUR)")
        if is_auto and price and price < 6500:
            score -= 10
            warnings.append("cheap premium automatic can scare buyers")
        if is_diesel and mileage and mileage >= int(config.get("youth_demand_diesel_max_mileage", 190000)):
            score -= 18
            blockers.append(f"youth diesel mileage too high ({mileage} km)")
        if mileage and mileage > max_youth_mileage:
            score -= 22
            blockers.append(f"youth-demand mileage too high ({mileage} km)")
        elif mileage and mileage > ideal_youth_mileage:
            score -= 8
            warnings.append("youth car mileage is acceptable only with strong condition")

        youth_quality_ok = bool(
            has_tuv
            and len(youth_condition_hits) >= min_condition_hits
            and not youth_risk_hits
            and (not price or price <= max_youth_price or strong_discount)
            and (not mileage or mileage <= max_youth_mileage)
        )
        youth_fast_exception = bool(
            youth_quality_ok
            and price
            and (fast_price is None or price <= safe_price or strong_discount)
        )

    if is_petrol:
        score += 8
        reasons.append("simple petrol buyer story")

    diesel_trust_terms = [
        "tuev", "tuv", "hu neu", "hu bis", "scheckheft", "service", "wartung",
        "zahnriemen", "steuerkette gemacht", "1 hand", "erste hand", "langstrecke"
    ]
    diesel_risk_terms = [
        "dpf", "partikelfilter", "egr", "agr", "adblue", "turbo", "turbolader",
        "injektor", "injektoren", "duese", "duesen", "rauch", "qualmt",
        "notlauf", "motorlampe", "motorkontrollleuchte", "ruckelt"
    ]
    durable_diesel_terms = [
        "1.9 tdi", "1,9 tdi", "2.0 tdi", "2,0 tdi", "1.6 tdi", "1,6 tdi",
        "1.7 cdti", "1,7 cdti", "1.6 tdci", "1,6 tdci", "2.0 tdci", "2,0 tdci",
        "cdi", "dci", "hdi", "crdi"
    ]
    diesel_has_trust = has_tuv and any(x in text for x in diesel_trust_terms)
    diesel_has_risk = any(x in text for x in diesel_risk_terms)
    diesel_known_type = any(x in text for x in durable_diesel_terms)

    if is_diesel and mileage:
        if mileage < 180000:
            reasons.append("diesel mileage still in normal buyer zone")
        elif mileage <= int(config.get("diesel_good_history_soft_max_mileage", 230000)) and diesel_known_type and diesel_has_trust and not diesel_has_risk:
            score += 4
            reasons.append("diesel mileage acceptable with TUV/service proof")
            warnings.append("diesel still needs DPF/EGR/turbo/injector check")
        elif mileage <= int(config.get("diesel_plain_soft_max_mileage", 210000)) and has_tuv and not diesel_has_risk:
            score -= 4
            warnings.append("diesel mileage is acceptable only with proof")
        else:
            score -= 18
            blockers.append(f"diesel mileage/risk too high without strong proof ({mileage} km)")
    if is_auto and price < 5000:
        score -= 8
        warnings.append("cheap automatic needs extra trust")

    sold_profile_hits = []
    for profile in db.get("sold_velocity_profiles", []):
        p_brand = _norm(profile.get("brand"))
        p_models = [_norm(x) for x in profile.get("models", [])]
        if p_brand and p_brand != brand:
            continue
        if p_models and not any(pm and (pm in model or pm in text) for pm in p_models):
            continue
        if profile.get("year_min") and year and year < int(profile["year_min"]):
            continue
        if profile.get("year_max") and year and year > int(profile["year_max"]):
            continue
        if profile.get("price_max") and price and price > float(profile["price_max"]):
            continue
        if profile.get("mileage_max") and mileage and mileage > int(profile["mileage_max"]):
            continue
        required_any = [_norm(x) for x in profile.get("required_any", [])]
        nice_any = [_norm(x) for x in profile.get("nice_any", [])]
        if required_any and not any(x in text for x in required_any):
            continue
        nice_hit = not nice_any or any(x in text for x in nice_any)
        bonus = int(profile.get("bonus") or 0)
        score += bonus
        sold_dna_hit = True
        sold_profile_hits.append(profile.get("name") or "sold velocity profile")
        reasons.append("sold-DNA match: " + str(profile.get("why") or profile.get("name")))
        if nice_any and not nice_hit:
            warnings.append("sold-DNA profile matched, but comfort/equipment wording is weak")

    if mileage:
        if mileage <= 130000:
            score += 10
            reasons.append("low/comfortable mileage")
        elif mileage <= 180000:
            score += 2
        elif mileage >= 220000:
            score -= 12
            warnings.append("mileage outside comfort zone")

    if seller == "dealer":
        score -= 12
        warnings.append("dealer/autohaus usually retail, not first-hour bargain")

    if observed_discount_pct is not None:
        if observed_discount_pct >= 25 and observed_comps >= 5:
            score += 18
            reasons.append(f"verified strong discount ({observed_discount_pct:.0f}%)")
        elif observed_discount_pct >= 12 and observed_comps >= 3:
            score += 8
            reasons.append(f"some verified discount ({observed_discount_pct:.0f}%)")
        elif observed_discount_pct < 5:
            score -= 12
            warnings.append("weak verified market edge")
    else:
        score -= 4
        warnings.append("market edge not verified")

    if final_score >= 0.72:
        score += 4
    elif final_score and final_score < 0.60:
        score -= 8

    score = max(0, min(100, int(score)))

    cheap_with_tuv = price and price <= float(config.get("buyer_demand_cheap_tuv_max_price", 1800)) and has_tuv

    # A safe-but-boring car is only useful when it is actually cheap.
    boring_safe = bool(is_safe_model or any(x in f"{brand} {model}" for x in [
        "citroen c3", "citroen c4", "peugeot 206", "peugeot 207", "ford ka",
        "opel meriva", "renault modus", "renault twingo", "renault clio",
        "mazda 3", "mitsubishi space"
    ]))
    if boring_safe and price and safe_price and price > safe_price and not strong_discount and not sold_dna_hit:
        blockers.append(f"boring safe car is too expensive ({price:.0f} > safe {safe_price:.0f})")
    if boring_safe and price and not safe_price and price > float(config.get("boring_safe_unknown_zone_max_price", 1800)) and not strong_discount and not sold_dna_hit:
        blockers.append("boring safe car has no price zone and is not cheap enough")

    if is_youth_model and not youth_fast_exception and not strong_discount:
        blockers.append("youth side lane not strong enough")
        category = "REJECT"
    elif blockers and not strong_discount and not cheap_with_tuv:
        category = "REJECT"
    elif score >= int(config.get("buyer_demand_fast_buy_min_score", 78)) and (is_fast_model or youth_fast_exception or sold_dna_hit or strong_discount) and (price <= (safe_price or 999999) if price else True):
        category = "FAST_BUY"
    elif strong_discount and score >= int(config.get("buyer_demand_flip_buy_min_score", 68)):
        category = "FLIP_BUY"
    elif score >= int(config.get("buyer_demand_safe_buy_min_score", 62)):
        category = "SAFE_BUY"
    else:
        category = "REJECT"

    if category == "SAFE_BUY" and sold_dna_hit and score >= int(config.get("buyer_demand_sold_dna_fast_min_score", 76)):
        category = "FAST_BUY"

    if category == "SAFE_BUY" and is_youth_model and youth_fast_exception and score >= int(config.get("buyer_demand_youth_fast_min_score", 76)):
        category = "FAST_BUY"

    if category == "SAFE_BUY" and is_fast_model and fast_price and price and price <= fast_price and has_tuv:
        category = "FAST_BUY"

    return {
        "category": category,
        "score": score,
        "summary": f"{category}: buyer demand score {score}/100",
        "reasons": reasons[:6],
        "warnings": warnings[:6],
        "blockers": blockers[:6],
    }


def _money(value: Any) -> str:
    try:
        return f"{float(value):,.0f} EUR"
    except Exception:
        return "?"


def _km(value: Any) -> str:
    try:
        return f"{int(value):,} km"
    except Exception:
        return "?"


def write_buyer_demand_watchlist(final_export, safe_candidates, config: dict, scan_count: int) -> None:
    latest_path = config.get("safe_buy_watchlist_file", "output/safe_buy_watchlist.txt")
    report_dir = config.get("safe_buy_report_dir", "output/reports")
    os.makedirs(os.path.dirname(latest_path), exist_ok=True)
    os.makedirs(report_dir, exist_ok=True)

    rows = []
    for listing, demand in safe_candidates:
        rows.append((int(demand.get("score") or 0), listing, demand))
    rows.sort(key=lambda x: x[0], reverse=True)
    rows = rows[: int(config.get("safe_buy_watchlist_rows", 10))]

    lines = []
    lines.append("AUTOHAWK SAFE BUY WATCHLIST")
    lines.append("=" * 72)
    lines.append("These are normal/safe buyer-service cars, not urgent hunting leads.")
    lines.append("FAST_BUY/strong FLIP_BUY remain in best_leads.txt.")
    lines.append("")

    if not rows:
        lines.append("No SAFE_BUY watchlist cars this scan.")
    else:
        for idx, (score, listing, demand) in enumerate(rows, 1):
            lines.append(f"{idx}. {listing.title or '?'}")
            lines.append(f"   Car: {listing.brand or '?'} {listing.model or '?'} | Year: {listing.year or '?'} | Km: {_km(listing.mileage)}")
            lines.append(f"   Price: {_money(listing.price)} | Score: {score}/100 | Category: {demand.get('category')}")
            lines.append(f"   Link: {listing.url or '-'}")
            if demand.get("reasons"):
                lines.append("   Why safe: " + "; ".join(demand.get("reasons")[:4]))
            if demand.get("warnings"):
                lines.append("   Why not urgent: " + "; ".join(demand.get("warnings")[:4]))
            lines.append("")

    content = "\n".join(lines)
    Path(latest_path).write_text(content, encoding="utf-8")
    history = Path(report_dir) / f"safe_buy_scan_{scan_count:04d}.txt"
    history.write_text(content, encoding="utf-8")

