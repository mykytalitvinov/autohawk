from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from src.utils import norm as norm


ROOT = Path(__file__).resolve().parents[1]
_TECHNICAL_DB_CACHE: dict[str, Any] | None = None


def _load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception:
        return {}


def _load_technical_db() -> dict[str, Any]:
    global _TECHNICAL_DB_CACHE
    if _TECHNICAL_DB_CACHE is None:
        _TECHNICAL_DB_CACHE = _load_json(ROOT / "data" / "technical_explanation_database.json")
    return _TECHNICAL_DB_CACHE


def _contains_any(text: str, words: list[str]) -> bool:
    clean = norm(text)
    return any(norm(word) in clean for word in words)


def _split_report_text(value: Any, limit: int = 7) -> list[str]:
    text = str(value or "").strip()
    if not text:
        return []
    parts = re.split(r"\n+|;|\|", text)
    out: list[str] = []
    for part in parts:
        cleaned = re.sub(r"^\s*[-*•]\s*", "", part).strip()
        if cleaned and cleaned not in out:
            out.append(cleaned)
        if len(out) >= limit:
            break
    return out


def _split_issue_text(value: Any, limit: int = 7) -> list[str]:
    text = str(value or "").strip()
    if not text:
        return []
    parts = re.split(r"\n+|;|\||,", text)
    out: list[str] = []
    for part in parts:
        cleaned = re.sub(r"^\s*[-*•]\s*", "", part).strip()
        if cleaned and cleaned not in out:
            out.append(cleaned)
        if len(out) >= limit:
            break
    return out


def _technical_profile_for(item: dict[str, Any]) -> dict[str, Any] | None:
    db = _load_technical_db()
    brand = norm(item.get("brand"))
    if brand == "vw":
        brand = "volkswagen"
    model = norm(item.get("model"))
    title = norm(item.get("title"))
    raw = norm(
        " ".join(
            [
                str(item.get("raw_text") or ""),
                title,
                brand,
                model,
            ]
        )
    )
    year = int(item.get("year") or 0)

    best: dict[str, Any] | None = None
    best_score = -1
    for profile in db.get("profiles", []):
        profile_brand = norm(profile.get("brand"))
        profile_model = norm(profile.get("model"))
        aliases = [norm(alias) for alias in profile.get("aliases", [])]
        if profile_brand != brand:
            continue

        score = 0
        if profile_model and (profile_model in model or profile_model in title or profile_model in raw):
            score += 10
        if any(alias and alias in raw for alias in aliases):
            score += 8

        years = profile.get("years") or [0, 9999]
        if year and int(years[0]) <= year <= int(years[1]):
            score += 6
        elif year:
            score -= 4

        if score > best_score:
            best = profile
            best_score = score

    return best if best_score >= 10 else None


