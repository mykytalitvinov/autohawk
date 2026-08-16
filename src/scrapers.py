"""
AUTOHAWK ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬Ãƒâ€¦Ã‚Â¡ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â¬ÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã…Â¡Ãƒâ€šÃ‚Â¬ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â Kleinanzeigen Scraper
Scrapes fresh car listings from Kleinanzeigen.de
Uses Playwright with realistic delays and headers.
Stops gracefully on captcha.
"""

import asyncio
import hashlib
import json
import logging
import random
import re
from datetime import datetime, timedelta
from typing import List, Optional
from src.utils import norm
logger = logging.getLogger("autohawk.scraper.kleinanzeigen")


EARLY_REJECT_RULES = [
    ("deleted listing", r"\bgeloescht\b|\bgeloscht\b|\bdeleted\b|\bnicht mehr verfuegbar\b"),
    ("purchase ad / car buyer", r"\bsuche\s+kaufe\b|\bwir\s+kaufen\b|\bfahrzeugankauf\b|\bautoankauf\b|\bankauf\b"),
    ("engine damage", r"\bmotorschaden\b|\bmotor\s*schaden\b|\bmotor\s*defekt\b"),
    ("engine runs poorly", r"\bmotor\b.{0,80}\b(unruhig|ruckelt|stottert|geht\s*aus|leistungsverlust)\b|\bunruhiger\s*motor\b|\bmotor\s*(?:laeuft|läuft|lauft)\s*(?:gelegentlich\s*)?unruhig\b|\bmotorproblem\b|\bmotor\s*problem\b"),
    ("cold-start engine issue", r"\bkalt\b.{0,40}\b(motor|laeuft|läuft|lauft|start)\b.{0,40}\b(schlecht|unruhig|ruckelt|stottert)\b|\bmotor\b.{0,40}\bkalt\b.{0,40}\b(schlecht|unruhig|ruckelt|stottert)\b"),
    ("warning light", r"\bmotorkontrollleuchte\b|\bmotor\s*kontrollleuchte\b|\bkontrollleuchte\b|\bmotorlampe\b|\bcheck\s*engine\b|\bmkl\b"),
    ("engine sensor defect", r"\b(?:oel|ol|öl)\s*standsensor\s*defekt\b|\b(?:oel|ol|öl)standsensor\s*defekt\b|\blambdasonde\b.{0,40}\b(erneuert|defekt|fehler)\b"),
    ("high oil consumption", r"\boelverbrauch\b|\boel\s*verbrauch\b|\bverbrauch[t]?\s*oel\b|\b[0-9]+(?:[,.][0-9]+)?\s*l(?:iter)?\s*oel\b.{0,20}\b(1000|1\.000)\s*km\b"),
    ("gearbox damage/problem", r"\bgetriebeschaden\b|\bgetriebe\s*schaden\b|\bgetriebe\s*defekt\b|\bgetriebe\s*problem\b|\bautomatik\s*problem\b|\bautomatikgetriebe\s*problem\b"),
    ("project/Bastler car", r"\bbastler\b|\bbastlerfahrzeug\b|\bprojektfahrzeug\b"),
    ("export only", r"\bnur\s*export\b|\bexport\s*only\b|\bexportfahrzeug\b"),
    ("not roadworthy / does not drive", r"\bnicht\s*fahrbereit\b|\bstartet\s*nicht\b|\bfaehrt\s*nicht\b"),
    ("accident/salvage wording", r"\bunfallwagen\b|\bunfallschaden\b|\btotalschaden\b|\b[0-9]+\s*unfaelle\b|\b[0-9]+\s*unfall\b|\bunfall\b.{0,40}\b(repariert|gehabt|vorbesitzer|bekannt|schaden)\b"),
    ("parts car", r"\bersatzteiltraeger\b|\bersatzteile\b|\bschlachtfest\b"),
]

EARLY_REJECT_PATTERNS = [pattern for _, pattern in EARLY_REJECT_RULES]



def _clean_lines(text: str) -> list[str]:
    return [line.strip() for line in (text or "").splitlines() if line.strip()]


def _value_after_label(text: str, labels: list[str], max_next_lines: int = 3) -> Optional[str]:
    """Read values that sit next to detail-page labels such as Kilometerstand."""
    lines = _clean_lines(text)
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
                if idx + offset < len(lines):
                    candidate = lines[idx + offset].strip()
                    if candidate:
                        return candidate
    return None


def _early_reject_reason(text: str) -> Optional[str]:
    normalized = norm(text)
    normalized = re.sub(r"\bunfall\s+frei\b", "unfallfrei", normalized)
    if re.search(
        r"\bunfall(?!frei)\b|\bunfallfahrzeug\b|\bunfallwagen\b|\bunfallschaden\b|"
        r"\bnach\s*unfall\b|\bfrontschaden\b|\bheckschaden\b|\bseitenschaden\b|"
        r"\btotalschaden\b|\brahmenschaden\b",
        normalized,
        re.IGNORECASE,
    ):
        return "accident/salvage wording"
    for label, pattern in EARLY_REJECT_RULES:
        if re.search(pattern, normalized, re.IGNORECASE):
            return label
    return None

def _is_generic_detail_heading(text: str) -> bool:
    """Kleinanzeigen can expose SEO/search headings as h1 on some pages.
    Never let those overwrite the real card title.
    """
    normalized = norm(text)
    if not normalized:
        return True
    generic_tokens = [
        "autos in ",
        "gebrauchtwagen in ",
        "fahrzeuge in ",
        "1 - 25 von",
        "1-25 von",
        "anzeigen in ",
    ]
    return any(token in normalized for token in generic_tokens)


def _parse_explicit_vehicle_year(text: str) -> Optional[int]:
    """Parse only clearly labelled vehicle years from description text.
    This avoids reading TUV/service dates as the car year.
    """
    if not text:
        return None
    current_year = datetime.now().year
    labelled = _value_after_label(text, ["Erstzulassung", "Baujahr", "Erstzul.", "EZ", "Bj", "BJ"], max_next_lines=2)
    candidates = []
    if labelled:
        candidates.append(labelled)
    candidates.append(text)
    for candidate in candidates:
        m = re.search(
            r"\b(?:erstzulassung|erstzul\.?|ez|baujahr|bj)\b\D{0,16}(?:\d{1,2}[./])?(19[5-9]\d|20[012]\d)\b",
            norm(candidate),
            re.IGNORECASE,
        )
        if m:
            year = int(m.group(1))
            if 1950 <= year <= current_year:
                return year
    return None


def _fingerprint(title: str, price, mileage, location: str) -> str:
    raw = f"{title}|{price}|{mileage}|{location}"
    return hashlib.md5(raw.encode()).hexdigest()[:16]


