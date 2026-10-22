# optiontool — option trade decisions rooted in the books you load

Every verdict this tool gives cites the book, chapter and page it comes from.
It ships with two books in its principle library:

| Id  | Book | Principles |
|-----|------|-----------|
| OTS | Kenneth R. Trester, *101 Option Trading Secrets* (2004) | 204 |
| IAO | Scott H. Fullman, *Increasing Alpha with Options* (2010) | 295 |

The two books divide the work between them:

* **IAO** supplies the market reading and strategy selection: trend, ADX, oscillators,
  breakouts, relative performance, implied volatility (IV) regime, and hedging.
* **OTS** supplies the gatekeeping: fair value, probability, time limits, position
  size, writer's defenses, spread rules and execution.

Pure Python 3.10+, standard library only, works offline.

## Quick start

```bash
cd trading-tool
python3 examples/make_sample_data.py                       # synthetic demo data
python3 -m optiontool books                                # what's loaded
python3 -m optiontool evaluate examples/long_call.json     # judge a trade
python3 -m optiontool analyze XYZ --history examples/XYZ.csv --benchmark examples/BENCH.csv --iv-regime low
python3 -m optiontool scan XYZ --history examples/XYZ.csv --chain examples/XYZ_chain.csv \
        --account 100000 --option-budget 50000 [--holding]
python3 -m optiontool principles stop loss                 # search the library
python3 -m optiontool explain OTS-024-a IAO-06-11
python3 -m unittest discover -s tests
```

## The web app: one click to pull data and get ideas

```bash
cd trading-tool
cp config.example.json config.json      # once (starts in CSV-folder mode)
python3 -m optiontool serve             # open http://127.0.0.1:8765
```

1. **Settings.** Set your watchlist, benchmarks (e.g. `AAPL=XLK`), the tickers
   you own, account value and option budget.
2. **Pull & analyze.** This does four things:
   * fetches two years of daily bars and the option chain for each ticker;
   * keeps the next 4 monthly expirations 20–160 days out, with strikes within
     ±15% of the price;
   * saves a dated snapshot under `data/snapshots/`;
   * runs the books' scan on each ticker.

   The page opens with **Today's summary**: one row per ticker with a single
   label.

   | Label | Meaning |
   |---|---|
   | **BUY** | passes every book rule |
   | **Buy – Small Budget Logic** | passes every rule except the position-size limits that small-account mode relaxes |
   | **Consider** | other book warnings, listed in the details |
   | **Reject** | breaks a hard rule, or there's no setup today |

   Each BUY and Small Budget row includes an **order ticket**: the exact
   contract (buy or sell to open, quantity, expiration, strike, call or put),
   limit price and the most to pay, cost, max loss, break-even, and the exit
   plan (stock-price stop alert, profit target, close-by date). The ideas,
   ranked candidates, findings with book citations and the technical reading
   sit under **Details**.
3. **History.** Earlier pulls stay viewable. Each pull also records an
   at-the-money IV to `data/iv_log.csv`. After about 60 trading days that log
   gives the tool its own IV history, so it can judge "extremely high or low" IV
   (OTS-057-b, IAO-17-05) without a paid data feed.

The app shows a warning when you pull outside regular trading hours, because
pre-market spreads trip the liquidity and fair-value checks. It listens only on
`127.0.0.1`. `config.json`, tokens and `data/` are git-ignored.

### Screener: find which tickers to pull chains for

On the **Screener** page, paste 20–100 tickers. A ticker can carry its own
sector benchmark, e.g. `ABC=XLF`. The screener downloads each one's price
history and ranks it using the books' history-based criteria, each with its
citation:

| Criterion | Source |
|---|---|
| price in a range where near-the-money options can be cheap | OTS-096-b/c (range is a user setting) |
| liquidity | IAO-07-03, OTS-075-a |
| trend and ADX | IAO-02-06/39/41, IAO-06-11 |
| momentum | IAO-07-06, IAO-06-08 |
| strength vs the benchmark | IAO-07-01, IAO-06-16 |
| overbought / oversold | IAO-06-01, IAO-06-06 |
| breakouts | IAO-04-23 |
| contracting bands | IAO-05-15 |
| surprise-volatility potential | OTS-032-b |

It also estimates what an at-the-money option would cost, so you can see which
names fit your per-trade cap. Tick the best few, click **Add checked to
watchlist**, get their chains into the inbox, and press **Pull & analyze**.
`python3 -m optiontool screen ABC DEF=XLF -v` does the same from a terminal.

### Small-account mode

The books' sizing rules assume a large account: positions under 5% of the
option budget, and the budget at 5–10% of the portfolio. Below roughly $50k,
almost every trade fails them.

Ticking **Small-account mode** in Settings changes three things:
* those two percentage limits become warnings instead of rejections;
* candidates are 1 contract;
* your own **max loss per trade** (dollars) is enforced as a hard limit.