def _technical_brief(item: dict[str, Any]) -> dict[str, list[str]]:
    brand = norm(item.get("brand"))
    raw = norm(
        " ".join(
            [
                str(item.get("title") or ""),
                str(item.get("brand") or ""),
                str(item.get("model") or ""),
                str(item.get("fuel") or ""),
                str(item.get("gearbox") or ""),
                str(item.get("raw_text") or ""),
            ]
        )
    )
    mileage = int(item.get("mileage") or 0)
    price = float(item.get("price") or 0)

    profile = _technical_profile_for(item)
    buy: list[str] = []
    avoid: list[str] = []
    check: list[str] = []
    questions: list[str] = []

    if profile:
        generation = profile.get("generation") or profile.get("model") or "model profile"
        market_role = profile.get("market_role")
        if market_role:
            buy.append(f"{generation}: {market_role}")
        buy.extend(profile.get("why_buy", [])[:4])
        avoid.extend(profile.get("why_not", [])[:4])

        body_checks = profile.get("body_checks", [])[:4]
        mechanical_checks = profile.get("mechanical_checks", [])[:4]
        if body_checks:
            check.append("body: " + ", ".join(body_checks))
        if mechanical_checks:
            check.append("mechanical: " + ", ".join(mechanical_checks))
        questions.extend(profile.get("seller_questions", [])[:3])

        matched_engine = False
        for engine in profile.get("engines", []):
            patterns = engine.get("patterns") or []
            if patterns and _contains_any(raw, patterns):
                matched_engine = True
                label = engine.get("label") or "engine"
                verdict = engine.get("verdict") or "check carefully"
                buy.append(f"{label}: {verdict}")
                risks = engine.get("risks", [])[:4]
                checks = engine.get("checks", [])[:4]
                if risks:
                    avoid.append(f"{label} risks: " + ", ".join(risks))
                if checks:
                    check.append(f"{label} checks: " + ", ".join(checks))

        if not matched_engine:
            avoid.append("exact engine is unclear; ask seller before driving out")
            questions.append("Welcher Motor genau? Hubraum, PS und Motorcode falls bekannt?")
    else:
        buy.append("generic small/budget car profile: can be interesting only if price, TUV and body are strong")
        avoid.append("no deep model profile matched yet; manual description/photo check matters more")
        check.extend(
            [
                "cold start, idle, clutch, gearbox and dashboard warning lights",
                "sills, arches, underbody rust and accident panel gaps",
                "TUV report, documents, service invoices and mileage plausibility",
            ]
        )

    if mileage:
        if mileage <= 130000:
            buy.append("mileage is attractive for this price class if interior wear matches")
        elif mileage >= 220000 and brand not in {"toyota", "honda", "mazda", "suzuki", "hyundai", "kia"}:
            avoid.append("high mileage: only interesting if TUV/service are strong and price is very low")

    if price:
        if price <= 1500:
            buy.append("low entry price gives room for cleaning, small repairs and negotiation")
        elif price >= 3000:
            avoid.append("above 3000 EUR needs clean TUV, service proof and a clear reason to beat normal market")

    if not _contains_any(raw, ["tuev", "tuv", "hu bis", "hu neu"]):
        avoid.append("TUV/HU is not visible in the available text")
        questions.append("Hat das Auto gueltigen TUV/HU? Bis wann genau?")

    questions.extend(
        [
            "Laufen Motor und Getriebe kalt und warm einwandfrei?",
            "Gibt es bekannte Maengel, Rost, Oelverlust oder Warnleuchten?",
            "Sind TUV-Bericht, Fahrzeugbrief und Rechnungen vorhanden?",
            "Ist eine Probefahrt moeglich?",
        ]
    )

    def unique(values: list[str], limit: int) -> list[str]:
        out: list[str] = []
        for value in values:
            clean = str(value).strip()
            if clean and clean not in out:
                out.append(clean)
            if len(out) >= limit:
                break
        return out

    return {
        "buy": unique(buy, 7),
        "avoid": unique(avoid, 7),
        "check": unique(check, 7),
        "questions": unique(questions, 6),
    }


def _label_from_listing(listing: Any) -> str:
    verdict = str(getattr(listing, "verdict", "") or "").upper()
    if verdict == "HOT":
        return "OPEN FIRST"
    if verdict in {"GOOD", "CHECK"}:
        return "OPEN"
    return verdict or "CHECK"


