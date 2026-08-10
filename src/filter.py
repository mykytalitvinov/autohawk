"""
AUTOHAWK quick rejection filter.

Goal: reject obvious junk BEFORE market scoring or AI analysis.
This is intentionally strict because AUTOHAWK should surface only fresh,
inspectable opportunities, not every cheap listing.
"""

import re
import unicodedata
from typing import Tuple
from src.replacement import replacements

def _normalize(text: str) -> str:
    text = (text or "").lower().strip()

    for old, new in replacements.items():
        text = text.replace(old, new)
    text = (
        text.replace("Ã¤", "ae")
        .replace("Ã¶", "oe")
        .replace("Ã¼", "ue")
        .replace("ÃŸ", "ss")
    )
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    text = re.sub(r"\s+", " ", text)
    return text

HARD_REJECT_PATTERNS = [
    (r"\bporsche\b|\bland\s*rover\b|\brange\s*rover\b|\bjaguar\b|\bmaserati\b|\bbentley\b|\baston\s*martin\b", "luxury/exotic brand out of scope"),
    (r"\bgeloescht\b|\bgeloscht\b|\bdeleted\b|\bnicht mehr verfuegbar\b", "deleted/unavailable listing"),
    (r"\bsuche\s+kaufe\b|\bwir\s+kaufen\b|\bfahrzeugankauf\b|\bautoankauf\b|\bankauf\b", "purchase ad / car buyer"),
    # Broken drivetrain / not roadworthy
    (r"\bmotor\s*schaden\b|\bmotorschaden\b|\bmotor\s*defekt\b", "engine damage"),
    (r"\bmotor\s*unruhig\b|\bunruhiger\s*motor\b|\bmotor\s*(?:laeuft|lauft)\s*unruhig\b|\bmotor\s*lÃ¤uft\s*unruhig\b|\bmotorproblem\b|\bmotor\s*problem\b", "engine runs poorly"),
    (r"\bkalt\b.{0,30}\b(motor|laeuft|lauft|start)\b.{0,30}\b(schlecht|unruhig|ruckelt)\b|\bmotor\b.{0,30}\bkalt\b.{0,30}\b(schlecht|unruhig|ruckelt)\b", "cold-start engine issue"),
    (r"\boelverbrauch\b|\boel\s*verbrauch\b|\bverbrauch[t]?\s*oel\b|\b[0-9]+(?:[,.][0-9]+)?\s*l(?:iter)?\s*oel\b.{0,20}\b(1000|1\.000)\s*km\b", "high oil consumption"),
    (r"\bgetriebe\s*schaden\b|\bgetriebeschaden\b|\bgetriebe\s*defekt\b|\bgetriebe\s*problem\b|\bautomatik\s*problem\b|\bautomatikgetriebe\s*problem\b", "gearbox damage/problem"),
    (r"\bkupplung\s*defekt\b|\bkupplungsschaden\b", "clutch damage"),
    (r"\bsteuerkette\s*(rasselt|defekt|schaden)\b", "timing chain issue"),
    (r"\bdpf\s*defekt\b|\begr\s*defekt\b|\bturbo\s*defekt\b", "diesel system defect"),
    (r"\bstartet\s*nicht\b|\bfaehrt\s*nicht\b|\bnicht\s*fahrbereit\b", "not roadworthy"),
    (r"\bkein\s*motor\b|\bohne\s*motor\b", "missing engine"),

    # Project/export/salvage cars
    (r"\bbastler\b|\bbastlerfahrzeug\b|\bprojektfahrzeug\b", "bastler/project car"),
    (r"\bnur\s*export\b|\bexport\s*only\b|\bexportfahrzeug\b|\bhaendler\s*export\b", "export only"),
    (r"\bersatzteiltraeger\b|\bersatzteile\b|\bschlachtfest\b", "parts car"),
    (r"\btotalschaden\b|\bschrottreif\b|\bverschrottet\b", "salvage car"),

    # Structural/body red flags
    (r"\bunfallwagen\b|\bunfallschaden\b|\bnach\s*unfall\b|\b[0-9]+\s*unfaelle\b|\b[0-9]+\s*unfall\b|\bunfall\b.{0,40}\b(repariert|gehabt|vorbesitzer|bekannt|schaden)\b", "accident damage"),
    (r"\bfrontschaden\b|\bheckschaden\b|\bseitenschaden\b", "body damage"),
    (r"\bdurchrostung\b|\bstarker\s*rost\b|\bstarke\s*rost\b|\bschwere\s*rost\b", "serious rust"),

    # Legal/document roadblocks
    (r"\bohne\s*papiere\b|\bkeine\s*papiere\b|\bohne\s*brief\b", "missing documents"),

    # Warning lights and hidden issues
    (r"\bkontrollleuchte\b|\bmotorlampe\b|\bcheck\s*engine\b", "warning light"),
    (r"\bairbag\s*leuchtet\b|\babs\s*leuchtet\b", "safety warning light"),
    (r"\boelverlust\b|\boel\s*verlust\b|\bwasserverlust\b", "visible fluid loss"),
    (r"\bzylinderkopfdichtung\b|\bkopfdichtung\b", "possible head gasket issue"),
    (r"\bkat\s*defekt\b|\bkatalysator\s*defekt\b", "catalytic converter defect"),
    (r"\bfahrzeug\s*springt\s*nicht\s*an\b|\bspringt\s*nicht\s*an\b", "does not start"),

    # Scam/spam signals
    (r"\bwhatsapp\s*only\b|\bcontact\s*via\s*email\b", "suspicious contact request"),
    (r"\bwire\s*transfer\b|\boverseas\b|\bi\s*am\s*currently\s*abroad\b", "scam wording"),
]


