# AUTOHAWK Config Notes

JSON does not support comments, so disabled feature flags are documented here.

## buyer_demand_gate_enabled = false

Status: NEEDS_WORK

`src/buyer_demand_engine.py` is a secondary demand classifier. It tries to classify final listings as `FAST_BUY`, `SAFE_BUY`, `FLIP_BUY`, or `REJECT` using `data/buyer_demand_database.json`, psychological price zones, TUV wording, trust/equipment words, youth-demand rules, diesel mileage nuance, seller type and observed discount.

Current state: the module itself is fairly complete, but the active `src/scanner.py` does not import or call it. The only active references are inside the module. A rollback snapshot shows it previously filtered `CHECK` rows during export and wrote `output/safe_buy_watchlist.txt`.

Reason disabled: it is an extra post-filter on top of the current dealer/Excel selection. Turning it on without a careful integration test could hide useful `CHECK` cars or create a second decision source after the current SQLite/Excel truth path.

Before enabling:
- Reconnect it only after Excel/best_of_scan alignment stays stable.
- Decide whether it is a hard gate or informational watchlist only.
- Add a smoke test showing the same listing count/order impact before and after the gate.

## velocity_gate_enabled = false

Status: NEEDS_WORK

`src/velocity_intelligence.py` estimates whether a listing has a fast-sale shape using `data/velocity_database.json`: fast core models, slow models, psychological price caps, TUV, service/equipment wording, seller motivation, dealer penalty, negative wording, diesel/high-mileage penalties and observed-market discount.

Current state: the evaluator returns a usable `{score, label, export_ok, good, bad}` result, but active `src/scanner.py` does not import or call it. A rollback snapshot shows it was previously applied only to `CHECK` listings during best-lead/export filtering.

Reason disabled: it is a second fast-sale filter that can fight the current dealer engine and observed-market export rules. It needs calibration against sold/removed tracking before becoming a gate again.

Before enabling:
- Run it in shadow mode first and write velocity score/label to logs or report text.
- Compare its `export_ok=false` rejects against cars that later sell/reserve quickly.
- Only then decide if it can safely filter `CHECK` listings.

## sold_speed_gate_enabled = false

Status: DEAD_CODE

The sold-speed gate is not an active module in the current source tree. It exists only inside `rollback_backup_before_may18_20260621_140447/src/scanner.py` as inline helper functions (`_sold_speed_price_cap`, `_sold_speed_gate`) and old config values.

Current state: not connected, not importable as a normal module, and not present in active `src/scanner.py`. The active project does have `src/sold_tracker.py`, but that is different: it tracks whether saved listings disappear/reserve later. It is not the old sold-speed export gate.

Reason disabled: after rollback, this logic was not restored into the active scanner. Keeping it as a flag in `config.json` currently has no effect.

Recommendation:
- Freeze this flag as historical/dead until there is a clean `sold_speed.py` module.
- Do not re-enable by copying old inline scanner code. If revived, rebuild it as a shadow-mode analyzer fed by real `sold_tracker.csv`.

## price_truth.py

Status: NEEDS_WORK

`src/price_truth.py` is a conservative price-discipline layer backed by `data/price_truth_database.json`. It matches brand/model/year/mileage bands and returns `market_low`, `market_mid`, `flip_buy_max`, `watch_max`, `over_retail`, `score_delta`, `score_cap`, reasons, risks and checks.

Current state: the module and database are useful, but active `src/scanner.py` and `src/dealer_engine.py` do not import or call it. A rollback snapshot shows it previously ran before dealer scoring, attached `_price_truth` to the raw listing, capped market price conservatively, and allowed dealer_engine to apply `score_delta`/`score_cap`.

Reason disabled/not connected: this was removed during rollback. Reconnecting it directly would change score and HOT/GOOD/CHECK behavior, so it should not be enabled silently.

Before enabling:
- First add it in informational/shadow mode: include price-truth verdict and buy-zone numbers in `ai_summary` or `what_to_check` without changing score.
- Validate on known examples where AUTOHAWK overestimated market price.
- After validation, optionally let it cap market price or dealer score, but only behind a new explicit flag such as `price_truth_gate_enabled`.
