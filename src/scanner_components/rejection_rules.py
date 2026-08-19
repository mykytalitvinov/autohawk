from __future__ import annotations

import re
from typing import Optional

from src.utils import norm


# These rules run both on search cards and after detail-page enrichment.
SHARED_REJECT_RULES = [
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
    ("accident/salvage wording", r"\bunfallwagen\b|\bunfallschaden\b|\btotalschaden\b|\bnach\s*unfall\b|\bfrontschaden\b|\bheckschaden\b|\bseitenschaden\b|\b[0-9]+\s*unfaelle\b|\b[0-9]+\s*unfall\b|\bunfall\b.{0,40}\b(repariert|gehabt|vorbesitzer|bekannt|schaden)\b"),
    ("parts car", r"\bersatzteiltraeger\b|\bersatzteile\b|\bschlachtfest\b"),
]

FILTER_LABELS = {
    "deleted listing": "deleted/unavailable listing",
    "not roadworthy / does not drive": "start / not roadworthy",
    "project/Bastler car": "bastler/project car",
    "accident/salvage wording": "accident damage",
}


def early_reject_reason(text: str) -> Optional[str]:
    normalized = norm(text)
    normalized = re.sub(r"\bunfall\s+frei\b", "unfallfrei", normalized)
    for label, pattern in SHARED_REJECT_RULES:
        if re.search(pattern, normalized, re.IGNORECASE):
            return label
    return None