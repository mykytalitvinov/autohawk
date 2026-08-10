from __future__ import annotations

import pytest

import src.dealer_engine as dealer_engine
from src.dealer_engine import analyze_dealer_candidate


BASE_CONFIG = {
    "freshness_min_minutes": 0,
    "freshness_max_minutes": 720,
    "max_mileage": 250000,
    "min_year": 2000,
    "min_liquidity_score": 55,
    "min_market_discount_pct": 5,
    "min_net_profit_eur": 350,
    "watchlist_max_price": 3500,
    "watchlist_min_year": 2004,
    "watchlist_max_mileage": 230000,
    "watchlist_min_liquidity": 62,
}


def patch_external_databases(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(dealer_engine, "find_model_card", lambda *args, **kwargs: None)
    monkeypatch.setattr(dealer_engine, "evaluate_generation_truth", lambda *args, **kwargs: {})
    monkeypatch.setattr(dealer_engine, "evaluate_vehicle_dossier", lambda *args, **kwargs: {})


def candidate(**overrides) -> dict:
    data = {
        "title": "VW Polo 1.2 5 Tuer TUV neu Klima Scheckheft 1. Hand VB gepflegt",
        "description": "Privatverkauf wegen Neuanschaffung. Motor und Getriebe laufen gut.",
        "brand": "Volkswagen",
        "model": "Polo",
        "year": 2012,
        "mileage": 120000,
        "price": 2500,
        "fuel": "benzin",
        "gearbox": "manual",
        "seller_type": "private",
        "listing_age_minutes": 20,
        "detail_verified": True,
        "photo_urls": '["fixture-image-1.jpg","fixture-image-2.jpg","fixture-image-3.jpg"]',
    }
    data.update(overrides)
    return data


@pytest.mark.parametrize(
    "case",
    [
        {
            "name": "strong_liquid_private_discount",
            "listing": candidate(),
            "market_price": 4000,
            "score_range": (75, 100),
            "actions": {"FAST_VERIFY", "BUY_CANDIDATE"},
            "risk": {"LOW", "MEDIUM"},
        },
        {
            "name": "hard_engine_damage_reject",
            "listing": candidate(
                title="VW Polo Motorschaden Bastler",
                description="Motor defekt, startet nicht.",
                price=1200,
            ),
            "market_price": 2800,
            "score_range": (0, 35),
            "actions": {"REJECT"},
            "risk": {"HIGH"},
        },
        {
            "name": "dealer_over_market_reject",
            "listing": candidate(
                title="Toyota Yaris gepflegt vom Autohaus",
                description="Gepflegt, Garantie, Finanzierung moeglich.",
                brand="Toyota",
                model="Yaris",
                year=2011,
                mileage=145000,
                price=4500,
                seller_type="dealer",
            ),
            "market_price": 3500,
            "score_range": (0, 55),
            "actions": {"REJECT", "INSPECTION_ONLY"},
            "risk": {"LOW", "MEDIUM"},
        },
        {
            "name": "missing_data_watchlist_baseline",
            "listing": candidate(
                title="Ford Focus Kombi 1.6 TUV bis 2027",
                description="Guter Zustand, einige Kratzer, VB.",
                brand="Ford",
                model="Focus",
                year=None,
                mileage=None,
                fuel=None,
                gearbox=None,
                price=1600,
                listing_age_minutes=None,
                detail_verified=False,
                photo_urls="[]",
            ),
            "market_price": 2600,
            "score_range": (40, 80),
            "actions": {"INSPECTION_ONLY", "FAST_VERIFY", "REJECT"},
            "risk": {"LOW", "MEDIUM"},
        },
    ],
)
def test_analyze_dealer_candidate_baseline_snapshots(monkeypatch: pytest.MonkeyPatch, case: dict) -> None:
    patch_external_databases(monkeypatch)

    result = analyze_dealer_candidate(
        listing=case["listing"],
        warnings=[],
        positive_signals=[],
        model_risks=[],
        market_price=case["market_price"],
        config=BASE_CONFIG,
    )

    low, high = case["score_range"]
    assert low <= result["opportunity_score"] <= high, case["name"]
    assert result["recommended_action"] in case["actions"], case["name"]
    assert result["risk_score"] in case["risk"], case["name"]
    assert set(result["score_breakdown"]) == {
        "freshness",
        "market_discount",
        "liquidity",
        "seller_quality",
        "known_model_problems",
        "repair_risk",
        "flip_potential",
        "data_quality",
        "deep_model_database",
    }


def test_problem_level_kill_reasons_are_exposed(monkeypatch: pytest.MonkeyPatch) -> None:
    patch_external_databases(monkeypatch)

    result = analyze_dealer_candidate(
        listing=candidate(description="Getriebeschaden, ohne Papiere, Bastlerfahrzeug."),
        warnings=[],
        positive_signals=[],
        model_risks=[],
        market_price=3500,
        config=BASE_CONFIG,
    )

    assert result["recommended_action"] == "REJECT"
    assert result["opportunity_score"] <= 35
    assert any("gearbox" in reason.lower() for reason in result["kill_reasons"])
    assert any("missing documents" in reason.lower() for reason in result["kill_reasons"])
