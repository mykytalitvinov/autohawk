"""
AUTOHAWK Kleinanzeigen Scraper
Scrapes fresh car listings from Kleinanzeigen.de
Uses Playwright with realistic delays and headers.
Stops gracefully on captcha.
"""

import asyncio
import json
import logging
import random
import re
from datetime import datetime
from typing import List
from src.utils import norm
from src.scanner_components.listing_normalizer import build_listing, refresh_fingerprint
from src.scanner_components.rejection_rules import SHARED_REJECT_RULES, early_reject_reason
from src.scanner_components.scraper_browser import body_text, create_browser_session, extract_detail_value, iter_cards
from src.scanner_components.source_queries import autoscout24_search_url, kleinanzeigen_search_url
from src.scanner_components.listing_metadata import (
    parse_detail_listing_age,
    parse_listing_age,
)
from src.scanner_components.quality_policy import data_quality_warnings
from src.scanner_components.vehicle_aliases import extract_brand_model
from src.scanner_components.vehicle_parsers import (
    extract_engine,
    parse_explicit_vehicle_year,
    parse_card_mileage,
    parse_fuel,
    parse_gearbox,
    parse_mileage,
    parse_price,
    parse_tuv_info,
    parse_year,
)

logger = logging.getLogger("autohawk.scraper.playwright")


EARLY_REJECT_RULES = SHARED_REJECT_RULES
EARLY_REJECT_PATTERNS = [pattern for _, pattern in EARLY_REJECT_RULES]


def _is_generic_detail_heading(text: str) -> bool:
    """Kleinanzeigen can expose SEO/search headings as h1 on some pages.
    Never let those overwrite the real card title.
    """
    normalized = norm(text)
    if not normalized:
        return True
    generic_tokens = [
        "autos in ",
        "gebrauchtwagen in ",
        "fahrzeuge in ",
        "1 - 25 von",
        "1-25 von",
        "anzeigen in ",
    ]
    return any(token in normalized for token in generic_tokens)


