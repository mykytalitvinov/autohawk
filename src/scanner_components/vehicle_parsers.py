from __future__ import annotations

import re
from datetime import datetime
from typing import Optional

from src.utils import norm


def clean_lines(text: str) -> list[str]:
    return [line.strip() for line in (text or "").splitlines() if line.strip()]


def value_after_label(text: str, labels: list[str], max_next_lines: int = 3) -> Optional[str]:
    lines = clean_lines(text)
    normalized_labels = [norm(label) for label in labels]
    for idx, line in enumerate(lines):
        line_norm = norm(line)
        for label in normalized_labels:
            if label not in line_norm:
                continue
            after = re.sub(re.escape(label), "", line_norm, count=1).strip(" :,-")
            if after:
                return line
            for offset in range(1, max_next_lines + 1):
                if idx + offset < len(lines) and lines[idx + offset].strip():
                    return lines[idx + offset].strip()
    return None


def parse_price(text: str) -> Optional[float]:
    if not text:
        return None
    clean_text = re.sub(r"[^\d.,]", "", text)
    if not clean_text:
        return None
    if "." in clean_text and "," in clean_text:
        if clean_text.rfind(".") > clean_text.rfind(","):
            clean_text = clean_text.replace(",", "")
        else:
            clean_text = clean_text.replace(".", "").replace(",", ".")
    elif "." in clean_text:
        parts = clean_text.split(".")
        if len(parts) > 2 or len(parts[-1]) == 3:
            clean_text = clean_text.replace(".", "")
    elif "," in clean_text:
        parts = clean_text.split(",")
        clean_text = clean_text.replace(",", "") if len(parts) > 2 or len(parts[-1]) == 3 else clean_text.replace(",", ".")
    try:
        value = float(clean_text)
        return value if 100 < value < 500000 else None
    except ValueError:
        return None


def parse_mileage(text: str) -> Optional[int]:
    if not text:
        return None
    labelled = value_after_label(text, ["Kilometerstand", "Laufleistung", "KM-Stand"], max_next_lines=2)
    if not labelled:
        return None
    match = re.search(r"\b(\d{1,3}(?:[.\s]\d{3})+|\d{4,6})\s*(?:km|kilometer)?\b", labelled, re.IGNORECASE)
    if not match:
        return None
    value = int(re.sub(r"[^\d]", "", match.group(1)))
    return value if 0 <= value <= 500000 else None


def parse_card_mileage(text: str) -> Optional[int]:
    for line in clean_lines(text):
        line_norm = norm(line)
        if any(word in line_norm for word in ["zahnriemen", "service", "inspektion", "olwechsel", "oelwechsel"]):
            continue
        match = re.search(r"\b(\d{1,3}(?:[.\s]\d{3})+|\d{5,6})\s*(?:km|kilometer)\b", line, re.IGNORECASE)
        if match:
            value = int(re.sub(r"[^\d]", "", match.group(1)))
            if 0 <= value <= 500000:
                return value
    return None


def parse_fuel(text: str) -> Optional[str]:
    text_l = norm(value_after_label(text, ["Kraftstoffart", "Kraftstoff", "Antriebsart"]) or text)
    for value in ["diesel", "benzin", "hybrid", "elektro", "lpg", "cng"]:
        if value in text_l:
            return value
    return None


def parse_gearbox(text: str) -> Optional[str]:
    text_l = norm(value_after_label(text, ["Getriebe"]) or text)
    if any(value in text_l for value in ["automatik", "automatic", "dsg"]):
        return "automatic"
    if any(value in text_l for value in ["schaltgetriebe", "schalter", "manuell"]):
        return "manual"
    return None


def extract_engine(text: str) -> Optional[str]:
    patterns = [
        r"\b([123]\.[0-9]\s*(?:tdi|tsi|tfsi|fsi|cdi|dci|hdi|cdti|ecoboost|jtd|multijet))\b",
        r"\b(3[12]0d|3[12]8d|3[12]0i|1[12]6i|1[12]8i|1[12]0d|1[12]0i)\b",
        r"\b(c\s?180|c\s?200|c\s?220|e\s?200|e\s?220)\b",
        r"\b(n47|n57|m271|om651)\b",
    ]
    for pattern in patterns:
        match = re.search(pattern, norm(text))
        if match:
            return re.sub(r"\s+", "", match.group(1)).upper()
    return None


