from __future__ import annotations

import csv
import json
import re
import unicodedata
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SOLD_TRACKER_CSV = ROOT / "output" / "sold_tracker.csv"
MANUAL_FEEDBACK_CSV = ROOT / "data" / "manual_sold_feedback.csv"
MEMORY_JSON = ROOT / "output" / "sold_velocity_memory.json"
MEMORY_SUMMARY_CSV = ROOT / "output" / "sold_velocity_memory_summary.csv"
REPORT_TXT = ROOT / "output" / "sold_velocity_report.txt"


def _norm(value: Any) -> str:
    text = str(value or "").lower()
    replacements = {
        "Ã¤": "ae", "Ã¶": "oe", "Ã¼": "ue", "ÃŸ": "ss",
        "Ã£Â¤": "ae", "Ã£Â¶": "oe", "Ã£Â¼": "ue",
        "ÃƒÂ¤": "ae", "ÃƒÂ¶": "oe", "ÃƒÂ¼": "ue", "ÃƒÅ¸": "ss",
        "ÃƒÆ’Ã‚Â¤": "ae", "ÃƒÆ’Ã‚Â¶": "oe", "ÃƒÆ’Ã‚Â¼": "ue", "ÃƒÆ’Ã…Â¸": "ss",
    }
    for old, new in replacements.items():
        text = text.replace(old, new)
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"\s+", " ", text).strip()


def _to_int(value: Any) -> int:
    try:
        return int(float(str(value or "").replace(",", ".").strip()))
    except Exception:
        return 0


def _to_float(value: Any) -> float:
    try:
        return float(str(value or "").replace(",", ".").strip())
    except Exception:
        return 0.0


