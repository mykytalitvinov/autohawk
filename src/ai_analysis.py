"""
AUTOHAWK analysis module.

Uses OpenAI when OPENAI_API_KEY is present. Without a key it still performs a
deep rule-based pre-inspection of the listing text and extracted car data.
The analyzer never claims certainty: it reports signals and things to verify.
"""

import json
import logging
import os
import re
from collections import Counter, defaultdict
from datetime import datetime
from typing import Optional

logger = logging.getLogger("autohawk.ai")

_AI_SCAN_METRICS: dict[str, dict] = defaultdict(
    lambda: {"success": 0, "fallback": 0, "errors": Counter()}
)


def reset_ai_scan_metrics() -> None:
    """Reset paid-AI health counters for one scanner cycle."""
    _AI_SCAN_METRICS.clear()


def get_ai_scan_metrics() -> dict[str, dict]:
    """Return a plain dict snapshot of paid-AI success/fallback counters."""
    snapshot = {}
    for provider, data in _AI_SCAN_METRICS.items():
        snapshot[provider] = {
            "success": int(data.get("success", 0)),
            "fallback": int(data.get("fallback", 0)),
            "errors": dict(data.get("errors", {})),
        }
    return snapshot


def _record_ai_success(provider: str) -> None:
    _AI_SCAN_METRICS[provider or "unknown"]["success"] += 1


def _record_ai_fallback(provider: str, error: str) -> None:
    bucket = _AI_SCAN_METRICS[provider or "unknown"]
    bucket["fallback"] += 1
    bucket["errors"][str(error or "unknown error")[:220]] += 1

try:
    from src.ai_memory import build_ai_memory_context
except Exception:
    def build_ai_memory_context(limit_chars: int = 4500) -> str:
        return ""

try:
    from openai import OpenAI
    OPENAI_AVAILABLE = True
except ImportError:
    OPENAI_AVAILABLE = False

try:
    from anthropic import Anthropic
    ANTHROPIC_AVAILABLE = True
except ImportError:
    ANTHROPIC_AVAILABLE = False

try:
    from google import genai
    from google.genai import types as genai_types
    GEMINI_AVAILABLE = True
except ImportError:
    GEMINI_AVAILABLE = False

try:
    import requests
    from PIL import Image
    from io import BytesIO
    GEMINI_VISION_DEPS = True
except ImportError:
    GEMINI_VISION_DEPS = False


def _get_openai_client() -> Optional[object]:
    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if not api_key or not OPENAI_AVAILABLE:
        return None
    try:
        return OpenAI(api_key=api_key)
    except Exception as exc:
        logger.warning(f"OpenAI client init failed: {exc}")
        return None


def _get_groq_client() -> Optional[object]:
    api_key = os.getenv("GROQ_API_KEY", "").strip()
    if not api_key or not OPENAI_AVAILABLE:
        return None
    try:
        return OpenAI(api_key=api_key, base_url="https://api.groq.com/openai/v1", max_retries=0)
    except Exception as exc:
        logger.warning(f"Groq client init failed: {exc}")
        return None


def _get_anthropic_client() -> Optional[object]:
    api_key = os.getenv("ANTHROPIC_API_KEY", "").strip()
    if not api_key or not ANTHROPIC_AVAILABLE:
        return None
    try:
        return Anthropic(api_key=api_key, timeout=30.0, max_retries=1)
    except Exception as exc:
        logger.warning(f"Anthropic client init failed: {exc}")
        return None


def _get_gemini_client() -> Optional[object]:
    api_key = os.getenv("GEMINI_API_KEY", "").strip()
    if not api_key or not GEMINI_AVAILABLE:
        return None
    try:
        return genai.Client(api_key=api_key)
    except Exception as exc:
        logger.warning(f"Gemini client init failed: {exc}")
        return None


def _norm(text: str) -> str:
    text = (text or "").lower()
    replacements = {
        "Ã¤": "ae",
        "Ã¶": "oe",
        "Ã¼": "ue",
        "ÃŸ": "ss",
        "ÃƒÂ¤": "ae",
        "ÃƒÂ¶": "oe",
        "ÃƒÂ¼": "ue",
        "ÃƒÅ¸": "ss",
        "ÃƒÆ’Ã‚Â¤": "ae",
        "ÃƒÆ’Ã‚Â¶": "oe",
        "ÃƒÆ’Ã‚Â¼": "ue",
        "ÃƒÆ’Ã…Â¸": "ss",
        "Ã¢â€šÂ¬": "eur",
    }
    for old, new in replacements.items():
        text = text.replace(old, new)
    return re.sub(r"\s+", " ", text).strip()


def _has(text: str, *patterns: str) -> bool:
    return any(re.search(pattern, text, re.IGNORECASE) for pattern in patterns)


def _bullet(items: list[str], limit: int = 8) -> str:
    cleaned = []
    for item in items:
        item = item.strip()
        if item and item not in cleaned:
            cleaned.append(item)
    return "\n".join(f"- {item}" for item in cleaned[:limit])