def parse_explicit_vehicle_year(text: str) -> Optional[int]:
    if not text:
        return None
    current_year = datetime.now().year
    labelled = value_after_label(text, ["Erstzulassung", "Baujahr", "Erstzul.", "EZ", "Bj", "BJ"], max_next_lines=2)
    for candidate in [labelled, text]:
        if not candidate:
            continue
        match = re.search(r"\b(?:erstzulassung|erstzul\.?|ez|baujahr|bj)\b\D{0,16}(?:\d{1,2}[./])?(19[5-9]\d|20[012]\d)\b", candidate, re.IGNORECASE)
        if match and 1950 <= int(match.group(1)) <= current_year:
            return int(match.group(1))
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
    exact = re.search(r"\b(?:erstzulassung|erstzul\.?|ez|baujahr|bj)\b\D{0,18}(?:\d{1,2}[./])?(19[5-9]\d|20[012]\d)", norm(text), re.IGNORECASE)
    if exact and not re.search(r"tuv|tuev|hu", exact.group(0), re.IGNORECASE):
        return int(exact.group(1))
    for match in re.finditer(r"\b(19[5-9]\d|20[012]\d)\b", text):
        year = int(match.group(1))
        context = norm(text[max(0, match.start() - 18): match.end() + 18])
        if year <= current_year and not any(value in context for value in ["online seit", "anzeige online", "eingestellt", "inseriert", "tuev", "tuv", "hauptuntersuchung"]):
            if year != current_year or re.search(r"\b(erstzulassung|baujahr|ez|bj)\b", context):
                return year
    return None


def parse_tuv_info(text: str) -> dict:
    result = {"tuv_text": None, "tuv_until": None, "tuv_months_left": None}
    if not text:
        return result
    text_norm = norm(text)
    terms = r"tuev|tuv|hu|hauptuntersuchung"
    if re.search(rf"\b(ohne|kein|keine|abgelaufen|faellig|fallig)\s*({terms})\b|\b({terms})\s*(abgelaufen|faellig|fallig)\b", text_norm, re.IGNORECASE):
        result.update({"tuv_text": "kein/unklar", "tuv_months_left": -1})
        return result
    months = {"jan": 1, "januar": 1, "feb": 2, "februar": 2, "mar": 3, "marz": 3, "maerz": 3, "apr": 4, "april": 4, "mai": 5, "jun": 6, "juni": 6, "jul": 7, "juli": 7, "aug": 8, "august": 8, "sep": 9, "sept": 9, "september": 9, "okt": 10, "oktober": 10, "nov": 11, "november": 11, "dez": 12, "dezember": 12}
    month_pattern = "|".join(sorted((re.escape(key) for key in months), key=len, reverse=True))
    candidates = []
    for pattern in [rf"\b(?:{terms})\s*(?:bis|gültig bis|gueltig bis)?\s*(0?[1-9]|1[0-2])\s*[./-]\s*(2[6-9]|20[2-3][0-9])\b", rf"\b({month_pattern})[.\s/-]*(2[6-9]|20[2-3][0-9])\b"]:
        for match in re.finditer(pattern, text_norm, re.IGNORECASE):
            month = int(match.group(1)) if match.group(1).isdigit() else months.get(match.group(1).lower(), 0)
            year = int(match.group(2))
            year += 2000 if year < 100 else 0
            if 1 <= month <= 12 and 2026 <= year <= 2035:
                candidates.append((year, month))
    if candidates:
        year, month = max(candidates)
        now = datetime.now()
        if re.search(r"\b(?:bekommen|gemacht|neu|frisch)\b", text_norm, re.IGNORECASE) and not re.search(rf"\b(?:{terms})\s*(?:bis|gueltig bis|gültig bis)\b", text_norm, re.IGNORECASE) and (year, month) < (now.year, now.month):
            year += 2
        result.update({"tuv_text": f"{month:02d}/{year}", "tuv_until": f"{year:04d}-{month:02d}", "tuv_months_left": (year - now.year) * 12 + month - now.month})
        return result
    if re.search(rf"\b(?:{terms})\s*(?:neu|frisch|gemacht|bekommen|gueltig|gültig)\b|\bfrisch(?:er)?\s*(?:{terms})\b", text_norm, re.IGNORECASE):
        result.update({"tuv_text": "neu/frisch", "tuv_months_left": 24})
    return result
