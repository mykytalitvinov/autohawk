"""
AUTOHAWK — Database Models
SQLite + SQLAlchemy
"""

from datetime import datetime
from sqlalchemy import create_engine, Column, String, Integer, Float, DateTime, Text, Boolean, text
from sqlalchemy.orm import declarative_base, sessionmaker
import os

Base = declarative_base()


class Listing(Base):
    __tablename__ = "listings"

    id = Column(Integer, primary_key=True, autoincrement=True)

    # Stable identity
    stable_id = Column(String, unique=True, index=True)   # platform:platform_id
    fingerprint = Column(String, index=True)               # fallback hash

    # Source
    platform = Column(String)                              # kleinanzeigen / autoscout24 / mobile_de
    platform_id = Column(String)
    url = Column(String)

    # Car data
    title = Column(String)
    brand = Column(String)
    model = Column(String)
    year = Column(Integer)
    mileage = Column(Integer)
    price = Column(Float)
    fuel = Column(String)
    gearbox = Column(String)
    engine = Column(String)
    location = Column(String)
    description = Column(Text)
    seller_type = Column(String)   # private / dealer
    photo_urls = Column(Text)      # JSON list

    # Timestamps
    found_at = Column(DateTime, default=datetime.utcnow)
    listed_at = Column(DateTime)                           # when seller posted it
    listing_age_minutes = Column(Integer)

    # Scores
    # Decision authority lives in dealer_engine/an optional paid-AI override.
    # final_score is the persisted dealer/AI opportunity score normalized to 0..1.
    # freshness_score, price_score, condition_score and urgency_score are
    # legacy scoring.py diagnostics only; they do not decide HOT/GOOD/CHECK.
    # risk_score and liquidity_score are persisted from the dealer/AI decision,
    # not from scoring.py's legacy brand-only helpers.
    freshness_score = Column(Float, default=0.0)
    price_score = Column(Float, default=0.0)
    condition_score = Column(Float, default=0.0)
    risk_score = Column(Float, default=0.0)
    liquidity_score = Column(Float, default=0.0)
    urgency_score = Column(Float, default=0.0)
    final_score = Column(Float, default=0.0)

    # Market analysis
    estimated_market_price = Column(Float)
    estimated_margin = Column(Float)
    undervaluation_pct = Column(Float)

    # AI analysis
    verdict = Column(String)           # HOT / GOOD / CHECK / SKIP
    confidence = Column(String)        # HIGH / MEDIUM / LOW
    why_interesting = Column(Text)
    possible_risks = Column(Text)
    model_specific_issues = Column(Text)
    what_to_check = Column(Text)
    seller_signals = Column(Text)
    ai_summary = Column(Text)

    # Flags
    is_junk = Column(Boolean, default=False)
    junk_reason = Column(String)
    exported_to_excel = Column(Boolean, default=False)
    captcha_blocked = Column(Boolean, default=False)

    # Sold tracker / market feedback
    is_sold = Column(Boolean, default=False)
    sold_status = Column(String)          # active / sold_or_removed / unknown
    sold_reason = Column(String)
    sold_checked_at = Column(DateTime)
    sold_detected_at = Column(DateTime)


def _ensure_listing_columns(engine):
    """Add new SQLite columns without deleting the existing local database."""
    required = {
        "is_sold": "BOOLEAN DEFAULT 0",
        "sold_status": "VARCHAR",
        "sold_reason": "VARCHAR",
        "sold_checked_at": "DATETIME",
        "sold_detected_at": "DATETIME",
    }
    with engine.begin() as conn:
        existing = {row[1] for row in conn.execute(text("PRAGMA table_info(listings)")).fetchall()}
        for name, ddl in required.items():
            if name not in existing:
                conn.execute(text(f"ALTER TABLE listings ADD COLUMN {name} {ddl}"))


def init_db(db_path: str = "database/autohawk.db") -> sessionmaker:
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    engine = create_engine(f"sqlite:///{db_path}", echo=False)
    Base.metadata.create_all(engine)
    _ensure_listing_columns(engine)
    Session = sessionmaker(bind=engine)
    return Session

