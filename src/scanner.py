"""
AUTOHAWK main scanner pipeline.

Architecture:
1. Scrape fresh listings.
2. Fast quality/junk rejection.
3. Deterministic dealer engine filters 95% of weak listings.
4. Optional paid AI only for finalists.
5. Export only strong leads to Excel.
"""

from __future__ import annotations

import asyncio
import csv
import json
from collections import Counter
import logging
import os
import re
import time
from datetime import datetime, timedelta
from typing import List, Optional

from sqlalchemy.exc import IntegrityError

from src.ai_analysis import ai_analyze, get_ai_scan_metrics, reset_ai_scan_metrics
from src.dealer_engine import analyze_dealer_candidate
from src.excel_writer import write_excel
from src.filter import quick_filter
from src.market import estimate_market_price
from src.learned_market import build_learned_market, estimate_from_learned_market
from src.models import Listing, init_db
from src.scoring import score_listing
from src.scrapers import scrape_autoscout24, scrape_kleinanzeigen
from src.sold_tracker import SoldTracker
from src.telegram_notifier import TelegramNotifier
from src.utils import (
    DURABLE_JAPANESE,
    DURABLE_KOREAN,
    MASS_MARKET,
    PREMIUM_BRANDS,
    VERY_DURABLE_WORKHORSES,
    apply_risky_penalty_and_append,
)

try:
    from src.best_of_scan_report import write_best_of_scan_report
except Exception:
    write_best_of_scan_report = None

logger = logging.getLogger("autohawk.scanner")

def _db_text(value) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, (list, tuple, set)):
        return "\n".join(f"- {str(item)}" for item in value if item is not None)
    if isinstance(value, dict):
        try:
            return json.dumps(value, ensure_ascii=False)
        except Exception:
            return str(value)
    return str(value)

DEFAULT_EXPORT_VERDICTS = {"HOT", "GOOD", "CHECK"}


