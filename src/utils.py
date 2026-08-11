"""Shared text normalization helpers for AUTOHAWK."""

from __future__ import annotations

import json
import re
import unicodedata
from pathlib import Path
from typing import Any, Mapping


from typing import Optional


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


# --- Added shared helpers to centralize duplicated logic ---
def safe_int(value: Any, default: int = 0) -> int:
    try:
        if value is None or value == "":
            return default
        return int(float(str(value).replace(",", ".")))
    except Exception:
        return default


def safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or value == "":
            return default
        return float(str(value).replace(",", "."))
    except Exception:
        return default


def load_json_db(path: Path, default: Optional[Any] = None) -> Any:
    if default is None:
        default = {}
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception:
        return default


RISKY_TERMS = [
    "dsg",
    "s-tronic",
    "dq200",
    "multitronic",
    "cvt",
    "n47",
    "puretech",
    "ecoboost",
    "tsi",
    "tfsi",
    "thp",
    "pneuma",
    "luftfahrwerk",
    "v8",
    "v10",
    "4.2",
    "5.2",
    "x5",
    "a8",
    "s8",
    "s6",
]


def has_risky_terms(text: str) -> bool:
    t = norm(text)
    return any(term in t for term in RISKY_TERMS)


DURABLE_JAPANESE = {"toyota", "honda", "mazda", "subaru", "lexus"}
DURABLE_KOREAN = {"kia", "hyundai"}
MASS_MARKET = {"volkswagen", "vw", "skoda", "seat", "opel", "ford", "nissan"}
PREMIUM_BRANDS = {"bmw", "mercedes", "audi", "porsche", "land rover", "jaguar", "mini"}


# Common conservative model tuples shared across modules
CONSERVATIVE_MARKET_MODELS = {
    ("volkswagen", "golf"), ("volkswagen", "polo"),
    ("skoda", "fabia"), ("skoda", "octavia"),
    ("opel", "astra"), ("opel", "corsa"),
    ("ford", "fiesta"), ("ford", "focus"),
    ("toyota", "yaris"), ("toyota", "auris"), ("toyota", "corolla"),
    ("honda", "jazz"), ("honda", "civic"),
    ("audi", "a3"), ("audi", "a4"),
}


# Very-durable workhorse pairs
VERY_DURABLE_WORKHORSES = [
    ("volkswagen", "golf"), ("vw", "golf"), ("volkswagen", "polo"), ("vw", "polo"),
    ("skoda", "octavia"), ("toyota", "yaris"), ("toyota", "corolla"),
    ("honda", "jazz"), ("honda", "civic"),
]


# Hot/liquid model set used by dealer engine
HOT_LIQUID_MODELS = {
    ("volkswagen", "golf"), ("vw", "golf"),
    ("volkswagen", "polo"), ("vw", "polo"),
    ("skoda", "fabia"), ("skoda", "octavia"),
    ("opel", "astra"), ("opel", "corsa"),
    ("ford", "fiesta"), ("ford", "focus"),
    ("toyota", "yaris"), ("toyota", "auris"), ("toyota", "corolla"),
    ("honda", "jazz"), ("honda", "civic"),
    ("suzuki", "swift"), ("mitsubishi", "colt"),
}


def apply_risky_mileage_penalty(max_mileage: int, text: str, cap: int = 170000) -> tuple[int, str]:
    """Apply a risky-engine/gearbox mileage cap if risky terms appear in text.

    Returns (new_max_mileage, suffix) where suffix is appended to a profile string when penalty applied.
    """
    if has_risky_terms(text):
        return min(max_mileage, cap), "_risk_engine_or_gearbox"
    return max_mileage, ""



def apply_risky_penalty_and_append(profile: str, max_mileage: int, text: str, /) -> tuple[int, str]:
    """Apply risky mileage penalty and append any returned suffix to `profile`.

    Returns the updated (max_mileage, profile).
    """
    new_max, suffix = apply_risky_mileage_penalty(max_mileage, text)
    if suffix:
        profile = profile + suffix
    return new_max, profile

