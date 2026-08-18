from __future__ import annotations

import asyncio
import logging

logger = logging.getLogger("autohawk.scanner_runner")


class ScannerRunner:
    """Runs scan cycles repeatedly according to configuration."""

    def __init__(self, config: dict, scan):
        self.config = config
        self.scan = scan

    async def run_forever(self) -> None:
        interval_min = self.config.get("scan_interval_minutes", 5)
        logger.info(f"AUTOHAWK started. Scanning every {interval_min} minute(s).")
        logger.info("Press Ctrl+C to stop.")

        while True:
            try:
                await self.scan()
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