def _parse_price(text: str) -> Optional[float]:
    if not text:
        return None
    nums = re.findall(r"[\d.,]+", text.replace(".", "").replace(",", "."))
    for n in nums:
        try:
            val = float(n)
            if 100 < val < 500000:
                return val
        except:
            pass
    return None


def _parse_mileage(text: str) -> Optional[int]:
    if not text:
        return None
    labelled = None
    lines = _clean_lines(text)
    for idx, line in enumerate(lines):
        line_norm = norm(line)
        if not re.match(r"^(kilometerstand|laufleistung|km-stand)\b", line_norm):
            continue
        after = re.sub(r"^(kilometerstand|laufleistung|km-stand)\b", "", line_norm, count=1).strip(" :,-")
        if after:
            labelled = line
            break
        for offset in range(1, 3):
            if idx + offset < len(lines):
                candidate = lines[idx + offset].strip()
                if candidate:
                    labelled = candidate
                    break
        if labelled:
            break
    if not labelled:
        labelled_match = re.search(
            r"(?im)^\s*(?:kilometerstand|laufleistung|km-stand)\b.{0,90}?(\d{1,3}(?:[.\s]\d{3})+|\d{4,6})\s*(?:km|kilometer)?\b",
            norm(text),
            re.IGNORECASE | re.DOTALL,
        )
        if labelled_match:
            labelled = labelled_match.group(0)
        else:
            return None

    m = re.search(
        r"\b(\d{1,3}(?:[.\s]\d{3})+|\d{4,6})\s*(?:km|kilometer)\b",
        labelled,
        re.IGNORECASE,
    )
    if not m:
        m = re.search(r"\b(\d{1,3}(?:[.\s]\d{3})+|\d{5,6})\b", labelled, re.IGNORECASE)
    if not m:
        return None
    try:
        value = int(re.sub(r"[^\d]", "", m.group(1)))
        if 0 <= value <= 500000:
            return value
    except Exception:
        pass
    return None


def _parse_card_mileage(text: str) -> Optional[int]:
    """Parse mileage from a structured search-result vehicle-features block only.

    This is deliberately separate from _parse_mileage(). Search cards often show
    a clean feature line like "230.000 km" without the "Kilometerstand" label,
    while seller descriptions contain misleading service-history numbers.
    """
    if not text:
        return None
    lines = _clean_lines(text)
    for line in lines:
        line_norm = norm(line)
        if any(word in line_norm for word in ["zahnriemen", "service", "inspektion", "olwechsel", "oelwechsel"]):
            continue
        match = re.search(r"\b(\d{1,3}(?:[.\s]\d{3})+|\d{5,6})\s*(?:km|kilometer)\b", line, re.IGNORECASE)
        if not match:
            continue
        try:
            value = int(re.sub(r"[^\d]", "", match.group(1)))
        except Exception:
            continue
        if 0 <= value <= 500000:
            return value
    return None


def _parse_tuv_info(text: str) -> dict:
    """Extract HU/TUV status from structured facts, title or seller description."""
    result = {"tuv_text": None, "tuv_until": None, "tuv_months_left": None}
    if not text:
        return result

    text_n = norm(text)
    if re.search(
        r"\b(ohne|kein|keine|abgelaufen|faellig|fallig)\s*(tuev|tuv|hu|hauptuntersuchung)\b|"
        r"\b(tuev|tuv|hu|hauptuntersuchung)\s*(abgelaufen|faellig|fallig)\b",
        text_n,
        re.IGNORECASE,
    ):
        result.update({"tuv_text": "kein/unklar", "tuv_months_left": -1})
        return result

    now = datetime.now()
    month_names = {
        "jan": 1, "januar": 1,
        "feb": 2, "februar": 2,
        "mar": 3, "marz": 3, "maerz": 3, "märz": 3,
        "apr": 4, "april": 4,
        "mai": 5,
        "jun": 6, "juni": 6,
        "jul": 7, "juli": 7,
        "aug": 8, "august": 8,
        "sep": 9, "sept": 9, "september": 9,
        "okt": 10, "oktober": 10,
        "nov": 11, "november": 11,
        "dez": 12, "dezember": 12,
    }

    def normalize_year(value: str) -> int:
        year = int(value)
        return year + 2000 if year < 100 else year

    def add_candidate(candidates: list[tuple[int, int]], month: int, year_raw: str) -> None:
        year = normalize_year(year_raw)
        if 2026 <= year <= 2035 and 1 <= month <= 12:
            candidates.append((year, month))

    candidates: list[tuple[int, int]] = []
    patterns = [
        r"\b(?:tuev|tuv|hu|hauptuntersuchung|tuev/au|tuv/au|hu/au)\s*(?:bis|gültig bis|gueltig bis)?\s*(0?[1-9]|1[0-2])\s*[./-]\s*(2[6-9]|20[2-3][0-9])\b",
        r"\b(0?[1-9]|1[0-2])\s*[./-]\s*(2[6-9]|20[2-3][0-9])\b.{0,28}\b(?:tuev|tuv|hu|hauptuntersuchung)\b",
    ]
    for pattern in patterns:
        for match in re.finditer(pattern, text_n, re.IGNORECASE):
            add_candidate(candidates, int(match.group(1)), match.group(2))

    # Real seller text often says "im Feb.26 TUV bekommen" instead of
    # "TUV bis 02/2028". Keep this separate and boring so it is hard to break.
    for month_name, month_num in sorted(month_names.items(), key=lambda item: len(item[0]), reverse=True):
        for match in re.finditer(rf"\b{re.escape(month_name)}\.?\s*[/\-\s.]?\s*(2[6-9]|20[2-3][0-9])\b", text_n, re.IGNORECASE):
            context = text_n[max(0, match.start() - 60): match.end() + 60]
            if re.search(r"\b(tuev|tuv|hu|hauptuntersuchung|bekommen|gemacht|neu|frisch)\b", context, re.IGNORECASE):
                add_candidate(candidates, month_num, match.group(1))

    month_pattern = "|".join(sorted((re.escape(k) for k in month_names), key=len, reverse=True))
    named_patterns = [
        rf"\b(?:tuev|tuv|hu|hauptuntersuchung)\s*(?:bis|gueltig bis|gultig bis)?\s*({month_pattern})[.\s/-]*(2[6-9]|20[2-3][0-9])\b",
        rf"\b({month_pattern})[.\s/-]*(2[6-9]|20[2-3][0-9])\b.{0,40}\b(?:tuev|tuv|hu|hauptuntersuchung)\b",
    ]
    for pattern in named_patterns:
        for match in re.finditer(pattern, text_n, re.IGNORECASE):
            add_candidate(candidates, month_names.get(match.group(1).lower(), 0), match.group(2))

    if candidates:
        year, month = max(candidates)
        got_inspection_wording = re.search(
            r"\b(?:tuev|tuv|hu|hauptuntersuchung)\b.{0,40}\b(?:bekommen|gemacht|neu|frisch)\b|"
            r"\b(?:bekommen|gemacht)\b.{0,40}\b(?:tuev|tuv|hu|hauptuntersuchung)\b",
            text_n,
            re.IGNORECASE,
        )
        explicit_until_wording = re.search(
            r"\b(?:tuev|tuv|hu|hauptuntersuchung)\s*(?:bis|gueltig bis|gültig bis)\b",
            text_n,
            re.IGNORECASE,
        )
        if got_inspection_wording and not explicit_until_wording and (year, month) < (now.year, now.month):
            year += 2
        months_left = (year - now.year) * 12 + (month - now.month)
        result.update({
            "tuv_text": f"{month:02d}/{year}",
            "tuv_until": f"{year:04d}-{month:02d}",
            "tuv_months_left": months_left,
        })
        return result

    if re.search(
        r"\b(?:tuev|tuv|hu|hauptuntersuchung)\s*(?:neu|frisch|gemacht|bekommen|gueltig|gültig)\b|"
        r"\bfrisch(?:er)?\s*(?:tuev|tuv|hu)\b",
        text_n,
        re.IGNORECASE,
    ):
        result.update({"tuv_text": "neu/frisch", "tuv_months_left": 24})

    return result


