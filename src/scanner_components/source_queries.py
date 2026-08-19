from __future__ import annotations


def adaptive_max_mileage(config: dict) -> int:
    configured = config.get("max_mileage", 250000)
    return max(configured, 300000 if config.get("adaptive_age_mileage", True) else configured)


def kleinanzeigen_search_url(config: dict) -> str:
    price_min = config.get("budget_min", 500)
    price_max = config.get("budget_max", 15000)
    max_mileage = adaptive_max_mileage(config)
    return (
        f"https://www.kleinanzeigen.de/s-autos/preis:{price_min}:{price_max}/c216"
        f"?kmMax={max_mileage}&sortingField=SORTING_DATE"
    )


def autoscout24_search_url(config: dict) -> str:
    price_min = config.get("budget_min", 500)
    price_max = config.get("budget_max", 15000)
    max_mileage = adaptive_max_mileage(config)
    return (
        f"https://www.autoscout24.de/lst?sort=age&desc=0"
        f"&pricefrom={price_min}&priceto={price_max}"
        f"&kmto={max_mileage}&ustate=N%2CU"
    )
