"""Conservative price-truth layer for AUTOHAWK.

This answers a different question than a normal market estimate:
    "Is this price good enough for profit?"

market_mid = rough asking-market reference.
flip_buy_max/watch_max = flipper buy discipline.
"""

from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

from src.utils import norm, load_json_db, safe_int, safe_float


ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "data" / "price_truth_database.json"


@lru_cache(maxsize=1)
def load_price_truth() -> dict[str, Any]:
    return load_json_db(DB_PATH, default={"cards": []})


def _match_card(brand: str, model: str, text: str) -> dict[str, Any] | None:
    b = norm(brand)
    m = norm(model)
    haystack = f"{b} {m} {norm(text)}"
    best: tuple[int, dict[str, Any]] | None = None
    for card in load_price_truth().get("cards", []):
        score = 0
        cb = norm(card.get("brand"))
        cm = norm(card.get("model"))
        aliases = [norm(x) for x in card.get("aliases", [])]
        if cb and cb == b:
            score += 6
        elif cb and cb in haystack:
            score += 3
        if cm and (cm == m or cm in m or m in cm):
            score += 8
        for alias in aliases:
            if alias and alias in haystack:
                score += 10
                break
        if score >= 9 and (best is None or score > best[0]):
            best = (score, card)
    return best[1] if best else None