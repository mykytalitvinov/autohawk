# AUTOHAWK — AI Used Car Deal Scanner 🦅

**AUTOHAWK scans German used car platforms and highlights potentially good deals before other buyers notice them.**

It is NOT a marketplace. It is an AI pre-filtering tool that:
- Reduces thousands of listings to a short, smart shortlist
- Explains why a car may be interesting
- Explains possible risks (without pretending to be a mechanic)
- Keeps everything fresh — good deals disappear fast

---

## ⚡ Quick Start (Non-Technical Users)

### Step 1 — Install Python

Download and install Python 3.11 or newer:
👉 https://www.python.org/downloads/

**Important:** During installation, check the box **"Add Python to PATH"**

### Step 2 — Download AUTOHAWK

Download this folder somewhere easy to find, like your Desktop.

### Step 3 — Start the Scanner

**Windows:**
→ Double-click `run_autohawk.bat`

**macOS / Linux:**
→ Double-click `run_autohawk.sh`
→ (First time: right-click → Open, then allow in Security settings)

That's it. The scanner will:
- Install all required software automatically (first launch only, takes 1-2 minutes)
- Start scanning immediately
- Save results to `output/deals.xlsx`

### Step 4 — Open Your Deals

Open `output/deals.xlsx` in Excel or LibreOffice.
The file updates automatically while the scanner runs.

---

## ⚙️ Configuration

Edit `config.json` to set your preferences:

```json
{
  "budget_max": 15000,        ← Maximum price (€)
  "budget_min": 1000,         ← Minimum price (€)
  "max_mileage": 200000,      ← Maximum km
  "min_year": 2005,           ← Oldest acceptable year
  "scan_interval_minutes": 5  ← How often to scan
}
```

---

## 🔑 OpenAI API Key (Optional but Recommended)

Without a key: AUTOHAWK uses smart rule-based analysis. **Works fine.**
With a key: AUTOHAWK uses GPT-4o-mini for deeper, more natural analysis.

To add your key:
1. Get a key at https://platform.openai.com/api-keys
2. Open `.env` in Notepad
3. Change this line:
   ```
   OPENAI_API_KEY=your-key-here
   ```

---

## 📊 Understanding the Spreadsheet

| Column | What it means |
|--------|---------------|
| **Verdict** | 🔥 HOT = act fast / ✅ GOOD = worth a look / 👀 CHECK = needs review |
| **Confidence** | How much data we had to base the analysis on |
| **Why Interesting** | Why the AI flagged this listing |
| **Possible Risks** | Things that might be wrong — always inspect in person! |
| **What To Check** | Specific things to look at when you visit |
| **Margin (€)** | Estimated difference between asking price and market value |
| **Listing Age** | How fresh the listing is (fresh = better) |

---

## ⚠️ Important Disclaimer

AUTOHAWK is a **pre-filtering assistant, not a mechanic.**

- It detects suspicious signals, it does NOT diagnose cars
- Always inspect a car in person before buying
- Always have a mechanic check it if unsure
- The AI uses phrases like "possible", "worth checking", "may indicate" — this is intentional
- Never buy a car based on AUTOHAWK alone

---

## 🗂 Folder Structure

```
autohawk/
├── run_autohawk.bat     ← Windows launcher (double-click this)
├── run_autohawk.sh      ← macOS/Linux launcher
├── main.py              ← Main program
├── config.json          ← Your settings
├── .env                 ← API keys (private, don't share)
├── requirements.txt     ← Dependencies list
├── src/
│   ├── scanner.py       ← Main scan loop
│   ├── scrapers.py      ← Web scrapers
│   ├── filter.py        ← Junk filter
│   ├── scoring.py       ← Scoring engine
│   ├── market.py        ← Market price estimator
│   ├── ai_analysis.py   ← AI analysis
│   ├── excel_writer.py  ← Excel output
│   └── models.py        ← Database
├── output/
│   └── deals.xlsx       ← Your results (opens in Excel)
├── logs/
│   └── autohawk.log     ← Activity log
└── database/
    └── autohawk.db      ← Internal database
```

---

## 🐛 Troubleshooting

**"Python not found"**
→ Install Python from python.org and check "Add Python to PATH"

**"Browser setup failed"**
→ Run manually: `python -m playwright install chromium`

**"deals.xlsx won't update" (file locked)**
→ Close Excel, then the scanner will save a backup file automatically

**Scanner finds nothing**
→ Check your budget/mileage settings in config.json
→ Some sources may show a captcha — scanner will skip them safely

**Captcha detected**
→ Normal. The scanner logs a warning and continues with other sources.
→ Try again after 15-30 minutes.

---

## 🔮 What's Next

Future planned features:
- Telegram alerts for HOT deals
- Mobile app
- More sources (mobile.de full scraping)
- Improved photo analysis

---

*AUTOHAWK — like having an experienced car friend constantly watching the market.*
