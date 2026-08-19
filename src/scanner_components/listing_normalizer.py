from __future__ import annotations

import hashlib
import json


def fingerprint(title: str, price, mileage, location: str) -> str:
    raw = f"{title}|{price}|{mileage}|{location}"
    return hashlib.md5(raw.encode()).hexdigest()[:16]


def build_listing(
    *,
    platform: str,
    platform_id: str,
    url: str,
    title: str,
    brand: str | None,
    model: str | None,
    price,
    mileage,
    year,
    fuel: str | None,
    gearbox: str | None,
    engine: str | None,
    tuv_info: dict,
    location: str,
    description: str,
    seller_type: str,
    listing_age_minutes,
    detail_verified: bool | None = None,
) -> dict:
    listing = {
        "platform": platform,
        "platform_id": platform_id,
        "stable_id": f"{platform}:{platform_id}" if platform_id else None,
        "fingerprint": fingerprint(title, price, mileage, location),
        "url": url,
        "title": title.strip(),
        "brand": brand,
        "model": model,
        "price": price,
        "mileage": mileage,
        "year": year,
        "fuel": fuel,
        "gearbox": gearbox,
        "engine": engine,
        "tuv_text": tuv_info.get("tuv_text"),
        "tuv_until": tuv_info.get("tuv_until"),
        "tuv_months_left": tuv_info.get("tuv_months_left"),
        "location": location.strip(),
        "description": description.strip(),
        "seller_type": seller_type,
        "listing_age_minutes": listing_age_minutes,
        "photo_urls": json.dumps([]),
    }
    if detail_verified is not None:
        listing["detail_verified"] = detail_verified
    return listing


def refresh_fingerprint(listing: dict) -> None:
    listing["fingerprint"] = fingerprint(
        listing.get("title", ""),
        listing.get("price"),
        listing.get("mileage"),
        listing.get("location", ""),
    )