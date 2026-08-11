"""
AUTOHAWK â€” Market Price Estimator
Estimates fair market value using heuristics when no live API is available.
Never crashes on missing data.
"""

import math
from datetime import datetime
from typing import Optional

from src.utils import norm as _normalize


# Base prices by brand/model (rough German market medians, 2024)
# These are starting points â€” adjusted by year and mileage
BASE_PRICES = {
    ("volkswagen", "golf"):       12000,
    ("volkswagen", "polo"):       9000,
    ("volkswagen", "fox"):        3500,
    ("volkswagen", "up"):         5500,
    ("volkswagen", "passat"):     14000,
    ("volkswagen", "tiguan"):     18000,
    ("volkswagen", "touran"):     13000,
    ("volkswagen", "t-roc"):      19000,
    ("bmw", "3er"):               18000,
    ("bmw", "1er"):               14000,
    ("bmw", "5er"):               22000,
    ("bmw", "x3"):                24000,
    ("bmw", "x1"):                18000,
    ("mercedes", "c-klasse"):     20000,
    ("mercedes", "a-klasse"):     16000,
    ("mercedes", "e-klasse"):     25000,
    ("mercedes", "glc"):          28000,
    ("audi", "a3"):               16000,
    ("audi", "a4"):               20000,
    ("audi", "a6"):               26000,
    ("audi", "q3"):               20000,
    ("audi", "q5"):               26000,
    ("opel", "astra"):            10000,
    ("opel", "corsa"):            8000,
    ("opel", "meriva"):           4500,
    ("opel", "zafira"):           6500,
    ("opel", "insignia"):         12000,
    ("ford", "focus"):            10000,
    ("ford", "fiesta"):           8000,
    ("ford", "kuga"):             15000,
    ("toyota", "yaris"):          9000,
    ("toyota", "corolla"):        14000,
    ("toyota", "rav4"):           20000,
    ("toyota", "auris"):          11000,
    ("skoda", "octavia"):         12000,
    ("skoda", "fabia"):           8000,
    ("skoda", "superb"):          17000,
    ("seat", "ibiza"):            8500,
    ("seat", "mii"):              5500,
    ("seat", "leon"):             11000,
    ("renault", "megane"):        9000,
    ("renault", "clio"):          7500,
    ("peugeot", "308"):           9000,
    ("peugeot", "208"):           8000,
    ("hyundai", "i30"):           10000,
    ("hyundai", "tucson"):        16000,
    ("kia", "ceed"):              10000,
    ("kia", "picanto"):           5500,
    ("kia", "venga"):             6500,
    ("kia", "soul"):              8000,
    ("kia", "sportage"):          16000,
    ("mazda", "3"):               13000,
    ("mazda", "cx-5"):            20000,
    ("honda", "civic"):           12000,
    ("mini", "mini"):             13000,
    ("volvo", "v40"):             13000,
    ("volvo", "xc60"):            22000,
    ("dacia", "duster"):          9000,
    ("dacia", "sandero"):         7000,
    ("fiat", "500"):              7500,
    ("alfa romeo", "giulietta"):  9000,
    ("porsche", "cayenne"):       38000,
    ("porsche", "macan"):         42000,
    ("mercedes", "glk"):          22000,
    ("mercedes", "b-klasse"):     13000,
    ("mercedes", "sprinter"):     26000,
    ("smart", "fortwo"):          9000,
    ("nissan", "qashqai"):        14000,
    ("mitsubishi", "colt"):       4500,
    ("skoda", "yeti"):            8500,
}


def _get_base_price(brand: str, model: str) -> Optional[float]:
    b = _normalize(brand)
    m = _normalize(model)

    # Try exact match first
    key = (b, m)
    if key in BASE_PRICES:
        return BASE_PRICES[key]

    # Try partial match on model
    for (kb, km), price in BASE_PRICES.items():
        if kb == b and (km in m or m in km):
            return price

    # Brand-only fallback averages
    brand_prices = [p for (kb, _), p in BASE_PRICES.items() if kb == b]
    if brand_prices:
        return sum(brand_prices) / len(brand_prices)

    return None


