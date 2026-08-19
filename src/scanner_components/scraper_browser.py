from __future__ import annotations

from typing import Optional
import asyncio
import random


async def create_browser_session(playwright, config: dict, *, user_agent: str, viewport: dict, init_script: str, channel: str | None = None):
    launch_options = {
        "headless": True,
        "args": [
            "--no-sandbox",
            "--disable-setuid-sandbox",
            "--disable-blink-features=AutomationControlled",
        ],
    }
    if channel:
        launch_options["channel"] = channel
    browser = await playwright.chromium.launch(**launch_options)
    context_options = {
        "user_agent": user_agent,
        "viewport": viewport,
        "locale": "de-DE",
    }
    if channel:
        context_options.update({"device_scale_factor": 1, "has_touch": False, "is_mobile": False})
    context = await browser.new_context(**context_options)
    timeout_ms = config.get("playwright_action_timeout_ms", 5000)
    context.set_default_timeout(timeout_ms)
    page = await context.new_page()
    page.set_default_timeout(timeout_ms)
    await page.add_init_script(init_script)
    return browser, context, page


async def iter_cards(cards, max_results: int, *, delay_min: float, delay_max: float):
    for card in cards[:max_results]:
        await asyncio.sleep(random.uniform(delay_min, delay_max))
        yield card


async def body_text(page) -> str:
    body = await page.query_selector("body")
    return await body.inner_text() if body else ""


async def extract_detail_value(page, labels: list[str]) -> Optional[str]:
    """Read one vehicle fact from a rendered detail page by its visible label."""
    script = r"""
    (labels) => {
        const norm = (value) => (value || "")
            .toString()
            .normalize("NFD")
            .replace(/[\u0300-\u036f]/g, "")
            .toLowerCase()
            .replace(/\s+/g, " ")
            .trim();
        const wanted = labels.map(norm);
        const hasLabel = (text) => wanted.some((label) => norm(text).includes(label));
        const clean = (text) => (text || "").toString().replace(/\s+/g, " ").trim();
        const stripLabel = (text) => {
            let value = clean(text);
            for (const label of labels) {
                value = value.replace(new RegExp(label.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"), "i"), "").trim();
            }
            return value.replace(/^[:\-–—|]+/, "").trim();
        };
        const useful = (text) => {
            const value = clean(text);
            return /\d/.test(value) && !hasLabel(value) && value.length <= 180;
        };

        if (wanted.some((label) => ["kilometerstand", "laufleistung", "km-stand"].includes(label))) {
            const bodyText = document.body ? (document.body.innerText || document.body.textContent || "") : "";
            const mileageMatch = bodyText.match(
                /(?:Kilometerstand|Laufleistung|KM-Stand)\s*[:\n\r ]{0,30}(\d{1,3}(?:[.\s]\d{3})+|\d{4,6})\s*(?:km|kilometer)?/i
            );
            if (mileageMatch) {
                return `${mileageMatch[1]} km`;
            }
        }

        for (const dt of Array.from(document.querySelectorAll("dt"))) {
            if (!hasLabel(dt.innerText || dt.textContent)) continue;
            const dd = dt.nextElementSibling;
            if (dd && useful(dd.innerText || dd.textContent)) {
                return stripLabel(dd.innerText || dd.textContent);
            }
        }

        for (const node of Array.from(document.querySelectorAll("li, tr, dl, section, div, p, span"))) {
            const rawText = (node.innerText || node.textContent || "").toString();
            const text = clean(rawText);
            if (!text || !hasLabel(rawText)) continue;

            const lines = rawText.split(/\n+/).map((line) => line.trim()).filter(Boolean);
            for (let i = 0; i < lines.length; i++) {
                if (!hasLabel(lines[i])) continue;
                const sameLine = stripLabel(lines[i]);
                if (useful(sameLine)) return sameLine;
                for (let j = i + 1; j < Math.min(lines.length, i + 4); j++) {
                    if (hasLabel(lines[j])) break;
                    if (useful(lines[j])) return stripLabel(lines[j]);
                }
            }

            const children = Array.from(node.children || []);
            for (let i = 0; i < children.length; i++) {
                if (!hasLabel(children[i].innerText || children[i].textContent)) continue;
                for (let j = i + 1; j < Math.min(children.length, i + 4); j++) {
                    const value = children[j].innerText || children[j].textContent;
                    if (useful(value)) return stripLabel(value);
                }
            }

            const sibling = node.nextElementSibling;
            if (sibling && useful(sibling.innerText || sibling.textContent)) {
                return stripLabel(sibling.innerText || sibling.textContent);
            }
        }
        return null;
    }
    """
    try:
        value = await page.evaluate(script, labels)
        return str(value).strip() if value else None
    except Exception:
        return None