def _age_label(age_min: Optional[int]) -> str:
    if age_min is None:
        return "posting age unknown"
    if age_min < 60:
        return f"{age_min} minutes old"
    return f"{age_min // 60}h {age_min % 60}m old"


def _money(value) -> str:
    try:
        return f"{float(value):,.0f} EUR"
    except Exception:
        return "unknown price"


def _text_signals(text: str) -> tuple[list[str], list[str], list[str]]:
    positives = []
    concerns = []
    questions = []

    if _has(text, r"\btuev\s*neu\b", r"\bhu\s*neu\b", r"hauptuntersuchung\s*neu"):
        positives.append("Fresh HU/TUV is claimed; verify the paper and date.")
    elif _has(text, r"\btuev\b", r"\bhu\b", r"hauptuntersuchung"):
        positives.append("HU/TUV is mentioned; check exact validity date.")
        questions.append("Bis wann ist HU/TUV genau gueltig?")
    else:
        concerns.append("No clear HU/TUV information found in the text.")
        questions.append("Hat das Fahrzeug aktuell gueltigen HU/TUV?")

    if _has(text, r"scheckheft", r"serviceheft", r"service\s*neu", r"wartung"):
        positives.append("Service history is mentioned; ask for invoices/photos.")
        questions.append("Gibt es ein Scheckheft oder Rechnungen zu den letzten Services?")
    else:
        concerns.append("Service history is not clearly documented in the listing.")
        questions.append("Wann war der letzte Service und gibt es Belege?")

    if _has(text, r"\b1\.\s*hand\b", r"erste\s*hand"):
        positives.append("First-owner signal; this can reduce history uncertainty if documented.")

    if _has(text, r"unfallfrei"):
        positives.append("Seller claims accident-free; still check paint, gaps and records.")
    elif _has(text, r"unfall", r"nachlack", r"lackiert", r"repariert"):
        concerns.append("Bodywork/accident wording appears; inspect paint, panel gaps and invoices.")
        questions.append("Gab es Unfallschaeden oder Nachlackierungen?")

    if _has(text, r"nichtraucher"):
        positives.append("Non-smoker claim; check interior smell and wear.")
    if _has(text, r"garagenfahrzeug"):
        positives.append("Garage-car claim; useful if body condition matches it.")

    if _has(text, r"rost", r"korrosion"):
        concerns.append("Rust is mentioned; inspect arches, sills, underbody and door bottoms.")
        questions.append("Wo genau gibt es Rost oder Korrosion?")
    if _has(text, r"kratzer", r"delle", r"beule"):
        concerns.append("Cosmetic damage is mentioned; use photos and inspection for negotiation.")
    if _has(text, r"oelverlust", r"wasserverlust", r"undicht", r"leck"):
        concerns.append("Possible leak wording appears; check engine, gearbox and coolant area.")
    if _has(text, r"kontrollleuchte", r"motorlampe", r"abs\s*leuchtet", r"airbag\s*leuchtet"):
        concerns.append("Warning-light wording appears; require diagnostics before buying.")
    if _has(text, r"verkauf\s*im\s*auftrag", r"im\s*auftrag"):
        concerns.append("Sold on behalf of someone else; ownership/history may be less transparent.")
        questions.append("Ist der Verkaeufer auch der eingetragene Halter?")
    if _has(text, r"keine\s*garantie", r"keine\s*gewaehrleistung", r"privatverkauf"):
        concerns.append("Private sale/no warranty wording; inspection matters more.")
    if _has(text, r"vb\b", r"vhb\b", r"verhandlungsbasis"):
        positives.append("Negotiation signal found (VB/VHB).")
    if _has(text, r"dringend", r"muss\s*weg", r"schnell", r"platzmangel", r"umzug"):
        positives.append("Seller urgency signal; respond fast but do not skip inspection.")

    return positives, concerns, questions


