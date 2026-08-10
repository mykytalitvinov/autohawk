"""Model knowledge loader for AUTOHAWK.

The JSON database is deliberately simple so it can be edited by hand.
All matching is fuzzy and conservative.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
KNOWLEDGE_PATH = ROOT / "data" / "model_knowledge.json"


def _norm(value: Any) -> str:
    text = str(value or "").lower().strip()
    replacements = {
        "\u00e4": "ae",
        "\u00f6": "oe",
        "\u00fc": "ue",
        "\u00df": "ss",
        "\u00c4": "ae",
        "\u00d6": "oe",
        "\u00dc": "ue",
    }
    for old, new in replacements.items():
        text = text.replace(old, new)
    return " ".join(text.split())


@lru_cache(maxsize=1)
def load_model_knowledge() -> dict[str, Any]:
    if not KNOWLEDGE_PATH.exists():
        return {"cards": []}
    try:
        return json.loads(KNOWLEDGE_PATH.read_text(encoding="utf-8-sig"))
    except Exception:
        return {"cards": []}


def find_model_card(brand: str | None, model: str | None, year: int | None = None, text: str = "") -> dict[str, Any] | None:
    brand_n = _norm(brand)
    model_n = _norm(model)
    text_n = _norm(text)
    haystack = f"{brand_n} {model_n} {text_n}".strip()
    if not haystack:
        return None

    best: tuple[int, dict[str, Any]] | None = None
    for card in load_model_knowledge().get("cards", []):
        card_brand = _norm(card.get("brand"))
        card_model = _norm(card.get("model"))
        aliases = [_norm(x) for x in card.get("aliases", [])]
        score = 0

        if card_brand and card_brand == brand_n:
            score += 4
        elif card_brand and card_brand in haystack:
            score += 2

        if card_model and (card_model == model_n or card_model in model_n or model_n in card_model):
            score += 5

        for alias in aliases:
            if alias and alias in haystack:
                score += 6
                break

        if score >= 6 and (best is None or score > best[0]):
            best = (score, card)

    return best[1] if best else None


def contains_any(text: str, values: list[str]) -> list[str]:
    text_n = _norm(text)
    found = []
    for value in values or []:
        value_n = _norm(value)
        if value_n and value_n in text_n:
            found.append(value)
    return found