Every other rule still applies. The page shows a banner while the mode is on,
because trading this way departs from the books.

### Aggressive mode, cheaper and shorter trades

These are all in **Settings**:

* **Aggressive mode (off-book).** Selected book rules become warnings instead of
  rejections:
  * buy only below fair value;
  * no long options in their last month;
  * the debit-spread 2× payoff rule;
  * the straddle and credit-spread odds rules;
  * a few soft guidelines, such as the 20% chance-of-profit floor.

  Trades let through this way are labeled **Buy – Aggressive (off-book)**, and
  their ticket lists every rule they break. Rules that protect the account
  never relax: your per-trade cap, the account limit, cash for assignment,
  unlimited risk, writers' stops, and no 3-leg structures.
* **Max option price.** Only buy options at or below this price, e.g. 0.50.
  Long options are sized to as many contracts (up to 3, per OTS-025-d) as fit
  your per-trade cap.
* **Include weekly / short-dated expirations.** Also lower "days out (min)".
  Short-dated tickets get a "take profits fast, close 2 days before expiry"
  exit.
* **Upcoming events** (`TICKER=YYYY-MM-DD`). Adds strangle ideas ahead of
  earnings or announcements while premiums are still low (IAO-16-30).

New strategy types: put credit spreads in uptrends with high IV, sized to the
cap; strangles before events; and expiration-week index long shots (OTS-039-a).

### My trades

Click **I placed this trade — track it** on any order ticket and enter your
actual fill price, or add a single option by hand on the **My trades** page.
Every **Pull & analyze** re-checks open trades against the books' exit rules
and shows one action with its reason and citation:

| Action | When |
|---|---|
| **EXIT** | stop hit (OTS-087-a); 5% trailing stop after a profit (OTS-022-a); close-by date reached (OTS-024-a / OTS-023-b); 2 days to expiry; trend turned against you (OTS-085) |
| **TAKE PROFIT** | option doubled (OTS-021); tripled (OTS-025-c); stock target reached |
| **BUY BACK** | credit trades with 80% of the premium captured (OTS-046-a) |
| **HOLD** | none of the above |

Mark a trade closed with your exit price to keep a running P/L. Held tickers
are pulled automatically. In CSV mode, keep their `_chain.csv` in the inbox.

### Data source: Schwab Trader API (recommended)

1. At https://developer.schwab.com, create an app with the **Market Data
   Production** product.
   * Set the callback URL to `https://127.0.0.1`.
   * Approval usually takes a few days.
2. Put the app key, secret and callback URL into **Settings**, choose *Schwab
   Trader API*, and click **Connect Schwab**.
3. Log in at Schwab. Your browser is then sent to `https://127.0.0.1/?code=…`,
   which won't load; that's expected.
4. Paste that address into the app.
   * The token is saved to `~/.optiontool/schwab_token.json`, readable only by
     you.
   * It refreshes itself automatically.
   * Schwab requires you to log in again every 7 days, and the app shows when
     that's due.

### Data source: CSV folder (no API needed; the default)

The folder named in Settings (default `data/inbox`) holds, for each watchlist
ticker:

| File | What | Needed? |
|---|---|---|
| `SYM_chain.csv` | option chain copied from your broker | yes |
| `SYM_spot.txt` | the stock's live price when you copied the chain, e.g. `343.99` | strongly recommended |
| `SYM.csv` | daily price history | no: downloaded from Yahoo Finance when missing |

* **Without `SYM_spot.txt`**, the tool falls back to yesterday's close and the
  page says so. That mismatch with live option quotes skews fair value and
  probabilities.
* **Benchmark symbols** (e.g. SPY for XLK) need only history, which is
  downloaded automatically.

### Hands-off schedule

`python3 -m optiontool pull` does the same as the button, from a terminal. You
can schedule it with cron (macOS/Linux) or Task Scheduler (Windows) and then
just open the app to read the results. For example, at 9:45 ET on weekdays:

```
45 9 * * 1-5  cd /path/to/trading-tool && python3 -m optiontool pull >> data/pull.log 2>&1
```

## Commands

* **evaluate** — takes a trade JSON (see `examples/*.json`) and returns:
  * a verdict: **REJECT** (a book "must/never" is violated), **CAUTION** (goes
    against guidance) or **ACCEPT**;
  * every finding, each with the principle ids it rests on;
  * a post-entry management plan: scale-out at 2x/3x, 5% trailing stop, time
    limits, buy-back rules and rolls;
  * the quoted sources.

  `--json` gives machine-readable output. `--exit-code` returns 2/1/0 for use in
  scripts.
* **analyze** — gives the technical reading of a price history and the
  strategies the books map to that condition.
* **scan** — builds concrete candidates from an option-chain CSV for each
  strategy idea, runs every candidate through `evaluate`, and ranks them.
