"""
AUTOHAWK — Scoring Engine (Steps 2-9)
Calculates all scores and final verdict.
"""

from datetime import datetime
from typing import Optional

# NOTE: This module is legacy/support scoring, not the main AUTOHAWK decision gate.
# scanner.py calls score_listing(), but dealer_engine.analyze_dealer_candidate()
# owns opportunity_score/recommended_action and the persisted HOT/GOOD/CHECK verdict.
# The only score_listing output currently passed into dealer_engine is
# model_specific_issues. estimated_margin is used as fallback/potential paid-AI
# context. The legacy final_score/verdict returned here are not the real product
# decision.

# ---------------------------------------------------------------------------
# Model-specific known risk database
# ---------------------------------------------------------------------------
MODEL_RISKS = {
    ("bmw", "n47"):       ["Timing chain risk at high mileage", "DPF/EGR wear", "Water pump failure"],
    ("bmw", "318d"):      ["Possible N47 timing chain risk on many 2007-2011 cars", "DPF/EGR wear"],
    ("bmw", "320d"):      ["Possible N47 timing chain risk on many 2007-2011 cars", "DPF/EGR wear"],
    ("bmw", "n57"):       ["DPF/EGR issues", "Injector wear at high mileage"],
    ("bmw", "n43"):       ["Injector failure", "High oil consumption"],
    ("bmw", "n54"):       ["Fuel injector failure", "Wastegate rattle", "High pressure fuel pump"],
    ("audi", "2.0 tfsi"): ["Oil consumption", "Timing chain tensioner", "Carbon buildup on intake"],
    ("audi", "1.8 tfsi"): ["Oil consumption", "Timing chain issues"],
    ("vw", "dsg"):        ["Mechatronic unit failure", "Clutch wear at high mileage"],
    ("volkswagen", "dsg"): ["Mechatronic unit failure", "Clutch wear at high mileage"],
    ("vw", "2.0 tdi"):    ["EGR valve", "DPF blockage", "Timing belt service critical"],
    ("volkswagen", "2.0 tdi"): ["EGR valve", "DPF blockage", "Timing belt service critical"],
    ("vw", "1.4 tsi"):    ["Timing chain", "Carbon buildup"],
    ("volkswagen", "1.4 tsi"): ["Timing chain", "Carbon buildup"],
    ("skoda", "dsg"):     ["Mechatronic unit failure", "Clutch wear at high mileage"],
    ("seat", "dsg"):      ["Mechatronic unit failure", "Clutch wear at high mileage"],
    ("skoda", "1.4 tsi"): ["Timing chain", "Carbon buildup"],
    ("seat", "1.4 tsi"):  ["Timing chain", "Carbon buildup"],
    ("mercedes", "om651"):["Timing chain", "Swirl flap failure", "Injector wear"],
    ("mercedes", "cdi"):  ["Injector wear", "DPF/EGR issues", "Glow plug system"],
    ("mercedes", "m271"): ["Timing chain", "Oil pump failure"],
    ("opel", "1.6 cdti"): ["EGR valve", "DPF issues", "Timing chain"],
    ("ford", "1.0 ecoboost"): ["Cooling system", "Head gasket at high mileage"],
    ("renault", "1.5 dci"):   ["EGR", "DPF", "Flywheel dual mass"],
    ("peugeot", "1.6 hdi"):   ["DPF", "EGR", "Timing belt critical"],
    ("citroen", "1.6 hdi"):   ["DPF", "EGR", "Timing belt critical"],
    ("toyota", "1.8 hybrid"):  ["Generally reliable, battery health check after 150k km"],
    ("mazda", "skyactiv-d"):   ["Carbon buildup", "EGR issues"],
}

# Brands known for good/bad reliability (simplistic heuristic)
RELIABLE_BRANDS = {"toyota", "honda", "mazda", "subaru", "lexus"}
PROBLEMATIC_BRANDS = {"alfa romeo", "lancia", "fiat", "land rover"}

# Liquidity: how easy is it to resell in Germany
LIQUIDITY_HIGH = {"volkswagen", "bmw", "mercedes", "audi", "opel", "ford", "toyota"}
LIQUIDITY_LOW = {"lancia", "alfa romeo", "dacia", "ssangyong", "chevrolet"}


# ---------------------------------------------------------------------------
# Step 2 — Freshness Score
# ---------------------------------------------------------------------------
def freshness_score(listing_age_minutes: Optional[int]) -> float:
    if listing_age_minutes is None:
        return 0.3
    if listing_age_minutes <= 30:
        return 1.0
    if listing_age_minutes <= 60:
        return 0.9
    if listing_age_minutes <= 180:
        return 0.75
    if listing_age_minutes <= 360:
        return 0.6
    if listing_age_minutes <= 720:
        return 0.45
    if listing_age_minutes <= 1440:
        return 0.3
    return 0.1


# ---------------------------------------------------------------------------
# Step 3 — Price Score
# ---------------------------------------------------------------------------
def price_score(price: float, estimated_market: float) -> tuple[float, float, float]:
    """Returns (score, margin_eur, undervaluation_pct)"""
    if not estimated_market or estimated_market <= 0 or not price or price <= 0:
        return 0.3, 0.0, 0.0

    margin = estimated_market - price
    underval_pct = (margin / estimated_market) * 100

    if underval_pct >= 30:
        score = 1.0
    elif underval_pct >= 20:
        score = 0.85
    elif underval_pct >= 10:
        score = 0.7
    elif underval_pct >= 5:
        score = 0.55
    elif underval_pct >= 0:
        score = 0.4
    else:
        # overpriced
        score = max(0.0, 0.4 + underval_pct / 100)

    return score, round(margin, 0), round(underval_pct, 1)


