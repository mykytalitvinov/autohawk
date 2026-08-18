from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta

from src.excel_writer import write_excel
from src.models import Listing
from src.scanner_components.scanner_services import OutputSafetyPolicy

logger = logging.getLogger("autohawk.export")
DEFAULT_EXPORT_VERDICTS = {"HOT", "GOOD", "CHECK"}


class ExportService:
    """Selects safe, useful listings and writes the Excel report."""

    def __init__(self, config: dict, session_factory, market_memory):
        self.config = config
        self.Session = session_factory
        self.market_memory = market_memory

    def update(self) -> list[Listing]:
        session = self.Session()
        try:
            export_verdicts = set(self.config.get("export_verdicts", list(DEFAULT_EXPORT_VERDICTS)))
            min_score = float(self.config.get("min_export_score", 0.64))
            max_rows = int(self.config.get("max_export_rows", 20))
            query = (
                session.query(Listing)
                .filter(Listing.verdict.in_(export_verdicts))
                .filter(Listing.final_score >= min_score)
                .filter(Listing.price.isnot(None))
                .filter((Listing.year.is_(None)) | (Listing.year <= int(self.config.get("max_model_year", datetime.now().year - 1))))
                .filter(
                    (Listing.estimated_market_price.is_(None))
                    | (Listing.price <= Listing.estimated_market_price * (1 + float(self.config.get("max_over_market_export_pct", 5)) / 100.0))
                )
                .filter(Listing.brand.isnot(None))
                .filter(Listing.model.isnot(None))
            )
            recent_hours = int(self.config.get("export_recent_hours", 24) or 0)
            if recent_hours > 0:
                query = query.filter(Listing.found_at >= datetime.utcnow() - timedelta(hours=recent_hours))
            if self.config.get("require_listing_age", False):
                query = query.filter(Listing.listing_age_minutes.isnot(None))
                query = query.filter(Listing.listing_age_minutes >= self.config.get("freshness_min_minutes", 1))
                query = query.filter(Listing.listing_age_minutes <= self.config.get("freshness_max_minutes", self.config.get("freshness_max_hours", 12) * 60))

            listings = query.order_by(Listing.final_score.desc(), Listing.found_at.desc()).limit(max_rows * 2).all()
            clean_export = []
            for listing in listings:
                title_text = f"{listing.title or ''} {listing.description or ''}".lower()
                title_text = re.sub(r"\bunfall\s+frei\b", "unfallfrei", title_text)
                if listing.mileage and listing.mileage > int(self.config.get("absolute_mileage_reject_km", 300000)):
                    continue
                if re.search(r"\bunfall(?!frei)\b", title_text, re.IGNORECASE) or any(term in title_text for term in OutputSafetyPolicy.BLOCKED_TERMS):
                    continue

                observed_market, observed_comps = self.market_memory.estimate_observed({
                    "brand": listing.brand, "model": listing.model, "year": listing.year,
                    "mileage": listing.mileage, "price": listing.price, "url": listing.url,
                })
                observed_discount_pct = None
                if observed_market and listing.price:
                    observed_discount_pct = (observed_market - listing.price) / observed_market * 100.0
                    max_over_market_pct = float(self.config.get("max_over_market_export_pct", 5))
                    if listing.price > observed_market * (1 + max_over_market_pct / 100.0):
                        logger.debug(
                            f"Excel skip above observed market: {listing.title[:60] if listing.title else ''} "
                            f"asking={listing.price:.0f} observed={observed_market:.0f} comps={observed_comps}"
                        )
                        continue

                if self.config.get("precision_export_mode", True) and listing.verdict == "CHECK":
                    min_check_discount = float(self.config.get("min_observed_discount_for_check_export", 7))
                    rescue_price_max = float(self.config.get("cheap_rescue_check_max_price", 1800))
                    rescue_score_min = float(self.config.get("cheap_rescue_min_score", 0.70))
                    market_verified_check = (
                        observed_discount_pct is not None
                        and observed_discount_pct >= min_check_discount
                        and observed_comps >= int(self.config.get("min_observed_comps_for_check", 3))
                    )
                    cheap_rescue_check = (
                        listing.price is not None and listing.price <= rescue_price_max
                        and listing.final_score is not None and listing.final_score >= rescue_score_min
                        and (listing.year is None or listing.year >= int(self.config.get("watchlist_min_year", 2004)))
                        and (listing.mileage is None or listing.mileage <= int(self.config.get("watchlist_max_mileage", 230000)))
                    )
                    if not market_verified_check and not cheap_rescue_check:
                        logger.debug(
                            f"Excel skip weak CHECK: {listing.title[:60] if listing.title else ''} "
                            f"discount={observed_discount_pct} comps={observed_comps} score={listing.final_score}"
                        )
                        continue

                min_margin = float(self.config.get("min_excel_margin_for_check", -500))
                if listing.verdict == "CHECK" and listing.estimated_margin is not None:
                    strong_rescue = (
                        listing.price is not None
                        and listing.price <= float(self.config.get("cheap_rescue_check_max_price", 1800))
                        and observed_discount_pct is not None
                        and observed_discount_pct >= float(self.config.get("strong_rescue_discount_pct", 20))
                    )
                    if listing.estimated_margin < min_margin and not strong_rescue:
                        logger.debug(
                            f"Excel skip weak margin: {listing.title[:60] if listing.title else ''} "
                            f"margin={listing.estimated_margin:.0f} discount={observed_discount_pct}"
                        )
                        continue

                clean_export.append(listing)
                if len(clean_export) >= max_rows:
                    break

            write_excel(clean_export, self.config.get("output_file", "output/deals.xlsx"))
            return list(clean_export)
        except Exception as exc:
            logger.error(f"Excel update failed: {exc}")
            return []
        finally:
            session.close()