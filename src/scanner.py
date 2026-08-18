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

import logging
import time
from datetime import datetime
from typing import Optional

from src.ai_analysis import reset_ai_scan_metrics
from src.models import Listing, init_db
from src.sold_tracker import SoldTracker
from src.telegram_notifier import TelegramNotifier
from src.scanner_components.scanner_services import CandidatePolicy, MarketMemoryService, OutputSafetyPolicy, ScanReportWriter
from src.scanner_components.paid_ai_service import PaidAIService
from src.scanner_components.telegram_delivery import TelegramDeliveryService
from src.scanner_components.export_service import ExportService
from src.scanner_components.scan_support import AIHealthReporter, SourceScanner
from src.scanner_components.listing_factory import ListingFactory
from src.scanner_components.market_gate import MarketGate
from src.scanner_components.scanner_runner import ScannerRunner
from src.scanner_components.candidate_scoring import CandidateScoringService
from src.scanner_components.source_processor import SourceProcessor
from src.scanner_components.decision_service import DecisionService

try:
    from src.best_of_scan_report import write_best_of_scan_report
except Exception:
    write_best_of_scan_report = None

logger = logging.getLogger("autohawk.scanner")

class AutohawkScanner:
    def __init__(self, config: dict):
        self.config = config
        self.Session = init_db("database/autohawk.db")
        self.scan_count = 0
        self.learned_market = {}
        self._last_export_listings = []
        self.candidate_policy = CandidatePolicy(config)
        self.market_memory = MarketMemoryService(config)
        self.output_safety = OutputSafetyPolicy(config, self.candidate_policy)
        self.report_writer = ScanReportWriter(config, lambda: self.scan_count)
        self.paid_ai = PaidAIService(config)
        self.export_service = ExportService(config, self.Session, self.market_memory)
        self.source_scanner = SourceScanner(config)
        self.ai_health_reporter = AIHealthReporter()
        self.listing_factory = ListingFactory()
        self.market_gate = MarketGate(config, self.market_memory)
        self.runner = ScannerRunner(config, self.run_scan)
        self.candidate_scoring = CandidateScoringService(config)
        self.source_processor = SourceProcessor()
        self.decision_service = DecisionService(
            config, self.candidate_policy, self.market_gate, self.candidate_scoring,
            self.paid_ai, self.listing_factory,
        )
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
        self.telegram_delivery = TelegramDeliveryService(
            config, self.Session, self.telegram_notifier, self.output_safety,
        )
        logger.info("AUTOHAWK Scanner initialized")


    def _refresh_learned_market(self):
        """Rebuild local market memory from observed listings."""
        if not self.config.get("use_learned_market", True):
            return
        try:
            self.learned_market = self.market_memory.refresh()
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

    def _process_listing(self, session, raw: dict) -> Optional[Listing]:
        return self.decision_service.process(raw, self.learned_market)

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
            raw_listings = await self.source_scanner.scan(source)
            logger.info(f"   Found {len(raw_listings)} raw listings")

            session = self.Session()
            try:
                result = self.source_processor.process(
                    source, raw_listings, session,
                    append_observation=self.report_writer.append_market_observation,
                    refresh_market=self._refresh_learned_market,
                    is_duplicate=self._get_or_skip,
                    process_listing=self._process_listing,
                    quality_issue=self.candidate_policy.quality_issue,
                    append_rejection=self.report_writer.append_rejection,
                )
                total_new += int(result["new"])
                hot += int(result["hot"])
                good += int(result["good"])
                check += int(result["check"])
            finally:
                session.close()


        if self.sold_tracker:
            self.sold_tracker.check_due()
        self._update_excel()
        self.telegram_delivery.send_pending()
        if self.config.get("best_of_scan_report_enabled", True) and write_best_of_scan_report:
            try:
                write_best_of_scan_report(self.config, self.scan_count, self._last_export_listings)
            except Exception as exc:
                logger.error(f"Best-of-scan report failed: {exc}")
        duration = time.time() - t_start
        self.ai_health_reporter.report(self.config)
        logger.info(f"Scan complete in {duration:.1f}s | New: {total_new} | HOT {hot} GOOD {good} CHECK {check}")
        if total_new == 0:
            logger.info("   No strong new opportunities this cycle. This is normal with strict dealer filtering.")

    def _update_excel(self):
        self._last_export_listings = self.export_service.update()

    async def run_forever(self):
        await self.runner.run_forever()

