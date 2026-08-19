"""Telegram notifications for AUTOHAWK finalist leads.

This module is report-only: it explains already selected HOT/GOOD/CHECK
listings and does not change scoring or filtering.
"""

from __future__ import annotations

import html
import logging
import os
import re
from typing import Any

import requests

from src.utils import norm, safe_float, safe_int

logger = logging.getLogger("autohawk.telegram")

MAX_TELEGRAM_TEXT = 3900





def _field(listing: Any, name: str) -> Any:
    return getattr(listing, name, None)


def _text_field(listing: Any, name: str) -> str:
    return str(_field(listing, name) or "").strip()


def _money(value: Any) -> str:
    amount = safe_float(value)
    if not amount:
        return "? EUR"
    return f"{amount:,.0f} EUR".replace(",", ".")


def _km(value: Any) -> str:
    km = safe_int(value)
    if not km:
        return "? km"
    return f"{km:,} km".replace(",", ".")


def _score(value: Any) -> int:
    raw = safe_float(value)
    if raw <= 1:
        raw *= 100
    return max(0, min(100, int(round(raw))))


def _escape(value: Any) -> str:
    return html.escape(str(value or ""), quote=False)


def _is_noise(item: str) -> bool:
    key = norm(item)
    noisy_fragments = [
        "description db no strong",
        "flip db check",
        "question 1",
        "question 2",
        "ask seller in german",
        "provider",
        "score breakdown",
    ]
    return any(fragment in key for fragment in noisy_fragments)


def _split_items(value: Any, limit: int = 5) -> list[str]:
    text = str(value or "").strip()
    if not text:
        return []
    text = text.replace("\\n", "\n")
    parts = re.split(r"\n+|;\s+|\|\s+|•\s+|- ", text)
    clean: list[str] = []
    seen: set[str] = set()
    for part in parts:
        item = re.sub(r"\s+", " ", part).strip(" -*\t\r\n")
        if not item or len(item) < 4 or _is_noise(item):
            continue
        key = norm(item)
        if key in seen:
            continue
        seen.add(key)
        clean.append(item[:190])
        if len(clean) >= limit:
            break
    return clean


def _all_text(listing: Any) -> str:
    return norm(
        " ".join(
            _text_field(listing, name)
            for name in [
                "title",
                "brand",
                "model",
                "fuel",
                "gearbox",
                "engine",
                "description",
                "why_interesting",
                "possible_risks",
                "model_specific_issues",
                "what_to_check",
                "seller_signals",
                "ai_summary",
            ]
        )
    )


def _extract_tuv(listing: Any) -> str:
    parsed = _text_field(listing, "tuv_text")
    if parsed:
        return parsed

    text = _all_text(listing)
    if re.search(r"\b(kein|keine|ohne|abgelaufen|faellig|fallig)\s*(tuev|tuv|hu)\b", text):
        return "kein/unklar"
    if re.search(r"\b(tuev|tuv|hu)\s*(neu|frisch|gemacht|bekommen)\b", text):
        return "neu/frisch"

    dates: list[tuple[int, int]] = []
    patterns = [
        r"\b(?:tuev|tuv|hu)\s*(?:bis)?\s*(0?[1-9]|1[0-2])\s*[./-]?\s*(2[6-9]|202[6-9])\b",
        r"\b(0?[1-9]|1[0-2])\s*[./-]?\s*(2[6-9]|202[6-9])\b.{0,24}\b(?:tuev|tuv|hu)\b",
    ]
    for pattern in patterns:
        for match in re.finditer(pattern, text):
            month = int(match.group(1))
            year = int(match.group(2))
            if year < 100:
                year += 2000
            dates.append((year, month))
    if dates:
        year, month = max(dates)
        return f"{month:02d}/{year}"
    return "?"


def _car_name(listing: Any) -> str:
    brand_model = " ".join(
        part for part in [_field(listing, "brand"), _field(listing, "model")] if part
    ).strip()
    return brand_model or _text_field(listing, "title") or "Auto"