def _vehicle_risks(listing: dict, model_risks: list[str]) -> tuple[list[str], list[str]]:
    risks = []
    checks = []

    brand = _norm(listing.get("brand", ""))
    model = _norm(listing.get("model", ""))
    engine = _norm(listing.get("engine", ""))
    fuel = _norm(listing.get("fuel", ""))
    gearbox = _norm(listing.get("gearbox", ""))
    mileage = listing.get("mileage") or 0
    year = listing.get("year") or 0
    age = datetime.now().year - year if year else None

    if age is not None and age >= 15:
        risks.append("Older car: rust, suspension wear and old rubber parts become important.")
        checks.append("Check underbody, sills, wheel arches, brake lines and suspension noise.")
    if mileage >= 150000:
        risks.append(f"High mileage ({mileage:,} km): service proof is critical.")
        checks.append("Ask for timing belt/chain, clutch, turbo and suspension service proof.")
    if mileage >= 180000 and ("diesel" in fuel or any(x in engine for x in ["tdi", "cdi", "dci", "hdi", "cdti"])):
        risks.append("High-mileage diesel: possible DPF/EGR/turbo/injector costs.")
        checks.append("During test drive check smoke, limp mode, turbo pull and DPF regeneration history.")
    if "automatic" in gearbox or "dsg" in engine:
        risks.append("Automatic/DSG gearbox: repairs can be expensive if shifting is not smooth.")
        checks.append("Test cold and warm shifts, kickdown, reverse engagement and hesitation.")
    if brand == "bmw" and (engine in {"n47", "318d", "320d"} or model == "3er") and 2007 <= year <= 2011:
        risks.append("BMW diesel 2007-2011 may have N47 timing-chain risk; verify engine and chain history.")
        checks.append("Listen for chain rattle on cold start and ask if timing chain was replaced.")
    if brand in {"volkswagen", "audi", "seat", "skoda"} and ("tsi" in engine or "tfsi" in engine):
        risks.append("Older TSI/TFSI engines can have timing-chain or oil-consumption issues.")
        checks.append("Ask about oil consumption and chain/tensioner history.")
    if brand in {"volkswagen", "audi", "seat", "skoda"} and ("tdi" in engine or "diesel" in fuel):
        checks.append("Check timing belt interval, DPF/EGR condition and cold-start behavior.")
    if brand == "mercedes" and ("om651" in engine or "c-klasse" in model or "e-klasse" in model):
        risks.append("Mercedes diesel can be solid, but injector, chain, DPF/EGR checks matter at mileage.")
        checks.append("Check injector noises, service invoices and diagnostic faults.")
    if brand in {"bmw", "mercedes", "audi"} and listing.get("price", 0) and listing.get("price", 0) < 5000:
        risks.append("Cheap premium car: low purchase price can hide high maintenance costs.")
        checks.append("Budget for diagnostics before purchase; avoid buying only because it is cheap.")

    for risk in model_risks:
        risks.append(f"Known model signal: {risk}")

    return risks, checks


def _inspection_plan(listing: dict, warnings: list, model_risks: list[str]) -> list[str]:
    mileage = listing.get("mileage") or 0
    gearbox = _norm(listing.get("gearbox", ""))
    fuel = _norm(listing.get("fuel", ""))

    plan = [
        "Check VIN, registration papers, seller identity and HU/TUV document.",
        "Start the engine cold; listen for chain rattle, knocking, misfire or smoke.",
        "Run OBD diagnostics before purchase, especially if any warning light is visible.",
        "Do a real test drive: braking straight, steering, clutch/gearbox, highway pull.",
        "Inspect underbody, arches, sills, door bottoms and engine/gearbox for leaks.",
    ]
    if mileage >= 120000:
        plan.append("Verify timing belt/chain, water pump, clutch/flywheel and suspension service history.")
    if "diesel" in fuel:
        plan.append("Check DPF/EGR/turbo behavior: smoke, limp mode, regeneration and fault codes.")
    if "automatic" in gearbox:
        plan.append("Test automatic gearbox cold and warm; any jerk, delay or shudder is a serious signal.")
    if warnings:
        plan.append("Use the seller's own warning words as inspection and negotiation points.")
    if model_risks:
        plan.append("Ask directly about the known model-specific issues listed by AUTOHAWK.")
    return plan


def _seller_questions(base_questions: list[str], listing: dict, model_risks: list[str]) -> list[str]:
    questions = list(base_questions)
    mileage = listing.get("mileage") or 0
    fuel = _norm(listing.get("fuel", ""))
    gearbox = _norm(listing.get("gearbox", ""))

    questions.extend([
        "Ist der Preis vor Ort noch verhandelbar, wenn Maengel gefunden werden?",
        "Kann ich eine Probefahrt machen und den Wagen per OBD auslesen lassen?",
        "Gibt es bekannte Maengel, Fehlermeldungen oder anstehende Reparaturen?",
    ])
    if mileage >= 120000:
        questions.append("Wurde Zahnriemen oder Steuerkette schon gemacht? Gibt es eine Rechnung?")
    if "diesel" in fuel:
        questions.append("Gab es Probleme mit DPF, AGR/EGR, Turbo oder Injektoren?")
    if "automatic" in gearbox:
        questions.append("Wurde das Automatik-/DSG-Getriebe gewartet oder gespult?")
    if model_risks:
        questions.append("Wurden die bekannten Schwachstellen dieses Modells schon geprueft oder repariert?")
    return questions


def _seller_message(listing: dict, questions: list[str]) -> str:
    car = " ".join(filter(None, [str(listing.get("brand") or ""), str(listing.get("model") or "")])).strip()
    if not car:
        car = "das Auto"
    first_questions = " ".join(questions[:3])
    return (
        f"Hallo, ich interessiere mich fuer {car}. "
        f"Ist das Fahrzeug noch verfuegbar? {first_questions} "
        "Wenn alles passt, koennte ich zeitnah zur Besichtigung kommen."
    )


