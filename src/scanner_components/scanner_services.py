from __future__ import annotations

import csv
import json
import logging
import os
import re
from datetime import datetime
from typing import Optional

from src.learned_market import build_learned_market, estimate_from_learned_market
from src.models import Listing
from src.utils import (
    DURABLE_JAPANESE,
    DURABLE_KOREAN,
    MASS_MARKET,
    PREMIUM_BRANDS,
    VERY_DURABLE_WORKHORSES,
    apply_risky_penalty_and_append,
)

logger = logging.getLogger("autohawk.scanner.services")


class CandidatePolicy:
    """Owns deterministic rules for accepting raw listings into the pipeline."""

    def __init__(self, config: dict):
        self.config = config

    def adaptive_limits(self, raw: dict) -> tuple[int, int, str]:
        brand = (raw.get("brand") or "").lower()
        model = (raw.get("model") or "").lower()
        engine = (raw.get("engine") or "").lower()
        fuel = (raw.get("fuel") or "").lower()
        gearbox = (raw.get("gearbox") or "").lower()
        text = " ".join([
            str(raw.get("title") or ""), str(raw.get("description") or ""),
            brand, model, engine, fuel, gearbox,
        ]).lower()

        min_year = int(self.config.get("min_year", 2000))
        max_mileage = int(self.config.get("max_mileage", 250000))
        profile = "default"
        if brand in DURABLE_JAPANESE:
            max_mileage, profile = 300000, "durable_japanese"
        elif brand in DURABLE_KOREAN:
            max_mileage, profile = 270000, "durable_korean"
        elif brand in MASS_MARKET:
            max_mileage, profile = 250000, "mass_market"
        elif brand in PREMIUM_BRANDS:
            if brand == "audi" and any(value in model for value in ["a3", "a4"]):
                max_mileage = int(self.config.get("audi_a3_a4_max_mileage", 230000))
                profile = "audi_liquid_compact"
            else:
                max_mileage, profile = 190000, "premium_strict"

        max_mileage, profile = apply_risky_penalty_and_append(profile, max_mileage, text)
        if any(brand_name in brand and model_name in model for brand_name, model_name in VERY_DURABLE_WORKHORSES):
            max_mileage = max(max_mileage, 260000)
            if brand in DURABLE_JAPANESE:
                max_mileage = max(max_mileage, 300000)
        return max_mileage, min_year, profile

    def quality_issue(self, raw: dict) -> Optional[str]:
        title = (raw.get("title") or "").strip()
        price, brand, model = raw.get("price"), raw.get("brand"), raw.get("model")
        year, mileage, age_min = raw.get("year"), raw.get("mileage"), raw.get("listing_age_minutes")
        max_mileage, min_year_limit, mileage_profile = self.adaptive_limits(raw)
        min_age = self.config.get("freshness_min_minutes", 1)
        max_age = self.config.get("freshness_max_minutes", self.config.get("freshness_max_hours", 12) * 60)
        if self.config.get("require_price", True) and not price:
            return "missing price"
        if self.config.get("require_brand_model", True) and (not brand or not model):
            return "missing brand/model"
        if self.config.get("require_year", False) and not year:
            return "missing year"
        if self.config.get("require_mileage", False) and not mileage:
            return "missing mileage"
        if self.config.get("require_listing_age", False) and age_min is None:
            return "missing listing age"
        if self.config.get("require_detail_verified", False) and not raw.get("detail_verified"):
            return "detail page not verified"
        if raw.get("hard_reject"):
            return raw.get("detail_error") or "hard reject wording"
        if raw.get("data_quality_warnings") and self.config.get("reject_data_quality_warnings", False):
            return "data quality: " + ", ".join(raw.get("data_quality_warnings", [])[:3])
        max_model_year = int(self.config.get("max_model_year", datetime.now().year - 1))
        if year and year > max_model_year:
            return f"suspicious/future model year ({year}, max {max_model_year})"
        if year and price and year >= datetime.now().year - 1 and float(price) < 5000:
            return f"suspicious model year/price mismatch ({year} for {price} EUR)"
        absolute_max = int(self.config.get("absolute_mileage_reject_km", 300000))
        if mileage and mileage > absolute_max:
            return f"mileage above absolute limit ({absolute_max} km)"
        if mileage and mileage > max_mileage:
            return f"mileage above adaptive limit ({mileage_profile}, max {max_mileage})"
        if year and year < min_year_limit:
            return f"older than adaptive min year ({min_year_limit})"
        if mileage and year and datetime.now().year - year >= 8 and mileage < 20000:
            return "implausibly low mileage for age"
        if len(title) < 5:
            return "title too short"
        if age_min is not None and age_min < min_age:
            return "too new / unstable age"
        if age_min is not None and age_min > max_age:
            return "older than freshness window"
        return None