class AutohawkScanner:
    def __init__(self, config: dict):
        self.config = config
        self.Session = init_db("database/autohawk.db")
        self.scan_count = 0
        self.learned_market = {}
        self._last_export_listings = []
        self.sold_tracker = SoldTracker(config, self.Session) if config.get("sold_tracker_enabled", True) else None
        self.telegram_notifier = None
        if config.get("telegram_enabled", True):
            try:
                self.telegram_notifier = TelegramNotifier(config)
                if self.telegram_notifier.ready():
                    logger.info("Telegram notifier enabled")
                else:
                    logger.info("Telegram notifier inactive: set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID to enable")
            except Exception as exc:
                logger.warning(f"Telegram notifier disabled after init error: {exc}")
        logger.info("AUTOHAWK Scanner initialized")


    def _refresh_learned_market(self):
        """Rebuild local market memory from observed listings."""
        if not self.config.get("use_learned_market", True):
            return
        observations = self.config.get("market_observations_file", "output/market_observations.csv")
        output_json = self.config.get("learned_market_file", "output/learned_market.json")
        summary_csv = self.config.get("learned_market_summary_file", "output/learned_market_summary.csv")
        quality_report_csv = self.config.get("learned_market_quality_report_file", "output/learned_market_quality_report.csv")
        try:
            self.learned_market = build_learned_market(observations, output_json, summary_csv, quality_report_csv)
            logger.info(
                "Learned market refreshed: "
                f"{len(self.learned_market.get('groups', {}))} buckets, "
                f"{self.learned_market.get('usable_rows', 0)} usable observations, "
                f"{self.learned_market.get('rejected_rows', 0)} rejected as noisy"
            )
        except Exception as exc:
            logger.debug(f"Learned market refresh failed: {exc}")

    def _get_or_skip(self, session, raw: dict) -> Optional[Listing]:
        stable_id = raw.get("stable_id")
        fingerprint = raw.get("fingerprint")
        if stable_id and session.query(Listing).filter_by(stable_id=stable_id).first():
            return None
        if fingerprint and session.query(Listing).filter_by(fingerprint=fingerprint).first():
            return None
        return True

    def _append_rejection_report(self, source: str, raw: dict, reason: str):
        if not self.config.get("write_rejection_report", True):
            return

        output_path = self.config.get("rejection_report_file", "output/rejections.csv")
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        exists = os.path.exists(output_path)
        fields = [
            "time",
            "scan",
            "source",
            "reason",
            "title",
            "price",
            "brand",
            "model",
            "year",
            "mileage",
            "age_minutes",
            "url",
        ]
        row = {
            "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "scan": self.scan_count,
            "source": source,
            "reason": reason,
            "title": raw.get("title") or "",
            "price": raw.get("price") or "",
            "brand": raw.get("brand") or "",
            "model": raw.get("model") or "",
            "year": raw.get("year") or "",
            "mileage": raw.get("mileage") or "",
            "age_minutes": raw.get("listing_age_minutes") if raw.get("listing_age_minutes") is not None else "",
            "url": raw.get("url") or "",
        }
        with open(output_path, "a", newline="", encoding="utf-8-sig") as fh:
            writer = csv.DictWriter(fh, fieldnames=fields)
            if not exists:
                writer.writeheader()
            writer.writerow(row)
    def _append_market_observation(self, source: str, raw: dict):
        """Store every raw listing price so future scans get a local comparable database."""
        if not self.config.get("write_market_observations", True):
            return

        price = raw.get("price")
        brand = raw.get("brand")
        model = raw.get("model")
        if not price or not brand or not model:
            return

        output_path = self.config.get("market_observations_file", "output/market_observations.csv")
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        exists = os.path.exists(output_path)
        fields = [
            "time", "scan", "source", "title", "price", "brand", "model",
            "year", "mileage", "fuel", "gearbox", "age_minutes", "url",
        ]
        row = {
            "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "scan": self.scan_count,
            "source": source,
            "title": raw.get("title") or "",
            "price": price,
            "brand": brand,
            "model": model,
            "year": raw.get("year") or "",
            "mileage": raw.get("mileage") or "",
            "fuel": raw.get("fuel") or "",
            "gearbox": raw.get("gearbox") or "",
            "age_minutes": raw.get("listing_age_minutes") if raw.get("listing_age_minutes") is not None else "",
            "url": raw.get("url") or "",
        }
        with open(output_path, "a", newline="", encoding="utf-8-sig") as fh:
            writer = csv.DictWriter(fh, fieldnames=fields)
            if not exists:
                writer.writeheader()
            writer.writerow(row)

    def _estimate_observed_market_price(self, raw: dict) -> tuple[Optional[float], int]:
        """Estimate from locally observed comparable listings, not just formula."""
        output_path = self.config.get("market_observations_file", "output/market_observations.csv")
        if not os.path.exists(output_path):
            return None, 0

        def norm_value(value):
            return str(value or "").lower().strip()

        def to_float(value):
            try:
                return float(str(value).replace(",", "."))
            except Exception:
                return None

        def to_int(value):
            try:
                return int(float(str(value).replace(",", ".")))
            except Exception:
                return None

        brand = norm_value(raw.get("brand"))
        model = norm_value(raw.get("model"))
        year = to_int(raw.get("year"))
        mileage = to_int(raw.get("mileage"))
        current_url = raw.get("url") or ""
        if not brand or not model or not year or not mileage:
            return None, 0

        candidates: list[float] = []
        try:
            with open(output_path, "r", newline="", encoding="utf-8-sig") as fh:
                reader = csv.DictReader(fh)
                for row in reader:
                    if current_url and row.get("url") == current_url:
                        continue
                    if norm_value(row.get("brand")) != brand:
                        continue
                    row_model = norm_value(row.get("model"))
                    if row_model != model and model not in row_model and row_model not in model:
                        continue

                    price = to_float(row.get("price"))
                    if price is None or price < 800 or price > 50000:
                        continue

                    row_year = to_int(row.get("year"))
                    row_mileage = to_int(row.get("mileage"))
                    if not row_year or not row_mileage:
                        continue
                    year_tolerance = 2 if year >= 2015 else 3
                    mileage_tolerance = 40000 if year >= 2015 else 60000
                    if abs(row_year - year) > year_tolerance:
                        continue
                    if abs(row_mileage - mileage) > mileage_tolerance:
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

        mid = len(candidates) // 2
        if len(candidates) % 2:
            median_price = candidates[mid]
        else:
            median_price = (candidates[mid - 1] + candidates[mid]) / 2

        return round(median_price, -2), len(candidates)

    def _adaptive_limits(self, raw: dict) -> tuple[int, int, str]:
        """Return (max_mileage, min_year, profile) before dealer scoring."""
        brand = (raw.get("brand") or "").lower()
        model = (raw.get("model") or "").lower()
        engine = (raw.get("engine") or "").lower()
        fuel = (raw.get("fuel") or "").lower()
        gearbox = (raw.get("gearbox") or "").lower()
        text = " ".join([
            str(raw.get("title") or ""),
            str(raw.get("description") or ""),
            brand,
            model,
            engine,
            fuel,
            gearbox,
        ]).lower()

        min_year = int(self.config.get("min_year", 2000))
        default_max = int(self.config.get("max_mileage", 250000))
        max_mileage = default_max
        profile = "default"

        if brand in DURABLE_JAPANESE:
            max_mileage = 300000
            profile = "durable_japanese"
        elif brand in DURABLE_KOREAN:
            max_mileage = 270000
            profile = "durable_korean"
        elif brand in MASS_MARKET:
            max_mileage = 250000
            profile = "mass_market"
        elif brand in PREMIUM_BRANDS:
            if brand == "audi" and any(x in model for x in ["a3", "a4"]):
                max_mileage = int(self.config.get("audi_a3_a4_max_mileage", 230000))
                profile = "audi_liquid_compact"
            else:
                max_mileage = 190000
                profile = "premium_strict"

        max_mileage, profile = apply_risky_penalty_and_append(profile, max_mileage, text)

        if any(b in brand and m in model for b, m in VERY_DURABLE_WORKHORSES):
            max_mileage = max(max_mileage, 260000)
            if brand in DURABLE_JAPANESE:
                max_mileage = max(max_mileage, 300000)

        return max_mileage, min_year, profile

    def _candidate_quality_issue(self, raw: dict) -> Optional[str]:
        title = (raw.get("title") or "").strip()
        price = raw.get("price")
        brand = raw.get("brand")
        model = raw.get("model")
        year = raw.get("year")
        mileage = raw.get("mileage")
        age_min = raw.get("listing_age_minutes")
        max_mileage, min_year_limit, mileage_profile = self._adaptive_limits(raw)
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
        absolute_max_mileage = int(self.config.get("absolute_mileage_reject_km", 300000))
        if mileage and mileage > absolute_max_mileage:
            return f"mileage above absolute limit ({absolute_max_mileage} km)"
        if mileage and max_mileage and mileage > max_mileage:
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

    def _is_blocked_for_output(self, listing: Listing) -> Optional[str]:
        """Final safety gate shared by Excel-like output and Telegram delivery."""
        blocked_terms = [
            "motor unruhig", "unruhiger motor", "motor laeuft unruhig", "motor lÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¤uft unruhig",
            "motorproblem", "motor problem", "motorschaden", "motor defekt",
            "motorkontrollleuchte", "motor kontrollleuchte", "kontrollleuchte", "motorlampe", "check engine", "mkl",
            "lambdasonde", "oelstandsensor", "olstandsensor", "standsensor defekt",
            "getriebeschaden", "getriebe defekt", "bastler", "export", "fahrzeugankauf",
            "suche kaufe", "wir kaufen", "ankauf", "nur tausch", "leasingÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¼bernahme", "leasinguebernahme",
            "kein kfz brief", "kein brief", "ohne brief", "brief fehlt", "keine papiere", "ohne papiere",
        ]
        title_text = f"{listing.title or ''} {listing.description or ''}".lower()
        title_text = re.sub(r"\bunfall\s+frei\b", "unfallfrei", title_text)
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
            max_mileage, _min_year, mileage_profile = self._adaptive_limits({
                "brand": listing.brand,
                "model": listing.model,
                "engine": listing.engine,
                "fuel": listing.fuel,
                "gearbox": listing.gearbox,
                "title": listing.title,
                "description": listing.description,
            })
            if max_mileage and listing.mileage > max_mileage:
                return f"mileage above adaptive Telegram limit ({mileage_profile}, max {max_mileage})"
        if re.search(r"\bunfall(?!frei)\b", title_text, re.IGNORECASE):
            return "accident wording"
        for term in blocked_terms:
            if term in title_text:
                return f"blocked term: {term}"
        return None

    def _send_pending_telegram_from_db(self) -> int:
        """Send Telegram alerts from persisted SQLite listings and update DB state."""
        if not self.telegram_notifier:
            return 0
        if not self.telegram_notifier.ready():
            logger.debug("Telegram DB queue inactive: missing TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID")
            return 0

        max_per_scan = int(self.config.get("telegram_max_per_scan", 5))
        max_attempts = int(self.config.get("telegram_max_attempts", 3))
        recent_hours = int(self.config.get("telegram_queue_recent_hours", self.config.get("export_recent_hours", 24)) or 0)
        verdicts = set(self.config.get("telegram_verdicts", ["HOT", "GOOD", "CHECK"]))
        min_score = float(self.config.get("telegram_min_score", 0.70))

        session = self.Session()
        sent_count = 0
        failed_count = 0
        skipped_count = 0
        try:
            query = (
                session.query(Listing)
                .filter(Listing.verdict.in_(verdicts))
                .filter(Listing.final_score >= min_score)
                .filter(Listing.price.isnot(None))
                .filter((Listing.is_junk.is_(False)) | (Listing.is_junk.is_(None)))
                .filter((Listing.telegram_status.is_(None)) | (Listing.telegram_status.in_(["pending", "failed"])))
                .filter((Listing.telegram_attempts.is_(None)) | (Listing.telegram_attempts < max_attempts))
            )
            if recent_hours > 0:
                query = query.filter(Listing.found_at >= datetime.utcnow() - timedelta(hours=recent_hours))
            if self.config.get("require_listing_age", False):
                query = query.filter(Listing.listing_age_minutes.isnot(None))
                query = query.filter(Listing.listing_age_minutes >= self.config.get("freshness_min_minutes", 1))
                query = query.filter(
                    Listing.listing_age_minutes
                    <= self.config.get("freshness_max_minutes", self.config.get("freshness_max_hours", 12) * 60)
                )

            candidates = (
                query.order_by(Listing.final_score.desc(), Listing.found_at.desc())
                .limit(max_per_scan * 4)
                .all()
            )

            for listing in candidates:
                if sent_count >= max_per_scan:
                    break

                now = datetime.utcnow()
                block_reason = self._is_blocked_for_output(listing)
                if block_reason:
                    listing.telegram_status = "skipped"
                    listing.telegram_error = block_reason
                    listing.telegram_last_attempt_at = now
                    session.commit()
                    skipped_count += 1
                    continue

                listing.telegram_attempts = int(listing.telegram_attempts or 0) + 1
                listing.telegram_last_attempt_at = now
                ok, error = self.telegram_notifier.post_listing(listing)
                if ok:
                    listing.telegram_status = "sent"
                    listing.telegram_sent_at = now
                    listing.telegram_error = None
                    sent_count += 1
                else:
                    listing.telegram_status = "failed"
                    listing.telegram_error = str(error or "unknown")[:500]
                    failed_count += 1
                session.commit()

            if sent_count or failed_count or skipped_count:
                logger.info(
                    "Telegram DB queue: "
                    f"sent={sent_count}, failed={failed_count}, skipped={skipped_count}"
                )
            return sent_count
        except Exception as exc:
            session.rollback()
            logger.warning(f"Telegram DB queue failed: {exc}")
            return sent_count
        finally:
            session.close()

    def _maybe_paid_ai(
        self,
        raw: dict,
        warnings: list[str],
        positive_signals: list[str],
        model_risks: list[str],
        market_price: float | None,
        margin: float,
        dealer_ai: dict,
    ) -> dict:
        if not self.config.get("use_paid_ai_for_finalists", False):
            return dealer_ai

        min_for_ai = int(self.config.get("paid_ai_min_dealer_score", 82))
        dealer_score = int(dealer_ai.get("opportunity_score") or 0)
        if dealer_score < min_for_ai:
            return dealer_ai

        provider = os.getenv("AI_PROVIDER", "rule").strip().lower()
        if provider in {"", "rule", "none", "local"}:
            return dealer_ai

        ai = ai_analyze(
            listing=raw,
            warnings=warnings,
            positive_signals=positive_signals,
            model_risks=model_risks,
            market_price=market_price or 0,
            margin=margin,
            config=self.config,
        )
        ai_score = ai.get("opportunity_score")
        ai_action = (ai.get("recommended_action") or "").upper()
        if ai_score is None or not ai_action:
            logger.info("Paid AI did not return gate score/action.")
            if self.config.get("require_paid_ai_success_for_export", True):
                return {
                    **dealer_ai,
                    "opportunity_score": 0,
                    "recommended_action": "REJECT",
                    "analysis_provider": provider,
                    "ai_summary": "Rejected because paid AI did not return a valid score/action.",
                }
            return dealer_ai

        ai["analysis_provider"] = ai.get("analysis_provider") or provider
        return ai

    def _process_listing(self, session, raw: dict) -> Optional[Listing]:
        try:
            quality_issue = self._candidate_quality_issue(raw)
            if quality_issue:
                raw["_skip_reason"] = quality_issue
                logger.debug(f"SKIP quality: {raw.get('title', '')[:60]} - {quality_issue}")
                return None

            is_junk, junk_reason, warnings, positive_signals = quick_filter(raw)
            if is_junk:
                raw["_skip_reason"] = f"quick filter: {junk_reason}"
                logger.debug(f"SKIP junk: {raw.get('title', '')[:60]} - {junk_reason}")
                return None

            market_price = estimate_market_price(
                brand=raw.get("brand"),
                model=raw.get("model"),
                year=raw.get("year"),
                mileage=raw.get("mileage"),
                fuel=raw.get("fuel"),
                gearbox=raw.get("gearbox"),
            )
            observed_market_price, observed_comps = self._estimate_observed_market_price(raw)
            learned_market_price, learned_comps, learned_key = estimate_from_learned_market(
                raw,
                self.learned_market,
                min_bucket_count=int(self.config.get("min_learned_bucket_count", 3)),
                min_model_count=int(self.config.get("min_learned_model_count", 8)),
            )
            if learned_market_price:
                raw["_market_learned_price"] = learned_market_price
                raw["_market_learned_comps"] = learned_comps
                raw["_market_learned_key"] = learned_key
                if not observed_market_price or observed_comps < int(self.config.get("min_market_observation_comps", 3)):
                    observed_market_price = learned_market_price
                    observed_comps = learned_comps
                elif market_price:
                    # If learned statistics and live comparable scan disagree, use the lower value.
                    # For flipping, conservative price truth is better than fake margin.
                    observed_market_price = min(observed_market_price, learned_market_price * 1.08)
            if observed_market_price:
                raw["_market_observed_comps"] = observed_comps
                raw["_market_observed_price"] = observed_market_price
                raw["_market_price_source"] = "observed_market"
                if market_price:
                    # Conservative rule: local observed analogs cap the heuristic.
                    # This prevents fake margins like Astra market 2600 when live peers are 1500-1600.
                    cap = observed_market_price * 1.12
                    market_price = min(market_price, cap)
                else:
                    market_price = observed_market_price
            asking_price = raw.get("price") or 0
            if market_price and asking_price:
                # First suppress obviously bad estimates. A car listed at 9800 with
                # market 2800 is usually an estimation/data problem, not proof it is above market.
                if market_price < asking_price * 0.55 or market_price > asking_price * 2.5:
                    logger.debug(
                        f"Market estimate suppressed: {raw.get('title', '')[:50]} asking={asking_price} estimate={market_price}"
                    )
                    market_price = None

            market_gate_allowed = True
            if self.config.get("require_mileage_for_above_market_gate", True) and not raw.get("mileage"):
                market_gate_allowed = False
                raw["_market_gate_skipped_reason"] = "missing mileage"
            learned_key_for_gate = str(raw.get("_market_learned_key") or "")
            if learned_key_for_gate.endswith("|unknown"):
                market_gate_allowed = False
                raw["_market_gate_skipped_reason"] = "unknown mileage market bucket"

            if market_price and asking_price and market_gate_allowed:
                max_over_market_pct = float(self.config.get("max_over_market_export_pct", 5))
                if asking_price > market_price * (1 + max_over_market_pct / 100.0):
                    raw["_skip_reason"] = (
                        f"above market gate: asking={asking_price:.0f} market={market_price:.0f} "
                        f"max_over={max_over_market_pct:.0f}%"
                    )
                    logger.debug(f"SKIP above market: {raw.get('title', '')[:60]} asking={asking_price} market={market_price}")
                    return None
            elif market_price and asking_price:
                logger.debug(
                    "Above-market gate not applied because market confidence is low: "
                    f"{raw.get('title', '')[:60]} asking={asking_price} market={market_price} "
                    f"reason={raw.get('_market_gate_skipped_reason', 'unknown')}"
                )

            # Legacy/support scoring. It does not decide HOT/GOOD/CHECK.
            # Main decision gate is dealer_engine below. From base_scores, only
            # model_specific_issues feeds dealer_engine directly; estimated_margin
            # is fallback/potential paid-AI context. Other base score fields are
            # persisted as diagnostic metrics only.
            base_scores = score_listing(raw, warnings, positive_signals, market_price or 0)
            model_risks = base_scores.get("model_specific_issues", [])

            dealer_ai = analyze_dealer_candidate(
                listing=raw,
                warnings=warnings,
                positive_signals=positive_signals,
                model_risks=model_risks,
                market_price=market_price,
                config=self.config,
            )

            dealer_score = int(dealer_ai.get("opportunity_score") or 0)
            dealer_action = (dealer_ai.get("recommended_action") or "").upper()
            min_dealer_score = int(self.config.get("min_dealer_score", 64))
            reject_actions = set(self.config.get("ai_reject_actions", ["REJECT", "IGNORE", "SKIP", "LOW_PRIORITY"]))
            accepted_actions = set(self.config.get("accepted_ai_actions", ["BUY_CANDIDATE", "FAST_VERIFY", "INSPECTION_ONLY"]))

            if dealer_action in reject_actions or dealer_score < min_dealer_score:
                raw["_skip_reason"] = (
                    f"dealer gate: score={dealer_score}/100 action={dealer_action or '?'} "
                    f"required_score={min_dealer_score}"
                )
                logger.debug(
                    f"Dealer gate rejected: {raw.get('title', '')[:60]} score={dealer_score} action={dealer_action}"
                )
                return None
            if dealer_action not in accepted_actions:
                raw["_skip_reason"] = f"dealer action not accepted: {dealer_action or '?'}"
                logger.debug(
                    f"Dealer action not accepted: {raw.get('title', '')[:60]} action={dealer_action}"
                )
                return None

            provider = os.getenv("AI_PROVIDER", "rule").strip().lower()
            strict_ai_mode = (
                self.config.get("use_paid_ai_for_finalists", False)
                and provider not in {"", "rule", "none", "local"}
                and self.config.get("require_paid_ai_success_for_export", True)
            )
            if strict_ai_mode:
                paid_ai_min = int(self.config.get("paid_ai_min_dealer_score", 82))
                if dealer_score < paid_ai_min:
                    raw["_skip_reason"] = (
                        f"strict AI shortlist: local_score={dealer_score}/100 required={paid_ai_min}"
                    )
                    logger.debug(
                        f"Strict shortlist rejected before paid AI: {raw.get('title', '')[:60]} "
                        f"dealer_score={dealer_score} required={paid_ai_min}"
                    )
                    return None
                logger.info(
                    f"   AI SHORTLIST | local_score={dealer_score}/100 | "
                    f"{raw.get('brand')} {raw.get('model')} {raw.get('year')} | {provider.upper()} analysis..."
                )

            ai = self._maybe_paid_ai(
                raw=raw,
                warnings=warnings,
                positive_signals=positive_signals,
                model_risks=model_risks,
                market_price=market_price,
                margin=base_scores.get("estimated_margin", 0),
                dealer_ai=dealer_ai,
            )

            ai_score = int(ai.get("opportunity_score") or dealer_score)
            ai_action = (ai.get("recommended_action") or dealer_action).upper()
            observed_comps_for_gate = int(raw.get("_market_observed_comps") or 0)
            min_observed_for_good = int(self.config.get("min_observed_comps_for_good", 3))
            requires_market_proof = bool(self.config.get("good_requires_observed_market", True))
            if requires_market_proof and ai_score >= 68 and (not market_price or observed_comps_for_gate < min_observed_for_good):
                ai_score = min(ai_score, int(self.config.get("unverified_market_score_cap", 72)))
                ai_action = "INSPECTION_ONLY"
                ai["recommended_action"] = ai_action
                ai["opportunity_type"] = "WATCHLIST_CHECK_MANUALLY"
                ai["price_view"] = (
                    str(ai.get("price_view") or "")
                    + f" | Market not verified enough: {observed_comps_for_gate} comparable observations. "
                    + "No GOOD/HOT without market proof."
                ).strip()

            if not raw.get("mileage") and ai_score > int(self.config.get("missing_mileage_score_cap", 62)):
                ai_score = int(self.config.get("missing_mileage_score_cap", 62))
                ai_action = "INSPECTION_ONLY"
                ai["recommended_action"] = ai_action
                ai["opportunity_type"] = "WATCHLIST_CHECK_MANUALLY"
                ai["possible_risks"] = (
                    str(ai.get("possible_risks") or "")
                    + "\n- Mileage missing: do not treat as HOT until Kilometerstand is confirmed."
                ).strip()
                ai["what_to_check"] = (
                    str(ai.get("what_to_check") or "")
                    + "\n- Confirm exact Kilometerstand from listing details/photo/TUV report."
                ).strip()

            if self.config.get("use_ai_gate", True):
                min_ai_score = int(self.config.get("min_ai_opportunity_score", min_dealer_score))
                if ai_score < min_ai_score:
                    raw["_skip_reason"] = f"final AI gate: score={ai_score}/100 required={min_ai_score}"
                    logger.debug(f"Final gate rejected: {raw.get('title', '')[:60]} score={ai_score}")
                    return None
                if ai_action in reject_actions:
                    raw["_skip_reason"] = f"final AI gate: action={ai_action}"
                    logger.debug(f"Final gate rejected: {raw.get('title', '')[:60]} action={ai_action}")
                    return None
                if ai_action and ai_action not in accepted_actions:
                    raw["_skip_reason"] = f"final AI action not accepted: {ai_action}"
                    logger.debug(f"Final action not accepted: {raw.get('title', '')[:60]} action={ai_action}")
                    return None

            # Persisted final_score/verdict come from dealer_engine or optional
            # paid AI. They intentionally do not use scoring.py's legacy
            # final_score/verdict.
            final_score = round(ai_score / 100.0, 3)
            opportunity_type = (ai.get("opportunity_type") or "").upper()
            # WATCHLIST / inspection-only leads are useful, but they are not GOOD deals yet.
            # They must stay CHECK until a human/Gemini confirms real under-market price and condition.
            market_verified_for_good = (
                not self.config.get("good_requires_observed_market", True)
                or int(raw.get("_market_observed_comps") or 0) >= int(self.config.get("min_observed_comps_for_good", 3))
            )
            if ai_action == "INSPECTION_ONLY" or opportunity_type == "WATCHLIST_CHECK_MANUALLY":
                verdict = "CHECK"
            elif ai_score >= 82 and market_verified_for_good:
                verdict = "HOT"
            elif ai_score >= 68 and market_verified_for_good:
                verdict = "GOOD"
            else:
                verdict = "CHECK"

            margin = ai.get("net_profit_estimate") if ai.get("net_profit_estimate") is not None else base_scores.get("estimated_margin")
            undervaluation_pct = base_scores.get("undervaluation_pct")
            estimated_market_price = market_price
            confidence = "MEDIUM" if raw.get("year") and raw.get("mileage") and raw.get("detail_verified") else "LOW"

            ai_summary_parts = [
                f"Provider: {ai.get('analysis_provider', 'local_dealer_engine')}",
                f"Opportunity score: {ai_score}/100",
                f"Action: {ai_action}",
            ]
            for key in ["profit_logic", "negotiation_angle", "model_market_reputation", "photo_analysis", "dashboard_warning_lights", "price_view", "score_breakdown", "estimated_costs", "risk_reserve", "net_profit_estimate", "ai_summary"]:
                value = ai.get(key)
                if value:
                    ai_summary_parts.append(str(value))
            ai_summary = " | ".join(ai_summary_parts)

            listing = Listing(
                stable_id=raw.get("stable_id"),
                fingerprint=raw.get("fingerprint"),
                platform=raw.get("platform"),
                platform_id=raw.get("platform_id"),
                url=raw.get("url"),
                title=raw.get("title"),
                brand=raw.get("brand"),
                model=raw.get("model"),
                year=raw.get("year"),
                mileage=raw.get("mileage"),
                price=raw.get("price"),
                fuel=raw.get("fuel"),
                gearbox=raw.get("gearbox"),
                engine=raw.get("engine"),
                tuv_text=raw.get("tuv_text"),
                tuv_until=raw.get("tuv_until"),
                tuv_months_left=raw.get("tuv_months_left"),
                location=raw.get("location"),
                description=raw.get("description"),
                seller_type=raw.get("seller_type"),
                photo_urls=raw.get("photo_urls"),
                found_at=datetime.utcnow(),
                listing_age_minutes=raw.get("listing_age_minutes"),
                # Informational-only legacy metrics from scoring.py. They are useful
                # for debugging/export context, but they are not the product gate.
                freshness_score=base_scores.get("freshness_score", 0),
                price_score=base_scores.get("price_score", 0),
                condition_score=base_scores.get("condition_score", 0),
                risk_score=max(0.0, 1.0 - ((0 if ai.get("risk_score") == "LOW" else 0.28 if ai.get("risk_score") == "MEDIUM" else 0.55))),
                liquidity_score=(float(ai.get("liquidity_score") or 0) / 100.0),
                urgency_score=base_scores.get("urgency_score", 0),
                final_score=final_score,
                estimated_market_price=estimated_market_price,
                estimated_margin=margin,
                undervaluation_pct=undervaluation_pct,
                verdict=verdict,
                confidence=confidence,
                why_interesting=_db_text(ai.get("why_interesting", "")),
                possible_risks=_db_text(ai.get("possible_risks", "")),
                model_specific_issues=_db_text(model_risks),
                what_to_check=_db_text(ai.get("what_to_check", "")),
                seller_signals=_db_text(ai.get("seller_signals", "")),
                ai_summary=_db_text(ai_summary),
                exported_to_excel=False,
            )
            return listing

        except Exception as exc:
            raw["_skip_reason"] = f"processing error: {exc}"
            logger.exception(f"Listing processing error: {exc} - {raw.get('url', '')}")
            return None

    async def _scan_source(self, source_name: str) -> List[dict]:
        timeout_seconds = int(self.config.get("source_scan_timeout_seconds", 240))
        try:
            if source_name == "kleinanzeigen":
                task = scrape_kleinanzeigen(self.config, max_results=self.config.get("max_listings_per_scan", 50))
            elif source_name == "autoscout24":
                task = scrape_autoscout24(self.config, max_results=self.config.get("max_listings_per_scan", 50))
            else:
                return []

            return await asyncio.wait_for(task, timeout=timeout_seconds)
        except asyncio.TimeoutError:
            logger.error(f"Source '{source_name}' timed out after {timeout_seconds}s - skipping and continuing")
            return []
        except Exception as exc:
            logger.error(f"Source '{source_name}' failed: {exc}")
            return []

    def _log_ai_scan_health(self) -> None:
        metrics = get_ai_scan_metrics()
        success = sum(int(data.get("success", 0)) for data in metrics.values())
        fallback = sum(int(data.get("fallback", 0)) for data in metrics.values())
        total = success + fallback

        if total == 0:
            logger.info("AI success rate this scan: 0/0 (n/a), fallback provider: none")
            return

        success_pct = (success / total) * 100.0
        failed_provider = "none"
        if fallback:
            failed_provider = max(
                metrics.items(),
                key=lambda item: int(item[1].get("fallback", 0)),
            )[0]
        fallback_provider = "rule-based" if fallback else "none"

        logger.info(
            f"AI success rate this scan: {success}/{total} "
            f"({success_pct:.0f}%), fallback provider: {fallback_provider}, "
            f"failed provider: {failed_provider}"
        )

        threshold = float(self.config.get("ai_success_warning_threshold_pct", 50))
        if success_pct >= threshold:
            return

        errors = Counter()
        provider_fallbacks = Counter()
        for provider, data in metrics.items():
            fallback_count = int(data.get("fallback", 0))
            if fallback_count:
                provider_fallbacks[provider] += fallback_count
            for error, count in (data.get("errors") or {}).items():
                errors[(provider, error)] += int(count)

        provider = provider_fallbacks.most_common(1)[0][0] if provider_fallbacks else "unknown"
        if errors:
            (error_provider, error_text), count = errors.most_common(1)[0]
            logger.warning(
                "Paid AI health warning: "
                f"success rate {success_pct:.0f}% is below {threshold:.0f}%; "
                f"most common provider={error_provider}, error={error_text} ({count}x)"
            )
        else:
            logger.warning(
                "Paid AI health warning: "
                f"success rate {success_pct:.0f}% is below {threshold:.0f}%; "
                f"fallback provider={provider}, no detailed error captured"
            )

    async def run_scan(self):
        reset_ai_scan_metrics()
        self.scan_count += 1
        total_new = 0
        hot = good = check = 0
        t_start = time.time()

        logger.info("")
        logger.info("=" * 55)
        logger.info(f"SCAN #{self.scan_count} - {datetime.now().strftime('%d.%m.%Y %H:%M:%S')}")
        logger.info("=" * 55)

        sources = self.config.get("sources", {})
        active_sources = [s for s, enabled in sources.items() if enabled]

        for source in active_sources:
            logger.info(f"Scanning: {source.upper()}")
            raw_listings = await self._scan_source(source)
            logger.info(f"   Found {len(raw_listings)} raw listings")

            session = self.Session()
            new_this_source = 0
            duplicate_count = 0
            skip_reasons = Counter()
            skip_examples = {}

            try:
                for observed_raw in raw_listings:
                    self._append_market_observation(source, observed_raw)

                self._refresh_learned_market()

                for raw in raw_listings:
                    if not self._get_or_skip(session, raw):
                        duplicate_count += 1
                        self._append_rejection_report(source, raw, "duplicate")
                        continue

                    listing = self._process_listing(session, raw)
                    if listing is None:
                        reason = raw.get("_skip_reason") or self._candidate_quality_issue(raw) or "dealer/scoring gate rejected"
                        skip_reasons[reason] += 1
                        self._append_rejection_report(source, raw, reason)
                        if reason not in skip_examples and len(skip_examples) < 8:
                            age = raw.get("listing_age_minutes")
                            age_text = f"{age}min" if age is not None else "?"
                            skip_examples[reason] = (
                                f"{raw.get('title', '')[:70]} | "
                                f"{raw.get('price', '?')} EUR | age {age_text} | "
                                f"{raw.get('brand', '?')} {raw.get('model', '?')}"
                            )
                        continue

                    try:
                        session.add(listing)
                        session.commit()
                        new_this_source += 1
                        total_new += 1

                        age_str = f"{listing.listing_age_minutes}min" if listing.listing_age_minutes is not None else "?"
                        logger.info(
                            f"   {listing.verdict} | score={listing.final_score:.2f} | "
                            f"{listing.brand} {listing.model} {listing.year} | "
                            f"{listing.price:,.0f} EUR | {age_str} old | {source} | {listing.url}"
                        )

                        if listing.verdict == "HOT":
                            hot += 1
                        elif listing.verdict == "GOOD":
                            good += 1
                        elif listing.verdict == "CHECK":
                            check += 1

                    except IntegrityError:
                        session.rollback()
                    except Exception as exc:
                        session.rollback()
                        logger.error(f"DB save error: {exc}")
            finally:
                session.close()

            logger.info(f"   {source.upper()}: {new_this_source} new listings processed")
            if duplicate_count:
                logger.info(f"      Duplicates skipped: {duplicate_count}")
            if skip_reasons:
                reason_text = "; ".join(f"{reason}: {count}" for reason, count in skip_reasons.most_common(8))
                logger.info(f"      Rejected before Excel: {reason_text}")
                for reason, example in list(skip_examples.items())[:5]:
                    logger.info(f"      Example skip [{reason}]: {example}")


        if self.sold_tracker:
            self.sold_tracker.check_due()
        self._update_excel()
        self._send_pending_telegram_from_db()
        if self.config.get("best_of_scan_report_enabled", True) and write_best_of_scan_report:
            try:
                write_best_of_scan_report(self.config, self.scan_count, self._last_export_listings)
            except Exception as exc:
                logger.error(f"Best-of-scan report failed: {exc}")
        duration = time.time() - t_start
        self._log_ai_scan_health()
        logger.info(f"Scan complete in {duration:.1f}s | New: {total_new} | HOT {hot} GOOD {good} CHECK {check}")
        if total_new == 0:
            logger.info("   No strong new opportunities this cycle. This is normal with strict dealer filtering.")

    def _update_excel(self):
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
                .filter(
                    (Listing.year.is_(None))
                    | (Listing.year <= int(self.config.get("max_model_year", datetime.now().year - 1)))
                )
                .filter(
                    (Listing.estimated_market_price.is_(None))
                    | (Listing.price <= Listing.estimated_market_price * (1 + float(self.config.get("max_over_market_export_pct", 5)) / 100.0))
                )
                .filter(Listing.brand.isnot(None))
                .filter(Listing.model.isnot(None))
            )
            export_recent_hours = int(self.config.get("export_recent_hours", 24) or 0)
            if export_recent_hours > 0:
                query = query.filter(Listing.found_at >= datetime.utcnow() - timedelta(hours=export_recent_hours))
            if self.config.get("require_listing_age", False):
                query = query.filter(Listing.listing_age_minutes.isnot(None))
                query = query.filter(Listing.listing_age_minutes >= self.config.get("freshness_min_minutes", 1))
                query = query.filter(
                    Listing.listing_age_minutes
                    <= self.config.get("freshness_max_minutes", self.config.get("freshness_max_hours", 12) * 60)
                )

            listings = (
                query.order_by(Listing.final_score.desc(), Listing.found_at.desc())
                .limit(max_rows * 2)
                .all()
            )
            blocked_export_terms = [
                "motor unruhig", "unruhiger motor", "motor laeuft unruhig", "motor lÃƒÆ’Ã‚Â¤uft unruhig",
                "motorproblem", "motor problem", "motorschaden", "motor defekt",
                "motorkontrollleuchte", "motor kontrollleuchte", "kontrollleuchte", "motorlampe", "check engine", "mkl",
                "lambdasonde", "oelstandsensor", "olstandsensor", "standsensor defekt",
                "getriebeschaden", "getriebe defekt", "bastler", "export", "fahrzeugankauf",
                "suche kaufe", "wir kaufen", "ankauf", "nur tausch", "leasingÃƒÆ’Ã‚Â¼bernahme", "leasinguebernahme", "kein kfz brief", "kein brief", "ohne brief", "brief fehlt", "keine papiere", "ohne papiere",
            ]
            clean_export = []
            for listing in listings:
                title_text = f"{listing.title or ''} {listing.description or ''}".lower()
                title_text = re.sub(r"\bunfall\s+frei\b", "unfallfrei", title_text)
                if listing.mileage and listing.mileage > int(self.config.get("absolute_mileage_reject_km", 300000)):
                    continue
                if re.search(r"\bunfall(?!frei)\b", title_text, re.IGNORECASE) or any(term in title_text for term in blocked_export_terms):
                    continue

                # Excel observed-market recheck:
                # Old DB rows can become clearly above-market after the local market
                # observation file has collected enough comparable listings.
                observed_market, observed_comps = self._estimate_observed_market_price({
                    "brand": listing.brand,
                    "model": listing.model,
                    "year": listing.year,
                    "mileage": listing.mileage,
                    "price": listing.price,
                    "url": listing.url,
                })

                observed_discount_pct = None
                if observed_market and listing.price:
                    observed_discount_pct = (observed_market - listing.price) / observed_market * 100.0
                    max_over_market_pct = float(self.config.get("max_over_market_export_pct", 5))
                    if listing.price > observed_market * (1 + max_over_market_pct / 100.0):
                        logger.debug(
                            "Excel skip above observed market: "
                            f"{listing.title[:60] if listing.title else ''} "
                            f"asking={listing.price:.0f} observed={observed_market:.0f} "
                            f"comps={observed_comps}"
                        )
                        continue

                # Precision mode: CHECK means "maybe", not "spend time now".
                # Export only CHECK rows with either verified discount or a very
                # cheap/liquid rescue profile. All other CHECK rows stay in DB/logs.
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
                        listing.price is not None
                        and listing.price <= rescue_price_max
                        and listing.final_score is not None
                        and listing.final_score >= rescue_score_min
                        and (listing.year is None or listing.year >= int(self.config.get("watchlist_min_year", 2004)))
                        and (listing.mileage is None or listing.mileage <= int(self.config.get("watchlist_max_mileage", 230000)))
                    )
                    if not market_verified_check and not cheap_rescue_check:
                        logger.debug(
                            "Excel skip weak CHECK: "
                            f"{listing.title[:60] if listing.title else ''} "
                            f"discount={observed_discount_pct} comps={observed_comps} "
                            f"score={listing.final_score}"
                        )
                        continue

                min_margin = float(self.config.get("min_excel_margin_for_check", -500))
                strong_rescue_discount = float(self.config.get("strong_rescue_discount_pct", 20))
                if listing.verdict == "CHECK" and listing.estimated_margin is not None:
                    strong_rescue = (
                        listing.price is not None
                        and listing.price <= float(self.config.get("cheap_rescue_check_max_price", 1800))
                        and observed_discount_pct is not None
                        and observed_discount_pct >= strong_rescue_discount
                    )
                    if listing.estimated_margin < min_margin and not strong_rescue:
                        logger.debug(
                            "Excel skip weak margin: "
                            f"{listing.title[:60] if listing.title else ''} "
                            f"margin={listing.estimated_margin:.0f} "
                            f"discount={observed_discount_pct}"
                        )
                        continue

                clean_export.append(listing)
                if len(clean_export) >= max_rows:
                    break
            self._last_export_listings = list(clean_export)
            write_excel(clean_export, self.config.get("output_file", "output/deals.xlsx"))
        except Exception as exc:
            logger.error(f"Excel update failed: {exc}")
            self._last_export_listings = []
        finally:
            session.close()

    async def run_forever(self):
        interval_min = self.config.get("scan_interval_minutes", 5)
        logger.info(f"AUTOHAWK started. Scanning every {interval_min} minute(s).")
        logger.info("Press Ctrl+C to stop.")

        while True:
            try:
                await self.run_scan()
            except KeyboardInterrupt:
                logger.info("Scanner stopped by user.")
                break
            except Exception as exc:
                logger.error(f"Unexpected error in scan loop: {exc}")

            logger.info(f"Next scan in {interval_min} minute(s)...")
            try:
                await asyncio.sleep(interval_min * 60)
            except asyncio.CancelledError:
                break

        logger.info("AUTOHAWK shut down.")

