from __future__ import annotations

import logging
import os

from src.ai_analysis import ai_analyze

logger = logging.getLogger("autohawk.paid_ai")


class PaidAIService:
    """Runs paid AI only for eligible finalists and normalizes fallback behavior."""

    def __init__(self, config: dict):
        self.config = config

    def analyze(
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