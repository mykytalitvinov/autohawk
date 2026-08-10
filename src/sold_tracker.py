"""
AUTOHAWK sold tracker.

This module checks only exported/interesting leads after a short delay.
If a listing disappears quickly, that is market feedback: the lead was
probably real/attractive. It is not proof that the car was good.
"""

from __future__ import annotations

import csv
import logging
import os
import re
import unicodedata
from datetime import datetime, timedelta
import requests

from src.models import Listing

logger = logging.getLogger("autohawk.sold_tracker")


SOLD_TEXT_PATTERNS = [
    r"anzeige\s+nicht\s+mehr\s+verfugbar",
    r"anzeige\s+wurde\s+geloscht",
    r"nicht\s+mehr\s+online",
    r"nicht\s+mehr\s+verfugbar",
    r"angebot\s+nicht\s+mehr\s+verfugbar",
    r"deleted",
    r"not\s+available",
]

BLOCKED_TEXT_PATTERNS = [
    r"captcha",
    r"zugriff\s+verweigert",
    r"access\s+denied",
    r"too\s+many\s+requests",
    r"robot",
]


def _norm(text: str) -> str:
    text = (text or "").lower()
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"\s+", " ", text)


class SoldTracker:
    def __init__(self, config: dict, session_factory):
        self.config = config
        self.Session = session_factory
        self.output_file = config.get("sold_tracker_file", "output/sold_tracker.csv")
        self.timeout = int(config.get("sold_tracker_timeout_seconds", 8))
        self.headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0 Safari/537.36"
            ),
            "Accept-Language": "de-DE,de;q=0.9,en;q=0.7",
        }

    def check_due(self) -> dict:
        if not self.config.get("sold_tracker_enabled", True):
            return {"checked": 0, "sold": 0, "active": 0, "unknown": 0}

        now = datetime.utcnow()
        min_age = int(self.config.get("sold_tracker_min_age_minutes", 20))
        recheck = int(self.config.get("sold_tracker_recheck_minutes", 30))
        max_age_hours = int(self.config.get("sold_tracker_max_age_hours", 48))
        max_checks = int(self.config.get("sold_tracker_max_checks_per_scan", 8))
        verdicts = self.config.get("sold_tracker_verdicts", ["HOT", "GOOD", "CHECK"])

        session = self.Session()
        stats = {"checked": 0, "sold": 0, "active": 0, "unknown": 0}
        try:
            due = (
                session.query(Listing)
                .filter(Listing.url.isnot(None))
                .filter(Listing.verdict.in_(verdicts))
                .filter((Listing.is_sold.is_(False)) | (Listing.is_sold.is_(None)))
                .filter(Listing.found_at <= now - timedelta(minutes=min_age))
                .filter(Listing.found_at >= now - timedelta(hours=max_age_hours))
                .filter(
                    (Listing.sold_checked_at.is_(None))
                    | (Listing.sold_checked_at <= now - timedelta(minutes=recheck))
                )
                .order_by(Listing.final_score.desc(), Listing.found_at.asc())
                .limit(max_checks)
                .all()
            )

            if not due:
                return stats

            logger.info(f"Sold tracker: checking {len(due)} recent leads")
            for listing in due:
                status, reason = self._check_url(listing.url or "")
                stats["checked"] += 1
                listing.sold_checked_at = datetime.utcnow()
                listing.sold_status = status
                listing.sold_reason = reason

                if status == "sold_or_removed":
                    if not listing.is_sold:
                        listing.is_sold = True
                        listing.sold_detected_at = datetime.utcnow()
                        logger.info(
                            "Sold tracker: SOLD/REMOVED fast | "
                            f"{listing.brand} {listing.model} {listing.year} | "
                            f"{listing.price:.0f} EUR | {listing.url}"
                        )
                    stats["sold"] += 1
                elif status == "active":
                    stats["active"] += 1
                else:
                    stats["unknown"] += 1

                self._append_event(listing, status, reason)

            session.commit()
            logger.info(
                "Sold tracker: "
                f"checked={stats['checked']} sold_or_removed={stats['sold']} "
                f"active={stats['active']} unknown={stats['unknown']}"
            )
            return stats
        except Exception as exc:
            session.rollback()
            logger.warning(f"Sold tracker failed: {exc}")
            return stats
        finally:
            session.close()

    def _check_url(self, url: str) -> tuple[str, str]:
        if not url:
            return "unknown", "missing url"
        try:
            response = requests.get(url, headers=self.headers, timeout=self.timeout, allow_redirects=True)
        except requests.RequestException as exc:
            return "unknown", f"request error: {exc.__class__.__name__}"

        if response.status_code in {404, 410}:
            return "sold_or_removed", f"http {response.status_code}"
        if response.status_code in {403, 429, 503}:
            return "unknown", f"blocked/http {response.status_code}"
        if response.status_code >= 500:
            return "unknown", f"http {response.status_code}"

        text = _norm(response.text[:60000])
        for pattern in BLOCKED_TEXT_PATTERNS:
            if re.search(pattern, text):
                return "unknown", f"blocked text: {pattern}"
        for pattern in SOLD_TEXT_PATTERNS:
            if re.search(pattern, text):
                return "sold_or_removed", f"page text: {pattern}"

        # Kleinanzeigen sometimes redirects removed ads back to search or category pages.
        final_url = response.url or url
        if "kleinanzeigen.de" in url and "/s-anzeige/" in url and "/s-anzeige/" not in final_url:
            return "sold_or_removed", "redirected away from listing"

        return "active", f"http {response.status_code}"

    def _append_event(self, listing: Listing, status: str, reason: str) -> None:
        os.makedirs(os.path.dirname(self.output_file), exist_ok=True)
        exists = os.path.exists(self.output_file)
        fields = [
            "time",
            "status",
            "reason",
            "found_at",
            "minutes_since_found",
            "verdict",
            "score",
            "title",
            "brand",
            "model",
            "year",
            "mileage",
            "price",
            "market_price",
            "url",
        ]
        minutes_since = ""
        if listing.found_at:
            minutes_since = int((datetime.utcnow() - listing.found_at).total_seconds() // 60)
        row = {
            "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "status": status,
            "reason": reason,
            "found_at": listing.found_at.strftime("%Y-%m-%d %H:%M:%S") if listing.found_at else "",
            "minutes_since_found": minutes_since,
            "verdict": listing.verdict or "",
            "score": f"{listing.final_score:.3f}" if listing.final_score is not None else "",
            "title": listing.title or "",
            "brand": listing.brand or "",
            "model": listing.model or "",
            "year": listing.year or "",
            "mileage": listing.mileage or "",
            "price": listing.price or "",
            "market_price": listing.estimated_market_price or "",
            "url": listing.url or "",
        }
        with open(self.output_file, "a", newline="", encoding="utf-8-sig") as fh:
            writer = csv.DictWriter(fh, fieldnames=fields)
            if not exists:
                writer.writeheader()
            writer.writerow(row)