* **serve / pull** — the web app and its scheduled-pull equivalent (above).
* **principles / explain / books** — browse the library and see which
  principles the rule checks enforce.

### Inputs

* **Price history:** CSV with `Date,Open,High,Low,Close,Volume` (Yahoo-style).
  It drives historical volatility, which OTS uses as the fair-value input
  (OTS-056-a/b), and the IAO technicals.
* **Option chain:** CSV with `expiration,strike,type,bid,ask[,iv,open_interest,volume]`.
  * Headers are case-insensitive.
  * Dates can be `YYYY-MM-DD` or `MM/DD/YYYY`; type can be `C`/`P`.
  * `$`, thousands commas, `28.5%` and `--` are all accepted.
  * Pull quotes during market hours: pre-market spreads trip the liquidity and
    fair-value checks.
* **IV history (optional):** a list of past IV readings. This enables the IV
  percentile (OTS-057-b, IAO-17-05). Without it, the IV regime is judged as IV vs
  historical volatility (IAO-07-11).
* **Trade `plan`:**
  * `stop`, `target` — on the underlying;
  * `option_buying_portfolio`;
  * `commission_per_contract`;
  * `confidence` — capped by the 60% rule;
  * `cash_available`;
  * `adjustment`.

## How decisions stay grounded

1. **`knowledge/*.json`** holds the books' principles. Each entry has an id, page,
   a short verbatim quote, and a structured rule. See `knowledge/SCHEMA.md`.
2. **`optiontool/rules.py`** holds the rule checks. Each check is registered with
   the principle ids it enforces. Its numbers are the books' own numbers, e.g.:
   * < 30 days for long options (OTS-024-a);
   * > 90% probability of success for naked writes (OTS-044-a);
   * ≥ 200% maximum return on debit spreads (OTS-073-a);
   * ADX 22 (IAO-06-11);
   * delta 0.40–0.55 for hedge puts (IAO-14-07).
3. **The engine refuses to run** if any finding cites an id that isn't in a
   loaded book. A unit test checks the same for every id in the code.
4. **Where a book is vague** (e.g. "most of the premium", "extremely high IV"),
   the threshold lives in `optiontool/settings.py`. The finding says *(user
   setting)*, so you always know what is the author's and what is yours.
   Override these with `--settings my.json`.

## Adding a book

Follow `knowledge/SCHEMA.md`:

1. Extract the text with `pdftotext`.
2. Write `knowledge/<ID>.json`.
3. Its principles become searchable immediately.
4. They start driving decisions once a check in `rules.py` (or the advisor in
   `advisor.py`) cites them.

The book PDFs and full text are deliberately **not** committed. The `books/`
folder is git-ignored.

## Layout

```
optiontool/
  pricing.py     Black-Scholes, greeks, implied vol, probability (above / below / touch)
  indicators.py  SMA/EMA, RSI, stochastic, CCI, Bollinger, ADX, momentum, OBV, MFI, HV, relative performance
  technical.py   IAO trend / oscillator / breakout / seasonality reading
  trade.py       legs, strategy classification, payoff, break-evens, probability of profit
  context.py     computes every metric once per trade
  rules.py       rule checks (OTS + IAO), each citing principles
  engine.py      runs the checks, verdict, management plan
  advisor.py     condition → strategy ideas, chain scanning and ranking
  report.py      text / JSON output
  providers.py   Schwab Trader API + CSV-folder data sources, monthly-expiration picker
  pipeline.py    pull → snapshot → analyze; IV log; market-hours check
  screener.py    rank many tickers on history-based book criteria
  ticket.py      action labels and order tickets
  positions.py   My trades: exit-rule checks on open positions
  webapp.py      local web app (stdlib http.server)
  cli.py         command line
knowledge/       OTS.json, IAO.json, SCHEMA.md
examples/        sample trades + synthetic data generator
tests/
```

## Limits — read before trading

* **The sample data is synthetic.** It is random, seeded prices, not real market
  data. Use real data from your broker or a data vendor.
* **Probabilities are model estimates**, not guarantees:
  * they use Black-Scholes with a lognormal distribution and zero drift;
  * "touch" uses the reflection-principle approximation, not a Monte Carlo
    simulation;
  * fat tails are real (OTS-032-a), so the model understates the chance of
    extreme moves.
* **Chart patterns are not detected automatically.** That covers
  head-and-shoulders, triangles, cup-and-handle, candlesticks and channels. The
  tool reads trend, ADX, oscillators, bands, momentum, volume and relative
  performance. Many IAO pattern principles are in the library for reference
  only.
* **Portfolio-level rules are reported, not enforced across positions.** This
  includes the put/call balance, diversification over time and annual budgets.
  The tool only knows what you pass in.
* **This is a decision aid built from two books' opinions, not financial
  advice.** The books themselves stress that past performance does not
  guarantee results.