def _market_line(listing: Any) -> str:
    price = safe_float(_field(listing, "price"))
    market = safe_float(_field(listing, "estimated_market_price"))
    margin = safe_float(_field(listing, "estimated_margin"))

    if market and price and market > price:
        gap = market - price
        pct = gap / market * 100
        extra = f", расчетная маржа около {_money(margin)}" if margin else ""
        return f"Цена примерно на {_money(gap)} ниже оценки рынка ({pct:.0f}%){extra}."
    if market and price and price > market:
        gap = price - market
        return f"Цена примерно на {_money(gap)} выше текущей оценки рынка. Нужна ручная проверка аналогов."
    return "Рыночная цена не подтверждена: нужно вручную сравнить 3-5 похожих объявлений."


def _lead_type(listing: Any) -> str:
    text = _all_text(listing)
    score = _score(_field(listing, "final_score"))
    price = safe_float(_field(listing, "price"))
    mileage = safe_int(_field(listing, "mileage"))
    brand = norm(_field(listing, "brand"))
    model = norm(_field(listing, "model"))

    premium = brand in {"audi", "bmw", "mercedes", "mini"} or any(x in text for x in ["s-line", "quattro", "amg"])
    if premium and price <= 2000 and mileage >= 280000:
        return "дешевый премиум-риск: интересен только как спекулятивный лот/донор, не как спокойная первая машина"
    if premium:
        return "ликвидный премиум-watchlist: может быстро уйти, но ремонтный риск выше обычного"
    if any(x in model for x in ["polo", "golf", "corsa", "fiesta", "focus", "astra", "yaris", "i20", "fabia"]):
        return "массовая ликвидная машина: главный плюс в быстрой перепродаже и понятном спросе"
    if score >= 80:
        return "сильный кандидат, но все равно нужен быстрый ручной чек"
    return "кандидат на ручную проверку, не автоматический buy"


def _local_context(listing: Any) -> list[str]:
    text = _all_text(listing)
    price = safe_float(_field(listing, "price"))
    mileage = safe_int(_field(listing, "mileage"))
    tuv = _extract_tuv(listing)
    brand = norm(_field(listing, "brand"))
    model = norm(_field(listing, "model"))
    fuel = norm(_field(listing, "fuel"))
    gearbox = norm(_field(listing, "gearbox"))

    notes: list[str] = []
    if tuv not in {"?", "kein/unklar"}:
        notes.append(f"TÜV/HU указан: {tuv}. Это сильный плюс для быстрой продажи, но нужен фото/скан отчета.")
    elif tuv == "kein/unklar":
        notes.append("TÜV/HU проблемный или неясный. Для обычного покупателя это сильный минус.")

    if mileage:
        if mileage >= 300000:
            notes.append(f"Пробег {_km(mileage)}: это уже зона очень высокого риска. Нужна диагностика, а не вера описанию.")
        elif mileage >= 220000 and fuel != "diesel":
            notes.append(f"Пробег {_km(mileage)} для бензина высокий. Брать только если цена реально перекрывает риск.")
        elif mileage >= 250000 and fuel == "diesel":
            notes.append(f"Пробег {_km(mileage)} для дизеля допустим только при истории обслуживания и живом моторе/турбине/DPF.")
    else:
        notes.append("Пробег не распознан. Без точного Kilometerstand нельзя считать вариант сильным.")

    if price and price <= 1800:
        notes.append("Цена очень низкая. Это может быть шанс, но сначала надо понять причину дешевизны.")
    if "beschadigtes fahrzeug" in text or "beschadigt" in text:
        notes.append("В объявлении есть Beschädigtes Fahrzeug: нужно выяснить, это косметика, ДТП или техническая проблема.")
    if "reserviert" in text:
        notes.append("Уже Reserviert: рынок среагировал быстро, значит цена/тип машины действительно цепляет.")

    if brand == "audi" and any(x in model for x in ["a3", "a4", "a6"]):
        notes.append("Audi хорошо продается по низкой цене, но смотреть коробку, DPF/EGR/турбину, подвеску и следы ДТП.")
    if brand == "bmw" and ("diesel" in fuel or any(x in text for x in ["118d", "318d", "320d", "n47"])):
        notes.append("BMW diesel: главный страх цепь N47, турбина, DPF/EGR и холодный запуск.")
    if brand in {"volkswagen", "vw"} and ("tsi" in text or "dsg" in text):
        notes.append("VAG TSI/DSG: нужна история цепи/масла/мехатроника. Без доказательств риск высокий.")
    if "automatic" in gearbox or "automatik" in text:
        notes.append("Автомат: обязательно проверить переключения на холодную и горячую.")

    return _dedupe(notes, 6)


