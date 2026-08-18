from __future__ import annotations

import asyncio
import logging
from collections import Counter

from src.ai_analysis import get_ai_scan_metrics
from src.scrapers import scrape_autoscout24, scrape_kleinanzeigen

logger = logging.getLogger("autohawk.scan_support")


class SourceScanner:
    """Fetches listings from configured sources with a per-source timeout."""

    def __init__(self, config: dict):
        self.config = config

    async def scan(self, source_name: str) -> list[dict]:
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


class AIHealthReporter:
    """Reports provider success and fallback rates for one scan."""

    def report(self, config: dict) -> None:
        metrics = get_ai_scan_metrics()
        success = sum(int(data.get("success", 0)) for data in metrics.values())
        fallback = sum(int(data.get("fallback", 0)) for data in metrics.values())
        total = success + fallback
        if total == 0:
            logger.info("AI success rate this scan: 0/0 (n/a), fallback provider: none")
            return

        success_pct = success / total * 100.0
        failed_provider = "none"
        if fallback:
            failed_provider = max(metrics.items(), key=lambda item: int(item[1].get("fallback", 0)))[0]
        logger.info(
            f"AI success rate this scan: {success}/{total} ({success_pct:.0f}%), "
            f"fallback provider: {'rule-based' if fallback else 'none'}, failed provider: {failed_provider}"
        )

        threshold = float(config.get("ai_success_warning_threshold_pct", 50))
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
                f"Paid AI health warning: success rate {success_pct:.0f}% is below {threshold:.0f}%; "
                f"most common provider={error_provider}, error={error_text} ({count}x)"
            )
        else:
            logger.warning(
                f"Paid AI health warning: success rate {success_pct:.0f}% is below {threshold:.0f}%; "
                f"fallback provider={provider}, no detailed error captured"
            )