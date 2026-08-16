"""
AUTOHAWK — Excel Output Writer
Writes HOT/GOOD/CHECK listings to deals.xlsx
Auto-saves on each update. Newest listings appear on top.
"""

import os
import re
import logging
from datetime import datetime

import pandas as pd
from openpyxl import load_workbook
from openpyxl.styles import PatternFill, Font, Alignment, Border, Side
from openpyxl.utils import get_column_letter

logger = logging.getLogger("autohawk.excel")

COLUMNS = [
    "Found Time",
    "Listing Age",
    "Source",
    "Link",
    "Car",
    "Year",
    "Mileage (km)",
    "Fuel",
    "Gearbox",
    "Engine",
    "TÜV/HU",
    "Price (€)",
    "Market Price (€)",
    "Margin (€)",
    "Verdict",
    "Product Segment",
    "Segment Scores",
    "Market Status",
    "Confidence",
    "Estimate Quality",
    "Why Interesting",
    "Possible Risks",
    "Model Issues",
    "What To Check",
    "Seller Signals",
    "AI Summary",
    "Final Score",
]

VERDICT_COLORS = {
    "HOT":   "FF4444",
    "GOOD":  "4CAF50",
    "CHECK": "FF9800",
}

VERDICT_TEXT_COLORS = {
    "HOT":   "FFFFFF",
    "GOOD":  "FFFFFF",
    "CHECK": "FFFFFF",
}

HEADER_FILL = PatternFill("solid", fgColor="1A1A2E")
HEADER_FONT = Font(color="FFFFFF", bold=True, size=10)

ROW_ALT_FILL = PatternFill("solid", fgColor="F8F9FA")
ROW_FILL    = PatternFill("solid", fgColor="FFFFFF")

THIN_BORDER = Border(
    left=Side(style="thin", color="E0E0E0"),
    right=Side(style="thin", color="E0E0E0"),
    top=Side(style="thin", color="E0E0E0"),
    bottom=Side(style="thin", color="E0E0E0"),
)

COLUMN_WIDTHS = {
    "Found Time":      18,
    "Listing Age":     14,
    "Source":          14,
    "Link":            30,
    "Car":             25,
    "Year":             8,
    "Mileage (km)":    13,
    "Fuel":            12,
    "Gearbox":         12,
    "Engine":          14,
    "TÜV/HU":           12,
    "Price (€)":       12,
    "Market Price (€)":14,
    "Margin (€)":      12,
    "Verdict":         10,
    "Product Segment":  18,
    "Segment Scores":   34,
    "Market Status":   18,
    "Confidence":      12,
    "Estimate Quality":24,
    "Why Interesting": 48,
    "Possible Risks":  55,
    "Model Issues":    35,
    "What To Check":   65,
    "Seller Signals":  55,
    "AI Summary":      60,
    "Final Score":     12,
}



def _extract_product_segment(ai_summary: str) -> tuple[str, str]:
    text = ai_summary or ""
    segment_match = re.search(r"\b(FLIP_PROFIT|SAFE_FIRST_CAR|BUYER_VALUE|WATCHLIST_ONLY)\b", text)
    segment = segment_match.group(1) if segment_match else "?"
    scores_match = re.search(r"(\{[^\}]*flip_profit[^\}]+\})", text)
    scores = scores_match.group(1) if scores_match else ""
    return segment, scores