def _listing_to_report_item(listing: Any) -> dict[str, Any]:
    score = int(round(float(getattr(listing, "final_score", 0) or 0) * 100))
    why = _split_report_text(getattr(listing, "why_interesting", ""), limit=7)
    risks = _split_report_text(getattr(listing, "possible_risks", ""), limit=8)
    model_issues = _split_issue_text(getattr(listing, "model_specific_issues", ""), limit=4)
    if model_issues:
        risks.extend([f"model issue: {item}" for item in model_issues if item not in risks])

    ai_summary = str(getattr(listing, "ai_summary", "") or "")
    if not why and ai_summary:
        why = _split_report_text(ai_summary, limit=4)

    return {
        "label": _label_from_listing(listing),
        "score": max(0, min(100, score)),
        "title": getattr(listing, "title", "") or "",
        "brand": getattr(listing, "brand", "") or "",
        "model": getattr(listing, "model", "") or "",
        "year": int(getattr(listing, "year", 0) or 0),
        "mileage": int(getattr(listing, "mileage", 0) or 0),
        "price": float(getattr(listing, "price", 0) or 0),
        "age": getattr(listing, "listing_age_minutes", None),
        "url": getattr(listing, "url", "") or "",
        "why": why[:7],
        "risks": risks[:8],
        "blockers": [],
        "fuel": getattr(listing, "fuel", "") or "",
        "gearbox": getattr(listing, "gearbox", "") or "",
        "raw_text": " ".join(
            str(value or "")
            for value in [
                getattr(listing, "title", ""),
                getattr(listing, "description", ""),
                getattr(listing, "brand", ""),
                getattr(listing, "model", ""),
                getattr(listing, "fuel", ""),
                getattr(listing, "gearbox", ""),
                getattr(listing, "engine", ""),
            ]
        ),
    }


def write_best_of_scan_report(
    config: dict[str, Any],
    scan_count: int,
    listings: list[Any] | None = None,
) -> None:
    """Write best_of_scan.txt from the final SQLite/Excel export list only."""
    if not config.get("best_of_scan_report_enabled", True):
        return

    output_path = ROOT / str(config.get("best_of_scan_report_file", "output/best_of_scan.txt"))
    scored = [_listing_to_report_item(listing) for listing in list(listings or [])]
    max_rows = int(config.get("best_of_scan_rows", 10))

    lines: list[str] = []
    lines.append("AUTOHAWK BEST OF SCAN")
    lines.append("=" * 72)
    lines.append(f"Scan: #{scan_count} | Time: {datetime.now().strftime('%d.%m.%Y %H:%M:%S')}")
    lines.append(
        "Final SQLite export rows: "
        f"{len(scored)} | Source: same HOT/GOOD/CHECK selection as deals.xlsx | "
        f"Shown: {min(max_rows, len(scored))}"
    )
    lines.append("")

    if not scored:
        lines.append("No final export listings passed the shared SQLite/Excel selection.")

    for idx, item in enumerate(scored[:max_rows], start=1):
        km = "?" if not item["mileage"] else f"{item['mileage']:,} km"
        year = "?" if not item["year"] else str(item["year"])
        price = "?" if not item["price"] else f"{item['price']:,.0f} EUR"
        age = "?" if item["age"] is None else f"{item['age']} min"
        lines.append(f"{idx}. [{item['label']}] score={item['score']} | {item['title']}")
        lines.append(
            f"   Car: {item['brand'] or '?'} {item['model'] or '?'} | "
            f"Year: {year} | Km: {km} | Price: {price} | Age: {age}"
        )
        lines.append(f"   Link: {item['url'] or '-'}")
        if item["why"]:
            lines.append("   Why: " + "; ".join(item["why"]))
        if item["risks"]:
            lines.append("   Risks: " + "; ".join(item["risks"]))
        if item["blockers"]:
            lines.append("   Blockers: " + "; ".join(item["blockers"]))

        tech = _technical_brief(item)
        lines.append("   Technical explanation:")
        if tech["buy"]:
            lines.append("      Why it can be good: " + "; ".join(tech["buy"]))
        if tech["avoid"]:
            lines.append("      Why it can be bad: " + "; ".join(tech["avoid"]))
        if tech["check"]:
            lines.append("      What to inspect: " + "; ".join(tech["check"]))
        if tech["questions"]:
            lines.append("      Seller questions: " + " | ".join(tech["questions"]))

    lines.append("")
    lines.append("-" * 72)
    lines.append("Meaning: OPEN FIRST/OPEN are manual-check priorities, not buy guarantees.")
    lines.append("This report now uses the same final SQLite export selection as deals.xlsx.")
    lines.append("")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("\n".join(lines), encoding="utf-8")
