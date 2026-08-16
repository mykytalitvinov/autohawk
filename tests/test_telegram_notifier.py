from __future__ import annotations

from types import SimpleNamespace

from src.telegram_notifier import format_telegram_message


def test_format_telegram_message_contains_specific_deal_analysis() -> None:
    listing = SimpleNamespace(
        brand="VW",
        model="Polo",
        title="VW Polo 1.4 TUV 08/2027",
        year=2009,
        price=3490,
        mileage=182000,
        estimated_market_price=4190,
        estimated_margin=450,
        final_score=0.91,
        risk_score=0.87,
        liquidity_score=0.94,
        condition_score=0.81,
        verdict="GOOD",
        url="https://example.test/polo",
        tuv_text="08/2027",
        what_to_check="Olverlust; Steuerkette; Kupplung",
        possible_risks="Possible chain noise on cold start",
        model_specific_issues="VW small petrol: check chain/service history",
        seller_signals="Private seller, fresh TUV",
        why_interesting="Fresh HU/TUV; high liquidity; estimated net profit after costs/reserve: 450 EUR",
        description="HU bis 08/2027",
    )

    message = format_telegram_message(listing)

    assert "НОВЫЙ ВАРИАНТ AUTOHAWK" in message
    assert "VW Polo" in message
    assert "3.490 EUR" in message
    assert "182.000 km" in message
    assert "TÜV/HU: <b>08/2027</b>" in message
    assert "score 91/100" in message
    assert "Цена примерно на 700 EUR ниже оценки рынка" in message
    assert "Надежность: 87/100" in message
    assert "Ликвидность: 94/100" in message
    assert "Olverlust" in message
    assert "Steuerkette" in message
    assert "Kupplung" in message
    assert "Открыть объявление" in message


def test_format_telegram_message_flags_cheap_high_mileage_premium_as_risk_lot() -> None:
    listing = SimpleNamespace(
        brand="Audi",
        model="A4",
        title="Audi A4 2.7 TDI",
        year=2008,
        price=1500,
        mileage=399626,
        estimated_market_price=2600,
        estimated_margin=300,
        final_score=0.74,
        risk_score=0.35,
        liquidity_score=0.70,
        condition_score=0.45,
        verdict="CHECK",
        url="https://example.test/audi",
        tuv_text="05/2028",
        what_to_check="Automatik; DPF/EGR; Turbo; OBD",
        possible_risks="Beschadigtes Fahrzeug; very high mileage",
        model_specific_issues="Audi V6 TDI: expensive diesel and automatic risks",
        seller_signals="Reserviert",
        why_interesting="Very cheap premium car with long TUV",
        description="Beschadigtes Fahrzeug, fahrbereit, Automatik",
    )

    message = format_telegram_message(listing)

    assert "дешевый премиум-риск" in message
    assert "399.626 km" in message
    assert "Beschädigtes Fahrzeug" in message or "Beschadigtes Fahrzeug" in message
    assert "DPF/EGR/Turbo/Injektoren" in message
    assert "Schaltet das Getriebe" in message
