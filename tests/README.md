# AUTOHAWK Tests

These tests cover risky business logic without real websites, Playwright, or network calls.

## Run

```bat
cd C:\Users\nikita\Desktop\autohawk
python -m pip install -r requirements.txt
python -m pip install -r requirements-dev.txt
python -m pytest
```

## Add A New Kleinanzeigen HTML Fixture

1. Save an anonymized HTML snippet in `tests/fixtures/`.
2. Remove real phone numbers, emails, names, exact addresses, and tracking data.
3. Keep the important visible fields: title, price, location, mileage, year, fuel, gearbox and listing age.
4. Add a test in `tests/test_scrapers_parsing.py` that reads the fixture and verifies the parser output.

Fixtures should be small and stable. They are not browser snapshots and must not require Playwright.
