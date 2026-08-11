"""Description intelligence for German used-car ads.

The goal is not keyword panic. The goal is to separate:
- cheap negotiation defects;
- dangerous profit killers;
- real positive proof.
"""

from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

from src.utils import norm

ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "data" / "description_intelligence_database.json"


@lru_cache(maxsize=1)
def load_description_database() -> dict[str, Any]:
    if not DB_PATH.exists():
        return {"terms": []}
    try:
        return json.loads(DB_PATH.read_text(encoding="utf-8-sig"))
    except Exception:
        return {"terms": []}


def _pattern_hit(text: str, raw_pattern: str) -> bool:
    pattern = norm(raw_pattern)
    if not pattern:
        return False
    # Plain phrase match first; then flexible whitespace regex.
    if pattern in text:
        return True
    escaped = re.escape(pattern).replace(r"\ ", r"\s+")
    return re.search(r"\b" + escaped + r"\b", text, re.IGNORECASE) is not None


def analyze_description_intelligence(listing: dict[str, Any]) -> dict[str, Any]:
    title = norm(listing.get("title"))
    desc = norm(listing.get("description"))
    text = f"{title} {desc} {norm(listing.get('engine'))} {norm(listing.get('gearbox'))} {norm(listing.get('fuel'))}"

    hits: list[dict[str, Any]] = []
    seen: set[str] = set()
    score_delta = 0
    repair_reserve = 0
    why: list[str] = []
    risks: list[str] = []
    checks: list[str] = []
    kill_hits = 0
    danger_hits = 0
    opportunity_hits = 0
    positive_hits = 0

    for term in load_description_database().get("terms", []):
        term_id = str(term.get("id") or "")
        if not term_id or term_id in seen:
            continue
        patterns = term.get("patterns") or []
        if not any(_pattern_hit(text, p) for p in patterns):
            continue
        seen.add(term_id)
        severity = str(term.get("severity") or "warning").lower()
        delta = int(term.get("score_delta") or 0)
        cost = int(term.get("repair_cost") or 0)
        score_delta += delta
        repair_reserve += cost
        meaning = str(term.get("meaning") or term_id)
        risk = str(term.get("risk") or "")
        ask = str(term.get("ask") or "")
        hits.append({
            "id": term_id,
            "severity": severity,
            "meaning": meaning,
            "score_delta": delta,
            "repair_cost": cost,
        })

        if severity == "kill":
            kill_hits += 1
            risks.append(f"Description DB KILL: {meaning}. {risk}".strip())
        elif severity == "danger":
            danger_hits += 1
            risks.append(f"Description DB danger: {meaning}. {risk}".strip())
        elif severity == "warning":
            risks.append(f"Description DB warning: {meaning}. {risk}".strip())
        elif severity in {"positive", "opportunity"}:
            if severity == "positive":
                positive_hits += 1
                why.append(f"Description DB positive: {meaning}.")
            else:
                opportunity_hits += 1
                why.append(f"Description DB opportunity: {meaning}.")
        else:
            risks.append(f"Description DB note: {meaning}. {risk}".strip())
        if ask:
            checks.append("Description DB ask/check: " + ask)

    # Avoid one long ad with many tiny hits overpowering hard reality.
    score_delta = max(-65, min(35, score_delta))
    confidence = "low"
    if len(desc) >= 250 and hits:
        confidence = "medium"
    if len(desc) >= 500 and hits:
        confidence = "high"

    cap = None
    if kill_hits:
        cap = 48
    elif danger_hits >= 2:
        cap = 58
    elif danger_hits == 1:
        cap = 68

    summary_bits = []
    if kill_hits:
        summary_bits.append(f"{kill_hits} kill signal(s)")
    if danger_hits:
        summary_bits.append(f"{danger_hits} danger signal(s)")
    if positive_hits:
        summary_bits.append(f"{positive_hits} positive proof signal(s)")
    if opportunity_hits:
        summary_bits.append(f"{opportunity_hits} cheap-opportunity signal(s)")

    return {
        "score_delta": score_delta,
        "repair_reserve": repair_reserve,
        "score_cap": cap,
        "confidence": confidence,
        "hits": hits,
        "why": why[:8],
        "risks": risks[:10],
        "checks": checks[:10],
        "summary": "Description DB: " + (", ".join(summary_bits) if summary_bits else "no strong wording signals"),
    }
