"""
AUTOHAWK deterministic dealer engine V2.

Core rule:
    profit > risk + repairs + resale time

This module is intentionally not an LLM prompt. It is the cheap flipper brain:
- liquidity first;
- market discount second;
- reason for low price;
- known engine/gearbox/model risks;
- estimated net profit after real costs;
- resale speed;
- strict rejection of most listings.

It never certifies condition. It only ranks opportunity signals.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from src.model_knowledge import contains_any, find_model_card
from src.flip_value_database import evaluate_flip_value, find_flip_value_card
from src.description_intelligence import analyze_description_intelligence
from src.utils import norm


def clamp(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return max(low, min(high, value))


def bullets(items: list[str], limit: int = 10) -> str:
    clean: list[str] = []
    for item in items:
        item = str(item or "").strip()
        if item and item not in clean:
            clean.append(item)
    return "\n".join(f"- {item}" for item in clean[:limit])


def _match(text: str, patterns: list[tuple[str, str]]) -> list[str]:
    found = []
    for pattern, label in patterns:
        if re.search(pattern, text, re.IGNORECASE):
            found.append(label)
    return found


def _photo_count(listing: dict) -> int:
    raw = listing.get("photo_urls") or "[]"
    try:
        urls = json.loads(raw) if isinstance(raw, str) else raw
    except Exception:
        urls = []
    return len([u for u in urls or [] if isinstance(u, str) and u.startswith("http")])


def _body(listing: dict) -> str:
    return norm(
        " ".join(
            str(listing.get(key) or "")
            for key in ["title", "description", "brand", "model", "engine", "gearbox", "fuel", "location"]
        )
    )



def _explicit_no_tuv(text: str) -> bool:
    return bool(
        re.search(
            r"\b(ohne|kein|keine|abgelaufen|faellig|fÃ¤llig)\s*(tuev|tuv|hu|hauptuntersuchung)\b|\b(tuev|tuv|hu)\s*(abgelaufen|faellig|fÃ¤llig)\b",
            text,
            re.IGNORECASE,
        )
    )


def _has_tuv_claim(text: str) -> bool:
    if _explicit_no_tuv(text):
        return False
    if re.search(r"\b(tuev|tuv|hu|hauptuntersuchung)\s*(neu|gemacht|bekommen|gueltig|gÃ¼ltig)\b", text, re.IGNORECASE):
        return True
    if re.search(r"\b(tuev|tuv|hu)\s*(bis)?\s*(0?[1-9]|1[0-2])\s*[./-]?\s*(2[6-9]|202[6-9])\b", text, re.IGNORECASE):
        return True
    if re.search(r"\b(0?[1-9]|1[0-2])\s*[./-]?\s*(2[6-9]|202[6-9])\b.{0,20}\b(tuev|tuv|hu)\b", text, re.IGNORECASE):
        return True
    return False






def _tuv_months_left(text: str) -> int | None:
    if _explicit_no_tuv(text):
        return -1
    now_year = 2026
    now_month = 6
    best: int | None = None
    for m in re.finditer(r"\b(?:tuev|tuv|hu)\s*(?:bis)?\s*(0?[1-9]|1[0-2])\s*[./-]?\s*(2[6-9]|202[6-9])\b", text, re.IGNORECASE):
        month = int(m.group(1))
        year = int(m.group(2))
        if year < 100:
            year += 2000
        months = (year - now_year) * 12 + (month - now_month)
        best = months if best is None else max(best, months)
    for m in re.finditer(r"\b(0?[1-9]|1[0-2])\s*[./-]?\s*(2[6-9]|202[6-9])\b.{0,24}\b(?:tuev|tuv|hu)\b", text, re.IGNORECASE):
        month = int(m.group(1))
        year = int(m.group(2))
        if year < 100:
            year += 2000
        months = (year - now_year) * 12 + (month - now_month)
        best = months if best is None else max(best, months)
    if re.search(r"\b(?:tuev|tuv|hu)\s*(?:neu|gemacht|bekommen|frisch)\b|\bfrisch(?:er)?\s*(?:tuev|tuv|hu)\b", text, re.IGNORECASE):
        best = 24 if best is None else max(best, 24)
    return best


def _door_profile(text: str) -> tuple[int, str]:
    if re.search(r"\b(?:5|fuenf|funf)\s*[- ]?(?:tuerer|tuerig|tuerig|tueren|tuer|door|doors)\b|\b5trg\b|\b5-trg\b", text, re.IGNORECASE):
        return 20, "5-door liquidity bonus"
    if re.search(r"\b(?:3|drei)\s*[- ]?(?:tuerer|tuerig|tueren|tuer|door|doors)\b|\b3trg\b|\b3-trg\b|\bcoupe\b|\bcabrio\b", text, re.IGNORECASE):
        return -15, "3-door/coupe liquidity penalty"
    return 0, "door count unknown"


def _seller_is_commercial(listing: dict, text: str) -> bool:
    seller_type = norm(listing.get("seller_type"))
    if seller_type == "dealer":
        return True
    return bool(re.search(r"\bautohaus\b|\bhaendler\b|\bhÃ¤ndler\b|\bim\s*kundenauftrag\b|\bim\s*auftrag\b|\bgebrauchtwagen\b|\bfinanzierung\b|\bgarantie\b", text, re.IGNORECASE))


def _simple_petrol_score(text: str, fuel: str, mileage: int) -> tuple[int, list[str], list[str]]:
    good: list[str] = []
    risks: list[str] = []
    score = 0
    fuel_l = norm(fuel)
    petrolish = "benzin" in fuel_l or "petrol" in fuel_l or "otto" in fuel_l or not fuel_l
    risky_turbo = re.search(r"\b(?:tsi|tfsi|fsi|t-jet|turbo|ecoboost|thp|puretech)\b", text, re.IGNORECASE)
    if petrolish and re.search(r"\b(?:mpi|1\.0\s*mpi|1\.1|1\.2|1\.25|1\.4)\b", text, re.IGNORECASE) and not risky_turbo:
        score += 12
        good.append("simple naturally aspirated petrol/MPI-type engine signal")
    if re.search(r"\b1\.4\s*tsi\b|\b1\.8\s*tsi\b|\b2\.0\s*tfsi\b", text, re.IGNORECASE):
        score -= 40 if mileage and mileage >= 180000 else 24
        risks.append("high-risk VAG TSI/TFSI engine for resale unless service proof is strong")
    if re.search(r"\bfsi\b", text, re.IGNORECASE):
        score -= 18
        risks.append("FSI found: not automatic reject, but not HOT without strong TUV/service/condition proof")
    if re.search(r"\becoboost\b", text, re.IGNORECASE):
        if re.search(r"\b(?:zahnriemen|wet\s*belt|nassriemen)\b.{0,40}\b(?:neu|gemacht|gewechselt|erneuert)\b", text, re.IGNORECASE):
            score -= 8
            risks.append("EcoBoost found but wet-belt/timing proof is mentioned; still verify invoices")
        else:
            score -= 40
            risks.append("EcoBoost without wet-belt proof is high-risk for flip")
    if risky_turbo and mileage and mileage >= 180000:
        score -= 18
        risks.append("turbo engine above 180k km reduces safe flip quality")
    return score, good, risks


def _resale_feature_score(text: str) -> tuple[int, list[str]]:
    points = 0
    hits: list[str] = []
    features = [
        (r"\bsitzheizung\b|\bshz\b", 5, "Sitzheizung"),
        (r"\bbluetooth\b|\bcarplay\b|\bandroid\s*auto\b", 6, "Bluetooth/CarPlay"),
        (r"\bklimaautomatik\b|\bklimatronic\b", 6, "Klimaautomatik"),
        (r"\b8\s*fach\b|\b8-fach\b|\bwinterreifen\b.{0,25}\bsommerreifen\b|\bsommerreifen\b.{0,25}\bwinterreifen\b", 7, "8-fach bereift"),
        (r"\bscheckheft\b|\blueckenlos\b|\blÃ¼ckenlos\b|\bserviceheft\b", 8, "Scheckheft/service proof"),
        (r"\b1\.\s*hand\b|\berste\s*hand\b|\bein\s*besitzer\b", 8, "1.Hand"),
        (r"\bahk\b|\banhaengerkupplung\b|\banhÃ¤ngerkupplung\b", 5, "tow hitch / AHK"),
        (r"\bkombi\b|\bturnier\b|\bvariant\b|\bsports\s*tourer\b|\bcw\b", 5, "Kombi/practical body"),
    ]
    for pattern, value, label in features:
        if re.search(pattern, text, re.IGNORECASE):
            points += value
            hits.append(label)
    return min(points, 25), hits


def _target_flip_profile(brand: str, model: str, year: int, price: float, mileage: int, text: str) -> tuple[bool, str, int]:
    b = norm(brand)
    m = norm(model)
    if b in {"volkswagen", "vw"} and "up" in m and 2012 <= year <= 2015:
        return True, "VW up 2012-2015 target: ideal buy <3700 EUR", 3700
    if b in {"volkswagen", "vw"} and "polo" in m and 2010 <= year <= 2014:
        return True, "VW Polo 2010-2014 target: ideal buy <3500 EUR", 3500
    if b in {"hyundai"} and "i10" in m:
        return True, "Hyundai i10 target: best buy 1400-1700 EUR", 1700
    if b == "ford" and "focus" in m and ("kombi" in text or "turnier" in text) and re.search(r"\b1\.8\b|\b2\.0\b", text):
        return True, "Ford Focus Mk2 Kombi target: best buy <1500 EUR", 1500
    if b == "ford" and "fiesta" in m:
        return True, "Ford Fiesta target: simple petrol/5-door/TUV car", 3200
    if b == "opel" and "corsa" in m:
        return True, "Opel Corsa target: cheap simple petrol/5-door/TUV car", 3200
    if b in {"volkswagen", "vw"} and "golf" in m and year and year <= 2004:
        return True, "Golf 4 target: only cheap, usually 1000-1600 EUR", 1600
    return False, "", 0


def _overpriced_youth_flip(brand: str, model: str, year: int, price: float, mileage: int, text: str) -> tuple[bool, str]:
    target, label, cap = _target_flip_profile(brand, model, year, price, mileage, text)
    if target and cap and price > cap:
        return True, f"{label}; asking {int(price)} EUR is above flipper buy zone"
    return False, ""
def _is_excluded_luxury(brand: str, model: str, text: str) -> bool:
    b = norm(brand)
    m = norm(model)
    haystack = f"{b} {m} {text}"
    if b in EXCLUDED_LUXURY:
        return True
    return any(name in haystack for name in EXCLUDED_LUXURY)

def _is_premium_price_over_young_budget(brand: str, price: float, config: dict | None = None) -> bool:
    cap = int((config or {}).get("young_buyer_premium_max_price", 5000))
    return norm(brand) in PREMIUM and bool(price and price > cap)

def _is_strict_premium_candidate(brand: str, model: str, year: int, price: float, text: str) -> bool:
    b = norm(brand)
    m = norm(model)
    if b not in PREMIUM:
        return False
    if price > 5500:
        return False
    premium_watch = any(x in m for x in ["a3", "a4", "a6", "1er", "3er", "5er", "c-klasse", "e-klasse"])
    generation_watch = any(x in text for x in ["e46", "e90", "a3 8p", "a4 b6", "a4 b7", "w203", "w204"])
    return (premium_watch or generation_watch) and (not year or year <= 2012)


def _is_golf4_candidate(brand: str, model: str, year: int, text: str) -> bool:
    b = norm(brand)
    m = norm(model)
    return b in {"volkswagen", "vw"} and "golf" in m and (
        "golf 4" in text or "golf iv" in text or "golf4" in text or (year and year <= 2004)
    )


def _has_exceptional_golf4_proof(text: str, mileage: int) -> bool:
    proof_hits = 0
    for token in ["tuev neu", "tuv neu", "hu neu", "tuev bis", "tuv bis", "hu bis", "1. hand", "erste hand", "scheckheft", "zahnriemen", "rechnungen", "kein rost"]:
        if token in text:
            proof_hits += 1
    if mileage and mileage <= 150000:
        proof_hits += 1
    return proof_hits >= 3 and _has_tuv_claim(text)

# Level 4: almost always reject.
LEVEL4_KILL = [
    (r"\bmotorschaden\b|\bmotor\s*schaden\b|\bmotor\s*defekt\b|\bmotor\s*klopft\b|\bmotor\s*stuckt\b", "level 4: engine damage/noise"),
    (r"\bmotor\s*unruhig\b|\bunruhiger\s*motor\b|\bmotor\s*(?:laeuft|lauft)\s*unruhig\b|\bmotor\s*lÃ¤uft\s*unruhig\b|\bmotorproblem\b|\bmotor\s*problem\b", "level 4: engine runs poorly"),
    (r"\bkalt\b.{0,30}\b(motor|laeuft|lauft|start)\b.{0,30}\b(schlecht|unruhig|ruckelt)\b|\bmotor\b.{0,30}\bkalt\b.{0,30}\b(schlecht|unruhig|ruckelt)\b", "level 4: cold-start engine issue"),
    (r"\boelverbrauch\b|\boel\s*verbrauch\b|\bverbrauch[t]?\s*oel\b|\b[0-9]+(?:[,.][0-9]+)?\s*l(?:iter)?\s*oel\b.{0,20}\b(1000|1\.000)\s*km\b", "level 4: high oil consumption wording"),
    (r"\bgetriebeschaden\b|\bgetriebe\s*schaden\b|\bgetriebe\s*defekt\b|\bgetriebe\s*problem\b|\bautomatik\s*problem\b|\bautomatikgetriebe\s*problem\b|\bschaltet\s*nicht\b", "level 4: gearbox damage/problem"),
    (r"\bbastler\b|\bbastlerfahrzeug\b|\bprojektfahrzeug\b", "level 4: Bastler/project car"),
    (r"\bnur\s*export\b|\bexport\s*only\b|\bexportfahrzeug\b|\bhaendler\s*export\b", "level 4: export-only wording"),
    (r"\bnicht\s*fahrbereit\b|\bfaehrt\s*nicht\b|\bstartet\s*nicht\b|\bspringt\s*nicht\s*an\b", "level 4: not roadworthy / does not start"),
    (r"\bkontrollleuchte\b|\bmotorlampe\b|\bcheck\s*engine\b|\babs\s*leuchtet\b|\bairbag\s*leuchtet\b", "level 4: warning light wording"),
    (r"\bohne\s*papiere\b|\bkeine\s*papiere\b|\bohne\s*brief\b|\bbrief\s*fehlt\b", "level 4: missing documents"),
    (r"\bunfallwagen\b|\bunfallschaden\b|\btotalschaden\b|\bschrott\b|\brahmen\s*schaden\b|\b[0-9]+\s*unfaelle\b|\b[0-9]+\s*unfall\b|\bunfall\b.{0,40}\b(repariert|gehabt|vorbesitzer|bekannt|schaden)\b", "level 4: accident/salvage/structural damage"),
    (r"\bdurchrostung\b|\btragende\s*teile\s*rost|\bstarker\s*rost\b|\bunterboden\s*durch\b", "level 4: serious/structural rust"),
    (r"\bkopfdichtung\b|\bzylinderkopfdichtung\b|\bueberhitzt\b|\bwasser\s*im\s*oel\b", "level 4: head gasket/overheating"),
    (r"\bkm\s*manipuliert\b|\btacho\s*manipuliert\b|\bkilometer\s*unklar\b", "level 4: mileage manipulation wording"),
    (r"\bfinanzierung\s*laeuft\b|\bpfand\b|\bzoll\b|\barrest\b", "level 4: legal/finance risk wording"),
    (r"\bersatzteiltraeger\b|\bersatzteile\b|\bschlachtfest\b", "level 4: parts car"),
]


# Level 3: dangerous, only with large discount and proof.
LEVEL3_DANGER = [
    (r"\bturbo\b.*\bdefekt\b|\bturbo\s*defekt\b", "level 3: turbo issue", 1300),
    (r"\binjektor\b|\bduese\b|\bduesen\b", "level 3: injector/fuel system risk", 900),
    (r"\bdpf\b|\bpartikelfilter\b|\begr\b|\bagr\b|\badblue\b", "level 3: diesel emissions system risk", 900),
    (r"\bsteuerkette\b|\bkette\b", "level 3: timing chain risk", 1400),
    (r"\bkupplung\b|\bzweimassen\b|\bzms\b", "level 3: clutch/flywheel risk", 900),
    (r"\bmechatronik\b|\bdsg\b|\bdq200\b|\bs-?tronic\b", "level 3: DSG/mechatronic risk", 1500),
    (r"\bcvt\b|\bmultitronic\b|\bvariator\b", "level 3: CVT/Multitronic risk", 1800),
    (r"\belektronik\s*problem\b|\bcan\s*bus\b|\bsteuergeraet\b", "level 3: electronics/control unit risk", 1000),
    (r"\bpneuma\b|\bluftfahrwerk\b", "level 3: air suspension risk", 1800),
    (r"\bdurchrostung\b|\btragende\s*teile\b.*\brost\b|\bschweller\b.*\brost\b|\bunterboden\b.*\brost\b|\brahmen\b.*\brost\b|\brostloch\b|\bloch\b.*\brost\b", "level 3: structural rust suspicion", 900),
]


# Level 2: acceptable only if margin covers it.
LEVEL2_ACCEPTABLE = [
    (r"\bsensor\b|\bsonde\b", "level 2: sensor issue", 250),
    (r"\btuev\s*faellig\b|\bhu\s*faellig\b|\btuev\s*bald\b|\bhu\s*bald\b", "level 2: HU/TUV soon due", 350),
    (r"\bohne\s*tuev\b|\bkein\s*tuev\b|\btuev\s*abgelaufen\b|\bohne\s*hu\b|\bkeine\s*hu\b|\bhu\s*abgelaufen\b", "level 2: no valid HU/TUV - strong negotiation/inspection risk", 800),
    (r"\bkleine\s*oel\b|\boel\s*schwitzt\b|\bleicht\s*undicht\b", "level 2: small leak wording", 400),
    (r"\bbremsen\b|\bbremse\b|\bbremsscheiben\b|\bbremsbelaege\b|\bbremse\s*defekt\b", "level 2: brakes may need work", 350),
    (r"\brost\b|\bkorrosion\b|\bradlauf\b.*\brost\b|\btuer\b.*\brost\b|\btuerkante\b.*\brost\b|\bkotfluegel\b.*\brost\b|\bfluegel\b.*\brost\b", "level 2: visible rust/body rust needs inspection", 550),
    (r"\bdelle\b|\bbeule\b|\beingedrueckt\b|\bverzogen\b|\bspaltmass\b|\bspaltmasse\b|\bpanel\s*gap\b|\bstossstange\b.*\bschief\b", "level 2: visible dent/body damage needs inspection", 450),
    (r"\bfahrwerk\b|\bquerlenker\b|\bstossdaempfer\b", "level 2: suspension work possible", 500),
    (r"\bservice\s*faellig\b|\binspektion\s*faellig\b", "level 2: service overdue", 350),
    (r"\bklima(?:anlage)?\b.{0,25}\b(defekt|funktioniert\s*nicht|geht\s*nicht|ohne\s*funktion)\b|\bklima\s*defekt\b", "level 2: air conditioning repair needed", 500),
    (r"\bkofferraum\b.{0,35}\b(nicht\s*oeffnen|geht\s*nicht\s*auf|oeffnet\s*nicht|schloss|schliessmechanismus)\b", "level 2: trunk lock/mechanism issue", 250),
    (r"\bfernbedienung\b.{0,35}\b(funktioniert\s*nicht|geht\s*nicht|defekt)\b|\bschluessel\b.{0,35}\b(neu|defekt|funktioniert\s*nicht|geht\s*nicht)\b", "level 2: key/remote issue", 250),
]


# Level 1: cheap problems can create upside.
LEVEL1_OPPORTUNITY = [
    (r"\bschlechte\s*fotos\b|\bwenig\s*fotos\b", "level 1: weak photo presentation"),
    (r"\bdreckig\b|\bschmutzig\b|\bverschmutzt\b|\baufbereitung\b|\breinigung\b", "level 1: dirty presentation"),
    (r"\bkratzer\b|\blackschaden\b", "level 1: light cosmetic scratches/paint marks"),
    (r"\bunscharf\b|\bverwackelt\b|\bschlechte\s*fotos\b|\bdreckig\b|\bschmutzig\b|\bverschmutzt\b", "level 1: ugly photos/dirty presentation, not a condition kill by itself"),
    (r"\bfelgen\s*kratzer\b|\breifen\s*alt\b", "level 1: wheels/tires presentation issue"),
    (r"\bbatterie\b", "level 1: battery issue can be cheap"),
    (r"\blampe\b|\bbirne\b", "level 1: bulb/light small issue"),
    (r"\bkurze\s*beschreibung\b|\bkeine\s*zeit\b", "level 1: weak listing text"),
    (r"\breserviert\b|\breserved\b", "market feedback: already reserved / strong demand signal"),
]


POSITIVE = [
    (r"\btuev\s*neu\b|\bhu\s*neu\b|\bhauptuntersuchung\s*neu\b", "fresh HU/TUV"),
    (r"\btuev\s*bis\b|\bhu\s*bis\b", "HU/TUV date mentioned"),
    (r"\bscheckheft\b|\bscheckheftgepflegt\b|\bserviceheft\b", "service history"),
    (r"\b1\.\s*hand\b|\berste\s*hand\b", "first owner"),
    (r"\bunfallfrei\b", "accident-free claimed"),
    (r"\bnichtraucher\b", "non-smoker"),
    (r"\bgaragenfahrzeug\b", "garage car"),
    (r"\bservice\s*neu\b|\binspektion\s*neu\b", "recent service"),
    (r"\bzahnriemen\s*neu\b|\bzahnriemen\s*gemacht\b", "timing belt done"),
    (r"\bbremsen\s*neu\b|\breifen\s*neu\b", "wear parts done"),
    (r"\bvin\b|\bfin\b|\bfahrgestellnummer\b", "VIN mentioned"),
    (r"\bfahrbereit\b|\bfahr\s*bereit\b", "roadworthy claimed"),
    (r"\btop\s*zustand\b|\bguter\s*zustand\b|\btop\s*gepflegt\b|\bgepflegt\b", "condition claimed good"),
    (r"\banfaengerauto\b|\banfÃ¤ngerauto\b|\bkleinwagen\b", "beginner/first-car demand wording"),
    (r"\bklima\b|\bklimaanlage\b", "air conditioning / Klima demand signal"),
]


SELLER_GOOD = [
    (r"\bprivat\b|\bprivatverkauf\b", "private seller"),
    (r"\bwegen\s*neuanschaffung\b|\bneues\s*auto\b|\bneuwagen\b|\bauto\s*schon\s*gekauft\b", "selling because new car"),
    (r"\bumzug\b|\bauswanderung\b|\bplatzmangel\b|\bgaragenplatz\b|\bstandplatz\b|\bfamilienzuwachs\b|\btrennung\b", "plausible forced sale reason"),
    (r"\bdringend\b|\bmuss\s*weg\b|\bschnell\s*weg\b|\bschnellstmoeglich\b|\bschnellstmöglich\b|\bsofort\s*abzugeben\b|\bbis\s*wochenende\b|\bheute\s*noch\b|\bkeine\s*zeit\b", "urgent sale signal"),
    (r"\bvb\b|\bvhb\b|\bverhandlungsbasis\b|\bpreis\s*verhandelbar\b|\bvor\s*ort\s*verhandelbar\b", "negotiation signal"),
]


SELLER_BAD = [
    (r"\bim\s*auftrag\b", "selling on behalf of someone else"),
    (r"\bnur\s*whatsapp\b|\bwhatsapp\s*only\b", "suspicious contact channel"),
    (r"\bkeine\s*diagnose\b|\bdiagnose\s*nicht\b|\bkeine\s*probefahrt\b", "diagnostics/test drive refusal wording"),
    (r"\bwas\s*letzte\s*preis\b", "low-quality seller/buyer wording"),
    (r"\bexport\b|\bhaendler\s*export\b", "export wording"),
]


LIQUID_MODELS: dict[tuple[str, str], int] = {
    ("volkswagen", "golf"): 94, ("volkswagen", "polo"): 91, ("volkswagen", "passat"): 86,
    ("volkswagen", "touran"): 82, ("volkswagen", "tiguan"): 86,
    ("skoda", "octavia"): 90, ("skoda", "fabia"): 83, ("skoda", "superb"): 78,
    ("seat", "leon"): 83, ("seat", "ibiza"): 79,
    ("opel", "astra"): 84, ("opel", "corsa"): 83, ("opel", "insignia"): 68,
    ("ford", "focus"): 83, ("ford", "fiesta"): 81, ("ford", "mondeo"): 66,
    ("toyota", "yaris"): 89, ("toyota", "auris"): 85, ("toyota", "corolla"): 89,
    ("honda", "jazz"): 84, ("honda", "civic"): 78,
    ("hyundai", "i20"): 78, ("hyundai", "i30"): 82, ("kia", "ceed"): 81, ("kia", "picanto"): 77,
    ("mercedes", "a-klasse"): 76, ("mercedes", "b-klasse"): 74, ("mercedes", "c-klasse"): 78,
    ("bmw", "1er"): 78, ("bmw", "3er"): 80, ("bmw", "x1"): 76,
    ("audi", "a3"): 80, ("audi", "a4"): 78, ("audi", "q3"): 76,
    ("mazda", "3"): 76, ("nissan", "qashqai"): 76,
    ("suzuki", "swift"): 84, ("mitsubishi", "colt"): 76,
    ("dacia", "sandero"): 72, ("dacia", "duster"): 74,
    ("fiat", "500"): 70,
}

BRAND_LIQUIDITY = {
    "volkswagen": 82, "vw": 82, "skoda": 78, "seat": 72, "opel": 76, "ford": 74,
    "toyota": 82, "honda": 76, "hyundai": 72, "kia": 72, "mazda": 72, "suzuki": 72, "mitsubishi": 68, "nissan": 68,
    "mercedes": 70, "bmw": 70, "audi": 70,
    "renault": 62, "peugeot": 60, "citroen": 56, "fiat": 52,
    "porsche": 45, "land rover": 35, "jaguar": 35, "alfa romeo": 42,
}

PREMIUM = {"bmw", "mercedes", "audi"}
EXCLUDED_LUXURY = {"porsche", "land rover", "range rover", "jaguar", "maserati", "bentley", "aston martin"}
LOW_TRUST = {"land rover", "jaguar", "alfa romeo", "chevrolet", "chrysler", "dodge", "lancia"}


KNOWN_REPUTATION = [
    (r"\bn47\b|318d|320d", "BMW N47/2.0d timing-chain fear", 18, 1400),
    (r"\b1\.4\s*tsi\b|\b1\.8\s*tsi\b|\b2\.0\s*tfsi\b", "older VAG TSI/TFSI oil/chain reputation", 16, 1200),
    (r"\bdq200\b", "VAG DQ200 dry DSG market fear", 24, 1700),
    (r"\bdsg\b|\bs-?tronic\b", "DSG/S-tronic gearbox risk", 14, 1400),
    (r"\bpowershift\b|\bpower\s*shift\b", "Ford PowerShift gearbox market fear", 28, 2200),
    (r"\b1\.0\s*ecoboost\b|\becoboost\b", "Ford EcoBoost wet-belt/cooling reputation", 16, 1200),
    (r"\bpuretech\b|\b1\.2\s*puretech\b|\bthp\b", "PSA PureTech/THP reputation risk", 24, 1500),
    (r"\bmultitronic\b|\bcvt\b|\bvariator\b", "CVT/Multitronic resale fear", 22, 1800),
    (r"\bpneuma\b|\bluftfahrwerk\b", "air suspension resale/repair risk", 20, 1800),
]


@dataclass
class DealerDecision:
    opportunity_score: int
    recommended_action: str
    opportunity_type: str
    liquidity_score: int
    resale_speed: str
    risk_level: str
    condition_grade: str
    estimated_costs: int
    risk_reserve: int
    net_profit_estimate: int | None
    market_discount_pct: float | None
    kill_reasons: list[str]
    missing_info: list[str]
    why_interesting: list[str]
    possible_risks: list[str]
    what_to_check: list[str]
    seller_signals: list[str]
    profit_logic: str
    negotiation_angle: str
    price_view: str
    photo_analysis: str
    model_market_reputation: str
    score_breakdown: dict[str, int]
    product_segment: str
    segment_scores: dict[str, int]

    def as_ai(self) -> dict[str, Any]:
        return {
            "opportunity_score": self.opportunity_score,
            "recommended_action": self.recommended_action,
            "opportunity_type": self.opportunity_type,
            "liquidity_score": self.liquidity_score,
            "resale_speed": self.resale_speed,
            "risk_score": self.risk_level,
            "condition_grade": self.condition_grade,
            "estimated_costs": self.estimated_costs,
            "risk_reserve": self.risk_reserve,
            "net_profit_estimate": self.net_profit_estimate,
            "market_discount_pct": self.market_discount_pct,
            "kill_reasons": self.kill_reasons,
            "missing_info": self.missing_info,
            "photo_analysis": self.photo_analysis,
            "price_view": self.price_view,
            "why_interesting": bullets(self.why_interesting),
            "possible_risks": bullets(self.possible_risks),
            "what_to_check": bullets(self.what_to_check, 14),
            "seller_signals": bullets(self.seller_signals),
            "profit_logic": self.profit_logic,
            "negotiation_angle": self.negotiation_angle,
            "model_market_reputation": self.model_market_reputation,
            "score_breakdown": self.score_breakdown,
            "product_segment": self.product_segment,
            "segment_scores": self.segment_scores,
            "ai_summary": self.summary(),
            "analysis_provider": "local_dealer_engine_v2",
        }

    def summary(self) -> str:
        profit = "unknown net profit" if self.net_profit_estimate is None else f"est. net profit {self.net_profit_estimate} EUR"
        return (
            f"Dealer V2: {self.recommended_action}, score {self.opportunity_score}/100, "
            f"liquidity {self.liquidity_score}/100, resale {self.resale_speed}, "
            f"risk {self.risk_level}, segment {self.product_segment}, {profit}. {self.profit_logic}"
        )


def _adaptive_limits(brand: str, model: str, text: str, config: dict) -> tuple[int, int, str]:
    brand_l = norm(brand)
    model_l = norm(model)
    default_max = int(config.get("max_mileage", 250000))
    min_year = int(config.get("min_year", 2000))
    max_mileage = default_max
    profile = "default"

    durable_japanese = {"toyota", "honda", "mazda", "subaru", "lexus"}
    durable_korean = {"kia", "hyundai"}
    mass_market = {"volkswagen", "vw", "skoda", "seat", "opel", "ford", "nissan"}
    premium = {"bmw", "mercedes", "audi"}
    if brand_l in durable_japanese:
        max_mileage = 300000
        profile = "durable_japanese"
    elif brand_l in durable_korean:
        max_mileage = 270000
        profile = "durable_korean"
    elif brand_l in mass_market:
        max_mileage = 250000
        profile = "mass_market"
    elif brand_l in premium:
        if brand_l == "audi" and any(x in model_l for x in ["a3", "a4"]):
            max_mileage = int(config.get("audi_a3_a4_max_mileage", 230000))
            profile = "audi_liquid_compact"
        else:
            max_mileage = 190000
            profile = "premium_strict"

    risky_terms = [
        "dsg", "s-tronic", "dq200", "multitronic", "cvt", "n47", "puretech",
        "ecoboost", "tsi", "tfsi", "thp", "pneuma", "luftfahrwerk",
        "v8", "v10", "4.2", "5.2", "x5", "a8", "s8", "s6",
    ]
    if any(term in text for term in risky_terms):
        max_mileage = min(max_mileage, 170000)
        profile += "_risk_engine_or_gearbox"

    workhorses = [
        ("volkswagen", "golf"), ("vw", "golf"), ("volkswagen", "polo"), ("vw", "polo"),
        ("skoda", "octavia"), ("toyota", "yaris"), ("toyota", "corolla"),
        ("honda", "jazz"), ("honda", "civic"),
        ("suzuki", "swift"), ("mitsubishi", "colt"),
    ]
    if any(b in brand_l and m in model_l for b, m in workhorses):
        max_mileage = max(max_mileage, 260000)
        if brand_l in durable_japanese:
            max_mileage = max(max_mileage, 300000)

    return max_mileage, min_year, profile


def _liquidity(brand: str, model: str, fuel: str, gearbox: str, text: str, mileage: int) -> tuple[int, str, str]:
    b = norm(brand)
    m = norm(model)
    score = BRAND_LIQUIDITY.get(b, 58)

    for (kb, km), value in LIQUID_MODELS.items():
        if kb == b and (m == km or km in m or m in km):
            score = value
            break

    door_points, _door_label = _door_profile(text)
    score += int(door_points * 0.35)
    if any(x in text for x in ["kombi", "variant", "avant", "touring"]):
        score += 5
    if any(x in norm(gearbox) for x in ["automatic", "automatik"]) or "automatik" in text:
        score += 3
    if "diesel" in norm(fuel):
        score += 2
    if any(x in text for x in ["schwarz", "grau", "silber", "weiss", "white", "black"]):
        score += 2
    if b in LOW_TRUST:
        score -= 22
    if b == "fiat" and "500" not in m:
        score -= 10
    if b in PREMIUM and not any(x in m for x in ["1er", "3er", "a3", "a4", "c-klasse", "a-klasse", "b-klasse", "x1", "q3"]):
        score -= 10
    if mileage >= 260000:
        score -= 16
    elif mileage >= 220000:
        score -= 10
    elif mileage >= 180000:
        score -= 5

    score = int(clamp(score))
    if score >= 84:
        return score, "1-7 days", "clear buyer pool"
    if score >= 70:
        return score, "1-3 weeks", "normal buyer pool"
    if score >= 55:
        return score, "1-2 months", "slow buyer pool"
    return score, "may hang", "unclear resale buyer"


def _freshness_points(age_min: int | None) -> tuple[int, str]:
    if age_min is None:
        return 0, "listing age unknown"
    if age_min <= 60:
        return 15, "0-1h: maximum priority"
    if age_min <= 180:
        return 13, "1-3h: very high priority"
    if age_min <= 360:
        return 9, "3-6h: high priority"
    if age_min <= 720:
        return 5, "6-12h: medium priority"
    return -20, "older than 12h: usually not worth hunting"


def _discount_points(price: float, market_price: float | None) -> tuple[int, float | None, str]:
    if not price or not market_price:
        return 0, None, "No reliable market estimate; compare live analogs manually."
    discount = (market_price - price) / market_price if market_price > 0 else 0.0
    pct = round(discount * 100, 1)
    if discount < 0:
        return -14, pct, f"Above observed market by about {abs(pct)}%; skip unless market estimate is proven wrong."
    if discount < 0.07:
        return 0, pct, f"Market price / weak discount ({pct}%); not enough edge for flipping."
    if discount < 0.12:
        return 4, pct, f"7-12% below market ({pct}%); only interesting with urgent seller or exceptional condition."
    if discount < 0.20:
        return 14, pct, f"12-20% below market ({pct}%); interesting if risk is controlled."
    if discount < 0.30:
        return 24, pct, f"20-30% below market ({pct}%); strong, but the reason must be cheap/understandable."
    return 18, pct, f"30%+ below market ({pct}%); very cheap, suspicious until proven technically."


def _problem_levels(text: str) -> tuple[list[str], list[str], list[str], list[str], int]:
    level4 = _match(text, LEVEL4_KILL)
    level1 = _match(text, LEVEL1_OPPORTUNITY)
    level2: list[str] = []
    level3: list[str] = []
    cost = 0
    for pattern, label, repair in LEVEL2_ACCEPTABLE:
        if re.search(pattern, text, re.IGNORECASE):
            level2.append(label)
            cost += repair
    for pattern, label, repair in LEVEL3_DANGER:
        if re.search(pattern, text, re.IGNORECASE):
            level3.append(label)
            cost += repair
    return level1, level2, level3, level4, cost


DESCRIPTION_PENALTY_SIGNALS = [
    (r"\b(?:hat|hatte)\s+[0-9]+\s*unfaelle\b|\b[0-9]+\s*unfaelle\b|\bmehrere\s*unfaelle\b", "seller admits accident history", 18, "Ask for accident photos, invoices and what exactly was repaired."),
    (r"\bunfall\b.{0,80}\b(nicht\s*schlimm|klein|leicht|nur\s*blech|repariert)\b", "accident is downplayed by seller", 12, "Do not trust wording; inspect paint thickness, gaps and invoices."),
    (r"\b(?:motor|wagen|auto)\b.{0,35}\b(ruckelt|stottert|geht\s*aus|nimmt\s*kein\s*gas|notlauf|leistungsverlust)\b", "engine running issue described", 22, "OBD scan before visit; usually skip unless extremely cheap."),
    (r"\braucht\b|\bblauer\s*rauch\b|\bweisser\s*rauch\b|\bweiÃŸer\s*rauch\b|\bqualmt\b", "smoke/engine wear signal", 22, "Ask when it smokes and avoid without diagnostics."),
    (r"\boeldruck\b|\boel\s*druck\b|\boellampe\b|\boel\s*lampe\b", "oil pressure warning signal", 28, "Oil pressure warning is a near-kill for profit."),
    (r"\bwasser\s*verlust\b|\bkuehlwasser\b.{0,30}\bverlust\b|\bueberhitzt\b|\btemperatur\b.{0,30}\bhoch\b", "cooling/head-gasket risk", 24, "Check coolant pressure/head gasket; usually skip."),
    (r"\bgetriebe\b.{0,45}\b(ruckelt|rutscht|schlaegt|schaltet\s*hart|problem|defekt)\b", "gearbox symptom described", 24, "Gearbox symptom can destroy margin."),
    (r"\bkupplung\b.{0,35}\b(rutscht|kommt\s*hoch|defekt|faellig|fÃ¤llig)\b", "clutch issue described", 14, "Use as negotiation only if margin covers clutch."),
    (r"\belektrik\b.{0,35}\b(problem|spinnt|fehler)\b|\belektronik\b.{0,35}\b(problem|spinnt|fehler)\b", "electronics issue described", 14, "Electrical problems are hard to price without OBD."),
    (r"\bairbag\b.{0,30}\b(leuchtet|fehler|problem)\b|\babs\b.{0,30}\b(leuchtet|fehler|problem)\b|\besp\b.{0,30}\b(leuchtet|fehler|problem)\b", "safety warning described", 18, "Safety lights hurt TUV/resale."),
    (r"\bschweller\b.{0,35}\brost\b|\bunterboden\b.{0,35}\brost\b|\bdurchrost", "structural rust wording", 22, "Structural rust is not cosmetic; inspect before considering."),
    (r"\broststelle[n]?\b|\brost\b.{0,35}\b(radlauf|tuer|tÃ¼r|kotfluegel|kotflÃ¼gel|schweller|unterboden)|\b(radlauf|tuer|tÃ¼r|kotfluegel|kotflÃ¼gel)\b.{0,35}\brost\b", "seller mentions visible body rust", 12, "Visible rust is not just ugly: inspect arches, doors, sills and underbody; only acceptable with price gap."),
    (r"\babgemeldet\b|\babgemeldet\s*seit\b", "car is deregistered", 6, "Not a reject alone, but needs plates/transport/test-drive plan."),
    (r"\bohne\s*gew[aÃ¤]hrleistung\b|\bkeine\s*gew[aÃ¤]hrleistung\b|\bgekauft\s*wie\s*gesehen\b", "private no-warranty wording", 4, "Normal private wording, but do not treat claims as guarantee."),
]

DESCRIPTION_POSITIVE_SIGNALS = [
    (r"\btuev\s*neu\b|\bhu\s*neu\b|\btuev\s*bis\s*(?:0?[4-9]|1[0-2])\s*[./-]?\s*(?:2[7-9]|202[7-9])\b|\bhu\s*bis\s*(?:0?[4-9]|1[0-2])\s*[./-]?\s*(?:2[7-9]|202[7-9])\b", "long/fresh HU/TUV", 8),
    (r"\b1\.\s*hand\b|\berste\s*hand\b|\bein\s*besitzer\b|\bein\s*vorbesitzer\b", "one owner / low-owner trust signal", 7),
    (r"\bscheckheft\b|\bscheckheftgepflegt\b|\bserviceheft\b|\brechnungen\b|\bbelege\b", "service proof mentioned", 7),
    (r"\bregelmaessig\b.{0,25}\boel\b|\bregelmÃ¤ÃŸig\b.{0,25}\bÃ¶l\b|\boelwechsel\b.{0,25}\bregelmaessig\b", "regular oil/service wording", 6),
    (r"\bzahnriemen\b.{0,30}\b(neu|gemacht|gewechselt)\b|\bsteuerkette\b.{0,30}\b(gemacht|erneuert)\b", "timing service proof wording", 7),
    (r"\bunfallfrei\b", "accident-free claim", 5),
    (r"\bklima\b|\bklimaanlage\b", "Klima demand signal", 3),
    (r"\banfaengerauto\b|\banfÃ¤ngerauto\b|\bkleinwagen\b|\bideal\s*fuer\s*fahranfaenger\b", "first-car demand wording", 5),
]



MODEL_CONFLICT_NAMES = [
    "micra", "qashqai", "juke", "note", "pixo",
    "yaris", "aygo", "corolla", "auris",
    "polo", "golf", "fox", "up", "touran", "passat",
    "corsa", "astra", "meriva", "zafira",
    "fiesta", "focus", "fusion", "c-max",
    "ibiza", "leon", "fabia", "octavia",
    "i10", "i20", "i30", "picanto", "rio",
]


def _model_conflict_signals(listing: dict, text: str) -> tuple[int, list[str], list[str]]:
    parsed_model = norm(listing.get("model"))
    title = norm(listing.get("title"))
    desc = norm(listing.get("description"))
    haystack = f"{title} {desc} {text}"
    hits = []
    for name in MODEL_CONFLICT_NAMES:
        if re.search(r"\b" + re.escape(name) + r"\b", haystack, re.IGNORECASE):
            hits.append(name)
    hits = list(dict.fromkeys(hits))
    risks: list[str] = []
    checks: list[str] = []
    penalty = 0
    if len(hits) >= 2:
        penalty -= 14
        risks.append("Model mismatch/conflict in listing text: " + ", ".join(hits[:4]) + ".")
        checks.append("Clarify exact model before visit: ask seller for Fahrzeugschein/FIN photo and confirm whether the car is really the listed model.")
    if parsed_model and hits and not any(h in parsed_model or parsed_model in h for h in hits):
        penalty -= 8
        risks.append(f"Parsed model '{parsed_model}' does not match model words in title/description: " + ", ".join(hits[:4]) + ".")
        checks.append("Do not trust price estimate until brand/model are confirmed; wrong model can make market price useless.")
    if re.search(r"\b(sehr\s*geehrte|fahrzeug\s*befindet\s*sich\s*in\s*einem\s*dem\s*alter|finanzierung\s*moeglich|export\s*moeglich|inzahlungnahme)\b", haystack, re.IGNORECASE):
        penalty -= 5
        risks.append("Template/dealer-like description wording; trust seller claims less and verify facts hard.")
        checks.append("Ask concrete facts, not opinions: TUV report, service invoices, accident history, rust, warning lights.")
    return max(-24, penalty), risks, checks


def _description_truth(listing: dict, text: str) -> tuple[int, list[str], list[str], list[str]]:
    desc = norm(listing.get("description"))
    if not desc:
        return -8, [], ["No detail description extracted; cannot trust condition yet."], ["Ask seller for full defect list, TUV, service and accident history."]

    score = 0
    why: list[str] = []
    risks: list[str] = []
    checks: list[str] = []

    description_intel = analyze_description_intelligence(listing)
    if description_intel:
        score += int(description_intel.get("score_delta") or 0)
        why.extend(list(description_intel.get("why") or [])[:6])
        risks.extend(list(description_intel.get("risks") or [])[:8])
        checks.extend(list(description_intel.get("checks") or [])[:6])
        if description_intel.get("summary"):
            checks.append(str(description_intel.get("summary")))
        cap = description_intel.get("score_cap")
        if cap is not None and score > int(cap) - 50:
            risks.append(f"Description DB cap signal: max-score pressure {int(cap)}/100 because wording contains serious risk.")

    if len(desc) < 80:
        score -= 5
        risks.append("Very short description: seller gives little condition proof.")
        checks.append("Ask direct yes/no questions about TUV, rust, accidents, warning lights, oil use and gearbox.")
    elif len(desc) > 450:
        score += 2
        why.append("Detailed description: more information to judge risks.")

    for pattern, label, points, question in DESCRIPTION_PENALTY_SIGNALS:
        if re.search(pattern, text, re.IGNORECASE) or re.search(pattern, desc, re.IGNORECASE):
            score -= points
            risks.append("Description red flag: " + label)
            checks.append(question)

    for pattern, label, points in DESCRIPTION_POSITIVE_SIGNALS:
        if re.search(pattern, text, re.IGNORECASE) or re.search(pattern, desc, re.IGNORECASE):
            score += points
            why.append("Description positive: " + label)

    if "unfallfrei" in text and re.search(r"\bunfall\b.{0,80}\b(repariert|schaden|gehabt|bekannt)", text):
        score -= 16
        risks.append("Contradictory description: accident-free wording conflicts with accident/repair wording.")
        checks.append("Clarify accident history before any visit.")

    if re.search(r"\btop\s*gepflegt\b|\bsehr\s*gepflegt\b", text) and re.search(r"\brost\b|\bdelle\b|\bbeule\b|\bkratzer\b|\bdefekt\b", text):
        score -= 4
        risks.append("Seller says well maintained but also mentions defects; verify condition carefully.")

    model_conflict_points, model_conflict_risks, model_conflict_checks = _model_conflict_signals(listing, text)
    score += model_conflict_points
    risks.extend(model_conflict_risks)
    checks.extend(model_conflict_checks)

    return int(max(-35, min(18, score))), why, risks, checks

def _known_reputation(listing: dict, text: str, model_risks: list[str]) -> tuple[int, int, list[str]]:
    penalty = 0
    reserve = 0
    risks: list[str] = []
    brand = norm(listing.get("brand"))
    model = norm(listing.get("model"))
    year = int(listing.get("year") or 0)

    for pattern, label, points, cost in KNOWN_REPUTATION:
        if re.search(pattern, text, re.IGNORECASE):
            penalty += points
            reserve += cost
            risks.append(label)

    if brand == "bmw" and ("3er" in model or "1er" in model or re.search(r"318d|320d|n47", text)) and 2007 <= year <= 2012:
        penalty += 18
        reserve += 1400
        risks.append("BMW 2007-2012 diesel/N47: ask chain proof, listen cold start")

    if brand in {"volkswagen", "audi", "skoda", "seat"} and "1.4 tsi" in text:
        penalty += 14
        reserve += 1100
        risks.append("VAG 1.4 TSI: chain/oil reputation can affect resale")

    for issue in model_risks or []:
        penalty += 4
        reserve += 250
        risks.append(f"Known model issue: {issue}")

    return min(25, penalty), reserve, risks



def _motivated_seller_points(text: str) -> tuple[int, list[str]]:
    patterns = [
        (r"\bdringend\b|\bmuss\s*weg\b|\bschnell\s*weg\b|\bsofort\s*abzugeben\b|\bbis\s*wochenende\b|\bheute\s*noch\b|\bkeine\s*zeit\b", 8, "urgent/time-pressure sale wording"),
        (r"\bumzug\b|\bauswanderung\b|\bplatzmangel\b|\bgaragenplatz\b|\bstandplatz\b|\bfamilienzuwachs\b|\btrennung\b", 5, "forced life-event sale reason"),
        (r"\bwegen\s*neuanschaffung\b|\bneues\s*auto\b|\bneuwagen\b|\bauto\s*schon\s*gekauft\b", 5, "replacement car already bought"),
        (r"\bvb\b|\bvhb\b|\bverhandlungsbasis\b|\bpreis\s*verhandelbar\b|\bvor\s*ort\s*verhandelbar\b", 3, "explicit negotiation signal"),
    ]
    points = 0
    hits: list[str] = []
    for pattern, value, label in patterns:
        if re.search(pattern, text, re.IGNORECASE):
            points += value
            hits.append(label)
    return min(points, 12), hits
def _seller_score(listing: dict, text: str, discount_pct: float | None = None) -> tuple[int, list[str], list[str]]:
    score = 0
    good = _match(text, SELLER_GOOD)
    bad = _match(text, SELLER_BAD)
    seller_type = (listing.get("seller_type") or "").lower()

    if seller_type == "private":
        score += 5
        good.append("private seller from source")
    elif seller_type == "dealer":
        if discount_pct is not None and discount_pct >= 15:
            score += 1
            good.append("dealer but price appears meaningfully below observed market")
        else:
            score -= 5
            bad.append("dealer/Autohaus: usually lower reseller margin unless clearly under market")

    if any("VIN" in x or "FIN" in x for x in _match(text, POSITIVE)):
        score += 3
    score += min(8, len(good) * 2)
    score -= min(12, len(bad) * 4)
    return int(max(-10, min(10, score))), good, bad
def _data_quality(listing: dict, photo_count: int) -> tuple[int, list[str]]:
    score = 8
    missing: list[str] = []
    for key, label in [
        ("mileage", "mileage missing"),
        ("year", "year missing"),
        ("fuel", "fuel missing"),
        ("gearbox", "gearbox missing"),
        ("listing_age_minutes", "listing age missing"),
    ]:
        if listing.get(key) in (None, "", 0):
            missing.append(label)
            score -= 3
    if photo_count == 0:
        missing.append("no photos extracted")
        score -= 4
    elif photo_count <= 2:
        missing.append("few photos; fast verify, not auto-reject")
        score -= 1
    if listing.get("detail_verified"):
        score += 2
    return int(max(-10, min(10, score))), missing


def _estimate_costs(
    price: float,
    market_price: float | None,
    problem_cost: int,
    reputation_reserve: int,
    risk_level3_count: int,
    risk_level2_count: int,
    warnings_count: int,
) -> tuple[int, int, int, int]:
    prep = 250
    logistics = 200
    diagnostic = 120
    sale_negotiation = int(max(250, (market_price or price or 0) * 0.035))
    repair = problem_cost + risk_level2_count * 150 + risk_level3_count * 350 + warnings_count * 120
    risk_reserve = int(reputation_reserve * 0.45 + risk_level3_count * 450 + warnings_count * 100)
    total = prep + logistics + diagnostic + sale_negotiation + repair + risk_reserve
    return int(total), int(repair), int(risk_reserve), int(sale_negotiation)





def _segment_scores(
    brand: str,
    model: str,
    price: float,
    mileage: int,
    text: str,
    flip_score: int,
    liquidity_raw: int,
    tuv_months: int | None,
    engine_points: int,
    model_penalty: int,
    level2: list[str],
    level3: list[str],
    kill_reasons: list[str],
    positives: list[str],
    resale_feature_points: int,
    discount_pct: float | None,
    net_profit: int | None,
    motivated_points: int,
    config: dict,
) -> tuple[str, dict[str, int], list[str]]:
    """Separate product logic from pure flipper logic."""
    b = norm(brand)
    m = norm(model)
    notes: list[str] = []

    if tuv_months == -1:
        tuv_score = -35
    elif tuv_months is None:
        tuv_score = -6
    elif tuv_months >= 18:
        tuv_score = 25
    elif tuv_months >= 6:
        tuv_score = 14
    else:
        tuv_score = -12

    mileage_score = 0
    if mileage:
        if mileage <= 130000:
            mileage_score = 16
        elif mileage <= 170000:
            mileage_score = 11
        elif mileage <= 220000:
            mileage_score = 5
        else:
            mileage_score = -10

    price_score = 0
    if price:
        if price <= 2500:
            price_score = 14
        elif price <= 4000:
            price_score = 9
        elif price <= float(config.get("youth_flip_general_max_price", 5000)):
            price_score = 4
        else:
            price_score = -12

    simple_engine_score = 16 if engine_points > 0 else (-22 if engine_points <= -25 or model_penalty >= 20 else 0)
    risk_penalty = len(level2) * 4 + len(level3) * 13 + len(kill_reasons) * 35
    proof_score = min(18, len(positives) * 3 + int(resale_feature_points * 0.5))

    safe_first_car = int(clamp(
        liquidity_raw * 0.28 + tuv_score + mileage_score + price_score + simple_engine_score + proof_score - risk_penalty,
        0,
        100,
    ))

    buyer_models = [
        "focus", "golf", "astra", "civic", "a3", "a4", "leon", "ibiza",
        "octavia", "mazda 3", "mazda3", "corolla", "auris",
    ]
    city_models = ["up", "polo", "fiesta", "corsa", "yaris", "aygo", "i10", "swift", "fabia", "jazz"]
    class_appeal = 0
    if any(x in m or x in text for x in buyer_models):
        class_appeal += 14
        notes.append("buyer-value segment: looks/comfort/autobahn usefulness can matter for young buyers")
    if any(x in m or x in text for x in city_models):
        class_appeal += 10
        notes.append("safe-first-car segment: small/simple/cheap running costs are strong buyer signals")
    if b in {"audi", "bmw", "mercedes"}:
        class_appeal += 8
        notes.append("premium badge has buyer appeal, but repair/TUV proof must be strict")

    buyer_value = int(clamp(
        safe_first_car * 0.48 + liquidity_raw * 0.22 + proof_score + class_appeal + max(0, motivated_points) - len(level3) * 8,
        0,
        100,
    ))

    flip_profit = int(clamp(flip_score, 0, 100))
    min_good_profit = int(config.get("min_good_net_profit", 800))
    has_real_flip_edge = (
        (net_profit is not None and net_profit >= min_good_profit)
        or (
            discount_pct is not None
            and discount_pct >= float(config.get("min_market_discount_pct", 12))
            and price <= float(config.get("youth_flip_general_max_price", 5000))
        )
    )

    if flip_profit >= 75 and has_real_flip_edge and not kill_reasons:
        segment = "FLIP_PROFIT"
    elif safe_first_car >= 76 and not kill_reasons:
        segment = "SAFE_FIRST_CAR"
    elif buyer_value >= 74 and not kill_reasons:
        segment = "BUYER_VALUE"
    else:
        segment = "WATCHLIST_ONLY"

    scores = {
        "flip_profit": flip_profit,
        "safe_first_car": safe_first_car,
        "buyer_value": buyer_value,
    }
    return segment, scores, notes




def _high_mileage_workhorse_profile(
    price: float,
    mileage: int,
    year: int,
    text: str,
    tuv_months: int | None,
    commercial_seller: bool,
) -> tuple[int, list[str], list[str], list[str]]:
    """Recognize cheap practical high-mileage cars without making them HOT."""
    points = 0
    hits: list[str] = []
    why: list[str] = []
    risks: list[str] = []
    checks: list[str] = []

    if not mileage or mileage < 180000 or mileage > 240000:
        return 0, why, risks, checks
    if not price or price > 2200:
        return 0, why, risks, checks
    if price <= 1800:
        hits.append("cheap entry price")

    simple_petrol = (
        re.search(r"\b1[.,]6\b|\b1[.,]4\b|\b1[.,]8\b", text, re.IGNORECASE)
        and re.search(r"\bbenzin\b|\bpetrol\b|\botto\b", text, re.IGNORECASE)
        and not re.search(r"\btsi\b|\btfsi\b|\bturbo\b|\becoboost\b|\bpuretech\b|\bthp\b", text, re.IGNORECASE)
    )
    if simple_petrol:
        hits.append("simple naturally aspirated petrol")
    if re.search(r"\bzahnriemen\b.{0,45}\b(neu|gemacht|gewechselt|erneuert)\b", text, re.IGNORECASE):
        hits.append("timing belt replaced")
    if re.search(r"\bkombi\b|\bturnier\b|\bvariant\b|\bsports\s*tourer\b|\bcw\b", text, re.IGNORECASE):
        hits.append("practical Kombi body")
    if re.search(r"\bahk\b|\banhaengerkupplung\b|\banhÃ¤ngerkupplung\b", text, re.IGNORECASE):
        hits.append("tow hitch / AHK")
    if not commercial_seller:
        hits.append("private seller / non-dealer price logic")

    if len(hits) >= 3:
        points = 8
        why.append("High-mileage workhorse exception: " + ", ".join(hits[:5]) + ".")
        checks.append("High-mileage workhorse check: cold start, oil leaks, clutch, suspension, rust underbody/sills, timing-belt invoice and TUV report.")
        risks.append("High mileage is acceptable only because the car is cheap/practical/simple; keep as CHECK, not HOT.")
        if year and year <= 2008:
            risks.append("Older than 18 years: rust, rubber parts, suspension and resale emotion must be checked hard.")
        if re.search(r"\beuro\s*4\b|\beuro4\b", text, re.IGNORECASE):
            risks.append("Euro4: acceptable cheap-entry car, but weaker for city restrictions and future resale than Euro5/6.")
        if tuv_months is not None and 0 <= tuv_months < 12:
            risks.append("TUV under 12 months: not a deal killer, but resale window is shorter and buyer confidence is lower.")
    return points, why, risks, checks


def analyze_dealer_candidate(
    listing: dict,
    warnings: list[str] | None = None,
    positive_signals: list[str] | None = None,
    model_risks: list[str] | None = None,
    market_price: float | None = None,
    config: dict | None = None,
) -> dict[str, Any]:
    warnings = warnings or []
    positive_signals = positive_signals or []
    model_risks = model_risks or []
    config = config or {}

    text = _body(listing)
    price = float(listing.get("price") or 0)
    mileage = int(listing.get("mileage") or 0)
    age_min = listing.get("listing_age_minutes")
    brand = listing.get("brand") or ""
    model = listing.get("model") or ""
    fuel = listing.get("fuel") or ""
    gearbox = listing.get("gearbox") or ""
    year = int(listing.get("year") or 0)
    knowledge_card = find_model_card(brand, model, year, text)
    flip_value_card = find_flip_value_card(brand, model, year, text)

    photo_count = _photo_count(listing)
    kill_reasons: list[str] = []
    missing_info: list[str] = []
    why: list[str] = []
    risks: list[str] = []
    checks: list[str] = []

    if not price:
        kill_reasons.append("missing price")
    if not brand or not model:
        kill_reasons.append("missing brand/model")
    if _is_excluded_luxury(brand, model, text):
        kill_reasons.append("excluded luxury/exotic brand for youth-first-car strategy")
    if age_min is not None:
        max_age = int(config.get("freshness_max_minutes", config.get("freshness_max_hours", 12) * 60))
        min_age = int(config.get("freshness_min_minutes", 1))
        if age_min < min_age:
            kill_reasons.append("too new / unstable listing age")
        if age_min > max_age:
            kill_reasons.append("older than 12h freshness window")
    else:
        missing_info.append("listing age missing")

    adaptive_max_mileage, adaptive_min_year, adaptive_profile = _adaptive_limits(brand, model, text, config)
    if mileage and mileage > adaptive_max_mileage:
        kill_reasons.append(f"mileage above adaptive limit ({adaptive_profile}, max {adaptive_max_mileage}, got {mileage} km)")
    if year and year < adaptive_min_year:
        kill_reasons.append(f"older than adaptive min year ({adaptive_min_year})")

    if _is_premium_price_over_young_budget(brand, price, config):
        kill_reasons.append("premium/luxury over young-buyer price cap (max 5000 EUR)")
    premium_requires_tuv_and_description = _is_strict_premium_candidate(brand, model, year, price, text)
    if premium_requires_tuv_and_description:
        if not _has_tuv_claim(text):
            kill_reasons.append("premium/luxury candidate without clear TUV/HU proof")
        if len(norm(listing.get("description"))) < 120:
            kill_reasons.append("premium/luxury candidate needs a detailed seller description")

    golf4_candidate = _is_golf4_candidate(brand, model, year, text)
    if golf4_candidate and price > 1800 and not _has_exceptional_golf4_proof(text, mileage):
        kill_reasons.append("Golf 4 priced too high for flipper strategy without exceptional proof")

    level1, level2, level3, level4, problem_cost = _problem_levels(text)
    description_points, description_why, description_risks, description_checks = _description_truth(listing, text)
    kill_reasons.extend(level4)
    why.extend(description_why)
    risks.extend(description_risks)
    checks.extend(description_checks)
    risks.extend(level3)
    risks.extend(level2)
    why.extend(level1)
    if any("visible rust" in item or "body rust" in item for item in level2):
        risks.append("Real feedback: rust is not just ugly presentation; check if it is cosmetic or structural before considering profit.")
        checks.append("Inspect rust depth: small wing/door/arch rust can be negotiable; sills, underbody, holes or structural rust are deal killers.")
    if any("brakes" in item for item in level2):
        why.append("Real feedback: brake work can be acceptable if the car is liquid and price leaves room.")
        checks.append("Check brake discs/pads/calipers and use it as a negotiation point.")
    if any(("air conditioning" in item or "trunk lock" in item or "key/remote" in item or "service overdue" in item) for item in level2):
        why.append("Real feedback: small comfort/service defects can be acceptable on a cheap liquid car if priced in.")
        checks.append("Price minor defects: trunk lock/key/AC/service can eat margin but are usually negotiation points, not automatic rejects.")
    if any("reserved" in item for item in level1):
        why.append("Market feedback: reserved quickly means this type of lead has real buyer demand.")

    positives = _match(text, POSITIVE)
    why.extend(positives[:6])
    if positive_signals:
        why.extend([f"Detected positive: {p}" for p in positive_signals[:4]])

    liquidity_raw, resale_speed, buyer_pool = _liquidity(brand, model, fuel, gearbox, text, mileage)
    if knowledge_card:
        card_liquidity = int(knowledge_card.get("liquidity_score", liquidity_raw))
        liquidity_raw = int(round((liquidity_raw + card_liquidity) / 2))
        resale_speed = knowledge_card.get("resale_speed", resale_speed)
        buyer_pool = "model knowledge card"
    if liquidity_raw >= 84:
        why.append("High liquidity: clear buyer pool and fast resale potential.")
    elif liquidity_raw < 55:
        risks.append("Low liquidity: even a cheap car can become dead money.")

    fresh_points, fresh_label = _freshness_points(age_min)
    discount_points, discount_pct, price_view = _discount_points(price, market_price)

    price_truth = listing.get("_price_truth") or {}
    price_truth_cap = None
    if price_truth:
        description_points += int(price_truth.get("score_delta") or 0)
        if price_truth.get("summary"):
            why.append("Price truth: " + str(price_truth.get("summary")))
        why.extend(list(price_truth.get("why") or [])[:4])
        risks.extend(list(price_truth.get("risks") or [])[:5])
        checks.extend(list(price_truth.get("checks") or [])[:4])
        if price_truth.get("score_cap"):
            price_truth_cap = int(price_truth.get("score_cap"))
    seller_points, seller_good, seller_bad = _seller_score(listing, text, discount_pct)
    motivated_points, motivated_hits = _motivated_seller_points(text)
    data_points, data_missing = _data_quality(listing, photo_count)
    missing_info.extend(data_missing)


    seller_type_norm = (listing.get("seller_type") or "").lower()
    commercial_seller = _seller_is_commercial(listing, text)
    if commercial_seller and seller_type_norm != "dealer":
        seller_type_norm = "dealer"
        seller_bad.append("commercial/Autohaus wording detected in listing text")

    dealer_retail_margin_risk = (
        commercial_seller
        and (discount_pct is None or discount_pct < int(config.get("dealer_min_discount_pct", 18)))
    )
    if dealer_retail_margin_risk:
        risks.append("Dealer/Autohaus seller: for flipping this is often retail-priced unless the discount is clearly proven.")
        checks.append("Do not treat dealer trust as margin. Compare hard with private listings and negotiate from wholesale logic.")

    body_rust_in_description = any("visible body rust" in item for item in description_risks + risks)

    model_penalty, reputation_reserve, reputation_risks = _known_reputation(listing, text, model_risks)

    tuv_months = _tuv_months_left(text)
    score_cap_reason = ""
    if tuv_months == -1:
        risks.append("No valid HU/TUV: strong negative for normal young buyers and resale.")
        if price > float(config.get("no_tuv_normal_max_price", 1800)) and not (discount_pct is not None and discount_pct >= 25):
            kill_reasons.append("kein TUV without very cheap price/deep discount")
    elif tuv_months is not None:
        if tuv_months >= 18:
            why.append("Fresh/long HU/TUV: 18+ months, strong resale signal.")
            description_points += 8
        elif tuv_months < 6:
            risks.append("HU/TUV below 6 months: resale friction and negotiation risk.")
            description_points -= 12

    door_points, door_label = _door_profile(text)
    if door_points > 0:
        why.append(door_label + ": strong youth/first-car liquidity factor.")
    elif door_points < 0:
        risks.append(door_label + ": smaller buyer pool; cap GOOD unless price is very strong.")
    liquidity_raw = int(clamp(liquidity_raw + door_points))

    engine_points, engine_good, engine_risks = _simple_petrol_score(text, fuel, mileage)
    description_points += engine_points
    why.extend(engine_good)
    risks.extend(engine_risks)
    if engine_points <= -35:
        model_penalty += 18

    resale_feature_points, resale_features = _resale_feature_score(text)
    if resale_features:
        description_points += resale_feature_points
        why.append("Resale features: " + ", ".join(resale_features[:5]))

    high_mileage_workhorse_points, high_mileage_workhorse_why, high_mileage_workhorse_risks, high_mileage_workhorse_checks = _high_mileage_workhorse_profile(
        price=price,
        mileage=mileage,
        year=year,
        text=text,
        tuv_months=tuv_months,
        commercial_seller=commercial_seller,
    )
    if high_mileage_workhorse_points:
        description_points += high_mileage_workhorse_points
        why.extend(high_mileage_workhorse_why)
        risks.extend(high_mileage_workhorse_risks)
        checks.extend(high_mileage_workhorse_checks)

    target_profile, target_note, target_price_cap = _target_flip_profile(brand, model, year, price, mileage, text)
    if target_profile:
        why.append("Target flip profile: " + target_note)
    overpriced_target, overpriced_reason = _overpriced_youth_flip(brand, model, year, price, mileage, text)
    if overpriced_target:
        risks.append("Youth-flip price cap: " + overpriced_reason)
        if discount_pct is None or discount_pct < 18:
            score_cap_reason = overpriced_reason
    
    flip_value = evaluate_flip_value(
        flip_value_card, listing, text, price, year, mileage, fuel, gearbox, discount_pct
    )
    flip_value_points = int(flip_value.get("score_delta") or 0)
    description_points += flip_value_points
    why.extend(list(flip_value.get("why") or [])[:8])
    risks.extend(list(flip_value.get("risks") or [])[:8])
    checks.extend(list(flip_value.get("checks") or [])[:6])
    flip_value_cap = flip_value.get("score_cap")
    if knowledge_card:
        why.append(
            f"Model knowledge card: {knowledge_card.get('brand')} {knowledge_card.get('model')} "
            f"liquidity {knowledge_card.get('liquidity_score')}/100, resale {knowledge_card.get('resale_speed')}."
        )
        if knowledge_card.get("flip_notes"):
            why.append("Flip note: " + str(knowledge_card.get("flip_notes")))
        if knowledge_card.get("market_fear_level"):
            risks.append("Market fear level: " + str(knowledge_card.get("market_fear_level")))

        bad_hits = contains_any(text, knowledge_card.get("bad_engines", []) + knowledge_card.get("bad_gearboxes", []))
        good_hits = contains_any(text, knowledge_card.get("good_engines", []) + knowledge_card.get("good_gearboxes", []))
        if bad_hits:
            model_penalty += min(25, 8 * len(bad_hits))
            reputation_reserve += min(3000, 700 * len(bad_hits))
            risks.append("Model card bad match: " + ", ".join(bad_hits))
        if good_hits:
            why.append("Model card good match: " + ", ".join(good_hits[:3]))

        for item in knowledge_card.get("typical_problems", [])[:4]:
            risks.append("Known typical issue: " + str(item))
        for item in knowledge_card.get("must_check", [])[:5]:
            checks.append("Model card check: " + str(item))
        card_max = knowledge_card.get("max_safe_mileage")
        if card_max and mileage and mileage > int(card_max):
            risks.append(f"Above model-card safe mileage ({card_max} km).")
            model_penalty += 8
            reputation_reserve += 600

    risks.extend(reputation_risks)
    risks.extend([f"Text warning: {w}" for w in warnings[:6]])
    if motivated_hits:
        seller_good.extend(motivated_hits)
        why.append("Motivated seller signal: " + ", ".join(motivated_hits[:3]))
    if seller_bad:
        risks.extend([f"Seller risk: {s}" for s in seller_bad])

    total_costs, repair_estimate, risk_reserve, resale_discount = _estimate_costs(
        price=price,
        market_price=market_price,
        problem_cost=problem_cost,
        reputation_reserve=reputation_reserve,
        risk_level3_count=len(level3),
        risk_level2_count=len(level2),
        warnings_count=len(warnings),
    )

    net_profit = None
    if price and market_price:
        net_profit = int(round(market_price - price - total_costs))

    liquidity_points = int(round((liquidity_raw / 100) * 20))
    model_problem_points = -int(min(25, model_penalty))
    high_mileage_penalty = 7 if high_mileage_workhorse_points else 12
    repair_risk_points = -int(min(30, len(level3) * 8 + len(level2) * 3 + len(warnings) * 3 + (high_mileage_penalty if mileage >= 180000 else 0)))

    flip_points = 0
    if net_profit is not None:
        if net_profit >= 2000:
            flip_points = 20
        elif net_profit >= 1000:
            flip_points = 15
        elif net_profit >= 500:
            flip_points = 8
        elif net_profit >= 0:
            flip_points = 2
        else:
            flip_points = -12
    else:
        if liquidity_raw >= 84 and fresh_points >= 13 and not level3:
            flip_points = 8
        elif liquidity_raw >= 70 and fresh_points >= 9:
            flip_points = 4

    score_breakdown = {
        "freshness": fresh_points,
        "market_discount": discount_points,
        "liquidity": liquidity_points,
        "seller_quality": seller_points,
        "motivated_seller": motivated_points,
        "known_model_problems": model_problem_points,
        "repair_risk": repair_risk_points,
        "flip_potential": flip_points,
        "data_quality": data_points,
        "description_truth": description_points,
        "flip_value_database": flip_value_points,
        "high_mileage_workhorse": high_mileage_workhorse_points,
    }
    score = sum(score_breakdown.values())

    mass_watch_brands = {
        "volkswagen", "vw", "skoda", "seat", "opel", "ford", "toyota", "honda",
        "hyundai", "kia", "mazda", "nissan", "renault", "peugeot", "citroen",
        "mitsubishi", "suzuki", "fiat",
    }
    street_watchlist = (
        (discount_pct is None or discount_pct >= 0)
        and norm(brand) in mass_watch_brands
        and price >= 1000
        and price <= int(config.get("watchlist_max_price", 3500))
        and (not year or year >= int(config.get("watchlist_min_year", 2004)))
        and (not mileage or mileage <= int(config.get("watchlist_max_mileage", 230000)))
        and liquidity_raw >= int(config.get("watchlist_min_liquidity", 62))
        and not kill_reasons
        and len(level3) <= 1
        and model_penalty < 22
    )
    if street_watchlist:
        why.append("WATCHLIST candidate: cheap liquid mass-market car; final score floor is applied after risk caps.")
        if positives:
            why.append("WATCHLIST positive proof: " + ", ".join(positives[:3]))

    # Business rules: this is where the app behaves like a conservative flipper.
    if "price_truth_cap" in locals() and price_truth_cap and score > price_truth_cap:
        score = min(score, price_truth_cap)
        risks.append("Price-truth cap applied: this looks more like retail/buyer-service price than quick-flip profit.")
    if dealer_retail_margin_risk and score > 62:
        score = min(score, 62)
        why.append("Dealer/Autohaus cap applied: trustworthy seller does not equal flipper margin.")

    if body_rust_in_description and score > 68:
        score = min(score, 68)
        risks.append("Score cap: description body-rust cap applied. Visible rust can still be acceptable on a cheap liquid car, but it needs manual inspection and a real price gap.")
        checks.append("Rust check: inspect arches, door bottoms, sills and underbody. Cosmetic wing/door rust can be negotiated; structural rust kills the flip.")

    if "golf4_candidate" in locals() and golf4_candidate and price > 1800 and score > 60:
        score = min(score, 60)
        risks.append("Golf 4 price cap applied: above about 1800 EUR it needs unusually strong proof, otherwise there is little flipper edge.")
        checks.append("Golf 4 check: compare against 1000-1600 EUR private examples before treating this as a deal.")

    if liquidity_raw < int(config.get("min_liquidity_score", 55)):
        kill_reasons.append("liquidity too low for flipper strategy")
    if discount_pct is not None and discount_pct < 0:
        risks.append("Asking price is above estimated market. This is not a flipper opportunity unless market estimate is proven wrong.")
        score = min(score, 50)
    elif discount_pct is not None and discount_pct < float(config.get("min_market_discount_pct", 5)):
        if not (liquidity_raw >= 88 and positives and len(level1) >= 1):
            risks.append("Price is around market; weak flip edge. Need at least 10-12% real discount or a motivated seller with negotiation room.")
            score = min(score, 52)
    if net_profit is not None and net_profit < int(config.get("min_net_profit_eur", 350)):
        risks.append("Expected net profit is too weak after costs/reserve.")
        score = min(score, 58)
    if mileage >= 240000 and liquidity_raw < 84:
        risks.append("Very high mileage is allowed only for durable/liquid models; resale discount must be strong.")
        score = min(score, 58)

    if len(level3) >= 2 and (net_profit is None or net_profit < 1500):
        risks.append("Multiple dangerous problems without enough margin.")
        score = min(score, 52)

    # Second-pass street flipper logic. This deliberately runs AFTER market/profit
    # caps, because cheap 2004-2014 mass-market cars are often under-scored by
    # rough market formulas. It does not create HOT deals; it only prevents
    # potentially useful cars from disappearing before a human/Gemini check.
    if street_watchlist and not kill_reasons:
        watch_score = int(config.get("watchlist_min_score", 60))
        if any("cosmetic rust" in item for item in level1 + level2):
            watch_score += 4
        if any("brakes" in item for item in level2):
            watch_score += 3
        if price <= 2500:
            watch_score += 4
        if mileage and mileage <= 150000:
            watch_score += 5
        elif mileage and mileage <= 180000:
            watch_score += 3
        if positives:
            watch_score += min(6, len(positives) * 2)
        if seller_good:
            watch_score += min(4, len(seller_good) * 2)
        if liquidity_raw >= 70:
            watch_score += 3
        if not level3:
            watch_score += 3
        if level2:
            watch_score -= min(5, len(level2) * 2)
        if net_profit is not None and net_profit < -1200:
            watch_score -= 4

        watch_score = int(clamp(watch_score, 55, 72))
        score = max(score, watch_score)
        why.append(f"WATCHLIST floor applied: {watch_score}/100. This is not a buy signal; it means inspect/check instead of silent reject.")
        risks.append("WATCHLIST risk: cheap mass-market lead needs manual price comparison, OBD scan, HU/TUV proof and rust check.")

    # Cheap liquid HOT rescue:
    # A real flipper must not miss boring mass-market cars that are
    # clearly below observed market just because the reserve model is
    # conservative. This is for Golf/Polo/Fabia/Fiesta/Astra-type cars,
    # not premium/risky toys.
    hot_liquid_models = {
        ("volkswagen", "golf"), ("vw", "golf"),
        ("volkswagen", "polo"), ("vw", "polo"),
        ("skoda", "fabia"), ("skoda", "octavia"),
        ("opel", "astra"), ("opel", "corsa"),
        ("ford", "fiesta"), ("ford", "focus"),
        ("toyota", "yaris"), ("toyota", "auris"), ("toyota", "corolla"),
        ("honda", "jazz"), ("honda", "civic"),
        ("suzuki", "swift"), ("mitsubishi", "colt"),
    }
    observed_comps = int(listing.get("_market_observed_comps") or listing.get("_market_learned_comps") or 0)
    gross_gap = int(round((market_price or 0) - price)) if price and market_price else 0
    is_hot_liquid_model = any(kb == norm(brand) and (km == norm(model) or km in norm(model) or norm(model) in km) for kb, km in hot_liquid_models)
    cheap_liquid_hot = (
        not kill_reasons
        and is_hot_liquid_model
        and price >= float(config.get("cheap_liquid_hot_min_price", 900))
        and price <= float(config.get("cheap_liquid_hot_max_price", 2300))
        and mileage
        and mileage <= int(config.get("cheap_liquid_hot_max_mileage", 230000))
        and observed_comps >= int(config.get("cheap_liquid_hot_min_comps", 8))
        and discount_pct is not None
        and discount_pct >= float(config.get("cheap_liquid_hot_min_discount_pct", 35))
        and gross_gap >= int(config.get("cheap_liquid_hot_min_gross_gap", 1100))
        and len(level3) == 0
        and model_penalty < int(config.get("cheap_liquid_hot_max_model_penalty", 18))
    )
    if cheap_liquid_hot:
        hot_floor = int(config.get("cheap_liquid_hot_score_floor", 84))
        if any("visible rust" in item or "body rust" in item for item in level2):
            hot_floor -= 4
        if any("brakes" in item for item in level2):
            hot_floor += 1
        score = max(score, hot_floor)
        why.append(
            f"CHEAP LIQUID HOT rescue: {brand} {model} is a liquid mass-market car, "
            f"observed comps={observed_comps}, discount={discount_pct}%, gross market gap={gross_gap} EUR."
        )
        risks.append(
            "HOT rescue still requires fast call, HU/TUV proof, cold start, OBD scan, rust check and test drive. "
            "This is a priority lead, not mechanical certainty."
        )

    # AUDI A3/A4 liquid premium watch:
    # A3/A4 can be excellent resale cars, but only with the right engine/gearbox
    # and enough discount. This prevents good Audi leads from being treated like
    # slow premium toys while still punishing Multitronic/S-tronic/TFSI traps.
    audi_liquid = norm(brand) == "audi" and any(x in norm(model) for x in ["a3", "a4"])
    audi_bad_terms = ["multitronic", "s-tronic", "s tronic", "dsg", "tfsi", "1.8t", "1.8 tfsi", "2.0 tfsi", "3.0 tdi", "2.7 tdi"]
    audi_bad_hit = any(term in text for term in audi_bad_terms)
    audi_watch = (
        audi_liquid
        and not kill_reasons
        and price >= float(config.get("audi_watch_min_price", 1500))
        and price <= float(config.get("audi_watch_max_price", 6500))
        and (not mileage or mileage <= int(config.get("audi_a3_a4_max_mileage", 230000)))
        and observed_comps >= int(config.get("audi_watch_min_comps", 3))
        and discount_pct is not None
        and discount_pct >= float(config.get("audi_watch_min_discount_pct", 12))
        and model_penalty < int(config.get("audi_watch_max_model_penalty", 26))
        and len(level3) <= 1
    )
    if audi_watch:
        audi_floor = int(config.get("audi_watch_score_floor", 70))
        if discount_pct >= 25 and gross_gap >= 1200 and not audi_bad_hit and model_penalty < 16:
            audi_floor = int(config.get("audi_strong_watch_score_floor", 78))
        score = max(score, audi_floor)
        why.append(
            f"AUDI A3/A4 liquid premium watch: badge/model liquidity is strong, "
            f"observed comps={observed_comps}, discount={discount_pct}%, gross market gap={gross_gap} EUR."
        )
        if audi_bad_hit:
            risks.append("Audi watch risk: TFSI/Multitronic/S-tronic/big diesel wording found; buy only with proof and strong discount.")
        else:
            risks.append("Audi watch risk: premium repair costs still require OBD, gearbox test, service invoices and accident/rust inspection.")

    if discount_pct is not None and discount_pct < -10:
        risks.append("Strong over-market price; reject for resale-profit strategy.")
        score = min(score, 42)

    # Real-world feedback loop:
    # listings that sold quickly often had bad photos or small visible defects
    # (cosmetic rust/brakes) but were cheap, liquid and not mechanically killed.
    quick_sale_pattern = (
        not kill_reasons
        and liquidity_raw >= 80
        and price
        and price <= float(config.get("quick_sale_pattern_max_price", 4500))
        and (fresh_points >= 5 or motivated_points >= 6)
        and len(level3) == 0
        and (level1 or any("brakes" in item for item in level2))
        and (discount_pct is None or discount_pct >= float(config.get("quick_sale_pattern_min_discount_pct", 0)))
    )
    if quick_sale_pattern:
        quick_floor = int(config.get("quick_sale_pattern_score_floor", 73))
        if price <= 2500:
            quick_floor += 4
        if discount_pct is not None and discount_pct >= 15:
            quick_floor += 4
        score = max(score, quick_floor)
        why.append("REAL FEEDBACK pattern: cheap/liquid car with small visible defect can sell fast; prioritize manual check.")
        risks.append("Feedback risk: ugly photos or dirt are okay; dents/rust must be priced as risk and checked hard. Do not buy if rust is structural or brakes indicate deeper neglect.")
    # Final flipper-quality caps. These run after rescue floors so retail-priced
    # Autohaus cars or expensive city cars cannot become GOOD by being merely clean.
    if dealer_retail_margin_risk and score > 62:
        score = min(score, 62)
        risks.append("Final cap: Autohaus/dealer retail pricing usually leaves no reseller margin.")
    if score_cap_reason and score > 62:
        score = min(score, 62)
        risks.append("Final cap: " + score_cap_reason)
    if door_points < 0 and score > 66 and not (discount_pct is not None and discount_pct >= 20):
        score = min(score, 66)
        risks.append("Final cap: 3-door/coupe needs a much stronger discount to be GOOD.")
    if tuv_months == -1 and score > 55:
        score = min(score, 55)
        risks.append("Final cap: kein TUV remains CHECK/low priority unless extremely cheap and verified.")
    if engine_points <= -35 and score > 55:
        score = min(score, 55)
        risks.append("Final cap: risky engine/gearbox pattern is not GOOD without hard proof.")
    if target_profile and target_price_cap and price > target_price_cap and score > 62:
        score = min(score, 62)
        risks.append("Final cap: target model is above our buy-zone for profitable youth flip.")
    if price > float(config.get("youth_flip_general_max_price", 5000)) and score > 45:
        score = min(score, 45)
        risks.append("Final cap: above youth-flip price ceiling; too expensive for current product strategy.")
    if re.search(r"\bfsi\b", text, re.IGNORECASE) and score > 72:
        score = min(score, 72)
        risks.append("Final cap: FSI engine stays CHECK until service/diagnostics prove it is clean.")


    
    min_good_profit = int(config.get("min_good_net_profit", 500))
    if net_profit is not None and net_profit < min_good_profit and score > 72:
        score = min(score, 72)
        risks.append("Final cap: weak net profit cannot be HOT/GOOD; keep as CHECK/best-available only.")
    if flip_value_cap is not None and score > int(flip_value_cap):
        score = min(score, int(flip_value_cap))
        risks.append(f"Flip DB cap applied: score capped at {int(flip_value_cap)} by model buy-zone/risk database.")
    if len(kill_reasons) > 0:
        score = min(score, 35)

    score = int(round(clamp(score)))


    product_segment, segment_scores, segment_notes = _segment_scores(
        brand=brand,
        model=model,
        price=price,
        mileage=mileage,
        text=text,
        flip_score=score,
        liquidity_raw=liquidity_raw,
        tuv_months=tuv_months,
        engine_points=engine_points,
        model_penalty=model_penalty,
        level2=level2,
        level3=level3,
        kill_reasons=kill_reasons,
        positives=positives,
        resale_feature_points=resale_feature_points,
        discount_pct=discount_pct,
        net_profit=net_profit,
        motivated_points=motivated_points,
        config=config,
    )
    why.append(f"Product segment: {product_segment} with segment scores {segment_scores}.")
    why.extend(segment_notes[:3])

    if len(kill_reasons) > 0 or len(level3) >= 3 or model_penalty >= 25:
        risk_level = "HIGH"
    elif len(level3) >= 1 or model_penalty >= 12 or mileage >= 180000:
        risk_level = "MEDIUM"
    else:
        risk_level = "LOW"

    if kill_reasons:
        action = "REJECT"
        opportunity_type = "NOT_INTERESTING"
    elif score >= 85 and risk_level != "HIGH" and not dealer_retail_margin_risk and not score_cap_reason:
        action = "BUY_CANDIDATE"
        opportunity_type = "RARE_STRONG_DEAL"
    elif score >= 75 and risk_level != "HIGH" and not dealer_retail_margin_risk and not score_cap_reason:
        action = "FAST_VERIFY"
        opportunity_type = "INTERESTING_FLIP"
    elif score >= 55 and risk_level != "HIGH":
        action = "INSPECTION_ONLY"
        opportunity_type = "WATCHLIST_CHECK_MANUALLY"
    else:
        action = "REJECT"
        opportunity_type = "NOT_INTERESTING"

    condition_grade = "UNKNOWN"
    if kill_reasons:
        condition_grade = "BAD"
    elif level3:
        condition_grade = "QUESTIONABLE"
    elif positives and not warnings and not level2:
        condition_grade = "OK"

    if net_profit is None:
        profit_logic = "Net profit unknown because market estimate is missing; verify against live analogs before spending time."
    elif net_profit >= 2000:
        profit_logic = "Strong theoretical net profit after prep, diagnostics, negotiation, repairs and risk reserve."
    elif net_profit >= 1000:
        profit_logic = "Interesting net profit if inspection confirms no expensive hidden issue."
    elif net_profit >= 500:
        profit_logic = "Small-to-medium profit; only worth it with low risk and fast resale."
    else:
        profit_logic = "Profit does not beat risk, costs and resale time; not a good flipper deal."
    if kill_reasons:
        profit_logic = "Rejected: hard risk/document/roadworthiness signal means profit can disappear fast."

    if level1:
        low_price_reason = "possible good reason for lower price: weak presentation/cosmetic issue"
    elif level2:
        low_price_reason = "possible acceptable reason for lower price: repair/service item"
    elif level3:
        low_price_reason = "dangerous reason for lower price: expensive mechanical/electronic risk"
    elif discount_pct is not None and discount_pct >= 10:
        low_price_reason = "discount reason is not clear yet; ask seller directly"
    else:
        low_price_reason = "no strong low-price reason found"

    negotiation = "Negotiate on missing info, HU/TUV, service proof, diagnostics result and visible cosmetic issues."
    if seller_good:
        negotiation += " Seller signals suggest possible negotiation: " + ", ".join(seller_good[:3]) + "."
    if level1:
        negotiation += " Cheap visual issues may scare normal buyers but can be fixed cheaply."
    if any("no valid HU/TUV" in item for item in level2):
        negotiation += " No HU/TUV is treated as a negotiation lever, not an automatic reject; buy only after checking why it failed or expired."
    if liquidity_raw >= 84:
        reputation = "Clear resale buyer: popular model/brand, likely fast if condition and price are honest."
    elif liquidity_raw >= 70:
        reputation = "Normal resale buyer pool; price and proof matter."
    elif liquidity_raw >= 55:
        reputation = "Slow resale risk; do not buy without a strong discount."
    else:
        reputation = "Unclear resale buyer; likely bad flipper material."

    checks.extend([
        "Question 1: Who buys this after me, and at what realistic price?",
        "Question 2: Why is it cheaper than market: cheap reason or expensive reason?",
        "Ask seller in German: Ist das Fahrzeug noch verfuegbar und ist der Preis vor Ort verhandelbar?",
        "Ask: Gibt es VIN/FIN, HU/TUV Bericht, Service-Rechnungen und bekannte Maengel?",
        "Ask: Sind Motor, Getriebe, Kupplung, Turbo, DPF/AGR/AdBlue und Elektronik fehlerfrei?",
        "Cold start, OBD scan and real test drive before any purchase.",
        "Check rust: arches, sills, underbody, door bottoms, trunk edge.",
        "Compare interior wear with mileage: steering wheel, seat, pedals, shifter, buttons.",
    ])
    if level3 or model_penalty:
        checks.append("Known-risk car: buy only with proof/diagnostics and enough discount to cover worst case.")

    if flip_value.get("summary"):
        price_view += " Flip DB buy-zone: " + str(flip_value.get("summary"))
    if discount_pct is not None:
        price_view += f" Estimated costs/reserve: {total_costs} EUR; resale negotiation reserve: {resale_discount} EUR."
        if net_profit is not None:
            price_view += f" Conservative net profit estimate: {net_profit} EUR."

    photo_analysis = (
        f"{photo_count} photo URL(s) extracted. Local engine rates photo quantity only; vision AI should ignore ugly/blurred/dirty presentation as a kill reason, but flag rust, dents, panel gaps, paint mismatch, dashboard warnings and engine bay leaks on finalists."
    )

    why.append(fresh_label)
    why.append(f"Liquidity: {liquidity_raw}/100, resale estimate: {resale_speed}, buyer pool: {buyer_pool}.")
    why.append(f"Adaptive mileage profile: {adaptive_profile}, max allowed mileage: {adaptive_max_mileage} km.")
    why.append(f"Low-price reason: {low_price_reason}.")
    if net_profit is not None:
        why.append(f"Estimated net profit after costs/reserve: {net_profit} EUR.")

    if missing_info:
        risks.append("Missing info: " + ", ".join(missing_info[:6]) + ".")
    if not risks:
        risks.append("No strong text red flags found; this does not replace inspection.")

    decision = DealerDecision(
        opportunity_score=score,
        recommended_action=action,
        opportunity_type=opportunity_type,
        liquidity_score=liquidity_raw,
        resale_speed=resale_speed,
        risk_level=risk_level,
        condition_grade=condition_grade,
        estimated_costs=total_costs,
        risk_reserve=risk_reserve,
        net_profit_estimate=net_profit,
        market_discount_pct=discount_pct,
        kill_reasons=kill_reasons,
        missing_info=missing_info,
        why_interesting=why,
        possible_risks=risks,
        what_to_check=checks,
        seller_signals=(seller_good or ["No strong good seller signal found."]) + ([f"Bad seller signal: {x}" for x in seller_bad] if seller_bad else []),
        profit_logic=profit_logic,
        negotiation_angle=negotiation,
        price_view=price_view,
        photo_analysis=photo_analysis,
        model_market_reputation=reputation,
        score_breakdown=score_breakdown,
        product_segment=product_segment,
        segment_scores=segment_scores,
    )
    return decision.as_ai()