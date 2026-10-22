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
