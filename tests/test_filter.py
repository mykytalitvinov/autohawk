from __future__ import annotations

import pytest

from src.filter import _has_accident_damage_wording, quick_filter
from src.scanner_components.rejection_rules import early_reject_reason


def listing(text: str, title: str = "VW Polo 1.2 TUV neu", price: int = 1800) -> dict:
    return {
        "title": title,
        "description": text,
        "price": price,
        "brand": "Volkswagen",
        "model": "Polo",
        "year": 2010,
        "mileage": 145000,
    }


@pytest.mark.parametrize(
    ("text", "reason_part"),
    [
        ("Leider Motorschaden, Auto wird verkauft.", "engine damage"),
        ("Getriebeschaden, schaltet nicht sauber.", "gearbox"),
        ("Bastlerfahrzeug, keine Garantie.", "bastler"),
        ("Nur Export, keine Anmeldung.", "export"),
        ("Unfallwagen mit Frontschaden.", "accident"),
        ("Fahrzeug startet nicht.", "start"),
        ("Motorkontrollleuchte leuchtet, ABS leuchtet.", "warning light"),
        ("Ohne Papiere und ohne Brief.", "missing documents"),
        ("Starker Rost am Unterboden.", "serious rust"),
    ],
)
def test_quick_filter_hard_rejects_dangerous_wording(text: str, reason_part: str) -> None:
    is_junk, reason, warnings, positives = quick_filter(listing(text))

    assert is_junk is True
    assert reason_part.lower() in reason.lower()
    assert warnings == []
    assert positives == []


def test_quick_filter_does_not_reject_unfallfrei() -> None:
    is_junk, reason, warnings, positives = quick_filter(
        listing("Unfallfrei, gepflegt, TUV bis 05/2028, Service neu.")
    )

    assert is_junk is False
    assert reason == ""
    assert "unfallfrei" in positives


def test_quick_filter_allows_clean_listing() -> None:
    is_junk, reason, warnings, positives = quick_filter(
        listing(
            "Sehr gepflegter Kleinwagen, TUV neu, 1. Hand, Scheckheft, "
            "Motor und Getriebe laufen gut."
        )
    )

    assert is_junk is False
    assert reason == ""
    assert warnings == []
    assert any(signal in positives for signal in ["tuev neu", "tuv neu"])
    assert "1. hand" in positives


@pytest.mark.parametrize(
    "text",
    [
        "Motor laeuft unruhig im kalten Zustand.",
        "Motor läuft unruhig im kalten Zustand.",
        "Motor lÃ¤uft unruhig im kalten Zustand.",
        "Getriebe defekt, nur fÃ¼r Bastler.",
    ],
)
def test_quick_filter_handles_german_variants_and_mojibake(text: str) -> None:
    is_junk, reason, _, _ = quick_filter(listing(text))

    assert is_junk is True
    assert "hard reject" in reason


def test_accident_helper_treats_unfallfrei_as_safe_edge_case() -> None:
    assert _has_accident_damage_wording("unfallfrei aus erster Hand") is False
    assert _has_accident_damage_wording("Unfall frei aus erster Hand") is False
    assert _has_accident_damage_wording("nach Unfall repariert") is True


def test_scraper_early_reject_matches_quick_filter_baseline() -> None:
    assert early_reject_reason("BMW startet nicht, nur Export") in {
        "export only",
        "not roadworthy / does not drive",
    }
    assert early_reject_reason("Unfallfrei, TUV neu, gepflegt") is None