def _year_factor(year: Optional[int]) -> float:
    """Depreciation factor based on age."""
    if not year:
        return 0.75
    current_year = datetime.now().year
    age = current_year - year
    if age <= 0:
        return 1.0
    # Roughly 12% per year, diminishing
    factor = math.pow(0.90, min(age, 15))
    return max(0.32, factor)


def _mileage_factor(mileage: Optional[int]) -> float:
    """Mileage depreciation factor."""
    if not mileage:
        return 0.85
    if mileage < 20000:
        return 1.0
    if mileage < 50000:
        return 0.92
    if mileage < 100000:
        return 0.80
    if mileage < 150000:
        return 0.65
    if mileage < 200000:
        return 0.58
    return 0.48


def estimate_market_price(
    brand: str,
    model: str,
    year: Optional[int],
    mileage: Optional[int],
    fuel: Optional[str] = None,
    gearbox: Optional[str] = None,
) -> Optional[float]:
    """
    Heuristic market price estimate.
    Returns None if data is too incomplete to estimate.
    IMPORTANT: This is a rough estimate, not a valuation.
    """
    if not brand or not model:
        return None

    base = _get_base_price(brand, model)
    if not base:
        return None
    year_f = _year_factor(year)
    mileage_f = _mileage_factor(mileage)

    # Fuel adjustment
    fuel_l = _normalize(fuel)
    fuel_bonus = 1.0
    if "elektro" in fuel_l or "electric" in fuel_l:
        fuel_bonus = 1.15
    elif "hybrid" in fuel_l:
        fuel_bonus = 1.08
    elif "diesel" in fuel_l:
        fuel_bonus = 0.95  # diesel slightly lower demand in cities

    # Gearbox adjustment
    gear_l = _normalize(gearbox)
    gear_bonus = 1.0
    if "automatik" in gear_l or "automatic" in gear_l or "dsg" in gear_l:
        gear_bonus = 1.05

    estimated = base * year_f * mileage_f * fuel_bonus * gear_bonus

    # Conservative old-car floor.
    # The first version used a high "running car with TUV" floor and inflated
    # cheap Astra/Golf/Fiesta leads. For flipper logic, false margin is worse
    # than a missed optimistic estimate, so old high-mileage cars are valued
    # against their cheap local peers.
    b = _normalize(brand)
    m = _normalize(model)
    old_car_floor = 0
    if year and year <= 2014:
        small_models = {"fiesta", "yaris", "polo", "corsa", "clio", "micra", "i10", "i20", "fox", "up", "mii", "picanto"}
        compact_models = {"golf", "astra", "focus", "ceed", "i30", "fabia", "leon", "colt", "207", "208", "c3"}
        family_models = {"zafira", "meriva", "touran", "sharan", "passat", "octavia", "mondeo", "yeti"}
        mass_brands = {"ford", "opel", "volkswagen", "skoda", "seat", "toyota", "honda", "hyundai", "kia", "mazda", "nissan", "renault", "peugeot", "citroen", "mitsubishi", "suzuki"}

        if b in mass_brands:
            if any(x in m for x in small_models):
                old_car_floor = 1400
            elif any(x in m for x in compact_models):
                old_car_floor = 1500
            elif any(x in m for x in family_models):
                old_car_floor = 1600
            else:
                old_car_floor = 1500

            if year >= 2012:
                old_car_floor += 200
            elif year <= 2005:
                old_car_floor -= 200

            if mileage:
                if mileage <= 90000:
                    old_car_floor += 700
                elif mileage <= 130000:
                    old_car_floor += 400
                elif mileage <= 170000:
                    old_car_floor += 150
                elif mileage >= 220000:
                    old_car_floor -= 350
                elif mileage >= 200000:
                    old_car_floor -= 200

            if b in {"toyota", "honda"} and mileage and mileage <= 220000:
                old_car_floor += 250

            old_car_floor = max(900, old_car_floor)

        elif b in {"bmw", "mercedes", "audi", "mini"}:
            old_car_floor = 2200
            if year <= 2005:
                old_car_floor -= 300
            if mileage and mileage >= 220000:
                old_car_floor -= 600
            elif mileage and mileage <= 150000:
                old_car_floor += 400
            old_car_floor = max(1200, old_car_floor)

    if old_car_floor:
        estimated = max(estimated, old_car_floor)

    return round(estimated, -2)  # Round to nearest 100

