from __future__ import annotations

import logging

from src.learned_market import estimate_from_learned_market
from src.market import estimate_market_price

logger = logging.getLogger("autohawk.market_gate")


class MarketGate:
    """Combines market estimates and rejects clearly above-market listings."""

    def __init__(self, config: dict, market_memory):
        self.config = config
        self.market_memory = market_memory

    def evaluate(self, raw: dict, learned_market: dict) -> float | None:
        market_price = estimate_market_price(
            brand=raw.get("brand"), model=raw.get("model"), year=raw.get("year"),
            mileage=raw.get("mileage"), fuel=raw.get("fuel"), gearbox=raw.get("gearbox"),
        )
        observed_market_price, observed_comps = self.market_memory.estimate_observed(raw)
        learned_market_price, learned_comps, learned_key = estimate_from_learned_market(
            raw, learned_market,
            min_bucket_count=int(self.config.get("min_learned_bucket_count", 3)),
        )
        if learned_market_price:
            raw["_market_learned_price"] = learned_market_price
            raw["_market_learned_comps"] = learned_comps
            raw["_market_learned_key"] = learned_key
            if not observed_market_price or observed_comps < int(self.config.get("min_market_observation_comps", 3)):
                observed_market_price, observed_comps = learned_market_price, learned_comps
            elif market_price:
                observed_market_price = min(observed_market_price, learned_market_price * 1.08)
        if observed_market_price:
            raw["_market_observed_comps"] = observed_comps
            raw["_market_observed_price"] = observed_market_price
            raw["_market_price_source"] = "observed_market"
            if market_price:
                market_price = min(market_price, observed_market_price * 1.12)
            else:
                market_price = observed_market_price

        asking_price = raw.get("price") or 0
        if market_price and asking_price and (market_price < asking_price * 0.55 or market_price > asking_price * 2.5):
            logger.debug(
                f"Market estimate suppressed: {raw.get('title', '')[:50]} "
                f"asking={asking_price} estimate={market_price}"
            )
            market_price = None

        market_gate_allowed = True
        if self.config.get("require_mileage_for_above_market_gate", True) and not raw.get("mileage"):
            market_gate_allowed = False
            raw["_market_gate_skipped_reason"] = "missing mileage"
        if str(raw.get("_market_learned_key") or "").endswith("|unknown"):
            market_gate_allowed = False
            raw["_market_gate_skipped_reason"] = "unknown mileage market bucket"

        if market_price and asking_price and market_gate_allowed:
            max_over_market_pct = float(self.config.get("max_over_market_export_pct", 5))
            if asking_price > market_price * (1 + max_over_market_pct / 100.0):
                raw["_skip_reason"] = (
                    f"above market gate: asking={asking_price:.0f} market={market_price:.0f} "
                    f"max_over={max_over_market_pct:.0f}%"
                )
                logger.debug(
                    f"SKIP above market: {raw.get('title', '')[:60]} "
                    f"asking={asking_price} market={market_price}"
                )
                return None
        elif market_price and asking_price:
            logger.debug(
                "Above-market gate not applied because market confidence is low: "
                f"{raw.get('title', '')[:60]} asking={asking_price} market={market_price} "
                f"reason={raw.get('_market_gate_skipped_reason', 'unknown')}"
            )
        return market_price