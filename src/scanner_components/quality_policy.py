from __future__ import annotations

from datetime import datetime


def data_quality_warnings(listing: dict) -> list[str]:
    warnings = []
    year = listing.get("year")
    mileage = listing.get("mileage")
    price = listing.get("price")
    current_year = datetime.now().year

    if year and (year < 1980 or year > current_year):
        warnings.append("implausible year")
    if mileage is not None and (mileage < 5000 or mileage > 500000):
        warnings.append("implausible mileage")
    if year and mileage and current_year - year >= 15 and mileage < 50000:
        warnings.append("very low mileage for age - verify odometer/TUV history")
    elif year and mileage and current_year - year >= 8 and mileage < 20000:
        warnings.append("very low mileage for age")
    if price is not None and price < 800:
        warnings.append("very low price")
    if not listing.get("detail_verified"):
        warnings.append("detail page not verified")
    return warnings