def listing_to_row(listing) -> dict:
    age_min = listing.listing_age_minutes
    if age_min is not None:
        if age_min < 60:
            age_str = f"{age_min}m"
        elif age_min < 1440:
            age_str = f"{age_min // 60}h {age_min % 60}m"
        else:
            age_str = f"{age_min // 1440}d"
    else:
        age_str = "?"

    car_name = " ".join(filter(None, [listing.brand, listing.model])) or listing.title or "?"
    mileage_str = f"{listing.mileage:,}" if listing.mileage else "?"
    found_str = listing.found_at.strftime("%d.%m.%Y %H:%M") if listing.found_at else "?"
    product_segment, segment_scores = _extract_product_segment(listing.ai_summary or "")
    missing = []
    if not listing.year:
        missing.append("year")
    if not listing.mileage:
        missing.append("mileage")
    if listing.listing_age_minutes is None:
        missing.append("age")
    if not listing.estimated_market_price:
        missing.append("market")
    estimate_quality = "MEDIUM - rough heuristic"
    if missing:
        estimate_quality = "LOW - missing " + ", ".join(missing)
    if not listing.estimated_market_price:
        estimate_quality = "NO MARKET ESTIMATE - analyze risks only"

    return {
        "Found Time":       found_str,
        "Listing Age":      age_str,
        "Source":           (listing.platform or "").capitalize(),
        "Link":             listing.url or "",
        "Car":              car_name,
        "Year":             listing.year or "?",
        "Mileage (km)":     mileage_str,
        "Fuel":             listing.fuel or "?",
        "Gearbox":          listing.gearbox or "?",
        "Engine":           listing.engine or "?",
        "TÜV/HU":           listing.tuv_text or "?",
        "Price (€)":        f"{listing.price:,.0f}" if listing.price else "?",
        "Market Price (€)": f"{listing.estimated_market_price:,.0f}" if listing.estimated_market_price else "?",
        "Margin (€)":       f"{listing.estimated_margin:,.0f}" if listing.estimated_margin else "?",
        "Verdict":          listing.verdict or "?",
        "Product Segment":  product_segment,
        "Segment Scores":   segment_scores,
        "Market Status":    listing.sold_status or "not checked",
        "Confidence":       listing.confidence or "?",
        "Estimate Quality": estimate_quality,
        "Why Interesting":  listing.why_interesting or "",
        "Possible Risks":   listing.possible_risks or "",
        "Model Issues":     "\n• ".join(listing.model_specific_issues.split(",")) if listing.model_specific_issues else "",
        "What To Check":    listing.what_to_check or "",
        "Seller Signals":   listing.seller_signals or "",
        "AI Summary":       listing.ai_summary or "",
        "Final Score":      f"{listing.final_score:.2f}" if listing.final_score else "?",
    }


def write_excel(listings: list, output_path: str = "output/deals.xlsx"):
    """Write/update the Excel file with new listings. Newest on top."""
    os.makedirs(os.path.dirname(output_path), exist_ok=True)

    rows = [listing_to_row(l) for l in listings]
    df = pd.DataFrame(rows, columns=COLUMNS)

    try:
        df.to_excel(output_path, index=False, sheet_name="AUTOHAWK Deals")
        _style_workbook(output_path, listings)
        logger.info(f"Excel updated: {output_path} ({len(rows)} listings)")
    except PermissionError:
        backup = output_path.replace(".xlsx", f"_backup_{datetime.now().strftime('%H%M%S')}.xlsx")
        df.to_excel(backup, index=False, sheet_name="AUTOHAWK Deals")
        _style_workbook(backup, listings)
        logger.warning(f"Original file locked. Saved to: {backup}")
    except Exception as e:
        logger.error(f"Excel write failed: {e}")


def _style_workbook(path: str, listings: list):
    try:
        wb = load_workbook(path)
        ws = wb.active

        # Style header row
        for col_idx, col_name in enumerate(COLUMNS, start=1):
            cell = ws.cell(row=1, column=col_idx)
            cell.fill = HEADER_FILL
            cell.font = HEADER_FONT
            cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
            cell.border = THIN_BORDER
            col_letter = get_column_letter(col_idx)
            ws.column_dimensions[col_letter].width = COLUMN_WIDTHS.get(col_name, 15)

        ws.row_dimensions[1].height = 30
        ws.freeze_panes = "A2"

        # Style data rows
        for row_idx in range(2, ws.max_row + 1):
            verdict = ws.cell(row=row_idx, column=COLUMNS.index("Verdict") + 1).value or ""
            is_alt = (row_idx % 2 == 0)

            for col_idx in range(1, len(COLUMNS) + 1):
                cell = ws.cell(row=row_idx, column=col_idx)
                cell.border = THIN_BORDER
                cell.alignment = Alignment(vertical="top", wrap_text=True)

                if col_idx == COLUMNS.index("Verdict") + 1:
                    color = VERDICT_COLORS.get(verdict, "AAAAAA")
                    cell.fill = PatternFill("solid", fgColor=color)
                    cell.font = Font(bold=True, color=VERDICT_TEXT_COLORS.get(verdict, "FFFFFF"), size=10)
                    cell.alignment = Alignment(horizontal="center", vertical="center")
                elif col_idx == COLUMNS.index("Link") + 1:
                    if cell.value:
                        cell.font = Font(color="1155CC", underline="single")
                elif is_alt:
                    cell.fill = ROW_ALT_FILL
                else:
                    cell.fill = ROW_FILL

            ws.row_dimensions[row_idx].height = 150

        wb.save(path)
    except Exception as e:
        logger.warning(f"Excel styling failed (file still usable): {e}")