async def _enrich_kleinanzeigen_detail(context, listing: dict, timeout_ms: int = 12000) -> dict:
    """Open the detail page and improve listing data, best-effort."""
    page = await context.new_page()
    try:
        await page.goto(listing["url"], timeout=timeout_ms, wait_until="domcontentloaded")
        await asyncio.sleep(random.uniform(1.0, 2.4))
        page_body_text = await body_text(page)

        article_text = page_body_text

        article_el = await page.query_selector("main article")

        if article_el:

            try:
                scoped_text = (await article_el.inner_text()).strip()

                if scoped_text:
                    article_text = scoped_text
            except Exception:
                article_text = page_body_text

        body_l = page_body_text.lower()
        if any(x in body_l for x in ["captcha", "i am not a robot", "bitte best", "robot check"]):
            listing["detail_verified"] = False
            listing["detail_error"] = "captcha"
            return listing
        reject_reason = early_reject_reason(article_text)
        if reject_reason:
            listing["detail_verified"] = False
            listing["detail_error"] = f"hard reject: {reject_reason}"
            listing["hard_reject"] = True
            listing["hard_reject_reason"] = reject_reason
            return listing

        title_el = await page.query_selector("h1")
        if title_el:
            detail_title = (await title_el.inner_text()).strip()
            if detail_title and not _is_generic_detail_heading(detail_title):
                listing["title"] = detail_title
                brand, model = extract_brand_model(detail_title)
                listing["brand"] = brand or listing.get("brand")
                listing["model"] = model or listing.get("model")

        price_text = ""
        for selector in ["#viewad-price", "[data-testid='vip-price']", ".boxedarticle--price", "h2"]:
            price_el = await page.query_selector(selector)
            if price_el:
                price_text = await price_el.inner_text()
                if "\u20ac" in price_text or "eur" in price_text.lower():
                    break
        price = parse_price(price_text) or listing.get("price")
        mileage_text = await extract_detail_value(page, ["Kilometerstand", "Laufleistung", "KM-Stand"])
        mileage = parse_mileage(f"Kilometerstand\n{mileage_text}") if mileage_text else listing.get("mileage")
        tuv_detail_text = await extract_detail_value(page, ["HU bis", "TUV bis", "TUEV bis", "TÜV bis", "Hauptuntersuchung"])
        year_detail_text = await extract_detail_value(page, ["Erstzulassung", "Baujahr", "Erstzul.", "EZ", "Bj"])
        structured_year = parse_year(f"Erstzulassung\n{year_detail_text}") if year_detail_text else None
        title_year = parse_year(listing.get("title") or "")
        detail_year = parse_year(article_text)
        year = structured_year or detail_year or title_year or listing.get("year")
        if title_year and detail_year and detail_year >= datetime.now().year - 1 and title_year <= datetime.now().year - 2 and title_year < detail_year:
            year = title_year
        if year and year >= datetime.now().year and not structured_year:
            year = title_year or listing.get("year")
        fuel = parse_fuel(article_text)
        gearbox = parse_gearbox(article_text)
        engine = extract_engine(f"{listing.get('title', '')} {article_text}")
        age_min = parse_detail_listing_age(article_text)

        if price:
            listing["price"] = price
        if mileage:
            listing["mileage"] = mileage
        if year:
            listing["year"] = year
        if fuel:
            listing["fuel"] = fuel
        if gearbox:
            listing["gearbox"] = gearbox
        if engine:
            listing["engine"] = engine
        if age_min is not None:
            existing_age = listing.get("listing_age_minutes")
            # Keep the fresher card age when detail parsing finds an older date.
            # Kleinanzeigen detail pages often contain TUV/service/registration dates.
            if existing_age is None or age_min <= existing_age:
                listing["listing_age_minutes"] = age_min

        desc_el = await page.query_selector("#viewad-description, [data-testid='vip-description'], .boxedarticle--details")
        if desc_el:
            desc = (await desc_el.inner_text()).strip()
            if desc:
                listing["description"] = desc[:3000]
        if not listing.get("description") or len(listing.get("description", "")) < 120:
            # Fallback for Kleinanzeigen/mobile.de embedded detail pages.
            desc_match = re.search(
                r"Beschreibung\s*(.*?)(?:Inserat bereitgestellt von|Rechtliche Angaben|Nachricht schreiben|Andere Anzeigen)",
                article_text,
                re.IGNORECASE | re.DOTALL,
            )
            if desc_match:
                desc = re.sub(r"\n{3,}", "\n\n", desc_match.group(1)).strip()
                if desc:
                    listing["description"] = desc[:5000]

        desc_text = listing.get("description", "") or ""
        combined_detail_text = f"{listing.get('title', '')} {desc_text} {article_text}"
        tuv_info = parse_tuv_info(
            " ".join(
                part
                for part in [f"HU bis {tuv_detail_text}" if tuv_detail_text else "", listing.get("title", ""), desc_text, article_text]
                if part
            )
        )
        if tuv_info.get("tuv_text"):
            listing.update(tuv_info)
        reject_reason = early_reject_reason(combined_detail_text)
        if reject_reason:
            listing["detail_verified"] = False
            listing["detail_error"] = f"hard reject: {reject_reason}"
            listing["hard_reject"] = True
            listing["hard_reject_reason"] = reject_reason
            return listing

        desc_year = parse_explicit_vehicle_year(desc_text)
        if desc_year:
            listing["year"] = desc_year
        normalized_body = norm(page_body_text)
        normalized_article = norm(article_text)
        if "gewerblicher nutzer" in normalized_body or "rechtliche angaben" in normalized_article:
            listing["seller_type"] = "dealer"
        elif "privater nutzer" in normalized_body:
            listing["seller_type"] = "private"

        image_urls = []
        for img in await page.query_selector_all("img"):
            src = await img.get_attribute("src")
            if src and src.startswith("http") and src not in image_urls:
                low_src = src.lower()
                if any(x in low_src for x in ["logo", "illustrations", "placeholder", "sprite", "icon", "connection-issue"]):
                    continue
                image_urls.append(src)
        if image_urls:
            listing["photo_urls"] = json.dumps(image_urls[:8])

        listing["detail_verified"] = True
        quality_warnings = data_quality_warnings(listing)
        if quality_warnings:
            listing["data_quality_warnings"] = quality_warnings
        return listing
    except Exception as exc:
        listing["detail_verified"] = False
        listing["detail_error"] = str(exc)[:120]
        return listing
    finally:
        await page.close()