def _dedupe(items: list[str], limit: int) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for item in items:
        clean = re.sub(r"\s+", " ", str(item or "")).strip()
        if not clean:
            continue
        key = norm(clean)
        if key in seen:
            continue
        seen.add(key)
        result.append(clean)
        if len(result) >= limit:
            break
    return result


def _seller_questions(listing: Any) -> list[str]:
    text = _all_text(listing)
    questions: list[str] = []

    if not safe_int(_field(listing, "mileage")):
        questions.append("Wie viele Kilometer hat das Auto genau? Kannst du ein Foto vom Tacho schicken?")
    if _extract_tuv(listing) != "?":
        questions.append("Kannst du ein Foto vom HU/TÜV-Bericht schicken?")
    else:
        questions.append("Bis wann ist HU/TÜV genau gültig?")

    if any(x in text for x in ["beschadigt", "unfall", "repariert", "lackiert"]):
        questions.append("Was genau ist beschädigt oder nachlackiert? Unfall oder nur optisch?")
    if any(x in text for x in ["audi", "tdi", "diesel", "dpf", "egr"]):
        questions.append("DPF/EGR/Turbo/Injektoren unauffällig? Gibt es Rechnungen?")
    if any(x in text for x in ["automatik", "s-tronic", "stronic", "multitronic", "dsg"]):
        questions.append("Schaltet das Getriebe kalt und warm sauber ohne Ruckeln?")
    if any(x in text for x in ["steuerkette", "n47", "118d", "318d", "320d", "corsa 1.2"]):
        questions.append("Rasselt die Steuerkette beim Kaltstart oder wurde sie schon gemacht?")
    if any(x in text for x in ["zahnriemen", "timing belt"]):
        questions.append("Wann wurde der Zahnriemen gemacht? Gibt es eine Rechnung?")
    if any(x in text for x in ["rost", "schweller", "radlauf", "unterboden", "golf", "corsa", "astra"]):
        questions.append("Gibt es Rost an Schweller, Radläufen oder Unterboden?")

    questions.extend([
        "Leuchten Warnlampen oder verliert der Motor Öl/Kühlwasser?",
        "Ist eine Probefahrt und OBD-Diagnose möglich?",
    ])
    return _dedupe(questions, 6)


def _final_take(listing: Any) -> str:
    score = _score(_field(listing, "final_score"))
    price = safe_float(_field(listing, "price"))
    mileage = safe_int(_field(listing, "mileage"))
    text = _all_text(listing)

    if "motorschaden" in text or "getriebeschaden" in text or "nicht fahrbereit" in text:
        return "Итог: не брать без очень сильной причины. Тут риск может съесть всю экономию."
    if price <= 1800 and mileage >= 280000:
        return "Итог: смотреть можно только как риск-лот. Потенциал есть из-за цены, но покупать только после жесткой проверки."
    if score >= 82:
        return "Итог: один из приоритетных вариантов. Писать быстро, но решение только после TÜV-отчета, холодного запуска и диагностики."
    if score >= 70:
        return "Итог: интересный CHECK. Не идеал, но может быть прибыль, если риски окажутся дешевыми."
    return "Итог: только ручная проверка. Вариант не должен считаться сильным без недостающих данных."


def _section(title: str, items: list[str]) -> list[str]:
    if not items:
        return []
    lines = ["", f"<b>{_escape(title)}</b>"]
    lines.extend(f"- {_escape(item)}" for item in items)
    return lines


