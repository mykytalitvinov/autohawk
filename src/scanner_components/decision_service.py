from __future__ import annotations

import logging
import os

from src.scanner_components.listing_factory import ListingFactory

logger = logging.getLogger("autohawk.decision_service")


class DecisionService:
    """Runs the complete post-market decision pipeline for one raw listing."""

    def __init__(self, config: dict, candidate_policy, market_gate, candidate_scoring, paid_ai, listing_factory: ListingFactory):
        self.config = config
        self.candidate_policy = candidate_policy
        self.market_gate = market_gate
        self.candidate_scoring = candidate_scoring
        self.paid_ai = paid_ai
        self.listing_factory = listing_factory

    def process(self, raw: dict, learned_market: dict):
        try:
            quality_issue = self.candidate_policy.quality_issue(raw)
            if quality_issue:
                raw["_skip_reason"] = quality_issue
                logger.debug(f"SKIP quality: {raw.get('title', '')[:60]} - {quality_issue}")
                return None

            market_price = self.market_gate.evaluate(raw, learned_market)
            is_junk, junk_reason, warnings, positive_signals, base_scores, model_risks, dealer_ai = self.candidate_scoring.evaluate(raw, market_price)
            if is_junk:
                raw["_skip_reason"] = f"quick filter: {junk_reason}"
                logger.debug(f"SKIP junk: {raw.get('title', '')[:60]} - {junk_reason}")
                return None

            dealer_score = int(dealer_ai.get("opportunity_score") or 0)
            dealer_action = (dealer_ai.get("recommended_action") or "").upper()
            min_dealer_score = int(self.config.get("min_dealer_score", 64))
            reject_actions = set(self.config.get("ai_reject_actions", ["REJECT", "IGNORE", "SKIP", "LOW_PRIORITY"]))
            accepted_actions = set(self.config.get("accepted_ai_actions", ["BUY_CANDIDATE", "FAST_VERIFY", "INSPECTION_ONLY"]))

            if dealer_action in reject_actions or dealer_score < min_dealer_score:
                raw["_skip_reason"] = f"dealer gate: score={dealer_score}/100 action={dealer_action or '?'} required_score={min_dealer_score}"
                logger.debug(f"Dealer gate rejected: {raw.get('title', '')[:60]} score={dealer_score} action={dealer_action}")
                return None
            if dealer_action not in accepted_actions:
                raw["_skip_reason"] = f"dealer action not accepted: {dealer_action or '?'}"
                logger.debug(f"Dealer action not accepted: {raw.get('title', '')[:60]} action={dealer_action}")
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
                    raw["_skip_reason"] = f"strict AI shortlist: local_score={dealer_score}/100 required={paid_ai_min}"
                    logger.debug(f"Strict shortlist rejected before paid AI: {raw.get('title', '')[:60]} dealer_score={dealer_score} required={paid_ai_min}")
                    return None
                logger.info(
                    f"   AI SHORTLIST | local_score={dealer_score}/100 | "
                    f"{raw.get('brand')} {raw.get('model')} {raw.get('year')} | {provider.upper()} analysis..."
                )

            ai = self.paid_ai.analyze(
                raw=raw, warnings=warnings, positive_signals=positive_signals,
                model_risks=model_risks, market_price=market_price,
                margin=base_scores.get("estimated_margin", 0), dealer_ai=dealer_ai,
            )
            ai_score = int(ai.get("opportunity_score") or dealer_score)
            ai_action = (ai.get("recommended_action") or dealer_action).upper()
            observed_comps = int(raw.get("_market_observed_comps") or 0)
            min_observed = int(self.config.get("min_observed_comps_for_good", 3))
            if self.config.get("good_requires_observed_market", True) and ai_score >= 68 and (not market_price or observed_comps < min_observed):
                ai_score = min(ai_score, int(self.config.get("unverified_market_score_cap", 72)))
                ai_action = "INSPECTION_ONLY"
                ai["recommended_action"] = ai_action
                ai["opportunity_type"] = "WATCHLIST_CHECK_MANUALLY"
                ai["price_view"] = (str(ai.get("price_view") or "") + f" | Market not verified enough: {observed_comps} comparable observations. No GOOD/HOT without market proof.").strip()

            if not raw.get("mileage") and ai_score > int(self.config.get("missing_mileage_score_cap", 62)):
                ai_score = int(self.config.get("missing_mileage_score_cap", 62))
                ai_action = "INSPECTION_ONLY"
                ai["recommended_action"] = ai_action
                ai["opportunity_type"] = "WATCHLIST_CHECK_MANUALLY"
                ai["possible_risks"] = (str(ai.get("possible_risks") or "") + "\n- Mileage missing: do not treat as HOT until Kilometerstand is confirmed.").strip()
                ai["what_to_check"] = (str(ai.get("what_to_check") or "") + "\n- Confirm exact Kilometerstand from listing details/photo/TUV report.").strip()

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

            opportunity_type = (ai.get("opportunity_type") or "").upper()
            market_verified = (
                not self.config.get("good_requires_observed_market", True)
                or observed_comps >= min_observed
            )
            if ai_action == "INSPECTION_ONLY" or opportunity_type == "WATCHLIST_CHECK_MANUALLY":
                verdict = "CHECK"
            elif ai_score >= 82 and market_verified:
                verdict = "HOT"
            elif ai_score >= 68 and market_verified:
                verdict = "GOOD"
            else:
                verdict = "CHECK"

            return self.listing_factory.create(
                raw=raw, base_scores=base_scores, ai=ai, model_risks=model_risks,
                market_price=market_price, ai_score=ai_score, ai_action=ai_action, verdict=verdict,
            )
        except Exception as exc:
            raw["_skip_reason"] = f"processing error: {exc}"
            logger.exception(f"Listing processing error: {exc} - {raw.get('url', '')}")
            return None