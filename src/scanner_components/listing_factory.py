from __future__ import annotations

import json
from datetime import datetime

from src.models import Listing


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


class ListingFactory:
    """Builds persisted Listing objects from processed pipeline data."""

    @staticmethod
    def create(raw: dict, base_scores: dict, ai: dict, model_risks: list, market_price, ai_score: int, ai_action: str, verdict: str) -> Listing:
        margin = ai.get("net_profit_estimate") if ai.get("net_profit_estimate") is not None else base_scores.get("estimated_margin")
        ai_summary_parts = [
            f"Provider: {ai.get('analysis_provider', 'local_dealer_engine')}",
            f"Opportunity score: {ai_score}/100",
            f"Action: {ai_action}",
        ]
        for key in ["profit_logic", "negotiation_angle", "model_market_reputation", "photo_analysis", "dashboard_warning_lights", "price_view", "score_breakdown", "estimated_costs", "risk_reserve", "net_profit_estimate", "ai_summary"]:
            value = ai.get(key)
            if value:
                ai_summary_parts.append(str(value))

        return Listing(
            stable_id=raw.get("stable_id"), fingerprint=raw.get("fingerprint"),
            platform=raw.get("platform"), platform_id=raw.get("platform_id"),
            url=raw.get("url"), title=raw.get("title"), brand=raw.get("brand"), model=raw.get("model"),
            year=raw.get("year"), mileage=raw.get("mileage"), price=raw.get("price"),
            fuel=raw.get("fuel"), gearbox=raw.get("gearbox"), engine=raw.get("engine"),
            tuv_text=raw.get("tuv_text"), tuv_until=raw.get("tuv_until"), tuv_months_left=raw.get("tuv_months_left"),
            location=raw.get("location"), description=raw.get("description"), seller_type=raw.get("seller_type"),
            photo_urls=raw.get("photo_urls"), found_at=datetime.utcnow(), listing_age_minutes=raw.get("listing_age_minutes"),
            freshness_score=base_scores.get("freshness_score", 0), price_score=base_scores.get("price_score", 0),
            condition_score=base_scores.get("condition_score", 0),
            risk_score=max(0.0, 1.0 - (0 if ai.get("risk_score") == "LOW" else 0.28 if ai.get("risk_score") == "MEDIUM" else 0.55)),
            liquidity_score=float(ai.get("liquidity_score") or 0) / 100.0,
            urgency_score=base_scores.get("urgency_score", 0), final_score=round(ai_score / 100.0, 3),
            estimated_market_price=market_price, estimated_margin=margin,
            undervaluation_pct=base_scores.get("undervaluation_pct"), verdict=verdict,
            confidence="MEDIUM" if raw.get("year") and raw.get("mileage") and raw.get("detail_verified") else "LOW",
            why_interesting=_db_text(ai.get("why_interesting", "")),
            possible_risks=_db_text(ai.get("possible_risks", "")),
            model_specific_issues=_db_text(model_risks), what_to_check=_db_text(ai.get("what_to_check", "")),
            seller_signals=_db_text(ai.get("seller_signals", "")), ai_summary=_db_text(" | ".join(ai_summary_parts)),
            exported_to_excel=False,
        )