def format_telegram_message(listing: Any) -> str:
    deal_score = _score(_field(listing, "final_score"))
    reliability = _score(_field(listing, "risk_score"))
    liquidity = _score(_field(listing, "liquidity_score"))
    condition = _score(_field(listing, "condition_score"))
    verdict = _text_field(listing, "verdict") or "CHECK"
    url = _text_field(listing, "url")

    why = _dedupe(
        _local_context(listing)
        + _split_items(_field(listing, "why_interesting"), 5)
        + _split_items(_field(listing, "seller_signals"), 3),
        8,
    )
    risks = _dedupe(
        _split_items(_field(listing, "possible_risks"), 6)
        + _split_items(_field(listing, "model_specific_issues"), 4),
        7,
    )
    checks = _dedupe(_split_items(_field(listing, "what_to_check"), 7), 7)
    if not checks:
        checks = ["TÜV-Bericht", "Kaltstart", "OBD-Fehlerspeicher", "Rost/Unterboden", "Probefahrt"]

    lines = [
        "<b>НОВЫЙ ВАРИАНТ AUTOHAWK</b>",
        "",
        f"<b>{_escape(_car_name(listing))}</b>",
    ]
    if _field(listing, "year"):
        lines.append(f"Год: {_escape(_field(listing, 'year'))}")
    lines.extend([
        f"Цена: <b>{_escape(_money(_field(listing, 'price')))}</b>",
        f"Пробег: <b>{_escape(_km(_field(listing, 'mileage')))}</b>",
        f"TÜV/HU: <b>{_escape(_extract_tuv(listing))}</b>",
        "",
        f"Вердикт: <b>{_escape(verdict)}</b> | score {deal_score}/100",
        f"Тип: {_escape(_lead_type(listing))}",
        _escape(_market_line(listing)),
        "",
        f"Надежность: {reliability}/100 | Ликвидность: {liquidity}/100 | Состояние: {condition}/100",
    ])

    lines.extend(_section("Почему вообще смотреть", why))
    lines.extend(_section("Что смущает", risks or ["Сильных текстовых красных флагов нет, но это не заменяет осмотр."]))
    lines.extend(_section("Проверить на месте", checks))
    lines.extend(_section("Спросить продавца", _seller_questions(listing)))
    lines.extend(["", f"<b>{_escape(_final_take(listing))}</b>"])

    if url:
        lines.extend(["", f'<a href="{html.escape(url, quote=True)}">Открыть объявление</a>'])

    message = "\n".join(lines)
    if len(message) > MAX_TELEGRAM_TEXT:
        message = message[: MAX_TELEGRAM_TEXT - 40].rstrip() + "\n\n...обрезано, смотри объявление."
    return message


class TelegramNotifier:
    def __init__(self, config: dict[str, Any]):
        self.config = config
        self.enabled = bool(config.get("telegram_enabled", True))
        self.token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip() or str(config.get("telegram_bot_token", "")).strip()
        configured_chat_ids = config.get("telegram_chat_ids", [])
        if isinstance(configured_chat_ids, str):
            configured_chat_ids = configured_chat_ids.split(",")
        self.chat_ids = self._clean_chat_ids(
            os.getenv("TELEGRAM_CHAT_IDS", "")
            or configured_chat_ids
            or os.getenv("TELEGRAM_CHAT_ID", "")
            or config.get("telegram_chat_id", "")
        )
        self.timeout = int(config.get("telegram_timeout_seconds", 10))

    @staticmethod
    def _clean_chat_ids(value: Any) -> list[str]:
        values = value.split(",") if isinstance(value, str) else value
        if not isinstance(values, (list, tuple, set)):
            values = [values]
        return list(dict.fromkeys(str(item).strip() for item in values if str(item).strip()))

    def ready(self) -> bool:
        return self.enabled and bool(self.token and self.chat_ids)

    def post_listing(self, listing: Any) -> tuple[bool, str]:
        """Send one listing to Telegram without changing scanner/database state."""
        if not self.ready():
            return False, "telegram_not_ready"
        url = f"https://api.telegram.org/bot{self.token}/sendMessage"
        errors: list[str] = []
        for chat_id in self.chat_ids:
            payload = {
                "chat_id": chat_id,
                "text": format_telegram_message(listing),
                "parse_mode": "HTML",
                "disable_web_page_preview": False,
            }
            try:
                response = requests.post(url, json=payload, timeout=self.timeout)
            except requests.RequestException as exc:
                errors.append(f"{chat_id}: {exc.__class__.__name__}")
                continue

            if not response.ok:
                error = f"{chat_id}: HTTP {response.status_code} {response.text[:200]}"
                logger.warning(f"Telegram send failed: {error}")
                errors.append(error)

        return (not errors), "; ".join(errors)