ANALYSIS_PROMPT = """You are AUTOHAWK, an AI car deal pre-screening assistant for the German used car market.

Analyze the listing like a cautious automotive pre-inspection advisor.

Rules:
- Never claim certainty. Use possible / suspicious / worth checking / may indicate.
- You are not a mechanic and not a certified valuation expert.
- Focus on defects, missing info, seller wording, documents, inspection questions and next action.
- Respond ONLY with valid JSON using these keys:

{{
  "why_interesting": "practical bullet points",
  "possible_risks": "practical bullet points",
  "what_to_check": "inspection checklist plus questions to ask",
  "seller_signals": "seller text signals and German message",
  "ai_summary": "short plain-language decision summary"
}}

Listing:
BRAND: {brand}
MODEL: {model}
YEAR: {year}
MILEAGE: {mileage} km
PRICE: {price} EUR
FUEL: {fuel}
GEARBOX: {gearbox}
ENGINE: {engine}
LOCATION: {location}
SELLER TYPE: {seller_type}
TITLE: {title}
DESCRIPTION: {description}
LISTING AGE: {age_minutes} minutes
ESTIMATED MARKET PRICE: {market_price} EUR
ESTIMATED MARGIN: {margin} EUR
WARNINGS DETECTED: {warnings}
POSITIVE SIGNALS: {positive_signals}
MODEL-SPECIFIC KNOWN ISSUES: {model_risks}
"""


def ai_analyze(
    listing: dict,
    warnings: list,
    positive_signals: list,
    model_risks: list,
    market_price: float,
    margin: float,
    config: dict | None = None,
) -> dict:
    config = config or {}
    provider = os.getenv("AI_PROVIDER", "auto").strip().lower()

    if provider in {"groq"}:
        client = _get_groq_client()
        if client:
            return _groq_analyze(client, listing, warnings, positive_signals, model_risks, market_price, margin, config)
        logger.info("Groq selected but GROQ_API_KEY/openai package is missing; using rule-based analysis.")
        _record_ai_fallback("groq", "missing GROQ_API_KEY or openai package")

    if provider in {"gemini", "google"}:
        client = _get_gemini_client()
        if client:
            return _gemini_analyze(client, listing, warnings, positive_signals, model_risks, market_price, margin)
        logger.info("Gemini selected but GEMINI_API_KEY/google-genai is missing; using rule-based analysis.")
        _record_ai_fallback("gemini", "missing GEMINI_API_KEY or google-genai package")

    if provider in {"claude", "anthropic", "auto"}:
        client = _get_anthropic_client()
        if client:
            return _anthropic_analyze(client, listing, warnings, positive_signals, model_risks, market_price, margin)
        if provider in {"claude", "anthropic"}:
            logger.info("Claude selected but ANTHROPIC_API_KEY/anthropic package is missing; using rule-based analysis.")
            _record_ai_fallback("claude", "missing ANTHROPIC_API_KEY or anthropic package")

    if provider in {"openai", "auto"}:
        client = _get_openai_client()
        if client:
            return _openai_analyze(client, listing, warnings, positive_signals, model_risks, market_price, margin)
        if provider == "openai":
            logger.info("OpenAI selected but OPENAI_API_KEY/openai package is missing; using rule-based analysis.")
            _record_ai_fallback("openai", "missing OPENAI_API_KEY or openai package")

    if provider == "auto":
        _record_ai_fallback("auto", "no paid AI client available")
    return _rule_based_analyze(listing, warnings, positive_signals, model_risks, market_price, margin)


def _build_prompt(listing, warnings, positive_signals, model_risks, market_price, margin) -> str:
    return ANALYSIS_PROMPT.format(
        brand=listing.get("brand", "Unknown"),
        model=listing.get("model", "Unknown"),
        year=listing.get("year", "Unknown"),
        mileage=listing.get("mileage", "Unknown"),
        price=listing.get("price", "Unknown"),
        fuel=listing.get("fuel", "Unknown"),
        gearbox=listing.get("gearbox", "Unknown"),
        engine=listing.get("engine", "Unknown"),
        location=listing.get("location", "Unknown"),
        seller_type=listing.get("seller_type", "Unknown"),
        title=listing.get("title", ""),
        description=(listing.get("description", "") or "")[:2200],
        age_minutes=listing.get("listing_age_minutes", "Unknown"),
        market_price=market_price or "Unknown",
        margin=margin or "Unknown",
        warnings=", ".join(warnings) if warnings else "none",
        positive_signals=", ".join(positive_signals) if positive_signals else "none",
        model_risks=", ".join(model_risks) if model_risks else "none",
    )

def _memory_context() -> str:
    try:
        memory = build_ai_memory_context()
    except Exception as exc:
        logger.debug(f"AI memory unavailable: {exc}")
        memory = ""
    return ("\n\n" + memory + "\n") if memory else ""

