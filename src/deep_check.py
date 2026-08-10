"""Deep candidate audit for AUTOHAWK.

This module is not a market-price estimator. It is the pre-call due diligence
layer: why cheap, what can kill profit, what is cheap to fix, and what must be
asked before visiting.
"""

from __future__ import annotations

import json
import re
from typing import Any
from  import replacements

def norm(value: Any) -> str:
    text = str(value or "").lower()
    for _ in range(2):
        try:
            fixed = text.encode("latin1").decode("utf-8")
        except UnicodeError:
            break
        if fixed == text:
            break
        text = fixed

    for old, new in replacements.items():
        text = text.replace(old, new)
    return re.sub(r"\s+", " ", text).strip()


def _has(text: str, pattern: str) -> bool:
    return re.search(pattern, text, re.IGNORECASE) is not None


def _photo_count(listing: dict[str, Any]) -> int:
    raw = listing.get("photo_urls") or "[]"
    if isinstance(raw, list):
        return len(raw)
    try:
        value = json.loads(raw)
        return len(value) if isinstance(value, list) else 0
    except Exception:
        return 0


def _money(value: Any) -> float:
    try:
        return float(value or 0)
    except Exception:
        return 0.0


def analyze_deep_check(
    listing: dict[str, Any],
    *,
    market_price: float | None = None,
    discount_pct: float | None = None,
    liquidity_score: int | None = None,
    tuv_months: int | None = None,
) -> dict[str, Any]:
    title = listing.get("title") or ""
    desc = listing.get("description") or ""
    brand = listing.get("brand") or "?"
    model = listing.get("model") or "?"
    year = listing.get("year")
    mileage = int(listing.get("mileage") or 0)
    price = _money(listing.get("price"))
    fuel = norm(listing.get("fuel"))
    gearbox = norm(listing.get("gearbox"))
    engine = norm(listing.get("engine"))
    seller_type = norm(listing.get("seller_type"))
    text = norm(f"{brand} {model} {title} {desc} {fuel} {gearbox} {engine} {seller_type}")
    photos = _photo_count(listing)

    score_delta = 0
    reserve = 0
    score_cap: int | None = None
    why: list[str] = []
    risks: list[str] = []
    checks: list[str] = []
    unknowns: list[str] = []
    cheap_fixes: list[str] = []
    expensive_risks: list[str] = []
    proof: list[str] = []

    # 1) Data quality: without facts, no confident buy signal.
    if not year:
        unknowns.append("year missing")
    if not mileage:
        unknowns.append("mileage missing")
    if len(desc.strip()) < 80:
        unknowns.append("description too short")
    if photos == 0:
        unknowns.append("no usable photos extracted")
    elif photos <= 2:
        unknowns.append("too few photos for condition confidence")
    if "vin" not in text and "fin" not in text:
        unknowns.append("VIN/FIN not mentioned")

    if unknowns:
        score_delta -= min(12, 3 * len(unknowns))
        checks.append("Missing facts: " + ", ".join(unknowns[:6]) + ". Ask before visiting.")

    # 2) Documents and TUV.
    no_tuv = _has(text, r"\b(kein\s+tuv|kein\s+hu|ohne\s+tuv|ohne\s+hu|tuv\s+abgelaufen|hu\s+abgelaufen)\b")
    long_tuv = _has(text, r"\b(tuv\s+neu|hu\s+neu|tuv\s+bis\s+(?:0[1-9]|1[0-2])[\./ ]?(?:27|28|2027|2028)|hu\s+bis\s+(?:0[1-9]|1[0-2])[\./ ]?(?:27|28|2027|2028))\b")
    if no_tuv:
        reserve += 800
        score_delta -= 18
        expensive_risks.append("No/expired TUV: can hide rust, brakes, emissions or suspension costs.")
        checks.append("Ask why it has no TUV and request the last HU report before any trip.")
        if price > 1800:
            score_cap = min(score_cap or 100, 55)
    elif long_tuv or (tuv_months is not None and tuv_months >= 18):
        score_delta += 8
        proof.append("long/fresh TUV")

    if _has(text, r"\b(keine\s+papiere|brief\s+fehlt|fahrzeugbrief\s+fehlt|zulassungsbescheinigung\s+fehlt|nur\s+mit\s+kaufvertrag)\b"):
        score_delta -= 35
        score_cap = min(score_cap or 100, 35)
        expensive_risks.append("Document problem: bad for resale and not worth a normal flip.")
        checks.append("Reject unless documents are fully clear before money changes hands.")

    # 3) Hard mechanical unknowns.
    hard_patterns = [
        (r"\bmotorschaden\b", "engine damage"),
        (r"\bgetriebeschaden\b", "gearbox damage"),
        (r"\bmotor\s+(?:klappert|klopf|macht\s+geraeusche|laeuft\s+nicht)\b", "engine noise/not running"),
        (r"\bspringt\s+nicht\s+an\b|\bstartet\s+nicht\b", "does not start"),
        (r"\bueberhitzt\b|\bkopfdichtung\b", "overheating/head gasket"),
        (r"\b(?:starker\s+)?rauch\b|\braucht\b", "smoke"),
    ]
    for pattern, label in hard_patterns:
        if _has(text, pattern):
            score_delta -= 30
            reserve += 1600
            score_cap = min(score_cap or 100, 42)
            expensive_risks.append(f"Hard mechanical risk: {label}.")

    if _has(text, r"\b(oelverbrauch|oel\s+verbrauch|verbrennt\s+oel)\b"):
        reserve += 1200
        score_delta -= 24
        score_cap = min(score_cap or 100, 48)
        expensive_risks.append("Oil consumption mentioned: can destroy resale trust.")
        checks.append("Ask exact oil use per 1000 km and avoid if above normal.")
    if _has(text, r"\b(1[,.]?\d?\s*l(?:iter)?\s*(?:auf|pro)\s*1000|1\s*l\s*/\s*1000)\b"):
        reserve += 1800
        score_delta -= 35
        score_cap = min(score_cap or 100, 38)
        expensive_risks.append("High oil use around 1L/1000km: usually not a flip candidate.")

    # 4) Gearbox / expensive systems.
    expensive_patterns = [
        (r"\bdsg\b|\bs-?tronic\b", "DSG/S-tronic: mechatronic/clutch risk without proof", 900, 12),
        (r"\bmultitronic\b|\bcvt\b|\bvariator\b", "CVT/Multitronic/variator: weak resale reputation", 1400, 20),
        (r"\bautomatik\b.{0,80}\b(ruckelt|schaltet\s+schlecht|problem|defekt)\b", "automatic gearbox symptom", 1600, 24),
        (r"\bkupplung\s+rutscht\b|\bkupplung\s+defekt\b", "clutch problem", 700, 12),
        (r"\bturbo\s+defekt\b|\bturbolader\b.{0,80}\b(defekt|pfeift|problem)\b", "turbo problem", 1100, 18),
        (r"\bdpf\b|\bpartikelfilter\b|\begr\b|\badblue\b", "diesel emissions system risk", 900, 14),
        (r"\binjektor\b|\bduese\b|\bhochdruckpumpe\b|\btdi\b.{0,80}\bproblem\b", "diesel injection risk", 1000, 14),
        (r"\bairbag\b|\babs\b|\besp\b|\bmotorkontrollleuchte\b|\bcheck\s*engine\b|\bmk?l\b", "dashboard warning light mentioned", 450, 10),
        (r"\blenkung\b.{0,80}\b(defekt|problem|spiel)\b|\blenkgetriebe\b", "steering/rack risk", 800, 12),
    ]
    for pattern, label, cost, penalty in expensive_patterns:
        if _has(text, pattern):
            reserve += cost
            score_delta -= penalty
            expensive_risks.append(label)

    # 5) Body/accident/rust.
    if _has(text, r"\bunfallfahrzeug\b|\bairbag\s+ausgeloest\b|\brahmenschaden\b|\brichtbank\b"):
        reserve += 1500
        score_delta -= 28
        score_cap = min(score_cap or 100, 45)
        expensive_risks.append("Serious accident wording: hard to resell cleanly.")
    elif _has(text, r"\bunfall\b|\bfrontschaden\b|\bheckschaden\b|\bseitenschaden\b"):
        reserve += 800
        score_delta -= 14
        expensive_risks.append("Accident/damage wording: margin must cover inspection and buyer discount.")

    if _has(text, r"\bschweller\b|\bdurchrostung\b|\bunterboden\b|\brostloch\b|\btragend\b"):
        reserve += 1400
        score_delta -= 26
        score_cap = min(score_cap or 100, 46)
        expensive_risks.append("Structural rust wording: sills/underbody/holes can kill TUV and resale.")
    elif _has(text, r"\brost\b|\bkorrosion\b"):
        reserve += 500
        score_delta -= 8
        expensive_risks.append("Rust mentioned: can be cheap cosmetic or structural; must inspect.")
        checks.append("Rust check: arches, door bottoms, sills, underbody, brake lines.")

    # 6) Cheap defects: opportunity only if car is liquid and price gap exists.
    cheap_patterns = [
        (r"\bbatterie\b.{0,60}\b(schwach|leer|defekt)\b", "battery"),
        (r"\bklima\b.{0,80}\b(defekt|geht\s+nicht|ohne\s+funktion)\b", "AC not working"),
        (r"\bschluessel\b.{0,80}\b(defekt|funk|fernbedienung)\b", "key/remote"),
        (r"\bkofferraum\b.{0,80}\b(oeffnet\s+nicht|schloss)\b", "trunk lock"),
        (r"\binspektion\s+faellig\b|\bservice\s+faellig\b", "service due"),
        (r"\breifen\b|\b8-?fach\b|\bwinterreifen\b|\bsommerreifen\b", "tires/wheels signal"),
        (r"\bkratzer\b|\bdelle\b|\bbeule\b|\bkosmetik\b", "cosmetic dents/scratches"),
        (r"\binnenraum\b.{0,80}\b(schmutzig|dreckig|gebrauch)\b|\breinigung\b", "dirty interior"),
        (r"\bauspuff\b", "exhaust"),
        (r"\bbremsen\b|\bbremse\b", "brakes"),
    ]
    for pattern, label in cheap_patterns:
        if _has(text, pattern):
            cheap_fixes.append(label)

    if cheap_fixes:
        cheap_unique = list(dict.fromkeys(cheap_fixes))
        why.append("Cheap/problem opportunity: " + ", ".join(cheap_unique[:6]) + ".")
        checks.append("Price these defects conservatively; they are negotiation points, not automatic profit.")
        if liquidity_score and liquidity_score >= 70 and (discount_pct is None or discount_pct >= 8 or price <= 1800):
            score_delta += min(8, 2 * len(cheap_unique))

    # 7) Proof and seller motive.
    proof_patterns = [
        (r"\bscheckheft\b|\bserviceheft\b|\brechnungen\b", "service/invoice proof"),
        (r"\b1\.?\s*hand\b|\berstbesitz\b", "1.Hand"),
        (r"\bzahnriemen\b.{0,80}\b(neu|gemacht|gewechselt|erneuert)\b", "timing belt done"),
        (r"\bkupplung\b.{0,80}\b(neu|gemacht|gewechselt|erneuert)\b", "clutch done"),
        (r"\boelwechsel\b.{0,80}\b(neu|gemacht|regelmaessig)\b", "oil service proof"),
        (r"\bbremsen\b.{0,80}\b(neu|gemacht|erneuert)\b", "brakes done"),
        (r"\bgaragenwagen\b|\bnichtraucher\b|\bfamilienauto\b", "trust/usage signal"),
    ]
    for pattern, label in proof_patterns:
        if _has(text, pattern):
            proof.append(label)

    seller_motive = []
    motive_patterns = [
        (r"\bumzug\b", "move"),
        (r"\bneues\s+auto\b|\bneuwagen\b", "new car bought"),
        (r"\bschnell\s+weg\b|\bschnell\s+verkaufen\b|\bdringend\b", "urgent sale"),
        (r"\bvb\b|\bverhandlungsbasis\b|\bpreis\s+verhandelbar\b", "negotiable"),
        (r"\bprivat\b", "private seller"),
    ]
    for pattern, label in motive_patterns:
        if _has(text, pattern):
            seller_motive.append(label)

    if proof:
        proof = list(dict.fromkeys(proof))
        why.append("Proof signals: " + ", ".join(proof[:6]) + ".")
        score_delta += min(12, 3 * len(proof))
    if seller_motive:
        seller_motive = list(dict.fromkeys(seller_motive))
        why.append("Seller/motive signals: " + ", ".join(seller_motive[:5]) + ".")
        score_delta += min(6, 2 * len(seller_motive))

    # 8) Price logic: cheap is not enough.
    if market_price and price:
        gap = market_price - price
        if gap <= 0:
            risks.append("Deep price check: asking is not below the reference market price.")
            score_delta -= 12
            score_cap = min(score_cap or 100, 55)
        elif gap < 500:
            risks.append("Deep price check: market gap is small; little room after prep/risk.")
            score_delta -= 6
        elif gap >= 1200 and not expensive_risks:
            why.append(f"Deep price check: gross gap about {int(gap)} EUR before costs.")
            score_delta += 8
        elif gap >= 1200 and expensive_risks:
            checks.append(f"Deep price check: gross gap {int(gap)} EUR exists, but expensive risks must explain the low price.")

    if liquidity_score is not None and liquidity_score < 55:
        risks.append("Deep liquidity check: buyer exit is weak; do not chase discount blindly.")
        score_delta -= 10
        score_cap = min(score_cap or 100, 58)
    elif liquidity_score is not None and liquidity_score >= 80:
        why.append("Deep liquidity check: strong buyer exit if condition is real.")

    # Decision.
    severe = len([x for x in expensive_risks if any(word in x.lower() for word in ["engine", "gearbox", "structural", "document", "high oil", "serious accident"])])
    if score_cap is not None and score_cap <= 45:
        decision = "SKIP"
    elif severe >= 2:
        decision = "SKIP"
    elif expensive_risks or unknowns:
        decision = "ASK_FIRST"
    else:
        decision = "CALL"

    confidence = "HIGH"
    if unknowns:
        confidence = "LOW" if len(unknowns) >= 3 else "MEDIUM"
    if expensive_risks:
        confidence = "LOW" if len(expensive_risks) >= 3 else "MEDIUM"

    report_lines = [
        f"Deep check: {decision} | confidence {confidence}",
        f"Car: {brand} {model} {year or '?'} | km {mileage or '?'} | price {int(price) if price else '?'} EUR",
        "Known proof: " + (", ".join(proof[:6]) if proof else "none found"),
        "Cheap defects/opportunity: " + (", ".join(list(dict.fromkeys(cheap_fixes))[:8]) if cheap_fixes else "none found"),
        "Expensive risks: " + (", ".join(expensive_risks[:8]) if expensive_risks else "none found"),
        "Unknowns: " + (", ".join(unknowns[:8]) if unknowns else "none critical"),
    ]
    if seller_motive:
        report_lines.append("Seller motive: " + ", ".join(seller_motive[:5]))

    if decision == "CALL":
        checks.insert(0, "Deep check decision: CALL now, but still verify TUV, cold start, OBD, rust and documents.")
    elif decision == "ASK_FIRST":
        checks.insert(0, "Deep check decision: ASK FIRST. Do not drive there until seller answers the red-flag questions.")
    else:
        checks.insert(0, "Deep check decision: SKIP unless the missing/risky facts are proven harmless.")

    return {
        "decision": decision,
        "confidence": confidence,
        "score_delta": max(-45, min(25, int(score_delta))),
        "score_cap": score_cap,
        "repair_reserve": reserve,
        "why": why[:10],
        "risks": risks + expensive_risks[:10],
        "checks": checks[:14],
        "unknowns": unknowns[:10],
        "cheap_fixes": list(dict.fromkeys(cheap_fixes))[:10],
        "expensive_risks": expensive_risks[:10],
        "proof": proof[:10],
        "summary": " | ".join(report_lines),
    }