class OutputSafetyPolicy:
    """Owns the final safety rules shared by external output channels."""

    BLOCKED_TERMS = (
        "motor unruhig", "unruhiger motor", "motor laeuft unruhig", "motor lÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¤uft unruhig", "motorproblem",
        "motor problem", "motorschaden", "motor defekt", "motorkontrollleuchte",
        "motor kontrollleuchte", "kontrollleuchte", "motorlampe", "check engine", "mkl",
        "lambdasonde", "oelstandsensor", "olstandsensor", "standsensor defekt",
        "getriebeschaden", "getriebe defekt", "bastler", "export", "fahrzeugankauf",
        "suche kaufe", "wir kaufen", "ankauf", "nur tausch", "leasingÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¼bernahme", "leasinguebernahme",
        "kein kfz brief", "kein brief", "ohne brief", "brief fehlt", "keine papiere", "ohne papiere",
    )

    def __init__(self, config: dict, candidate_policy: CandidatePolicy):
        self.config = config
        self.candidate_policy = candidate_policy

    def blocked_reason(self, listing: Listing) -> Optional[str]:
        text = f"{listing.title or ''} {listing.description or ''}".lower()
        text = re.sub(r"\bunfall\s+frei\b", "unfallfrei", text)
        if self.config.get("require_listing_age_for_telegram", True):
            if listing.listing_age_minutes is None:
                return "missing listing age for Telegram"
            min_age = int(self.config.get("freshness_min_minutes", 0))
            max_age = int(self.config.get("freshness_max_minutes", self.config.get("freshness_max_hours", 12) * 60))
            if listing.listing_age_minutes < min_age:
                return "too new / unstable listing age for Telegram"
            if listing.listing_age_minutes > max_age:
                return "older than freshness window for Telegram"
        if self.config.get("require_mileage_for_telegram", True) and not listing.mileage:
            return "missing mileage for Telegram"
        if listing.mileage and listing.mileage > int(self.config.get("absolute_mileage_reject_km", 300000)):
            return "mileage above absolute output limit"
        if listing.mileage:
            limits = self.candidate_policy.adaptive_limits({
                "brand": listing.brand, "model": listing.model, "engine": listing.engine,
                "fuel": listing.fuel, "gearbox": listing.gearbox,
                "title": listing.title, "description": listing.description,
            })
            if listing.mileage > limits[0]:
                return f"mileage above adaptive Telegram limit ({limits[2]}, max {limits[0]})"
        if re.search(r"\bunfall(?!frei)\b", text, re.IGNORECASE):
            return "accident wording"
        for term in self.BLOCKED_TERMS:
            if term in text:
                return f"blocked term: {term}"
        return None


