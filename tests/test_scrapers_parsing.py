from __future__ import annotations

import re
from html.parser import HTMLParser
from pathlib import Path

from src.scrapers import (
    _extract_brand_model,
    _parse_fuel,
    _parse_gearbox,
    _parse_listing_age,
    _parse_mileage,
    _parse_price,
    _parse_year,
)


FIXTURES = Path(__file__).resolve().parent / "fixtures"


class TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        clean = data.strip()
        if clean:
            self.parts.append(clean)


def html_text(name: str) -> str:
    parser = TextExtractor()
    parser.feed((FIXTURES / name).read_text(encoding="utf-8"))
    return "\n".join(parser.parts)


def html_title(name: str) -> str:
    html = (FIXTURES / name).read_text(encoding="utf-8")
    match = re.search(r"<h1[^>]*>(.*?)</h1>", html, re.IGNORECASE | re.DOTALL)
    assert match, f"fixture {name} has no h1"
    return re.sub(r"<[^>]+>", "", match.group(1)).strip()


def html_location(name: str) -> str:
    html = (FIXTURES / name).read_text(encoding="utf-8")
    match = re.search(r'data-testid="vip-address"[^>]*>(.*?)<', html, re.IGNORECASE | re.DOTALL)
    assert match, f"fixture {name} has no anonymized location"
    return re.sub(r"\s+", " ", match.group(1)).strip()


def test_parse_kleinanzeigen_polo_fixture() -> None:
    text = html_text("kleinanzeigen_polo_detail.html")
    title = html_title("kleinanzeigen_polo_detail.html")
    brand, model = _extract_brand_model(title)

    assert title == "VW Polo 1.2 United TUV neu Klima"
    assert _parse_price(text) == 2350
    assert html_location("kleinanzeigen_polo_detail.html") == "45127 Essen"
    assert (brand, model) == ("Volkswagen", "Polo")
    assert _parse_mileage(text) == 145000
    assert _parse_year(text) == 2009
    assert _parse_fuel(text) == "benzin"
    assert _parse_gearbox(text) == "manual"
    assert _parse_listing_age(text) == 8


def test_parse_kleinanzeigen_golf_fixture_with_bad_encoding() -> None:
    text = html_text("kleinanzeigen_golf_bad_encoding_detail.html")
    title = html_title("kleinanzeigen_golf_bad_encoding_detail.html")
    brand, model = _extract_brand_model(title)

    assert (brand, model) == ("Volkswagen", "Golf")
    assert _parse_price(text) == 1700
    assert html_location("kleinanzeigen_golf_bad_encoding_detail.html") == "50667 Koeln"
    assert _parse_mileage(text) == 188000
    assert _parse_year(text) == 2006
    assert _parse_fuel(text) == "benzin"
    assert _parse_gearbox(text) == "manual"
    assert _parse_listing_age(text) == 60


def test_parse_kleinanzeigen_yaris_fixture() -> None:
    text = html_text("kleinanzeigen_yaris_detail.html")
    title = html_title("kleinanzeigen_yaris_detail.html")
    brand, model = _extract_brand_model(title)

    assert (brand, model) == ("Toyota", "Yaris")
    assert _parse_price(text) == 1950
    assert html_location("kleinanzeigen_yaris_detail.html") == "10115 Berlin"
    assert _parse_mileage(text) == 172000
    assert _parse_year(text) == 2007
    assert _parse_fuel(text) == "benzin"
    assert _parse_gearbox(text) == "manual"


def test_mileage_is_only_parsed_from_kilometerstand_like_field() -> None:
    description = (
        "Kupplung wurde bei 25.700 km gemacht. Zahnriemen bei 180.000 km. "
        "Aktuell sehr guter Zustand."
    )
    detail_text = "Kilometerstand\n250.700 km\n" + description

    assert _parse_mileage(description) is None
    assert _parse_mileage(detail_text) == 250700