async def scrape_kleinanzeigen(config: dict, max_results: int = 30) -> List[dict]:
    """
    Scrape fresh car listings from Kleinanzeigen.
    Returns list of normalized listing dicts.
    """
    listings = []

    try:
        from playwright.async_api import async_playwright
    except ImportError:
        logger.error("Playwright not installed. Run: pip install playwright && playwright install chromium")
        return []

    min_age = config.get("freshness_min_minutes", 1)
    max_age = config.get("freshness_max_minutes", config.get("freshness_max_hours", 12) * 60)
    url = kleinanzeigen_search_url(config)

    async with async_playwright() as p:
        try:
            browser, context, page = await create_browser_session(
                p,
                config,
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0.0.0 Safari/537.36"
                ),
                viewport={"width": 1366, "height": 768},
                init_script="Object.defineProperty(navigator, 'webdriver', {get: () => undefined});",
            )

            logger.info(f"Kleinanzeigen: opening {url}")
            await page.goto(url, timeout=30000, wait_until="domcontentloaded")
            await asyncio.sleep(random.uniform(2.0, 4.0))

            # Captcha detection
            content = await page.content()
            if any(x in content.lower() for x in ["captcha", "i am not a robot", "bitte best", "robot check"]):
                logger.warning("Kleinanzeigen: CAPTCHA detected - skipping this source")
                await browser.close()
                return []

            # Find listing cards
            cards = await page.query_selector_all("article.aditem")
            if not cards:
                cards = await page.query_selector_all("[data-adid]")

            logger.info(f"Kleinanzeigen: found {len(cards)} cards")

            async for card in iter_cards(cards, max_results, delay_min=0.1, delay_max=0.3):
                try:
                    # Title & URL
                    title_el = await card.query_selector("h2 a, .ellipsis a, a.aditem-main--middle--titleadlink")
                    title = await title_el.inner_text() if title_el else ""
                    href = await title_el.get_attribute("href") if title_el else ""
                    if href and not href.startswith("http"):
                        href = "https://www.kleinanzeigen.de" + href

                    # Price
                    price_el = await card.query_selector(".aditem-main--middle--price-shipping--price, p.aditem-main--middle--price")
                    price_text = await price_el.inner_text() if price_el else ""
                    price = parse_price(price_text)

                    # Location & time
                    # Kleinanzeigen usually keeps location on the left and publish time on the right.
                    # Reading only the left block makes fresh ads look like "age unknown".
                    meta_parts = []
                    for selector in [
                        ".aditem-main--top--left",
                        ".aditem-main--top--right",
                        ".aditem-details",
                    ]:
                        meta_el = await card.query_selector(selector)
                        if meta_el:
                            try:
                                part = (await meta_el.inner_text()).strip()
                                if part:
                                    meta_parts.append(part)
                            except Exception:
                                pass

                    meta_text = "\n".join(meta_parts)
                    location = ""
                    age_min = None
                    if meta_text:
                        lines = [l.strip() for l in meta_text.split("\n") if l.strip()]
                        if lines:
                            location = lines[0]
                        for line in lines:
                            parsed_age = parse_listing_age(line)
                            if parsed_age is not None:
                                age_min = parsed_age
                                break

                    # Description snippet
                    desc_el = await card.query_selector(".aditem-main--middle--description, p.description")
                    desc = await desc_el.inner_text() if desc_el else ""

                    detail_text = ""
                    detail_els = await card.query_selector_all(".simpletag, .aditem-main--middle--vehiclefeatures, .aditem-main--middle")
                    for detail_el in detail_els:
                        try:
                            detail_text += " " + await detail_el.inner_text()
                        except Exception:
                            continue

                    reject_reason = early_reject_reason(f"{title} {desc} {detail_text}")
                    if reject_reason:
                        logger.debug(f"Kleinanzeigen early reject [{reject_reason}]: {title[:60]}")
                        continue

                    if age_min is not None and (age_min < min_age or age_min > max_age):
                        logger.debug(f"Kleinanzeigen age reject: {title[:60]} age={age_min}min")
                        continue

                    # Platform ID from URL or data attribute
                    pid = await card.get_attribute("data-adid") or ""
                    if not pid and href:
                        m = re.search(r"/(\d+)\.html", href)
                        if m:
                            pid = m.group(1)

                    if not title or not href:
                        continue

                    brand, model = extract_brand_model(title)
                    tuv_info = parse_tuv_info(f"{title} {desc} {detail_text}")

                    title_year = parse_year(title)
                    detail_year = parse_year(detail_text)
                    year = (
                        title_year
                        if title_year and detail_year and detail_year >= datetime.now().year - 1 and title_year <= datetime.now().year - 2
                        else detail_year
                    )
                    listing = build_listing(
                        platform="kleinanzeigen",
                        platform_id=pid,
                        url=href,
                        title=title,
                        brand=brand,
                        model=model,
                        price=price,
                        mileage=parse_card_mileage(detail_text),
                        year=year,
                        fuel=None,
                        gearbox=None,
                        engine=extract_engine(f"{title} {desc} {detail_text}"),
                        tuv_info=tuv_info,
                        location=location,
                        description=desc,
                        seller_type="private",
                        listing_age_minutes=age_min,
                    )
                    listings.append(listing)

                except Exception as e:
                    logger.debug(f"Kleinanzeigen card parse error: {e}")
                    continue

            if config.get("verify_detail_pages", True) and listings:
                detail_limit = min(len(listings), config.get("max_detail_pages_per_scan", 20))
                detail_timeout = config.get("detail_page_timeout_ms", 12000)
                logger.info(f"Kleinanzeigen: verifying {detail_limit} detail pages")
                verified = []
                for idx, listing in enumerate(listings):
                    if idx < detail_limit:
                        await asyncio.sleep(random.uniform(0.4, 1.1))
                        logger.info(f"Kleinanzeigen: detail {idx + 1}/{detail_limit} - {listing.get('title', '')[:45]}")
                        try:
                            listing = await asyncio.wait_for(
                                _enrich_kleinanzeigen_detail(context, listing, timeout_ms=detail_timeout),
                                timeout=(detail_timeout / 1000) + 6,
                            )
                        except asyncio.TimeoutError:
                            logger.warning(
                                f"Kleinanzeigen: detail timeout - skipping enrichment for {listing.get('title', '')[:55]}"
                            )
                            listing["detail_verified"] = False
                            listing["detail_error"] = "detail timeout"
                        refresh_fingerprint(listing)
                    verified.append(listing)
                listings = verified

            await browser.close()

        except Exception as e:
            logger.error(f"Kleinanzeigen scraper error: {e}")

    logger.info(f"Kleinanzeigen: scraped {len(listings)} listings")
    return listings