def _extract_photo_urls(listing: dict, max_images: int = 4) -> list[str]:
    raw = listing.get("photo_urls") or listing.get("image_urls") or "[]"
    try:
        urls = json.loads(raw) if isinstance(raw, str) else raw
    except Exception:
        urls = []
    cleaned = []
    for url in urls or []:
        if not isinstance(url, str):
            continue
        if not url.startswith("http"):
            continue
        low = url.lower()
        if any(x in low for x in ["logo", "placeholder", "sprite", "icon"]):
            continue
        if url not in cleaned:
            cleaned.append(url)

    if max_images <= 0 or not cleaned:
        return []
    if len(cleaned) <= max_images:
        return cleaned

    # Do not send only the first gallery photos. Sellers often put damage,
    # dashboard, wheel/fender or interior photos near the end.
    last = len(cleaned) - 1
    candidate_indices = [0, last]
    if max_images >= 3:
        candidate_indices.append(1)
    if max_images >= 4:
        candidate_indices.append(last // 2)
    if max_images >= 5:
        candidate_indices.append(max(0, last - 1))
    if max_images >= 6:
        candidate_indices.append(max(0, last // 3))
    if max_images >= 7:
        candidate_indices.append(min(last, (last * 2) // 3))

    selected = []
    for idx in sorted(dict.fromkeys(candidate_indices)):
        if 0 <= idx <= last and cleaned[idx] not in selected:
            selected.append(cleaned[idx])
        if len(selected) >= max_images:
            break
    return selected[:max_images]


def _anthropic_content_blocks(listing: dict, prompt: str) -> list[dict]:
    use_vision = os.getenv("ANTHROPIC_USE_VISION", "true").strip().lower() in {"1", "true", "yes", "y"}
    max_images = int(os.getenv("ANTHROPIC_MAX_IMAGES", "4") or "4")
    blocks = []
    if use_vision:
        for url in _extract_photo_urls(listing, max_images=max_images):
            blocks.append({
                "type": "image",
                "source": {
                    "type": "url",
                    "url": url,
                },
            })
    blocks.append({"type": "text", "text": prompt})
    return blocks

GEMINI_FINALIST_PROMPT = """
You are AUTOHAWK's final paid-AI reviewer for a German used-car reseller.

The deterministic engine already filtered most bad listings. Your job:
- inspect text and photos if provided;
- judge if this finalist is actually worth fast contact;
- think like a conservative car flipper, not like a normal buyer;
- prefer liquidity, controllable risk, clear resale buyer and negotiation angle;
- reject killed/problem cars even if cheap;
- tolerate weak/blurred/ugly photos, dirt, bad lighting and poor presentation if no hard red flags and the car is fresh/liquid.
- do not treat dirty car or ugly photos as damage; those can be negotiation opportunities.
- treat visible rust, dents, panel gaps, paint mismatch, accident repair signs and dashboard warning lights as real risk signals.

Return strict JSON only:
{
  "opportunity_score": 0-100,
  "recommended_action": "BUY_CANDIDATE" | "FAST_VERIFY" | "INSPECTION_ONLY" | "REJECT",
  "photo_analysis": "uncertain visual findings: rust, gaps, paint, interior wear, dashboard, engine bay",
  "dashboard_warning_lights": ["visible/suspicious warning lights such as check engine, ABS, airbag, ESP, oil, battery, brake"],
  "price_view": "price plausibility and margin logic without pretending certainty",
  "why_interesting": "bullet points",
  "possible_risks": "bullet points",
  "what_to_check": "inspection checklist and urgent questions",
  "seller_signals": "seller wording, trust, negotiation angle",
  "ai_summary": "short final dealer decision"
}

Never claim certainty from photos. Use possible / suspicious / worth checking. Separate cheap presentation issues from real body/technical risks.
Classify dashboard lights by likely cost:
- cheap/common: bulb, battery voltage, service reminder, simple sensor suspicion;
- medium: ABS/ESP sensor, lambda, thermostat, brake wear;
- expensive/red flag: airbag, oil pressure, overheating, gearbox, DPF/AdBlue, multiple warnings together.
"""


def _download_gemini_images(listing: dict, max_images: int = 4) -> list:
    if not GEMINI_VISION_DEPS:
        return []
    images = []
    for url in _extract_photo_urls(listing, max_images=max_images):
        try:
            resp = requests.get(url, timeout=10, headers={"User-Agent": "Mozilla/5.0"})
            if resp.status_code >= 400 or len(resp.content) < 1000:
                continue
            img = Image.open(BytesIO(resp.content))
            img.thumbnail((1400, 1400))
            images.append(img.copy())
        except Exception:
            continue
    return images


def _gemini_analyze(client, listing, warnings, positive_signals, model_risks, market_price, margin) -> dict:
    base_prompt = _build_prompt(
        listing, warnings, positive_signals, model_risks, market_price, margin
    )
    prompt = GEMINI_FINALIST_PROMPT + _memory_context() + "\n\n" + base_prompt
    model = os.getenv("GEMINI_MODEL", "gemini-2.5-flash").strip() or "gemini-2.5-flash"
    max_images = int(os.getenv("GEMINI_MAX_IMAGES", "4") or "4")
    contents = [prompt]
    if os.getenv("GEMINI_USE_VISION", "true").strip().lower() in {"1", "true", "yes", "y"}:
        contents.extend(_download_gemini_images(listing, max_images=max_images))

    try:
        config = genai_types.GenerateContentConfig(
            response_mime_type="application/json",
            temperature=0.12,
            max_output_tokens=1400,
        )
        response = client.models.generate_content(
            model=model,
            contents=contents,
            config=config,
        )
        raw = (response.text or "").strip().replace("```json", "").replace("```", "").strip()
        parsed = json.loads(raw)
        shaped = _ensure_analysis_shape(parsed)
        shaped["analysis_provider"] = "gemini"
        _record_ai_success("gemini")
        return shaped
    except json.JSONDecodeError as exc:
        logger.warning(f"Gemini JSON parse failed: {exc}. Falling back to rule-based.")
        _record_ai_fallback("gemini", f"JSON parse failed: {exc}")
    except Exception as exc:
        logger.warning(f"Gemini API call failed: {exc}. Falling back to rule-based.")
        _record_ai_fallback("gemini", f"API call failed: {exc}")
    return _rule_based_analyze(listing, warnings, positive_signals, model_risks, market_price, margin)


def _groq_content_blocks(listing: dict, prompt: str) -> list[dict]:
    blocks = [{"type": "text", "text": prompt}]
    use_vision = os.getenv("GROQ_USE_VISION", "true").strip().lower() in {"1", "true", "yes", "y"}
    max_images = int(os.getenv("GROQ_MAX_IMAGES", "3") or "3")
    if use_vision:
        for url in _extract_photo_urls(listing, max_images=max_images):
            blocks.append({"type": "image_url", "image_url": {"url": url}})
    return blocks


def _groq_analyze(client, listing, warnings, positive_signals, model_risks, market_price, margin, config: dict | None = None) -> dict:
    config = config or {}
    base_prompt = _build_prompt(
        listing, warnings, positive_signals, model_risks, market_price, margin
    )
    prompt = GEMINI_FINALIST_PROMPT + _memory_context() + "\n\n" + base_prompt
    model = (
        os.getenv("GROQ_MODEL", "").strip()
        or str(config.get("groq_model", "")).strip()
        or "meta-llama/llama-4-scout-17b-16e-instruct"
    )

    try:
        response = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": _groq_content_blocks(listing, prompt)}],
            response_format={"type": "json_object"},
            max_tokens=750,
            temperature=0.12,
        )
        raw = (response.choices[0].message.content or "").strip()
        raw = raw.replace("```json", "").replace("```", "").strip()
        parsed = json.loads(raw)
        shaped = _ensure_analysis_shape(parsed)
        shaped["analysis_provider"] = "groq"
        shaped["ai_model"] = model
        _record_ai_success("groq")
        return shaped
    except json.JSONDecodeError as exc:
        logger.warning(f"Groq JSON parse failed: {exc}. Falling back to rule-based.")
        _record_ai_fallback("groq", f"JSON parse failed: {exc}")
    except Exception as exc:
        logger.warning(f"Groq API call failed: {exc}. Falling back to rule-based.")
        _record_ai_fallback("groq", f"API call failed: {exc}")
    return _rule_based_analyze(listing, warnings, positive_signals, model_risks, market_price, margin)

def _anthropic_analyze(client, listing, warnings, positive_signals, model_risks, market_price, margin) -> dict:
    prompt = _build_prompt(listing, warnings, positive_signals, model_risks, market_price, margin)
    prompt += _memory_context()
    prompt += """

Now act as AUTOHAWK's THOROUGH PROFITABLE-BUY DUE DILIGENCE ANALYST.

Mission:
Choose cars that are actually worth buying, not just cheap. A profitable purchase can be:
1. UNDERPRICED_NOT_KILLED: price is attractive but the car does not show severe damage/neglect.
2. CLEAN_AT_FAIR_PRICE: price is normal, but condition/history/liquidity look better than average, so resale/use value is strong.
3. FAST_VERIFY: listing has too little data/photos, but it is fresh, liquid, plausible, and has no hard kill flags. Do NOT reject only because data/photos are missing.
4. INSPECTION_ONLY: possible opportunity, but only with diagnostics/inspection because risk is material.

Hard truth:
Do not show random weak cars. Do not show killed cars. Do not show a car only because it is cheap.
But also do not reject a potentially great private listing only because the seller wrote little or uploaded few photos.

Hard reject / kill flags:
- Motorschaden, Getriebeschaden, Bastler, Export-only, not roadworthy, starts/drives not.
- No valid HU/TUV is a serious risk and negotiation lever, but not an automatic reject when price is very low, model is liquid, mileage is plausible, and the reason can be verified.
- Warning lights, ABS/airbag/check-engine, missing documents.
- Heavy rust, structural rust, accident/salvage/body damage, obvious poor repair.
- Interior visibly destroyed/abused, mileage/condition mismatch, very neglected cabin.
- Engine bay looks strongly suspicious: leaks, missing covers, extreme neglect, suspicious washing.
- Scam/evasive seller wording, impossible price, too-good-to-be-true.
- Premium/high-risk car where likely repairs can erase all upside.

Missing information policy:
- Few photos, short text, missing interior photo, or unknown market estimate are NOT kill flags.
- If no kill flags and the car is fresh/liquid/price-plausible, classify as FAST_VERIFY and list urgent questions.

Decision classes:
- BUY_CANDIDATE: strong buy candidate. Good condition or strong price, no hard red flags.
- FAST_VERIFY: possible hidden gem with limited info. Contact fast and ask precise questions.
- INSPECTION_ONLY: maybe profitable but only after diagnostics/inspection.
- REJECT: not worth user's time.

Profitability thinking:
Estimate whether the car has positive expected value after likely repairs, fees, and resale liquidity.
Good condition at a fair price can be better than a cheap risky car.
Cheap damaged cars are not opportunities.

Photo analysis:
If images are provided, inspect exterior, interior, dashboard, engine bay if visible.
Look for rust, panel gaps, paint mismatch, dents, dirty/abused interior, steering/seat/pedal wear, warning lights, leaks.
Never claim certainty from photos. Say possible/suspicious/worth checking.

Return ONLY strict JSON:
{
  "opportunity_score": 0-100,
  "recommended_action": "BUY_CANDIDATE" | "FAST_VERIFY" | "INSPECTION_ONLY" | "REJECT",
  "opportunity_type": "UNDERPRICED_NOT_KILLED" | "CLEAN_AT_FAIR_PRICE" | "FAST_VERIFY" | "RISKY_BUT_POSSIBLE" | "NOT_INTERESTING",
  "condition_grade": "CLEAN" | "OK" | "UNKNOWN" | "QUESTIONABLE" | "BAD",
  "profit_logic": "why this could or could not be profitable after risk/repairs/liquidity",
  "kill_reasons": ["only severe reject reasons, empty if none"],
  "missing_info": ["important missing info that requires fast verification"],
  "urgent_questions_de": ["German questions to ask seller immediately"],
  "photo_analysis": "photo-based signals, with uncertainty",
  "price_view": "price plausibility, without inventing market price",
  "why_interesting": "bullet points",
  "possible_risks": "bullet points",
  "what_to_check": "inspection checklist and questions",
  "seller_signals": "seller text and trust signals",
  "ai_summary": "short decision summary"
}

Scoring:
- 82-100 BUY_CANDIDATE: strong, contact now.
- 65-81 FAST_VERIFY / BUY_CANDIDATE: promising enough to show.
- 58-64 INSPECTION_ONLY or FAST_VERIFY: show only if no kill flags and there is clear upside.
- 0-57 REJECT / not enough upside.

Be conservative about damage, but tolerant of missing information.
"""
    model = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-6").strip() or "claude-sonnet-4-6"

    try:
        response = client.messages.create(
            model=model,
            max_tokens=1200,
            temperature=0.15,
            system=(
                "You are AUTOHAWK, a cautious German used-car opportunity analyst. "
                "Select only the most promising listings. Return strict JSON only."
            ),
            messages=[{"role": "user", "content": _anthropic_content_blocks(listing, prompt)}],
        )
        raw = "".join(block.text for block in response.content if getattr(block, "type", "") == "text").strip()
        raw = raw.replace("```json", "").replace("```", "").strip()
        parsed = json.loads(raw)
        shaped = _ensure_analysis_shape(parsed)
        shaped["analysis_provider"] = "claude"
        shaped["ai_model"] = model
        _record_ai_success("claude")
        return shaped
    except json.JSONDecodeError as exc:
        logger.warning(f"Claude JSON parse failed: {exc}. Falling back to rule-based.")
        _record_ai_fallback("claude", f"JSON parse failed: {exc}")
    except Exception as exc:
        logger.warning(f"Claude API call failed: {exc}. Falling back to rule-based.")
        _record_ai_fallback("claude", f"API call failed: {exc}")
    return _rule_based_analyze(listing, warnings, positive_signals, model_risks, market_price, margin)



def _openai_analyze(client, listing, warnings, positive_signals, model_risks, market_price, margin) -> dict:
    prompt = _build_prompt(listing, warnings, positive_signals, model_risks, market_price, margin)
    prompt += _memory_context()

    try:
        response = client.chat.completions.create(
            model=os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
            messages=[{"role": "user", "content": prompt}],
            max_tokens=900,
            temperature=0.2,
        )
        raw = response.choices[0].message.content.strip()
        raw = raw.replace("```json", "").replace("```", "").strip()
        parsed = json.loads(raw)
        shaped = _ensure_analysis_shape(parsed)
        shaped["analysis_provider"] = "openai"
        shaped["ai_model"] = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
        _record_ai_success("openai")
        return shaped
    except json.JSONDecodeError as exc:
        logger.warning(f"OpenAI JSON parse failed: {exc}. Falling back to rule-based.")
        _record_ai_fallback("openai", f"JSON parse failed: {exc}")
    except Exception as exc:
        logger.warning(f"OpenAI API call failed: {exc}. Falling back to rule-based.")
        _record_ai_fallback("openai", f"API call failed: {exc}")
    return _rule_based_analyze(listing, warnings, positive_signals, model_risks, market_price, margin)


def _as_text_for_db(value) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, (list, tuple, set)):
        return "\n".join(f"- {str(item)}" for item in value if item is not None)
    if isinstance(value, dict):
        try:
            return json.dumps(value, ensure_ascii=False)
        except Exception:
            return str(value)
    return str(value)

def _ensure_analysis_shape(data: dict) -> dict:
    score = data.get("opportunity_score")
    try:
        score = int(float(score)) if score is not None else None
    except Exception:
        score = None
    if score is not None:
        score = max(0, min(100, score))

    return {
        "opportunity_score": score,
        "recommended_action": str(data.get("recommended_action", "") or "").upper(),
        "photo_analysis": _as_text_for_db(data.get("photo_analysis", "")),
        "dashboard_warning_lights": data.get("dashboard_warning_lights", []) if isinstance(data.get("dashboard_warning_lights", []), list) else [str(data.get("dashboard_warning_lights", ""))],
        "price_view": _as_text_for_db(data.get("price_view", "")),
        "why_interesting": _as_text_for_db(data.get("why_interesting", "")),
        "possible_risks": _as_text_for_db(data.get("possible_risks", "")),
        "what_to_check": _as_text_for_db(data.get("what_to_check", "")),
        "seller_signals": _as_text_for_db(data.get("seller_signals", "")),
        "ai_summary": _as_text_for_db(data.get("ai_summary", "")),
    }



def _rule_based_analyze(listing, warnings, positive_signals, model_risks, market_price, margin) -> dict:
    brand = listing.get("brand", "") or "Unknown"
    model = listing.get("model", "") or "Unknown"
    year = listing.get("year") or "?"
    mileage = listing.get("mileage") or 0
    price = listing.get("price") or 0
    age_min = listing.get("listing_age_minutes")
    text = _norm(f"{listing.get('title', '')} {listing.get('description', '')}")

    text_pos, text_concerns, base_questions = _text_signals(text)
    vehicle_risks, vehicle_checks = _vehicle_risks(listing, model_risks)

    why = []
    if age_min is not None and age_min <= 60:
        why.append(f"Very fresh listing: {_age_label(age_min)}. Speed matters, but still verify first.")
    elif age_min is not None and age_min <= 180:
        why.append(f"Fresh listing: {_age_label(age_min)}.")
    elif age_min is not None:
        why.append(f"Still inside the freshness window: {_age_label(age_min)}.")

    if margin and margin > 500:
        why.append(f"Price may be below the rough heuristic estimate by about {_money(margin)}.")
    elif market_price:
        why.append("Market-price estimate is only a rough helper; inspection signals matter more here.")

    why.extend(text_pos[:4])
    if positive_signals:
        why.append("Detected listing positives: " + ", ".join(positive_signals[:4]) + ".")
    if not why:
        why.append("The listing has enough extracted data to justify a structured check.")

    risks = []
    risks.extend(text_concerns)
    risks.extend(f"Listing warning: {warning}" for warning in warnings[:5])
    risks.extend(vehicle_risks)
    if listing.get("data_quality_warnings"):
        risks.append("Data quality warning: " + ", ".join(listing.get("data_quality_warnings", [])) + ".")
    if not risks:
        risks.append("No strong red flags found in text, but this does not replace inspection.")

    checks = _inspection_plan(listing, warnings, model_risks)
    checks.extend(vehicle_checks)

    questions = _seller_questions(base_questions, listing, model_risks)
    message = _seller_message(listing, questions)

    seller_signals = []
    if text_pos:
        seller_signals.append("Positive text signals: " + "; ".join(text_pos[:5]) + ".")
    if text_concerns:
        seller_signals.append("Concern text signals: " + "; ".join(text_concerns[:5]) + ".")
    if not text_pos and not text_concerns:
        seller_signals.append("Seller text is neutral or too short; ask direct questions before visiting.")
    seller_signals.append("German message: " + message)

    decision = "Worth checking quickly, but only after the seller answers the key questions."
    if len(risks) >= 5:
        decision = "Potentially interesting, but risk-heavy; do not go without clear answers and diagnostics."
    if any("warning-light" in r.lower() or "diagnostics" in r.lower() for r in risks):
        decision = "Only proceed with diagnostics; warning-light wording can turn a cheap car into an expensive one."

    summary = (
        f"{year} {brand} {model}, {mileage:,} km, listed {_age_label(age_min)} at {_money(price)}. "
        f"{decision} AUTOHAWK is flagging signals, not certifying condition."
    )

    what_to_check = _bullet(checks, 9)
    if questions:
        what_to_check += "\n\nQuestions to ask:\n" + _bullet(questions, 8)

    return {
        "why_interesting": _bullet(why, 7),
        "possible_risks": _bullet(risks, 9),
        "what_to_check": what_to_check,
        "seller_signals": _bullet(seller_signals, 4),
        "ai_summary": summary,
    }













