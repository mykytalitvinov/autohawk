from __future__ import annotations

import logging
from collections import Counter
from typing import Callable

from sqlalchemy.exc import IntegrityError

from src.models import Listing

logger = logging.getLogger("autohawk.source_processor")


class SourceProcessor:
    """Processes one scraped source and persists accepted listings."""

    def process(self, source: str, raw_listings: list[dict], session, *, append_observation: Callable, refresh_market: Callable, is_duplicate: Callable, process_listing: Callable, quality_issue: Callable, append_rejection: Callable) -> dict[str, object]:
        for raw in raw_listings:
            append_observation(source, raw)
        refresh_market()

        new_count = duplicate_count = 0
        hot = good = check = 0
        skip_reasons = Counter()
        skip_examples = {}
        for raw in raw_listings:
            if not is_duplicate(session, raw):
                duplicate_count += 1
                append_rejection(source, raw, "duplicate")
                continue

            listing = process_listing(session, raw)
            if listing is None:
                reason = raw.get("_skip_reason") or quality_issue(raw) or "dealer/scoring gate rejected"
                skip_reasons[reason] += 1
                append_rejection(source, raw, reason)
                if reason not in skip_examples and len(skip_examples) < 8:
                    age = raw.get("listing_age_minutes")
                    age_text = f"{age}min" if age is not None else "?"
                    skip_examples[reason] = (
                        f"{raw.get('title', '')[:70]} | {raw.get('price', '?')} EUR | age {age_text} | "
                        f"{raw.get('brand', '?')} {raw.get('model', '?')}"
                    )
                continue

            try:
                session.add(listing)
                session.commit()
                new_count += 1
                age_text = f"{listing.listing_age_minutes}min" if listing.listing_age_minutes is not None else "?"
                logger.info(
                    f"   {listing.verdict} | score={listing.final_score:.2f} | "
                    f"{listing.brand} {listing.model} {listing.year} | {listing.price:,.0f} EUR | "
                    f"{age_text} old | {source} | {listing.url}"
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

        logger.info(f"   {source.upper()}: {new_count} new listings processed")
        if duplicate_count:
            logger.info(f"      Duplicates skipped: {duplicate_count}")
        if skip_reasons:
            reason_text = "; ".join(f"{reason}: {count}" for reason, count in skip_reasons.most_common(8))
            logger.info(f"      Rejected before Excel: {reason_text}")
            for reason, example in list(skip_examples.items())[:5]:
                logger.info(f"      Example skip [{reason}]: {example}")
        return {"new": new_count, "hot": hot, "good": good, "check": check}