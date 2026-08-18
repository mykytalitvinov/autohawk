from __future__ import annotations

import logging
from datetime import datetime, timedelta

from src.models import Listing

logger = logging.getLogger("autohawk.telegram_delivery")


class TelegramDeliveryService:
    """Delivers persisted Telegram candidates and records delivery state."""

    def __init__(self, config: dict, session_factory, notifier, safety_policy):
        self.config = config
        self.Session = session_factory
        self.notifier = notifier
        self.safety_policy = safety_policy

    def send_pending(self) -> int:
        if not self.notifier or not self.notifier.ready():
            if self.notifier:
                logger.debug("Telegram DB queue inactive: missing TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID")
            return 0

        max_per_scan = int(self.config.get("telegram_max_per_scan", 5))
        max_attempts = int(self.config.get("telegram_max_attempts", 3))
        recent_hours = int(self.config.get("telegram_queue_recent_hours", self.config.get("export_recent_hours", 24)) or 0)
        verdicts = set(self.config.get("telegram_verdicts", ["HOT", "GOOD", "CHECK"]))
        min_score = float(self.config.get("telegram_min_score", 0.70))

        session = self.Session()
        sent_count = failed_count = skipped_count = 0
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

            candidates = query.order_by(Listing.final_score.desc(), Listing.found_at.desc()).limit(max_per_scan * 4).all()
            for listing in candidates:
                if sent_count >= max_per_scan:
                    break
                now = datetime.utcnow()
                block_reason = self.safety_policy.blocked_reason(listing)
                if block_reason:
                    listing.telegram_status = "skipped"
                    listing.telegram_error = block_reason
                    listing.telegram_last_attempt_at = now
                    session.commit()
                    skipped_count += 1
                    continue

                listing.telegram_attempts = int(listing.telegram_attempts or 0) + 1
                listing.telegram_last_attempt_at = now
                ok, error = self.notifier.post_listing(listing)
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
                logger.info(f"Telegram DB queue: sent={sent_count}, failed={failed_count}, skipped={skipped_count}")
            return sent_count
        except Exception as exc:
            session.rollback()
            logger.warning(f"Telegram DB queue failed: {exc}")
            return sent_count
        finally:
            session.close()