async def scrape_autoscout24(config: dict, max_results: int = 30) -> List[dict]:
    """
    Scrape fresh car listings from AutoScout24 using built-in Playwright anti-detection.
    """
    listings = []

    try:
        from playwright.async_api import async_playwright
    except ImportError:
        logger.error("Playwright not installed.")
        return []

    url = autoscout24_search_url(config)

    async with async_playwright() as p:
        try:
            browser, context, page = await create_browser_session(
                p,
                config,
                channel="chrome",
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/125.0.0.0 Safari/537.36"
                ),
                viewport={"width": 1440, "height": 900},
                init_script="""
                Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
                window.navigator.chrome = { runtime: {} };
                Object.defineProperty(navigator, 'languages', { get: () => ['de-DE', 'de', 'en-US', 'en'] });
                Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
                """,
            )

            logger.info("AutoScout24: opening search")
            await page.goto(url, timeout=30000, wait_until="domcontentloaded")
            
            # Імітація поведінки людини: рандомна пауза та скрол
            await asyncio.sleep(random.uniform(3.0, 5.5))
            await page.mouse.wheel(0, random.randint(300, 600))
            await asyncio.sleep(random.uniform(1.0, 2.0))

            content = await page.content()
            if any(x in content.lower() for x in ["captcha", "robot"]):
                logger.warning("AutoScout24: CAPTCHA detected")

            # Пошук карток оголошень
            cards = await page.query_selector_all("article[data-guid], .cldt-summary-full-item, article.cldt-summary-full-item")
            if not cards:
                cards = await page.query_selector_all("[data-item-name='listing-summary']")

            logger.info(f"AutoScout24: found {len(cards)} cards")

            async for card in iter_cards(cards, max_results, delay_min=0.1, delay_max=0.25):
                try:
                    # Збираємо всі дані за один швидкий JS-запит у браузері
                    data = await card.evaluate("""el => {
                        const getText = (selectors) => {
                            for (let sel of selectors.split(',')) {
                                let node = el.querySelector(sel.trim());
                                if (node && node.innerText.trim()) return node.innerText.trim();
                            }
                            return "";
                        };

                        // Ціна з очищенням суфікса '1' (тег sup)
                        let priceEl = el.querySelector("[data-testid='regular-price'], [data-item-name='price'], [class*='Price_price']");
                        let priceText = "";
                        if (priceEl) {
                            let clone = priceEl.cloneNode(true);
                            let sup = clone.querySelector('sup');
                            if (sup) sup.remove();
                            priceText = clone.innerText;
                        }

                        let linkEl = el.querySelector("a.DeclutteredListItem_overlay_anchor__jqEyM, a[href*='/angebote/']");

                        return {
                            title: getText("h2, [class*='ListItemTitle_heading'], a[data-item-name='detail-page-link']"),
                            href: linkEl ? linkEl.getAttribute("href") : "",
                            priceText: priceText,
                            mileageText: getText("[data-testid='VehicleDetails-mileage_odometer'], [data-item-name='mileage']"),
                            yearText: getText("[data-testid='VehicleDetails-calendar'], [data-item-name='first-registration']"),
                            fuelText: getText("[data-testid='VehicleDetails-gas_pump'], [data-item-name='fuel-type']"),
                            engineText: getText("[data-testid='VehicleDetails-speedometer'], [data-item-name='engine']"),
                            location: getText("[data-testid='dealer-address'], [data-item-name='location']"),
                            sellerAttr: el.getAttribute("data-seller-type") || "",
                            guid: el.getAttribute("data-guid") || el.getAttribute("id") || ""
                        };
                    }""")

                    # Обробка та парсинг на стороні Python
                    title = " ".join(data["title"].split())
                    href = data["href"]
                    if href and not href.startswith("http"):
                        href = "https://www.autoscout24.de" + href

                    if not title or not href:
                        continue

                    price = parse_price(data["priceText"])
                    mileage = parse_mileage(f"Kilometerstand\n{data['mileageText']}") if data["mileageText"] else None
                    year = parse_year(data["yearText"] + " " + title)
                    tuv_info = parse_tuv_info(f"{title} {data['yearText']}")

                    seller_type = "dealer" if data["sellerAttr"] == "d" else ("private" if data["sellerAttr"] else "unknown")

                    pid = data["guid"]
                    if not pid and href:
                        m = re.search(r"/angebote/([^/]+)", href)
                        pid = m.group(1) if m else ""

                    brand, model = extract_brand_model(title)

                    listing = build_listing(
                        platform="autoscout24",
                        platform_id=pid,
                        url=href,
                        title=title,
                        brand=brand,
                        model=model,
                        price=price,
                        mileage=mileage,
                        year=year,
                        fuel=data["fuelText"],
                        gearbox=None,
                        engine=data["engineText"],
                        tuv_info=tuv_info,
                        location=data["location"],
                        description="",
                        seller_type=seller_type,
                        listing_age_minutes=0,
                    )
                    listings.append(listing)

                except Exception as e:
                    logger.debug(f"AutoScout24 card error: {e}")
                    continue

            await browser.close()

        except Exception as e:
            logger.error(f"AutoScout24 scraper error: {e}")

    logger.info(f"AutoScout24: scraped {len(listings)} listings")
    return listings