def _parse_year(text: str) -> Optional[int]:
    if not text:
        return None
    current_year = datetime.now().year
    labelled = _value_after_label(text, ["Erstzulassung", "Baujahr", "Erstzul.", "EZ", "Bj", "BJ"])
    if labelled:
        m = re.search(r"(?:\d{1,2}[./])?(19[5-9]\d|20[012]\d)", labelled)
        if m:
            year = int(m.group(1))
            if 1950 <= year <= current_year:
                return year

    text_norm = norm(text)
    exact = re.search(
        r"\b(?:erstzulassung|erstzul\.?|ez|baujahr|bj)\b\D{0,18}(?:\d{1,2}[./])?(19[5-9]\d|20[012]\d)",
        text_norm,
        re.IGNORECASE,
    )
    if exact:
        year = int(exact.group(1))
        if year <= current_year and not ("tuv" in norm(exact.group(0)) or "tuev" in norm(exact.group(0)) or "hu" in norm(exact.group(0))):
            return year
    for m in re.finditer(r"\b(19[5-9]\d|20[012]\d)\b", text):
        year = int(m.group(1))
        context = text[max(0, m.start() - 18): m.end() + 18].lower()
        context_norm = norm(context)
        if year > current_year:
            continue
        # Skip generic current-year matches unless they are clearly registration/year labels;
        # many listings contain HU/TUV expiry dates such as 07/26 or 12/26.
        if year == current_year and not re.search(r"\b(erstzulassung|baujahr|ez|bj)\b", context_norm):
            continue
        if any(x in context_norm for x in ["online seit", "anzeige online", "eingestellt", "inseriert"]):
            continue
        if "tuev" in context_norm or "tuv" in context_norm or "hauptuntersuchung" in context_norm:
            continue
        if "tÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã¢â‚¬Â ÃƒÂ¢Ã¢â€šÂ¬Ã¢â€žÂ¢ÃƒÆ’Ã†â€™ÃƒÂ¢Ã¢â€šÂ¬Ã…Â¡ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â¼v" in context or "hauptuntersuchung" in context:
            continue
        if "tuv" in context or "tÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã¢â‚¬Â ÃƒÂ¢Ã¢â€šÂ¬Ã¢â€žÂ¢ÃƒÆ’Ã†â€™ÃƒÂ¢Ã¢â€šÂ¬Ã…Â¡ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â¼v" in context or "hu" in context:
            continue
        return year
    return None