class MarketMemoryService:
    """Owns observed and learned market data used by the scanner."""

    def __init__(self, config: dict):
        self.config = config

    def refresh(self) -> dict:
        if not self.config.get("use_learned_market", True):
            return {}
        try:
            return build_learned_market(
                self.config.get("market_observations_file", "output/market_observations.csv"),
                self.config.get("learned_market_file", "output/learned_market.json"),
                self.config.get("learned_market_summary_file", "output/learned_market_summary.csv"),
                self.config.get("learned_market_quality_report_file", "output/learned_market_quality_report.csv"),
            )
        except Exception as exc:
            logger.debug(f"Learned market refresh failed: {exc}")
            return {}

    def estimate_observed(self, raw: dict) -> tuple[Optional[float], int]:
        path = self.config.get("market_observations_file", "output/market_observations.csv")
        if not os.path.exists(path):
            return None, 0
        norm = lambda value: str(value or "").lower().strip()
        def number(value, integer=False):
            try:
                parsed = float(str(value).replace(",", "."))
                return int(parsed) if integer else parsed
            except Exception:
                return None
        brand, model = norm(raw.get("brand")), norm(raw.get("model"))
        year, mileage = number(raw.get("year"), True), number(raw.get("mileage"), True)
        if not brand or not model or not year or not mileage:
            return None, 0
        candidates = []
        try:
            with open(path, "r", newline="", encoding="utf-8-sig") as fh:
                for row in csv.DictReader(fh):
                    if raw.get("url") and row.get("url") == raw.get("url"):
                        continue
                    if norm(row.get("brand")) != brand:
                        continue
                    row_model = norm(row.get("model"))
                    if row_model != model and model not in row_model and row_model not in model:
                        continue
                    price = number(row.get("price"))
                    row_year, row_mileage = number(row.get("year"), True), number(row.get("mileage"), True)
                    if price is None or not 800 <= price <= 50000 or not row_year or not row_mileage:
                        continue
                    if abs(row_year - year) > (2 if year >= 2015 else 3):
                        continue
                    if abs(row_mileage - mileage) > (40000 if year >= 2015 else 60000):
                        continue
                    candidates.append(price)
        except Exception as exc:
            logger.debug(f"Market observations read failed: {exc}")
            return None, 0
        if len(candidates) < int(self.config.get("min_market_observation_comps", 3)):
            return None, len(candidates)
        candidates.sort()
        if len(candidates) >= 7:
            trim = max(1, int(len(candidates) * 0.15))
            candidates = candidates[trim:-trim] or candidates
        middle = len(candidates) // 2
        median = candidates[middle] if len(candidates) % 2 else (candidates[middle - 1] + candidates[middle]) / 2
        return round(median, -2), len(candidates)


class ScanReportWriter:
    """Owns append-only CSV reports generated while scanning."""

    def __init__(self, config: dict, scan_number: callable):
        self.config = config
        self.scan_number = scan_number

    def append_rejection(self, source: str, raw: dict, reason: str) -> None:
        if not self.config.get("write_rejection_report", True):
            return
        path = self.config.get("rejection_report_file", "output/rejections.csv")
        fields = ["time", "scan", "source", "reason", "title", "price", "brand", "model", "year", "mileage", "age_minutes", "url"]
        row = {"time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "scan": self.scan_number(), "source": source, "reason": reason}
        row.update({key: raw.get(key) or "" for key in ["title", "price", "brand", "model", "year", "mileage", "url"]})
        row["age_minutes"] = raw.get("listing_age_minutes") if raw.get("listing_age_minutes") is not None else ""
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        exists = os.path.exists(path)
        with open(path, "a", newline="", encoding="utf-8-sig") as fh:
            writer = csv.DictWriter(fh, fieldnames=fields)
            if not exists:
                writer.writeheader()
            writer.writerow(row)

    def append_market_observation(self, source: str, raw: dict) -> None:
        if not self.config.get("write_market_observations", True) or not raw.get("price") or not raw.get("brand") or not raw.get("model"):
            return
        path = self.config.get("market_observations_file", "output/market_observations.csv")
        fields = ["time", "scan", "source", "title", "price", "brand", "model", "year", "mileage", "fuel", "gearbox", "age_minutes", "url"]
        row = {"time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "scan": self.scan_number(), "source": source}
        row.update({key: raw.get(key) or "" for key in ["title", "price", "brand", "model", "year", "mileage", "fuel", "gearbox", "url"]})
        row["age_minutes"] = raw.get("listing_age_minutes") if raw.get("listing_age_minutes") is not None else ""
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        exists = os.path.exists(path)
        with open(path, "a", newline="", encoding="utf-8-sig") as fh:
            writer = csv.DictWriter(fh, fieldnames=fields)
            if not exists:
                writer.writeheader()
            writer.writerow(row)