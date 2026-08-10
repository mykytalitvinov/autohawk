"""
AUTOHAWK dealer memory.

This is not a neural memory. It is a compact set of real lessons from checked
listings that is injected into the final AI prompt, so the model judges new
cars with the same resale logic every time.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path


BASE_DEALER_LESSONS = [
    {
        "signal": "oil consumption near 1L/1000km or higher",
        "lesson": "For a reseller this is usually a reject. It can mean engine wear, stuck rings, valve seals or turbo issues. Do not treat it as a small defect.",
    },
    {
        "signal": "cold engine runs poorly, rough idle, misfire, bad cold start",
        "lesson": "High-risk engine signal. Only consider with very large price gap and diagnostics; otherwise repair can eat all profit.",
    },
    {
        "signal": "two accidents, accident history, repaired accident, known crash damage",
        "lesson": "Reduce trust and resale liquidity strongly. Accident cars need a large discount and invoices/photos; structural suspicion is a reject.",
    },
    {
        "signal": "rust on sills, structural rust, heavy underbody rust",
        "lesson": "Major resale and TUV risk. Cosmetic wing/door rust can be negotiable, but sills/underbody are dangerous.",
    },
    {
        "signal": "trunk lock, remote key, AC not working, inspection due",
        "lesson": "These are normally level-2 defects, not automatic rejects. They are negotiation points if the car is liquid and the price already reflects them.",
    },
    {
        "signal": "family use, regular oil changes, invoices, one owner",
        "lesson": "Positive trust signal. Still verify documents, but this can make a fair-price car interesting.",
    },
    {
        "signal": "reserved or sold quickly after listing",
        "lesson": "Strong market-demand signal. Learn that this model/price/condition combination was attractive, but do not assume condition was good.",
    },
    {
        "signal": "deregistered car or check engine light",
        "lesson": "Caution, not automatic reject. Needs OBD and documents. Good only when cheap, liquid and the fault looks bounded.",
    },
    {
        "signal": "bad photos, short text, dirty interior",
        "lesson": "Can be an opportunity only if no hard red flags appear. Bad presentation alone is not a reason to reject.",
    },
    {
        "signal": "ugly or blurred photos, dirty exterior/interior",
        "lesson": "Do not reject because it looks ugly or dirty. Dirt and bad presentation can create margin. But visible rust, dents, panel gaps, paint mismatch or warning lights are real risk signals.",
    },
    {
        "signal": "Toyota Yaris XP90 cheap candidate",
        "lesson": "Toyota Yaris XP90 is the safest cheap candidate: very reliable, very liquid, cheap to maintain and easy to resell. It may not create huge margin, but it is the lowest-risk money-safe option if TUV/rust/service are acceptable.",
    },
    {
        "signal": "Opel Corsa D around 1000 EUR",
        "lesson": "Opel Corsa D is often the best price/quality play. Simple petrol/manual cars can be profitable because repair costs are low and demand is broad. Still check TUV, rust, chain noise, cooling and Easytronic risk.",
    },
    {
        "signal": "Skoda Fabia cheap candidate",
        "lesson": "Skoda Fabia is practical and cheap to own, but older cars are less comfortable/emotional. Treat it as a solid backup, not a top deal, unless condition is very clean or price is clearly below market.",
    },
    {
        "signal": "Volkswagen Golf 5 cheap candidate",
        "lesson": "Golf 5 can be the tastiest cheap lead because demand is huge. But it is riskier than Yaris/Corsa/Fabia: inspect rust, TUV, engine, gearbox, warning lights and service proof. Cheap Golf is HOT only if not mechanically or structurally tired.",
    },
    {
        "signal": "VW Up from Autohaus/Automarkt",
        "lesson": "VW Up is liquid, but if it comes from Autohaus/Automarkt at normal retail price, reseller margin is usually already gone. Cap score unless the price is clearly below private-market comparables.",
    },    {
        "signal": "dealer/Autohaus price without a clear discount",
        "lesson": "For a reseller this is usually weak. Dealer trust can help a normal buyer, but margin is often already captured by the dealer. Only promote if price is clearly below observed market.",
    },
]


def _project_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _load_json_rules(path: Path) -> list[str]:
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception:
        return []
    rows = data if isinstance(data, list) else data.get("lessons", [])
    out: list[str] = []
    for row in rows:
        if isinstance(row, dict):
            signal = str(row.get("signal", "")).strip()
            lesson = str(row.get("lesson", "")).strip()
            if signal and lesson:
                out.append(f"- {signal}: {lesson}")
        elif isinstance(row, str) and row.strip():
            out.append(f"- {row.strip()}")
    return out


def _load_csv_feedback(path: Path, limit: int = 20) -> list[str]:
    if not path.exists():
        return []
    out: list[str] = []
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as fh:
            for row in csv.DictReader(fh):
                title = (row.get("title") or row.get("car") or "").strip()
                verdict = (row.get("verdict") or row.get("lesson") or "").strip()
                reason = (row.get("reason") or row.get("notes") or "").strip()
                if verdict or reason:
                    prefix = f"{title}: " if title else ""
                    out.append(f"- {prefix}{verdict}. {reason}".strip())
                if len(out) >= limit:
                    break
    except Exception:
        return []
    return out


def build_ai_memory_context(limit_chars: int = 2600) -> str:
    root = _project_root()
    recent_json_rules = list(reversed(_load_json_rules(root / "data" / "ai_feedback_rules.json")))
    manual_sold_rules = _load_csv_feedback(root / "data" / "manual_sold_feedback.csv", limit=12)

    lines = []
    lines.extend(recent_json_rules)
    lines.extend(manual_sold_rules)
    lines.extend([
        "- " + item["signal"] + ": " + item["lesson"]
        for item in BASE_DEALER_LESSONS
    ])
    lines.extend(_load_csv_feedback(root / "output" / "ai_feedback.csv"))

    text = "\n\nREAL DEALER MEMORY FROM PREVIOUS CHECKED LISTINGS:\n" + "\n".join(lines)
    text += "\n\nUse these lessons as hard practical guidance. If a lesson conflicts with generic optimism, follow the lesson."
    return text[:limit_chars]