def _parse_listing_age(text: str) -> Optional[int]:
    """Parse relative time to minutes."""
    if not text:
        return None
    text = text.lower().strip()
    now = datetime.now()
    online_match = re.search(r"(?:online seit|anzeige online seit|eingestellt am)\s*[:\n ]*([^\n\r]+)", text)
    if online_match:
        text = online_match.group(1).strip()
    if "gerade" in text or "soeben" in text or "just" in text:
        return 2
    m = re.search(r"vor\s+(\d+)\s*min", text)
    if m:
        return int(m.group(1))
    m = re.search(r"(\d+)\s*min", text)
    if m:
        return int(m.group(1))
    m = re.search(r"vor\s+(\d+)\s*stunde?n?", text)
    if m:
        return int(m.group(1)) * 60
    m = re.search(r"(\d+)\s*stunde?n?", text)
    if m:
        return int(m.group(1)) * 60
    m = re.search(r"heute\s*,?\s*(\d{1,2}):(\d{2})", text)
    if m:
        posted = now.replace(hour=int(m.group(1)), minute=int(m.group(2)), second=0, microsecond=0)
        return max(0, int((now - posted).total_seconds() // 60))
    m = re.search(r"gestern\s*,?\s*(\d{1,2}):(\d{2})", text)
    if m:
        posted = (now - timedelta(days=1)).replace(hour=int(m.group(1)), minute=int(m.group(2)), second=0, microsecond=0)
        return max(0, int((now - posted).total_seconds() // 60))
    m = re.search(r"(\d+)\s*tag", text)
    if m:
        return int(m.group(1)) * 1440
    m = re.search(r"\b(\d{1,2})[.](\d{1,2})[.](20\d{2})\b", text)
    if m:
        posted = datetime(int(m.group(3)), int(m.group(2)), int(m.group(1)))
        return max(0, int((now - posted).total_seconds() // 60))
    return None


def _parse_detail_listing_age(text: str) -> Optional[int]:
    labelled = _value_after_label(text, ["Online seit", "Anzeige online seit", "Eingestellt am"], max_next_lines=2)
    if labelled:
        parsed = _parse_listing_age(labelled)
        if parsed is not None:
            return parsed
    # Do not scan the whole listing text for arbitrary dates. Service, TUV,
    # Erstzulassung or invoice dates can look like listing age and falsely kill
    # good fresh ads before they ever reach the dealer engine.
    return None


def _extract_brand_model(title: str) -> tuple:
    """Extract brand/model from noisy German listing titles.

    Important for flipper mode: many strong Kleinanzeigen deals are badly written
    ("Golf 5", "Polo 86c", "C180", "Schoner Golf") and should not be lost just
    because the seller did not write the brand cleanly.
    """
    normalized_title = norm(title or "")

    brand_aliases = [
        ("mercedes-benz", "Mercedes"), ("mercedes", "Mercedes"), ("benz", "Mercedes"),
        ("volkswagen", "Volkswagen"), ("vw", "Volkswagen"),
        ("bmw", "Bmw"), ("audi", "Audi"), ("opel", "Opel"), ("ford", "Ford"),
        ("toyota", "Toyota"), ("skoda", "Skoda"), ("seat", "Seat"),
        ("renault", "Renault"), ("peugeot", "Peugeot"), ("hyundai", "Hyundai"),
        ("kia", "Kia"), ("mazda", "Mazda"), ("honda", "Honda"),
        ("volvo", "Volvo"), ("dacia", "Dacia"), ("fiat", "Fiat"),
        ("alfa romeo", "Alfa Romeo"), ("mini", "Mini"), ("nissan", "Nissan"),
        ("suzuki", "Suzuki"), ("mitsubishi", "Mitsubishi"), ("chevrolet", "Chevrolet"),
        ("jeep", "Jeep"), ("range rover", "Land Rover"), ("land rover", "Land Rover"), ("jaguar", "Jaguar"),
        ("lexus", "Lexus"), ("porsche", "Porsche"), ("subaru", "Subaru"),
        ("citroen", "Citroen"), ("smart", "Smart"), ("tesla", "Tesla"),
    ]

    # alias -> (model, inferred brand). Brand inference prevents good badly-written
    # listings like "Golf 5 1.6 Automatik" from being rejected as missing brand.
    model_aliases = [
        ("golf4", "Golf", "Volkswagen"), ("golf 4", "Golf", "Volkswagen"),
        ("golf5", "Golf", "Volkswagen"), ("golf 5", "Golf", "Volkswagen"),
        ("golf6", "Golf", "Volkswagen"), ("golf 6", "Golf", "Volkswagen"),
        ("golf7", "Golf", "Volkswagen"), ("golf 7", "Golf", "Volkswagen"),
        ("golf", "Golf", "Volkswagen"), ("polo", "Polo", "Volkswagen"),
        ("passat", "Passat", "Volkswagen"), ("touran", "Touran", "Volkswagen"),
        ("tiguan", "Tiguan", "Volkswagen"), ("caddy", "Caddy", "Volkswagen"),
        ("sharan", "Sharan", "Volkswagen"), ("t5", "T5", "Volkswagen"),
        ("t4", "T4", "Volkswagen"), ("transporter", "T5", "Volkswagen"),
        ("caravelle", "T5", "Volkswagen"), ("multivan", "T5", "Volkswagen"),
        ("octavia", "Octavia", "Skoda"), ("fabia", "Fabia", "Skoda"),
        ("superb", "Superb", "Skoda"), ("leon", "Leon", "Seat"),
        ("ibiza", "Ibiza", "Seat"), ("alhambra", "Alhambra", "Seat"),
        ("astra", "Astra", "Opel"), ("corsa", "Corsa", "Opel"),
        ("zafira", "Zafira", "Opel"), ("insignia", "Insignia", "Opel"),
        ("vectra", "Vectra", "Opel"), ("crossland", "Crossland", "Opel"),
        ("focus", "Focus", "Ford"), ("fokus", "Focus", "Ford"),
        ("fiesta", "Fiesta", "Ford"), ("mondeo", "Mondeo", "Ford"),
        ("galaxy", "Galaxy", "Ford"), ("s-max", "S-Max", "Ford"),
        ("smax", "S-Max", "Ford"), ("kuga", "Kuga", "Ford"),
        ("yaris", "Yaris", "Toyota"), ("auris", "Auris", "Toyota"),
        ("corolla", "Corolla", "Toyota"), ("avensis", "Avensis", "Toyota"),
        ("jazz", "Jazz", "Honda"), ("civic", "Civic", "Honda"),
        ("i30", "i30", "Hyundai"), ("i 30", "i30", "Hyundai"),
        ("i20", "i20", "Hyundai"), ("i 20", "i20", "Hyundai"),
        ("ix35", "ix35", "Hyundai"), ("rio", "Rio", "Kia"),
        ("ceed", "Ceed", "Kia"), ("c'eed", "Ceed", "Kia"),
        ("picanto", "Picanto", "Kia"), ("sportage", "Sportage", "Kia"),
        ("mazda 2", "2", "Mazda"), ("mazda2", "2", "Mazda"),
        ("mazda 3", "3", "Mazda"), ("mazda3", "3", "Mazda"),
        ("mazda 6", "6", "Mazda"), ("mazda6", "6", "Mazda"),
        ("mx-5", "MX-5", "Mazda"), ("mx5", "MX-5", "Mazda"),
        ("qashqai", "Qashqai", "Nissan"), ("clio", "Clio", "Renault"),
        ("twingo", "Twingo", "Renault"), ("megane", "Megane", "Renault"),
        ("scenic", "Scenic", "Renault"), ("captur", "Captur", "Renault"),
        ("sandero", "Sandero", "Dacia"), ("duster", "Duster", "Dacia"),
        ("peugeot 208", "208", "Peugeot"), ("peugeot 308", "308", "Peugeot"),
        ("citroen c3", "C3", "Citroen"), ("c3", "C3", "Citroen"), ("c4", "C4", "Citroen"),
        ("fiat 500", "500", "Fiat"), ("up", "UP", "Volkswagen"), ("fox", "Fox", "Volkswagen"),
        ("mii", "Mii", "Seat"), ("meriva", "Meriva", "Opel"), ("venga", "Venga", "Kia"),
        ("soul", "Soul", "Kia"), ("colt", "Colt", "Mitsubishi"), ("yeti", "Yeti", "Skoda"),
        ("a3", "A3", "Audi"), ("a4", "A4", "Audi"), ("a5", "A5", "Audi"),
        ("a6", "A6", "Audi"), ("q5", "Q5", "Audi"),
        ("116i", "1er", "Bmw"), ("118i", "1er", "Bmw"), ("120i", "1er", "Bmw"),
        ("116d", "1er", "Bmw"), ("118d", "1er", "Bmw"), ("120d", "1er", "Bmw"),
        ("316i", "3er", "Bmw"), ("316", "3er", "Bmw"), ("318i", "3er", "Bmw"), ("320i", "3er", "Bmw"), ("328i", "3er", "Bmw"),
        ("318d", "3er", "Bmw"), ("320d", "3er", "Bmw"), ("330d", "3er", "Bmw"),
        ("e34", "5er", "Bmw"), ("520i", "5er", "Bmw"), ("e46", "3er", "Bmw"), ("e90", "3er", "Bmw"), ("e91", "3er", "Bmw"), ("e92", "3er", "Bmw"), ("x1", "X1", "Bmw"), ("x3", "X3", "Bmw"), ("x5", "X5", "Bmw"),
        ("a180", "A-Klasse", "Mercedes"), ("a 180", "A-Klasse", "Mercedes"),
        ("b180", "B-Klasse", "Mercedes"), ("b 180", "B-Klasse", "Mercedes"),
        ("c klasse", "C-Klasse", "Mercedes"), ("c-klasse", "C-Klasse", "Mercedes"), ("c180", "C-Klasse", "Mercedes"), ("c 180", "C-Klasse", "Mercedes"),
        ("c200", "C-Klasse", "Mercedes"), ("c 200", "C-Klasse", "Mercedes"),
        ("c220", "C-Klasse", "Mercedes"), ("c 220", "C-Klasse", "Mercedes"),
        ("e200", "E-Klasse", "Mercedes"), ("e 200", "E-Klasse", "Mercedes"),
        ("e220", "E-Klasse", "Mercedes"), ("e 220", "E-Klasse", "Mercedes"),
        ("evoque", "Evoque", "Land Rover"), ("discovery", "Discovery", "Land Rover"), ("freelander", "Freelander", "Land Rover"),
    ]

    brand = None
    matched_alias = None
    for alias, normalized in brand_aliases:
        if re.search(rf"\b{re.escape(alias)}\b", normalized_title):
            brand = normalized
            matched_alias = alias
            break

    model = None
    inferred_brand = None
    for alias, normalized_model, alias_brand in model_aliases:
        if re.search(rf"\b{re.escape(alias)}\b", normalized_title):
            model = normalized_model
            inferred_brand = alias_brand
            break

    if not brand and inferred_brand:
        brand = inferred_brand

    if brand and not model and matched_alias:
        match = re.search(rf"\b{re.escape(matched_alias)}\b\s+([a-z0-9-]{{2,18}})", normalized_title)
        if match:
            candidate = match.group(1).strip(" ,.-").upper()
            blocked = {"verkaufe", "auto", "pkw", "gebraucht", "benz", "klasse", "schoener", "schoner"}
            if candidate.lower() not in blocked:
                model = candidate

    return brand, model

def _parse_fuel(text: str) -> Optional[str]:
    labelled = _value_after_label(text, ["Kraftstoffart", "Kraftstoff", "Antriebsart"])
    text_l = norm(labelled or text)
    for value in ["diesel", "benzin", "hybrid", "elektro", "lpg", "cng"]:
        if value in text_l:
            return value
    return None


def _parse_gearbox(text: str) -> Optional[str]:
    labelled = _value_after_label(text, ["Getriebe"])
    text_l = norm(labelled or text)
    if "automatik" in text_l or "automatic" in text_l or "dsg" in text_l:
        return "automatic"
    if "schaltgetriebe" in text_l or "schalter" in text_l or "manuell" in text_l:
        return "manual"
    return None


def _extract_engine(text: str) -> Optional[str]:
    normalized = norm(text)
    patterns = [
        r"\b([123]\.[0-9]\s*(?:tdi|tsi|tfsi|fsi|cdi|dci|hdi|cdti|ecoboost|jtd|multijet))\b",
        r"\b(3[12]0d|3[12]8d|3[12]0i|1[12]6i|1[12]8i|1[12]0d|1[12]0i)\b",
        r"\b(c\s?180|c\s?200|c\s?220|e\s?200|e\s?220)\b",
        r"\b(n47|n57|m271|om651)\b",
    ]
    for pattern in patterns:
        m = re.search(pattern, normalized)
        if m:
            return re.sub(r"\s+", "", m.group(1)).upper()
    return None


def _data_quality_warnings(listing: dict) -> list[str]:
    warnings = []
    year = listing.get("year")
    mileage = listing.get("mileage")
    price = listing.get("price")
    current_year = datetime.now().year

    if year and (year < 1980 or year > current_year):
        warnings.append("implausible year")
    if mileage is not None and (mileage < 5000 or mileage > 500000):
        warnings.append("implausible mileage")
    if year and mileage and current_year - year >= 15 and mileage < 50000:
        warnings.append("very low mileage for age - verify odometer/TUV history")
    elif year and mileage and current_year - year >= 8 and mileage < 20000:
        warnings.append("very low mileage for age")
    if price is not None and price < 800:
        warnings.append("very low price")
    if not listing.get("detail_verified"):
        warnings.append("detail page not verified")
    return warnings


async def _body_text(page) -> str:
    body = await page.query_selector("body")
    return await body.inner_text() if body else ""


async def _extract_detail_value(page, labels: list[str]) -> Optional[str]:
    """Read one vehicle fact from the rendered detail page by its visible label."""
    script = r"""
    (labels) => {
        const norm = (value) => (value || "")
            .toString()
            .normalize("NFD")
            .replace(/[\u0300-\u036f]/g, "")
            .toLowerCase()
            .replace(/\s+/g, " ")
            .trim();
        const wanted = labels.map(norm);
        const hasLabel = (text) => wanted.some((label) => norm(text).includes(label));
        const clean = (text) => (text || "").toString().replace(/\s+/g, " ").trim();
        const stripLabel = (text) => {
            let value = clean(text);
            for (const label of labels) {
                value = value.replace(new RegExp(label.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"), "i"), "").trim();
            }
            return value.replace(/^[:\-–—|]+/, "").trim();
        };
        const useful = (text) => {
            const value = clean(text);
            return /\d/.test(value) && !hasLabel(value) && value.length <= 180;
        };

        // Mileage must come from the structured vehicle fact, not from seller
        // description text such as "Zahnriemen bei 180.000 km".
        if (wanted.some((label) => ["kilometerstand", "laufleistung", "km-stand"].includes(label))) {
            const bodyText = document.body ? (document.body.innerText || document.body.textContent || "") : "";
            const mileageMatch = bodyText.match(
                /(?:Kilometerstand|Laufleistung|KM-Stand)\s*[:\n\r ]{0,30}(\d{1,3}(?:[.\s]\d{3})+|\d{4,6})\s*(?:km|kilometer)?/i
            );
            if (mileageMatch) {
                return `${mileageMatch[1]} km`;
            }
        }

        for (const dt of Array.from(document.querySelectorAll("dt"))) {
            if (!hasLabel(dt.innerText || dt.textContent)) continue;
            const dd = dt.nextElementSibling;
            if (dd && useful(dd.innerText || dd.textContent)) {
                return stripLabel(dd.innerText || dd.textContent);
            }
        }

        for (const node of Array.from(document.querySelectorAll("li, tr, dl, section, div, p, span"))) {
            const rawText = (node.innerText || node.textContent || "").toString();
            const text = clean(rawText);
            if (!text || !hasLabel(rawText)) continue;

            const lines = rawText.split(/\n+/).map((line) => line.trim()).filter(Boolean);
            for (let i = 0; i < lines.length; i++) {
                if (!hasLabel(lines[i])) continue;
                const sameLine = stripLabel(lines[i]);
                if (useful(sameLine)) return sameLine;
                for (let j = i + 1; j < Math.min(lines.length, i + 4); j++) {
                    if (hasLabel(lines[j])) break;
                    if (useful(lines[j])) return stripLabel(lines[j]);
                }
            }

            const children = Array.from(node.children || []);
            for (let i = 0; i < children.length; i++) {
                if (!hasLabel(children[i].innerText || children[i].textContent)) continue;
                for (let j = i + 1; j < Math.min(children.length, i + 4); j++) {
                    const value = children[j].innerText || children[j].textContent;
                    if (useful(value)) return stripLabel(value);
                }
            }

            const sibling = node.nextElementSibling;
            if (sibling && useful(sibling.innerText || sibling.textContent)) {
                return stripLabel(sibling.innerText || sibling.textContent);
            }
        }
        return null;
    }
    """
    try:
        value = await page.evaluate(script, labels)
        return str(value).strip() if value else None
    except Exception:
        return None


async def _enrich_kleinanzeigen_detail(context, listing: dict, timeout_ms: int = 12000) -> dict:
    """Open the detail page and improve listing data, best-effort."""
    page = await context.new_page()
    try:
        await page.goto(listing["url"], timeout=timeout_ms, wait_until="domcontentloaded")
        await asyncio.sleep(random.uniform(1.0, 2.4))
        body_text = await _body_text(page)

        article_text = body_text

        article_el = await page.query_selector("main article")

        if article_el:

            try:

                scoped_text = (await article_el.inner_text()).strip()

                if scoped_text:

                    article_text = scoped_text

            except Exception:

                article_text = body_text

        body_l = body_text.lower()
        if any(x in body_l for x in ["captcha", "i am not a robot", "bitte best", "robot check"]):
            listing["detail_verified"] = False
            listing["detail_error"] = "captcha"
            return listing
        reject_reason = _early_reject_reason(article_text)
        if reject_reason:
            listing["detail_verified"] = False
            listing["detail_error"] = f"hard reject: {reject_reason}"
            listing["hard_reject"] = True
            listing["hard_reject_reason"] = reject_reason
            return listing

        title_el = await page.query_selector("h1")
        if title_el:
            detail_title = (await title_el.inner_text()).strip()
            if detail_title and not _is_generic_detail_heading(detail_title):
                listing["title"] = detail_title
                brand, model = _extract_brand_model(detail_title)
                listing["brand"] = brand or listing.get("brand")
                listing["model"] = model or listing.get("model")

        price_text = ""
        for selector in ["#viewad-price", "[data-testid='vip-price']", ".boxedarticle--price", "h2"]:
            price_el = await page.query_selector(selector)
            if price_el:
                price_text = await price_el.inner_text()
                if "\u20ac" in price_text or "eur" in price_text.lower():
                    break
        price = _parse_price(price_text) or listing.get("price")
        mileage_text = await _extract_detail_value(page, ["Kilometerstand", "Laufleistung", "KM-Stand"])
        mileage = _parse_mileage(f"Kilometerstand\n{mileage_text}") if mileage_text else listing.get("mileage")
        tuv_detail_text = await _extract_detail_value(page, ["HU bis", "TUV bis", "TUEV bis", "TÜV bis", "Hauptuntersuchung"])
        year_detail_text = await _extract_detail_value(page, ["Erstzulassung", "Baujahr", "Erstzul.", "EZ", "Bj"])
        structured_year = _parse_year(f"Erstzulassung\n{year_detail_text}") if year_detail_text else None
        title_year = _parse_year(listing.get("title") or "")
        detail_year = _parse_year(article_text)
        year = structured_year or detail_year or title_year or listing.get("year")
        if title_year and detail_year and detail_year >= datetime.now().year - 1 and title_year <= datetime.now().year - 2 and title_year < detail_year:
            year = title_year
        if year and year >= datetime.now().year and not structured_year:
            year = title_year or listing.get("year")
        fuel = _parse_fuel(article_text)
        gearbox = _parse_gearbox(article_text)
        engine = _extract_engine(f"{listing.get('title', '')} {article_text}")
        age_min = _parse_detail_listing_age(article_text)

        if price:
            listing["price"] = price
        if mileage:
            listing["mileage"] = mileage
        if year:
            listing["year"] = year
        if fuel:
            listing["fuel"] = fuel
        if gearbox:
            listing["gearbox"] = gearbox
        if engine:
            listing["engine"] = engine
        if age_min is not None:
            existing_age = listing.get("listing_age_minutes")
            # Keep the fresher card age when detail parsing finds an older date.
            # Kleinanzeigen detail pages often contain TUV/service/registration dates.
            if existing_age is None or age_min <= existing_age:
                listing["listing_age_minutes"] = age_min

        desc_el = await page.query_selector("#viewad-description, [data-testid='vip-description'], .boxedarticle--details")
        if desc_el:
            desc = (await desc_el.inner_text()).strip()
            if desc:
                listing["description"] = desc[:3000]
        if not listing.get("description") or len(listing.get("description", "")) < 120:
            # Fallback for Kleinanzeigen/mobile.de embedded detail pages.
            desc_match = re.search(
                r"Beschreibung\s*(.*?)(?:Inserat bereitgestellt von|Rechtliche Angaben|Nachricht schreiben|Andere Anzeigen)",
                article_text,
                re.IGNORECASE | re.DOTALL,
            )
            if desc_match:
                desc = re.sub(r"\n{3,}", "\n\n", desc_match.group(1)).strip()
                if desc:
                    listing["description"] = desc[:5000]

        desc_text = listing.get("description", "") or ""
        combined_detail_text = f"{listing.get('title', '')} {desc_text} {article_text}"
        tuv_info = _parse_tuv_info(
            " ".join(
                part
                for part in [f"HU bis {tuv_detail_text}" if tuv_detail_text else "", listing.get("title", ""), desc_text, article_text]
                if part
            )
        )
        if tuv_info.get("tuv_text"):
            listing.update(tuv_info)
        reject_reason = _early_reject_reason(combined_detail_text)
        if reject_reason:
            listing["detail_verified"] = False
            listing["detail_error"] = f"hard reject: {reject_reason}"
            listing["hard_reject"] = True
            listing["hard_reject_reason"] = reject_reason
            return listing

        desc_year = _parse_explicit_vehicle_year(desc_text)
        if desc_year:
            listing["year"] = desc_year
        normalized_body = norm(body_text)
        normalized_article = norm(article_text)
        if "gewerblicher nutzer" in normalized_body or "rechtliche angaben" in normalized_article:
            listing["seller_type"] = "dealer"
        elif "privater nutzer" in normalized_body:
            listing["seller_type"] = "private"

        image_urls = []
        for img in await page.query_selector_all("img"):
            src = await img.get_attribute("src")
            if src and src.startswith("http") and src not in image_urls:
                low_src = src.lower()
                if any(x in low_src for x in ["logo", "illustrations", "placeholder", "sprite", "icon", "connection-issue"]):
                    continue
                image_urls.append(src)
        if image_urls:
            listing["photo_urls"] = json.dumps(image_urls[:8])

        listing["detail_verified"] = True
        quality_warnings = _data_quality_warnings(listing)
        if quality_warnings:
            listing["data_quality_warnings"] = quality_warnings
        return listing
    except Exception as exc:
        listing["detail_verified"] = False
        listing["detail_error"] = str(exc)[:120]
        return listing
    finally:
        await page.close()


async def scrape_kleinanzeigen(config: dict, max_results: int = 30) -> List[dict]:
    """
    Scrape fresh car listings from Kleinanzeigen.
    Returns list of normalized listing dicts.
    """
    listings = []

    try:
        from playwright.async_api import async_playwright
    except ImportError:
        logger.error("Playwright not installed. Run: pip install playwright && playwright install chromium")
        return []

    price_min = config.get("budget_min", 500)
    price_max = config.get("budget_max", 15000)
    max_mileage = max(config.get("max_mileage", 250000), 300000 if config.get("adaptive_age_mileage", True) else config.get("max_mileage", 250000))
    min_age = config.get("freshness_min_minutes", 1)
    max_age = config.get("freshness_max_minutes", config.get("freshness_max_hours", 12) * 60)

    url = (
        f"https://www.kleinanzeigen.de/s-autos/preis:{price_min}:{price_max}/c216"
        f"?kmMax={max_mileage}&sortingField=SORTING_DATE"
    )

    async with async_playwright() as p:
        try:
            browser = await p.chromium.launch(
                headless=True,
                args=["--no-sandbox", "--disable-setuid-sandbox", "--disable-blink-features=AutomationControlled"],
            )
            context = await browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0.0.0 Safari/537.36"
                ),
                viewport={"width": 1366, "height": 768},
                locale="de-DE",
            )
            context.set_default_timeout(config.get("playwright_action_timeout_ms", 5000))
            page = await context.new_page()
            page.set_default_timeout(config.get("playwright_action_timeout_ms", 5000))

            # Mask automation signals
            await page.add_init_script("""
                Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
            """)

            logger.info(f"Kleinanzeigen: opening {url}")
            await page.goto(url, timeout=30000, wait_until="domcontentloaded")
            await asyncio.sleep(random.uniform(2.0, 4.0))

            # Captcha detection
            content = await page.content()
            if any(x in content.lower() for x in ["captcha", "i am not a robot", "bitte best", "robot check"]):
                logger.warning("Kleinanzeigen: CAPTCHA detected - skipping this source")
                await browser.close()
                return []

            # Find listing cards
            cards = await page.query_selector_all("article.aditem")
            if not cards:
                cards = await page.query_selector_all("[data-adid]")

            logger.info(f"Kleinanzeigen: found {len(cards)} cards")

            for card in cards[:max_results]:
                try:
                    await asyncio.sleep(random.uniform(0.1, 0.3))

                    # Title & URL
                    title_el = await card.query_selector("h2 a, .ellipsis a, a.aditem-main--middle--titleadlink")
                    title = await title_el.inner_text() if title_el else ""
                    href = await title_el.get_attribute("href") if title_el else ""
                    if href and not href.startswith("http"):
                        href = "https://www.kleinanzeigen.de" + href

                    # Price
                    price_el = await card.query_selector(".aditem-main--middle--price-shipping--price, p.aditem-main--middle--price")
                    price_text = await price_el.inner_text() if price_el else ""
                    price = _parse_price(price_text)

                    # Location & time
                    # Kleinanzeigen usually keeps location on the left and publish time on the right.
                    # Reading only the left block makes fresh ads look like "age unknown".
                    meta_parts = []
                    for selector in [
                        ".aditem-main--top--left",
                        ".aditem-main--top--right",
                        ".aditem-details",
                    ]:
                        meta_el = await card.query_selector(selector)
                        if meta_el:
                            try:
                                part = (await meta_el.inner_text()).strip()
                                if part:
                                    meta_parts.append(part)
                            except Exception:
                                pass

                    meta_text = "\n".join(meta_parts)
                    location = ""
                    age_min = None
                    if meta_text:
                        lines = [l.strip() for l in meta_text.split("\n") if l.strip()]
                        if lines:
                            location = lines[0]
                        for line in lines:
                            parsed_age = _parse_listing_age(line)
                            if parsed_age is not None:
                                age_min = parsed_age
                                break

                    # Description snippet
                    desc_el = await card.query_selector(".aditem-main--middle--description, p.description")
                    desc = await desc_el.inner_text() if desc_el else ""

                    detail_text = ""
                    detail_els = await card.query_selector_all(".simpletag, .aditem-main--middle--vehiclefeatures, .aditem-main--middle")
                    for detail_el in detail_els:
                        try:
                            detail_text += " " + await detail_el.inner_text()
                        except Exception:
                            continue

                    reject_reason = _early_reject_reason(f"{title} {desc} {detail_text}")
                    if reject_reason:
                        logger.debug(f"Kleinanzeigen early reject [{reject_reason}]: {title[:60]}")
                        continue

                    if age_min is not None and (age_min < min_age or age_min > max_age):
                        logger.debug(f"Kleinanzeigen age reject: {title[:60]} age={age_min}min")
                        continue

                    # Platform ID from URL or data attribute
                    pid = await card.get_attribute("data-adid") or ""
                    if not pid and href:
                        m = re.search(r"/(\d+)\.html", href)
                        if m:
                            pid = m.group(1)

                    if not title or not href:
                        continue

                    brand, model = _extract_brand_model(title)
                    tuv_info = _parse_tuv_info(f"{title} {desc} {detail_text}")

                    listing = {
                        "platform": "kleinanzeigen",
                        "platform_id": pid,
                        "stable_id": f"kleinanzeigen:{pid}" if pid else None,
                        "fingerprint": _fingerprint(title, price, None, location),
                        "url": href,
                        "title": title.strip(),
                        "brand": brand,
                        "model": model,
                        "price": price,
                        "mileage": _parse_card_mileage(detail_text),
                        "year": (
                            _parse_year(title)
                            if _parse_year(title) and _parse_year(detail_text) and _parse_year(detail_text) >= datetime.now().year - 1 and _parse_year(title) <= datetime.now().year - 2
                            else _parse_year(detail_text)
                        ),
                        "fuel": None,
                        "gearbox": None,
                        "engine": _extract_engine(f"{title} {desc} {detail_text}"),
                        "tuv_text": tuv_info.get("tuv_text"),
                        "tuv_until": tuv_info.get("tuv_until"),
                        "tuv_months_left": tuv_info.get("tuv_months_left"),
                        "location": location,
                        "description": desc.strip(),
                        "seller_type": "private",
                        "listing_age_minutes": age_min,
                        "photo_urls": json.dumps([]),
                    }
                    listings.append(listing)

                except Exception as e:
                    logger.debug(f"Kleinanzeigen card parse error: {e}")
                    continue

            if config.get("verify_detail_pages", True) and listings:
                detail_limit = min(len(listings), config.get("max_detail_pages_per_scan", 20))
                detail_timeout = config.get("detail_page_timeout_ms", 12000)
                logger.info(f"Kleinanzeigen: verifying {detail_limit} detail pages")
                verified = []
                for idx, listing in enumerate(listings):
                    if idx < detail_limit:
                        await asyncio.sleep(random.uniform(0.4, 1.1))
                        logger.info(f"Kleinanzeigen: detail {idx + 1}/{detail_limit} - {listing.get('title', '')[:45]}")
                        try:
                            listing = await asyncio.wait_for(
                                _enrich_kleinanzeigen_detail(context, listing, timeout_ms=detail_timeout),
                                timeout=(detail_timeout / 1000) + 6,
                            )
                        except asyncio.TimeoutError:
                            logger.warning(
                                f"Kleinanzeigen: detail timeout - skipping enrichment for {listing.get('title', '')[:55]}"
                            )
                            listing["detail_verified"] = False
                            listing["detail_error"] = "detail timeout"
                        listing["fingerprint"] = _fingerprint(
                            listing.get("title", ""),
                            listing.get("price"),
                            listing.get("mileage"),
                            listing.get("location", ""),
                        )
                    verified.append(listing)
                listings = verified

            await browser.close()

        except Exception as e:
            logger.error(f"Kleinanzeigen scraper error: {e}")

    logger.info(f"Kleinanzeigen: scraped {len(listings)} listings")
    return listings


async def scrape_autoscout24(config: dict, max_results: int = 30) -> List[dict]:
    """
    Scrape fresh car listings from AutoScout24.
    """
    listings = []

    try:
        from playwright.async_api import async_playwright
    except ImportError:
        logger.error("Playwright not installed.")
        return []

    price_min = config.get("budget_min", 500)
    price_max = config.get("budget_max", 15000)
    max_mileage = max(config.get("max_mileage", 250000), 300000 if config.get("adaptive_age_mileage", True) else config.get("max_mileage", 250000))

    url = (
        f"https://www.autoscout24.de/lst?sort=age&desc=0"
        f"&pricefrom={price_min}&priceto={price_max}"
        f"&kmto={max_mileage}&ustate=N%2CU"
    )

    async with async_playwright() as p:
        try:
            browser = await p.chromium.launch(
                headless=True,
                args=["--no-sandbox", "--disable-setuid-sandbox"],
            )
            context = await browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0.0.0 Safari/537.36"
                ),
                viewport={"width": 1440, "height": 900},
                locale="de-DE",
            )
            context.set_default_timeout(config.get("playwright_action_timeout_ms", 5000))
            page = await context.new_page()
            page.set_default_timeout(config.get("playwright_action_timeout_ms", 5000))
            await page.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined});")

            logger.info(f"AutoScout24: opening search")
            await page.goto(url, timeout=30000, wait_until="domcontentloaded")
            await asyncio.sleep(random.uniform(2.5, 5.0))

            content = await page.content()
            if any(x in content.lower() for x in ["captcha", "robot", "challenge"]):
                logger.warning("AutoScout24: CAPTCHA detected - skipping")
                await browser.close()
                return []

            # Try multiple possible selectors
            cards = await page.query_selector_all("article[data-guid], .cldt-summary-full-item, article.cldt-summary-full-item")
            if not cards:
                cards = await page.query_selector_all("[data-item-name='listing-summary']")

            logger.info(f"AutoScout24: found {len(cards)} cards")

            for card in cards[:max_results]:
                try:
                    await asyncio.sleep(random.uniform(0.1, 0.25))

                    title_el = await card.query_selector("h2, .ListItem_title__znkQ7, a[data-item-name='detail-page-link']")
                    title = await title_el.inner_text() if title_el else ""

                    link_el = await card.query_selector("a[href*='/angebote/']")
                    href = await link_el.get_attribute("href") if link_el else ""
                    if href and not href.startswith("http"):
                        href = "https://www.autoscout24.de" + href

                    price_el = await card.query_selector("[data-item-name='price'], .Price_price__APlgs")
                    price_text = await price_el.inner_text() if price_el else ""
                    price = _parse_price(price_text)

                    mileage_el = await card.query_selector("[data-item-name='mileage']")
                    mileage_text = await mileage_el.inner_text() if mileage_el else ""
                    mileage = _parse_mileage(f"Kilometerstand\n{mileage_text}") if mileage_text else None

                    year_el = await card.query_selector("[data-item-name='first-registration']")
                    year_text = await year_el.inner_text() if year_el else ""
                    year = _parse_year(year_text + " " + title)
                    tuv_info = _parse_tuv_info(f"{title} {year_text}")

                    location_el = await card.query_selector("[data-item-name='location']")
                    location = await location_el.inner_text() if location_el else ""

                    pid = await card.get_attribute("data-guid") or ""
                    if not pid and href:
                        m = re.search(r"/angebote/([^/]+)", href)
                        pid = m.group(1) if m else ""

                    if not title or not href:
                        continue

                    brand, model = _extract_brand_model(title)

                    listing = {
                        "platform": "autoscout24",
                        "platform_id": pid,
                        "stable_id": f"autoscout24:{pid}" if pid else None,
                        "fingerprint": _fingerprint(title, price, mileage, location),
                        "url": href,
                        "title": title.strip(),
                        "brand": brand,
                        "model": model,
                        "price": price,
                        "mileage": mileage,
                        "year": year,
                        "fuel": None,
                        "gearbox": None,
                        "engine": None,
                        "tuv_text": tuv_info.get("tuv_text"),
                        "tuv_until": tuv_info.get("tuv_until"),
                        "tuv_months_left": tuv_info.get("tuv_months_left"),
                        "location": location.strip(),
                        "description": "",
                        "seller_type": "unknown",
                        "listing_age_minutes": None,
                        "photo_urls": json.dumps([]),
                    }
                    listings.append(listing)

                except Exception as e:
                    logger.debug(f"AutoScout24 card error: {e}")
                    continue

            await browser.close()

        except Exception as e:
            logger.error(f"AutoScout24 scraper error: {e}")

    logger.info(f"AutoScout24: scraped {len(listings)} listings")
    return listings











