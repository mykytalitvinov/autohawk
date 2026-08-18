from __future__ import annotations

from src.dealer_engine import analyze_dealer_candidate
from src.filter import quick_filter
from src.scoring import score_listing


class CandidateScoringService:
    """Runs fast junk filtering and deterministic candidate scoring."""

    def __init__(self, config: dict):
        self.config = config

    def evaluate(self, raw: dict, market_price: float | None) -> tuple[bool, str, list[str], list[str], dict, list, dict]:
        is_junk, junk_reason, warnings, positive_signals = quick_filter(raw)
        if is_junk:
            return True, junk_reason, warnings, positive_signals, {}, [], {}

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
        return False, "", warnings, positive_signals, base_scores, model_risks, dealer_ai