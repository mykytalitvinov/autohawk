"""
AUTOHAWK â€” Main Entry Point
Run this file to start the scanner.
"""

import asyncio
import json
import logging
import os
import sys
from pathlib import Path

# â”€â”€ Setup paths â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

# â”€â”€ Load .env â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
except ImportError:
    pass  # python-dotenv not installed, .env won't be loaded

# â”€â”€ Create required directories â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
for folder in ["output", "logs", "database"]:
    os.makedirs(ROOT / folder, exist_ok=True)

# â”€â”€ Logging setup â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
log_level = os.getenv("LOG_LEVEL", "INFO").upper()

logging.basicConfig(
    level=getattr(logging, log_level, logging.INFO),
    format="%(asctime)s  %(levelname)-8s  %(name)s  â†’  %(message)s",
    datefmt="%H:%M:%S",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(ROOT / "logs" / "autohawk.log", encoding="utf-8"),
    ],
)

logger = logging.getLogger("autohawk")


def prevent_windows_sleep() -> None:
    """Keep Windows awake while the scanner is running."""
    if os.name != "nt":
        return
    try:
        import ctypes

        ES_CONTINUOUS = 0x80000000
        ES_SYSTEM_REQUIRED = 0x00000001
        ES_DISPLAY_REQUIRED = 0x00000002
        ctypes.windll.kernel32.SetThreadExecutionState(
            ES_CONTINUOUS | ES_SYSTEM_REQUIRED | ES_DISPLAY_REQUIRED
        )
        logger.info("Windows sleep prevention enabled while AUTOHAWK is running")
    except Exception as exc:
        logger.warning(f"Could not enable Windows sleep prevention: {exc}")


def load_config() -> dict:
    config_path = ROOT / "config.json"
    if not config_path.exists():
        logger.warning("config.json not found â€” using defaults")
        return {}
    with open(config_path, encoding="utf-8-sig") as f:
        return json.load(f)


def print_banner():
    banner = r"""
  ___  _   _ _____ ___  _   _    ___        ___  ____
 / _ \| | | |_   _/ _ \| | | |  / _ \      / _ \|  _ \
| | | | | | | | || | | | |_| | | | | |____| | | | |_) |
| |_| | |_| | | || |_| |  _  | | |_| |____| |_| |  __/
 \___/ \___/  |_| \___/|_| |_|  \___/      \___/|_|

         AI-Powered Used Car Deal Scanner â€” Germany
         â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
"""
    print(banner)


async def main():
    print_banner()
    logger.info("AUTOHAWK starting up...")
    prevent_windows_sleep()

    config = load_config()

    provider = os.getenv("AI_PROVIDER", "auto").strip().lower()
    anthropic_key = os.getenv("ANTHROPIC_API_KEY", "").strip()
    openai_key = os.getenv("OPENAI_API_KEY", "").strip()
    gemini_key = os.getenv("GEMINI_API_KEY", "").strip()
    groq_key = os.getenv("GROQ_API_KEY", "").strip()
    gemini_model = os.getenv("GEMINI_MODEL", "gemini-2.5-flash").strip()
    groq_model = (
        os.getenv("GROQ_MODEL", "").strip()
        or str(config.get("groq_model", "")).strip()
        or "meta-llama/llama-4-scout-17b-16e-instruct"
    )

    if provider == "groq" and groq_key:
        logger.info(f"Groq API key found - AI photo analysis ENABLED ({groq_model})")
    elif provider in {"gemini", "google"} and gemini_key:
        logger.info(f"Gemini API key found - AI shortlist analysis ENABLED ({gemini_model})")
    elif provider in {"claude", "anthropic"} and anthropic_key:
        logger.info("Claude / Anthropic API key found - AI analysis ENABLED")
    elif provider == "openai" and openai_key:
        logger.info("OpenAI API key found - AI analysis ENABLED")
    elif provider == "auto" and groq_key:
        logger.info(f"Groq API key found - AI photo analysis ENABLED ({groq_model})")
    elif provider == "auto" and gemini_key:
        logger.info(f"Gemini API key found - AI shortlist analysis ENABLED ({gemini_model})")
    elif provider == "auto" and anthropic_key:
        logger.info("Claude / Anthropic API key found - AI analysis ENABLED")
    elif provider == "auto" and openai_key:
        logger.info("OpenAI API key found - AI analysis ENABLED")
    else:
        logger.info("No AI API key found - using rule-based analysis (still works)")

    # Check OpenAI key
    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if False and api_key:
        logger.info("âœ… OpenAI API key found â€” AI analysis ENABLED")
    elif False:
        logger.info("â„¹ï¸  No OpenAI key â€” using rule-based analysis (still works)")

    logger.info(f"ðŸ’° Budget: {config.get('budget_min', 1000)}â‚¬ â€“ {config.get('budget_max', 15000)}â‚¬")
    logger.info(f"ðŸ“ Max mileage: {config.get('max_mileage', 200000):,} km")
    logger.info(f"ðŸ—“  Min year: {config.get('min_year', 2005)}")
    logger.info(f"â±  Scan interval: {config.get('scan_interval_minutes', 5)} min")
    logger.info(
        f"Freshness window: {config.get('freshness_min_minutes', 1)}-"
        f"{config.get('freshness_max_minutes', config.get('freshness_max_hours', 12) * 60)} min"
    )
    logger.info(f"ðŸ“‚ Output: {config.get('output_file', 'output/deals.xlsx')}")

    enabled_sources = [k for k, v in config.get("sources", {}).items() if v]
    logger.info(f"ðŸ“¡ Active sources: {', '.join(enabled_sources)}")
    logger.info("")

    from src.scanner import AutohawkScanner
    scanner = AutohawkScanner(config)

    try:
        await scanner.run_forever()
    except KeyboardInterrupt:
        logger.info("\nðŸ›‘ Interrupted by user. Goodbye!")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nAutohawk stopped.")