def _year_bucket(year: int) -> str:
    if not year:
        return "year_unknown"
    if year < 2000:
        return "pre_2000"
    start = (year // 5) * 5
    return f"{start}-{start + 4}"


def _price_bucket(price: float) -> str:
    if not price:
        return "price_unknown"
    if price <= 1200:
        return "0-1200 EUR"
    if price <= 1800:
        return "1200-1800 EUR"
    if price <= 2500:
        return "1800-2500 EUR"
    if price <= 4000:
        return "2500-4000 EUR"
    if price <= 6500:
        return "4000-6500 EUR"
    return "6500+ EUR"


def _mileage_bucket(mileage: int) -> str:
    if not mileage:
        return "km unknown"
    if mileage <= 130000:
        return "0-130k km"
    if mileage <= 180000:
        return "130-180k km"
    if mileage <= 230000:
        return "180-230k km"
    if mileage <= 300000:
        return "230-300k km"
    return "300k+ km"


def _signature(row: dict[str, Any]) -> tuple[str, str, str, str, str]:
    brand = _norm(row.get("brand"))
    if brand == "vw":
        brand = "volkswagen"
    model = _norm(row.get("model"))
    year = _to_int(row.get("year"))
    mileage = _to_int(row.get("mileage"))
    price = _to_float(row.get("price"))
    return (brand, model, _year_bucket(year), _price_bucket(price), _mileage_bucket(mileage))


def _family_signature(row: dict[str, Any]) -> tuple[str, str, str, str]:
    brand, model, year_b, price_b, _km_b = _signature(row)
    return (brand, model, year_b, price_b)


def _read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as fh:
            return list(csv.DictReader(fh))
    except Exception:
        return []


def _sold_status(status: str) -> bool:
    s = _norm(status)
    return s in {"sold", "reserved", "reserve", "reserviert", "removed", "deleted", "sold_or_removed", "verkauft"}


def _event_from_tracker(row: dict[str, str]) -> dict[str, Any] | None:
    status = _norm(row.get("status"))
    if status not in {"sold_or_removed", "active"}:
        return None
    return {
        "status": status,
        "source": "sold_tracker",
        "title": row.get("title", ""),
        "brand": row.get("brand", ""),
        "model": row.get("model", ""),
        "year": row.get("year", ""),
        "mileage": row.get("mileage", ""),
        "price": row.get("price", ""),
        "minutes_since_found": row.get("minutes_since_found", ""),
        "url": row.get("url", ""),
    }


def _event_from_manual(row: dict[str, str]) -> dict[str, Any] | None:
    if not _sold_status(row.get("status", "")):
        return None
    return {
        "status": "sold_or_removed",
        "source": "manual_feedback",
        "title": row.get("title", ""),
        "brand": row.get("brand", ""),
        "model": row.get("model", ""),
        "year": row.get("year", ""),
        "mileage": row.get("mileage", ""),
        "price": row.get("price", ""),
        "minutes_since_found": row.get("minutes_to_sold", "") or row.get("minutes_since_found", ""),
        "url": row.get("url", ""),
    }


def _add(bucket: dict[str, dict[str, Any]], key: tuple[Any, ...], event: dict[str, Any]) -> None:
    k = "|".join(map(str, key))
    item = bucket.setdefault(k, {
        "key": list(key),
        "sold": 0,
        "fast_sold": 0,
        "active": 0,
        "manual_sold": 0,
        "unknown_removed": 0,
        "examples": [],
    })
    status = _norm(event.get("status"))
    minutes = _to_int(event.get("minutes_since_found"))
    if status == "active":
        item["active"] += 1
    elif status == "sold_or_removed":
        item["sold"] += 1
        if event.get("source") == "manual_feedback":
            item["manual_sold"] += 1
        if not minutes or minutes <= 24 * 60:
            item["fast_sold"] += 1
        if len(item["examples"]) < 6:
            item["examples"].append({
                "title": event.get("title", ""),
                "price": event.get("price", ""),
                "year": event.get("year", ""),
                "mileage": event.get("mileage", ""),
                "minutes": minutes,
                "source": event.get("source", ""),
                "url": event.get("url", ""),
            })


def build_sold_memory() -> dict[str, Any]:
    events: list[dict[str, Any]] = []
    for row in _read_csv(SOLD_TRACKER_CSV):
        event = _event_from_tracker(row)
        if event:
            events.append(event)
    for row in _read_csv(MANUAL_FEEDBACK_CSV):
        event = _event_from_manual(row)
        if event:
            events.append(event)

    exact: dict[str, dict[str, Any]] = {}
    family: dict[str, dict[str, Any]] = {}

    for event in events:
        sig = _signature(event)
        fam = _family_signature(event)
        if not sig[0] or not sig[1]:
            continue
        _add(exact, sig, event)
        _add(family, fam, event)

    memory = {
        "version": "2026-07-06-report-only-v1",
        "mode": "REPORT_ONLY_DOES_NOT_AFFECT_SCORING",
        "totals": {
            "events": len(events),
            "sold_events": sum(1 for e in events if e.get("status") == "sold_or_removed"),
            "active_events": sum(1 for e in events if e.get("status") == "active"),
            "exact_buckets": len(exact),
            "family_buckets": len(family),
        },
        "exact": exact,
        "family": family,
    }

    MEMORY_JSON.parent.mkdir(parents=True, exist_ok=True)
    MEMORY_JSON.write_text(json.dumps(memory, ensure_ascii=False, indent=2), encoding="utf-8")
    write_summary(memory)
    write_report(memory)
    return memory


def _bucket_rows(memory: dict[str, Any]) -> list[tuple[str, str, dict[str, Any]]]:
    rows: list[tuple[str, str, dict[str, Any]]] = []
    for typ in ["exact", "family"]:
        for key, value in memory.get(typ, {}).items():
            rows.append((typ, key, value))
    rows.sort(
        key=lambda row: (
            int(row[2].get("manual_sold", 0)),
            int(row[2].get("fast_sold", 0)),
            int(row[2].get("sold", 0)),
            -int(row[2].get("active", 0)),
        ),
        reverse=True,
    )
    return rows


def write_summary(memory: dict[str, Any]) -> None:
    with MEMORY_SUMMARY_CSV.open("w", encoding="utf-8-sig", newline="") as fh:
        fields = ["type", "key", "sold", "fast_sold", "active", "manual_sold", "example", "url"]
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        for typ, key, value in _bucket_rows(memory)[:800]:
            ex = value.get("examples", [{}])[0] if value.get("examples") else {}
            writer.writerow({
                "type": typ,
                "key": key,
                "sold": value.get("sold", 0),
                "fast_sold": value.get("fast_sold", 0),
                "active": value.get("active", 0),
                "manual_sold": value.get("manual_sold", 0),
                "example": ex.get("title", ""),
                "url": ex.get("url", ""),
            })


def write_report(memory: dict[str, Any]) -> None:
    lines: list[str] = []
    totals = memory.get("totals", {})
    lines.append("AUTOHAWK SOLD VELOCITY MEMORY - REPORT ONLY")
    lines.append("=" * 72)
    lines.append("This file does NOT affect scoring/filtering. It is only market memory.")
    lines.append("")
    lines.append(
        f"Events: {totals.get('events', 0)} | Sold/removed: {totals.get('sold_events', 0)} | "
        f"Active: {totals.get('active_events', 0)} | Buckets: {totals.get('family_buckets', 0)} family / {totals.get('exact_buckets', 0)} exact"
    )
    lines.append("")
    lines.append("Top fast-moving patterns:")
    shown = 0
    for typ, key, value in _bucket_rows(memory):
        sold = int(value.get("sold", 0))
        fast = int(value.get("fast_sold", 0))
        manual = int(value.get("manual_sold", 0))
        active = int(value.get("active", 0))
        if sold <= 0:
            continue
        shown += 1
        lines.append(f"{shown}. [{typ}] {key}")
        lines.append(f"   sold={sold} fast<=24h={fast} manual={manual} active_seen={active}")
        for ex in value.get("examples", [])[:3]:
            price = ex.get("price") or "?"
            year = ex.get("year") or "?"
            km = ex.get("mileage") or "?"
            minutes = ex.get("minutes") or "?"
            lines.append(f"   - {ex.get('title','')} | {year} | {km} km | {price} EUR | after {minutes} min")
            if ex.get("url"):
                lines.append(f"     {ex.get('url')}")
        if shown >= 40:
            break
    if shown == 0:
        lines.append("No confirmed sold/reserved patterns yet. Add manual feedback or wait for sold tracker confirmations.")
    lines.append("")
    lines.append("Manual feedback file:")
    lines.append(str(MANUAL_FEEDBACK_CSV))
    REPORT_TXT.write_text("\n".join(lines), encoding="utf-8")


if __name__ == "__main__":
    memory = build_sold_memory()
    print(json.dumps(memory.get("totals", {}), ensure_ascii=False, indent=2))
    print(f"Wrote: {MEMORY_JSON}")
    print(f"Wrote: {MEMORY_SUMMARY_CSV}")
    print(f"Wrote: {REPORT_TXT}")