WARNING_SIGNALS = {
    "rost": "rust mentioned",
    "korrosion": "corrosion mentioned",
    "nachlackiert": "possible paintwork",
    "lackschaden": "paint damage mentioned",
    "kratzer": "scratches mentioned",
    "delle": "dent mentioned",
    "beule": "dent mentioned",
    "hoher kilometerstand": "high mileage wording",
    "service fehlt": "missing service history",
    "scheckheft fehlt": "missing service booklet",
    "im auftrag": "seller is selling on behalf of someone else",
    "privatverkauf": "private-sale/no-warranty wording",
    "keine gewaehrleistung": "no-warranty wording",
    "keine garantie": "no-warranty wording",
    "repariert": "repair wording mentioned",
    "turbo": "turbo mentioned",
    "agr": "EGR/AGR mentioned",
    "egr": "EGR mentioned",
    "dpf": "DPF mentioned",
    "steuerkette": "timing chain mentioned",
    "zahnriemen": "timing belt mentioned",
    "kupplung": "clutch mentioned",
    "klima defekt": "air conditioning defect mentioned",
    "klimaanlage funktioniert nicht": "air conditioning defect mentioned",
    "kofferraum": "trunk lock/mechanism issue mentioned",
    "schluessel": "key issue mentioned",
    "fernbedienung": "remote key issue mentioned",
    "inspektion faellig": "inspection due mentioned",
    "reserviert": "reserved/demand signal",
    "fensterheber defekt": "window regulator defect mentioned",
    "notlauf": "limp mode mentioned",
    "ruckelt": "running/shifting issue mentioned",
    "qualmt": "smoke mentioned",
    "raucht": "smoke mentioned",
    "abgemeldet": "deregistered car mentioned",
}


URGENCY_SIGNALS = [
    "dringend",
    "sofort",
    "heute",
    "muss weg",
    "schnell",
    "vb",
    "vhb",
    "verhandlungsbasis",
    "wegen neuanschaffung",
    "platzmangel",
    "umzug",
    "schnell abzugeben",
    "sofort abzugeben",
]


POSITIVE_SIGNALS = [
    "tuev neu",
    "tuv neu",
    "hu neu",
    "hauptuntersuchung neu",
    "tuev bis",
    "tuv bis",
    "hu bis",
    "scheckheft",
    "scheckheftgepflegt",
    "top gepflegt",
    "anfaengerauto",
    "anfÃ¤ngerauto",
    "1. hand",
    "erste hand",
    "gepflegt",
    "nichtraucher",
    "garagenfahrzeug",
    "unfallfrei",
    "service neu",
    "bremsen neu",
    "reifen neu",
    "zahnriemen neu",
    "steuerkette gemacht",
    "sommerreifen",
    "winterreifen",
    "8-fach bereift",
    
]


MIN_PRICE = 300
MAX_PRICE = 150000


def quick_filter(listing: dict) -> Tuple[bool, str, list, list]:
    title = _normalize(listing.get("title", ""))
    desc = _normalize(listing.get("description", ""))
    combined = f"{title} {desc}"
    price = listing.get("price", 0) or 0

    if 0 < price < MIN_PRICE:
        return True, f"suspicious price: {price} EUR", [], []
    if price > MAX_PRICE:
        return True, f"price exceeds scanner scope: {price} EUR", [], []

    for pattern, label in HARD_REJECT_PATTERNS:
        if re.search(pattern, combined, re.IGNORECASE):
            return True, f"hard reject: {label}", [], []

    warnings = []
    for keyword, reason in WARNING_SIGNALS.items():
        if keyword in combined:
            warnings.append(reason)

    found_positive = []
    for signal in URGENCY_SIGNALS:
        if signal in combined:
            found_positive.append(f"urgency: {signal}")
    for signal in POSITIVE_SIGNALS:
        if signal in combined:
            found_positive.append(signal)

    return False, "", warnings, found_positive








