from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Optional

from src.scanner_components.vehicle_parsers import value_after_label
from src.utils import norm


def parse_listing_age(text: str) -> Optional[int]:
    """Parse relative listing time into minutes."""
    if not text:
        return None
    text = text.lower().strip()
    now = datetime.now()
    online_match = re.search(r"(?:online seit|anzeige online seit|eingestellt am)\s*[:\n ]*([^\n\r]+)", text)
    if online_match:
        text = online_match.group(1).strip()
    if "gerade" in text or "soeben" in text or "just" in text:
        return 2
    match = re.search(r"vor\s+(\d+)\s*min", text) or re.search(r"(\d+)\s*min", text)
    if match:
        return int(match.group(1))
    match = re.search(r"vor\s+(\d+)\s*stunde?n?", text) or re.search(r"(\d+)\s*stunde?n?", text)
    if match:
        return int(match.group(1)) * 60
    match = re.search(r"heute\s*,?\s*(\d{1,2}):(\d{2})", text)
    if match:
        posted = now.replace(hour=int(match.group(1)), minute=int(match.group(2)), second=0, microsecond=0)
        return max(0, int((now - posted).total_seconds() // 60))
    match = re.search(r"gestern\s*,?\s*(\d{1,2}):(\d{2})", text)
    if match:
        posted = (now - timedelta(days=1)).replace(hour=int(match.group(1)), minute=int(match.group(2)), second=0, microsecond=0)
        return max(0, int((now - posted).total_seconds() // 60))
    match = re.search(r"(\d+)\s*tag", text)
    if match:
        return int(match.group(1)) * 1440
    match = re.search(r"\b(\d{1,2})[.](\d{1,2})[.](20\d{2})\b", text)
    if match:
        posted = datetime(int(match.group(3)), int(match.group(2)), int(match.group(1)))
        return max(0, int((now - posted).total_seconds() // 60))
    return None


def parse_detail_listing_age(text: str) -> Optional[int]:
    labelled = value_after_label(text, ["Online seit", "Anzeige online seit", "Eingestellt am"], max_next_lines=2)
    if labelled:
        return parse_listing_age(labelled)
    return None


def parse_explicit_vehicle_year(text: str) -> Optional[int]:
    if not text:
        return None
    current_year = datetime.now().year
    labelled = value_after_label(text, ["Erstzulassung", "Baujahr", "Erstzul.", "EZ", "Bj", "BJ"], max_next_lines=2)
    for candidate in [labelled, text]:
        if not candidate:
            continue
        match = re.search(
            r"\b(?:erstzulassung|erstzul\.?|ez|baujahr|bj)\b\D{0,16}(?:\d{1,2}[./])?(19[5-9]\d|20[012]\d)\b",
            candidate,
            re.IGNORECASE,
        )
        if match:
            year = int(match.group(1))
            if 1950 <= year <= current_year:
                return year
    return None


def parse_year(text: str) -> Optional[int]:
    if not text:
        return None
    current_year = datetime.now().year
    labelled = value_after_label(text, ["Erstzulassung", "Baujahr", "Erstzul.", "EZ", "Bj", "BJ"])
    if labelled:
        match = re.search(r"(?:\d{1,2}[./])?(19[5-9]\d|20[012]\d)", labelled)
        if match and 1950 <= int(match.group(1)) <= current_year:
            return int(match.group(1))

    text_norm = norm(text)
    exact = re.search(
        r"\b(?:erstzulassung|erstzul\.?|ez|baujahr|bj)\b\D{0,18}(?:\d{1,2}[./])?(19[5-9]\d|20[012]\d)",
        text_norm,
        re.IGNORECASE,
    )
    if exact:
        year = int(exact.group(1))
        if year <= current_year and not re.search(r"tuv|tuev|hu", norm(exact.group(0)), re.IGNORECASE):
            return year

    for match in re.finditer(r"\b(19[5-9]\d|20[012]\d)\b", text):
        year = int(match.group(1))
        context_norm = norm(text[max(0, match.start() - 18): match.end() + 18])
        if year > current_year or (year == current_year and not re.search(r"\b(erstzulassung|baujahr|ez|bj)\b", context_norm)):
            continue
        if any(value in context_norm for value in ["online seit", "anzeige online", "eingestellt", "inseriert", "tuev", "tuv", "hauptuntersuchung"]):
            continue
        return year
    return None


def parse_tuv_info(text: str) -> dict:
    result = {"tuv_text": None, "tuv_until": None, "tuv_months_left": None}
    if not text:
        return result
    text_norm = norm(text)
    tuv_terms = r"tuev|tuv|hu|hauptuntersuchung"
    if re.search(rf"\b(ohne|kein|keine|abgelaufen|faellig|fallig)\s*({tuv_terms})\b|\b({tuv_terms})\s*(abgelaufen|faellig|fallig)\b", text_norm, re.IGNORECASE):
        result.update({"tuv_text": "kein/unklar", "tuv_months_left": -1})
        return result

    month_names = {
        "jan": 1, "januar": 1, "feb": 2, "februar": 2, "mar": 3, "marz": 3, "maerz": 3,
        "apr": 4, "april": 4, "mai": 5, "jun": 6, "juni": 6, "jul": 7, "juli": 7,
        "aug": 8, "august": 8, "sep": 9, "sept": 9, "september": 9, "okt": 10,
        "oktober": 10, "nov": 11, "november": 11, "dez": 12, "dezember": 12,
    }
    candidates: list[tuple[int, int]] = []
    month_pattern = "|".join(sorted((re.escape(key) for key in month_names), key=len, reverse=True))
    patterns = [
        rf"\b(?:{tuv_terms})\s*(?:bis|gültig bis|gueltig bis)?\s*(0?[1-9]|1[0-2])\s*[./-]\s*(2[6-9]|20[2-3][0-9])\b",
        rf"\b({month_pattern})[.\s/-]*(2[6-9]|20[2-3][0-9])\b",
    ]
    for pattern in patterns:
        for match in re.finditer(pattern, text_norm, re.IGNORECASE):
            month_value = match.group(1)
            month = int(month_value) if month_value.isdigit() else month_names.get(month_value.lower(), 0)
            year = int(match.group(2))
            year = year + 2000 if year < 100 else year
            if 1 <= month <= 12 and 2026 <= year <= 2035:
                candidates.append((year, month))

    if candidates:
        year, month = max(candidates)
        now = datetime.now()
        inspection_completed = re.search(r"\b(?:bekommen|gemacht|neu|frisch)\b", text_norm, re.IGNORECASE)
        explicit_until = re.search(rf"\b(?:{tuv_terms})\s*(?:bis|gueltig bis|gültig bis)\b", text_norm, re.IGNORECASE)
        if inspection_completed and not explicit_until and (year, month) < (now.year, now.month):
            year += 2
        months_left = (year - now.year) * 12 + month - now.month
        result.update({"tuv_text": f"{month:02d}/{year}", "tuv_until": f"{year:04d}-{month:02d}", "tuv_months_left": months_left})
        return result
    if re.search(rf"\b(?:{tuv_terms})\s*(?:neu|frisch|gemacht|bekommen|gueltig|gültig)\b|\bfrisch(?:er)?\s*(?:{tuv_terms})\b", text_norm, re.IGNORECASE):
        result.update({"tuv_text": "neu/frisch", "tuv_months_left": 24})
    return result