# ---------------------------------------------------------------------------
# Step 5 — Model Risk Score
# ---------------------------------------------------------------------------
def get_model_risks(brand: str, engine: str, mileage: int) -> list[str]:
    risks = []
    brand_l = (brand or "").lower()
    engine_l = (engine or "").lower()

    for (b, e), risk_list in MODEL_RISKS.items():
        if b in brand_l and e in engine_l:
            risks.extend(risk_list)

    # High mileage generic risks
    if mileage and mileage > 150000:
        risks.append("High mileage: check timing belt/chain service history")
        if "diesel" in engine_l or "tdi" in engine_l or "dci" in engine_l or "cdi" in engine_l:
            risks.append("High mileage diesel: DPF/EGR/turbo wear likely")
        if "dsg" in engine_l or "s-tronic" in engine_l or "dct" in engine_l:
            risks.append("High mileage DSG: mechatronic and clutch pack wear")

    return list(set(risks))


def risk_score_from_signals(warnings: list, model_risks: list, mileage: int, year: int) -> float:
    """Lower score = higher risk."""
    penalty = 0.0
    penalty += len(warnings) * 0.08
    penalty += len(model_risks) * 0.06

    age = datetime.now().year - (year or 2010)
    if age > 15:
        penalty += 0.1
    if mileage and mileage > 180000:
        penalty += 0.15

    return max(0.0, 1.0 - penalty)


# ---------------------------------------------------------------------------
# Step 7 — Liquidity Score
# ---------------------------------------------------------------------------
def liquidity_score(brand: str) -> float:
    b = (brand or "").lower()
    if b in LIQUIDITY_HIGH:
        return 0.9
    if b in LIQUIDITY_LOW:
        return 0.4
    return 0.65


# ---------------------------------------------------------------------------
# Step 9 — Final Score & Verdict
# ---------------------------------------------------------------------------
def calculate_final_score(
    fresh: float,
    price: float,
    condition: float,
    risk: float,
    liquidity: float,
    urgency: float,
) -> float:
    weights = {
        "fresh": 0.25,
        "price": 0.25,
        "condition": 0.10,
        "risk": 0.20,
        "liquidity": 0.10,
        "urgency": 0.10,
    }
    score = (
        fresh * weights["fresh"]
        + price * weights["price"]
        + condition * weights["condition"]
        + risk * weights["risk"]
        + liquidity * weights["liquidity"]
        + urgency * weights["urgency"]
    )
    return round(score, 3)


def verdict_from_score(score: float, risk: float) -> str:
    if risk < 0.3:
        return "SKIP"   # Too risky regardless of score
    if score >= 0.72:
        return "HOT"
    if score >= 0.55:
        return "GOOD"
    if score >= 0.38:
        return "CHECK"
    return "SKIP"


def confidence_from_data(listing: dict) -> str:
    """How confident is the analysis based on data completeness."""
    fields = ["price", "mileage", "year", "brand", "model", "description", "listing_age_minutes"]
    present = sum(1 for f in fields if listing.get(f))
    if listing.get("data_quality_warnings"):
        return "LOW"
    if not listing.get("detail_verified"):
        return "LOW"
    if not listing.get("mileage") or not listing.get("year"):
        return "LOW"
    if listing.get("listing_age_minutes") is None:
        return "LOW"
    if present >= 6:
        return "MEDIUM"   # Never HIGH — AI is never certain
    if present >= 3:
        return "LOW"
    return "LOW"


def urgency_from_signals(positive_signals: list) -> float:
    urgency_count = sum(1 for s in positive_signals if "urgency" in s)
    if urgency_count >= 2:
        return 0.9
    if urgency_count == 1:
        return 0.7
    return 0.4


def condition_from_warnings(warnings: list) -> float:
    if not warnings:
        return 0.75   # unknown but not flagged
    return max(0.2, 0.75 - len(warnings) * 0.1)


# ---------------------------------------------------------------------------
# Full scoring pipeline
# ---------------------------------------------------------------------------
def score_listing(listing: dict, warnings: list, positive_signals: list, estimated_market: float) -> dict:
    """Return legacy/support scores.

    Decision-impacting today:
    - model_specific_issues: passed to dealer_engine as model_risks.
    - estimated_margin: fallback DB metric and optional paid-AI context.

    Informational only today:
    - freshness_score, price_score, condition_score, urgency_score.
    - legacy final_score, legacy verdict, legacy confidence.
    - legacy risk_score/liquidity_score are superseded in scanner.py by
      dealer/AI-derived values before saving Listing.
    """
    age_min = listing.get("listing_age_minutes")
    price = listing.get("price", 0) or 0
    mileage = listing.get("mileage", 0) or 0
    year = listing.get("year", 2010) or 2010
    brand = listing.get("brand", "")
    engine = listing.get("engine", "")

    fresh = freshness_score(age_min)
    p_score, margin, underval = price_score(price, estimated_market)
    model_risks = get_model_risks(brand, engine, mileage)
    risk = risk_score_from_signals(warnings, model_risks, mileage, year)
    liq = liquidity_score(brand)
    urgency = urgency_from_signals(positive_signals)
    condition = condition_from_warnings(warnings)

    final = calculate_final_score(fresh, p_score, condition, risk, liq, urgency)
    verdict = verdict_from_score(final, risk)
    confidence = confidence_from_data(listing)

    return {
        "freshness_score": fresh,
        "price_score": p_score,
        "condition_score": condition,
        "risk_score": risk,
        "liquidity_score": liq,
        "urgency_score": urgency,
        "final_score": final,
        "estimated_market_price": estimated_market,
        "estimated_margin": margin,
        "undervaluation_pct": underval,
        "verdict": verdict,
        "confidence": confidence,
        "model_specific_issues": model_risks,
    }