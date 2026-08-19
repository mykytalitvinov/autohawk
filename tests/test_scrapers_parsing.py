from __future__ import annotations

import re
from html.parser import HTMLParser
from pathlib import Path

from src.scrapers import (
    _extract_brand_model,
    parse_card_mileage,
    parse_fuel,
    parse_gearbox,
    _parse_listing_age,
    parse_mileage,
    parse_price,
    _parse_tuv_info,
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
    assert parse_price(text) == 2350
    assert html_location("kleinanzeigen_polo_detail.html") == "45127 Essen"
    assert (brand, model) == ("Volkswagen", "Polo")
    assert parse_mileage(text) == 145000
    assert _parse_year(text) == 2009
    assert parse_fuel(text) == "benzin"
    assert parse_gearbox(text) == "manual"
    assert _parse_listing_age(text) == 8


def test_parse_kleinanzeigen_golf_fixture_with_bad_encoding() -> None:
    text = html_text("kleinanzeigen_golf_bad_encoding_detail.html")
    title = html_title("kleinanzeigen_golf_bad_encoding_detail.html")
    brand, model = _extract_brand_model(title)

    assert (brand, model) == ("Volkswagen", "Golf")
    assert parse_price(text) == 1700
    assert html_location("kleinanzeigen_golf_bad_encoding_detail.html") == "50667 Koeln"
    assert parse_mileage(text) == 188000
    assert _parse_year(text) == 2006
    assert parse_fuel(text) == "benzin"
    assert parse_gearbox(text) == "manual"
    assert _parse_listing_age(text) == 60


def test_parse_kleinanzeigen_yaris_fixture() -> None:
    text = html_text("kleinanzeigen_yaris_detail.html")
    title = html_title("kleinanzeigen_yaris_detail.html")
    brand, model = _extract_brand_model(title)

    assert (brand, model) == ("Toyota", "Yaris")
    assert parse_price(text) == 1950
    assert html_location("kleinanzeigen_yaris_detail.html") == "10115 Berlin"
    assert parse_mileage(text) == 172000
    assert _parse_year(text) == 2007
    assert parse_fuel(text) == "benzin"
    assert parse_gearbox(text) == "manual"


def test_mileage_is_only_parsed_from_kilometerstand_like_field() -> None:
    description = (
        "Kupplung wurde bei 25.700 km gemacht. Zahnriemen bei 180.000 km. "
        "Aktuell sehr guter Zustand."
    )
    detail_text = "Kilometerstand\n250.700 km\n" + description

    assert parse_mileage(description) is None
    assert parse_mileage(detail_text) == 250700
    assert parse_mileage("Der Kilometerstand beim Zahnriemenwechsel war 180.000 km") is None
    assert parse_mileage("Kilometerstand\n230.000 km\nFahrzeugzustand\nUnbeschädigtes Fahrzeug") == 230000


def test_mileage_accepts_labelled_value_without_km_suffix() -> None:
    assert parse_mileage("Kilometerstand\n145.000\nBeschreibung: Zahnriemen bei 90.000 km") == 145000
    assert parse_mileage("Kilometerstand: 98 500") == 98500
    assert parse_mileage("Beschreibung: Service bei 98 500 gemacht") is None


def test_card_mileage_reads_structured_search_feature_without_touching_description() -> None:
    card_features = "EZ 03/2009\n230.000 km\nBenzin\nSchaltgetriebe"
    description = "Zahnriemen bei 180.000 km gemacht, Service bei 220.000 km."

    assert parse_card_mileage(card_features) == 230000
    assert parse_card_mileage(description) is None
    assert parse_mileage(card_features) is None


def test_parse_tuv_from_title_description_and_detail_fields() -> None:
    assert _parse_tuv_info("VW Polo 1.2 TÜV 08/2027")["tuv_until"] == "2027-08"
    assert _parse_tuv_info("HU bis 03.28 Klima 5 Türen")["tuv_text"] == "03/2028"
    assert _parse_tuv_info("Das Fahrzeug hat im Feb.26 TÜV bekommen.")["tuv_until"] == "2028-02"
    assert _parse_tuv_info("TÜV neu, Bremse hinten neu")["tuv_text"] == "neu/frisch"
    assert _parse_tuv_info("ohne TÜV, nur Export")["tuv_months_left"] == -1


def test_parse_year_does_not_use_online_or_tuv_year_as_model_year() -> None:
    assert _parse_year("Corsa C Tuv NEU !!!!! Online seit 15.08.2026") is None
    assert _parse_year("TUV 08/2027, guter Zustand") is None
    assert _parse_year("EZ 03/2005 TUV neu") == 2005
    assert _parse_year("Baujahr 2014 Ford Fiesta") == 2014
