from src.utils import norm


def test_norm_collapses_whitespace_and_applies_replacements() -> None:
    assert norm("  VW   Ä  ") == "volkswagen"


def test_norm_supports_custom_aliases_and_special_cases() -> None:
    assert norm("  ŠKODA  ", aliases={"škoda": "skoda"}) == "skoda"
    assert norm("vw", special_cases={"vw": "volkswagen"}) == "volkswagen"
