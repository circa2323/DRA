---
name: schwab-chain-pull
description: Pull option-chain data from Schwab's website into the trading tool's inbox. Use when the user says "pull my Schwab chains", "get today's data for the trading tool", or "do the Claude pull".
---

# Schwab chain pull (read-only)

This fills the trading tool's CSV inbox from Schwab's website while the
Schwab API is not available. **Never place, stage or preview orders, and never
change account settings. Only read pages.**

## 1. Find the request

The trading tool writes `_PULL_REQUEST.json` into its inbox when the user
clicks **Prepare a Claude pull**. The default location is
`Desktop\trading-tool\data\inbox\_PULL_REQUEST.json`. If you can't find it, ask
the user for the inbox path, or for the instruction text the app shows.

The file contains:

| Field | Meaning |
|---|---|
| `inbox` | folder to save every file in |
| `columns` | the exact CSV header row |
| `tickers[]` | one entry per ticker (fields below) |
| `done_file` | write this last |

Each `tickers[]` entry has `symbol`, `expirations` (YYYY-MM-DD),
`strike_band_pct`, `chain_file` and `spot_file`.

## 2. For each ticker

1. Open Schwab's option chain for the symbol: Trade → Options, or the ticker's
   Options tab.
2. Write the stock's current price, just the number, into `spot_file`.
3. For each requested expiration, read calls and puts for every strike within
   ±`strike_band_pct`% of the price:
   - **Greeks view:** bid, ask, IV and open interest.
   - **Regular chain view:** volume.
   - If an expiration isn't listed, use the nearest listed one and note it.
4. Write `chain_file` with exactly the `columns` header, one row per contract:
   - `expiration` as YYYY-MM-DD;
   - `type` as `call` or `put`;
   - plain numbers: no `$`, no thousands commas;
   - `iv` as a percent number (28.5);
   - leave a cell blank when a value isn't shown.
5. Validate the file: bid ≤ ask on every row, and the strikes bracket the
   price. Fix anything that's off before moving on.

## 3. Finish

- After every ticker is saved, write `done_file` containing `done`. The app
  then analyzes automatically.
- Report each file written, plus anything missing or substituted.
- Pull only during market hours (after 9:35 am ET). Pre-market spreads are too
  wide to use.
- If Schwab asks for a login or a security code, stop and ask the user.
