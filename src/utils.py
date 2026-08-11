"""Shared text normalization helpers for AUTOHAWK."""

from __future__ import annotations

import re
import unicodedata
from typing import Any, Mapping


DEFAULT_REPLACEMENTS = {
        "Ä": "ae",
        "Ö": "oe",
        "Ü": "ue",
        "ß": "ss",
        "ä": "ae",
        "ö": "oe",
        "ü": "ue",
    }


DEFAULT_ALIASES: dict[str, str] = {
    "vw": "volkswagen",
    "mercedes-benz": "mercedes",
    "citroën": "citroen",
    "škoda": "skoda",
}

DEFAULT_SPECIAL_CASES: dict[str, str] = {
    "vw": "volkswagen",
}


def norm(
    value: Any,
    *,
    aliases: Mapping[str, str] | None = None,
    special_cases: Mapping[str, str] | None = None,
) -> str:
    text = str(value or "")
    if not text:
        return ""

    text = text.lower().strip()

    for _ in range(2):
        try:
            fixed = text.encode("latin1").decode("utf-8")
        except UnicodeError:
            break
        if fixed == text:
            break
        text = fixed

    for old, new in DEFAULT_REPLACEMENTS.items():
        text = text.replace(old, new)

    text = unicodedata.normalize("NFKD", text)
    text = text.encode("ascii", "ignore").decode("ascii")
    text = re.sub(r"\s+", " ", text).strip()

    for mapping in (DEFAULT_ALIASES, aliases or {}):
        text = mapping.get(text, text)

    for mapping in (DEFAULT_SPECIAL_CASES, special_cases or {}):
        text = mapping.get(text, text)

    return text
