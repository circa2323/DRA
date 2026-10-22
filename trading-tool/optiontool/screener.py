"""Screener: rank many tickers on the books' history-based criteria so you
only pull option chains for the best few.

Criteria and their sources:
* price in a range where near-the-money options can be cheap
  (OTS-096-b, OTS-096-c; the range itself is a user setting)
* liquid, actively traded underlyings (IAO-07-03, OTS-TUT-a; volume floor is a user setting)
* trend direction and strength (IAO-02-06, IAO-02-39, IAO-02-41, IAO-06-11)
* rising momentum, not fading (IAO-07-06, IAO-06-08)
* strength versus its sector/benchmark (IAO-07-01, IAO-06-16, IAO-08-10)
* not overbought on daily and weekly for buyers (IAO-06-01); not oversold for put buyers (IAO-06-06)
* breakouts (IAO-04-23); contracting bands = cheaper options (IAO-05-15, IAO-06-13)
* surprise-volatility potential (OTS-032-b, OTS-096-e)
"""

from __future__ import annotations

import math
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field

from .data import PriceHistory
from .technical import analyze

DEFAULTS = {"min_price": 10.0, "max_price": 50.0, "min_avg_volume": 1_000_000,
            "direction": "both", "benchmark": "SPY", "est_days": 60}


@dataclass
class ScreenRow:
    symbol: str
    ok: bool
    error: str = ""
    price: float | None = None
    avg_volume: float | None = None
    hv: float | None = None
    trend: str = ""
    strength: str = ""
    rel_perf: float | None = None
    benchmark: str = ""
    est_atm_cost: float | None = None
    bias: str = "wait"            # bull / bear / wait
    score: float = 0.0
    passed_filters: bool = False
    reasons: list[dict] = field(default_factory=list)


def _add(row: ScreenRow, pts: float, text: str, *ids: str) -> None:
    row.score += pts
    row.reasons.append({"points": pts, "text": text, "principles": list(ids)})


def score_one(sym: str, hist: PriceHistory, bench: PriceHistory | None, opts: dict) -> ScreenRow:
    row = ScreenRow(sym, True, benchmark=bench.symbol if bench else "")
    if len(hist) < 60:
        return ScreenRow(sym, False, f"only {len(hist)} days of history")
    tv = analyze(hist, bench)
    row.price, row.hv = hist.last, tv.hv
    row.trend, row.strength, row.rel_perf = tv.primary_trend, tv.trend_strength, tv.rel_perf_slope
    vols = [v for v in hist.volumes[-50:] if v]
    row.avg_volume = sum(vols) / len(vols) if vols else 0.0
    if tv.hv:
        # rough at-the-money premium: 0.4 x price x vol x sqrt(t), per contract (estimate from HV)
        row.est_atm_cost = 0.4 * row.price * tv.hv * math.sqrt(opts["est_days"] / 365) * 100

    # ---- filters (user settings, motivated by the books) ----
    in_range = opts["min_price"] <= row.price <= opts["max_price"]
    liquid = row.avg_volume >= opts["min_avg_volume"]
    if not in_range:
        _add(row, 0, f"price {row.price:.2f} outside {opts['min_price']:g}–{opts['max_price']:g} "
                     "(user range for cheap near-the-money options)", "OTS-096-b", "OTS-096-c")
    if not liquid:
        _add(row, 0, f"average volume {row.avg_volume:,.0f} below {opts['min_avg_volume']:,.0f} "
                     "(user floor; thin markets mean wide option spreads)", "IAO-07-03", "OTS-075-a")
    row.passed_filters = in_range and liquid

    # ---- direction ----
    want = opts["direction"]
    if tv.primary_trend == "up" and want in ("both", "bull"):
        row.bias = "bull"
    elif tv.primary_trend == "down" and want in ("both", "bear"):
        row.bias = "bear"
    else:
        _add(row, 0, f"trend {tv.primary_trend}: no directional setup", "IAO-02-06", "IAO-02-07")
        return row
    sign = 1 if row.bias == "bull" else -1
    _add(row, 3, f"{tv.primary_trend}trend ({tv.swing_structure})", "IAO-02-06", "IAO-02-39", "IAO-02-41")

    if tv.adx is not None and tv.adx >= 22:
        _add(row, 1, f"trend confirmed, ADX {tv.adx:.0f} ≥ 22", "IAO-06-11")
    if tv.momentum is not None:
        if tv.momentum_fading:
            _add(row, -2, "momentum fading toward zero — early warning", "IAO-06-08")
        elif sign * tv.momentum > 0:
            _add(row, 1, "momentum agrees with the trend", "IAO-07-06")
    if tv.rel_perf_slope is not None:
        if sign * tv.rel_perf_slope > 0:
            _add(row, 2, f"{'outperforming' if sign > 0 else 'underperforming'} {row.benchmark} "
                         f"({tv.rel_perf_slope:+.1%} over 14 days)", "IAO-07-01", "IAO-06-16", "IAO-08-10")
        else:
            _add(row, -1, f"relative performance vs {row.benchmark} runs against the trade "
                          f"({tv.rel_perf_slope:+.1%})", "IAO-07-01", "IAO-08-10")
    if row.bias == "bull" and tv.overbought_daily and tv.overbought_weekly:
        _add(row, -2, "overbought on daily and weekly — the books overwrite here, not buy", "IAO-06-01")
    if row.bias == "bear" and tv.oversold_daily:
        _add(row, -2, "oversold — countertrend rallies likely", "IAO-06-06")
    if tv.breakout and row.bias == "bull":
        _add(row, 2, "breakout above the 21-day MA and upper band on volume", "IAO-04-23")
    if tv.band_width_contracting:
        _add(row, 1, "Bollinger Bands contracting — options tend to be cheaper", "IAO-05-15", "IAO-06-13")
    if tv.hv is not None and tv.hv >= 0.35:
        _add(row, 1, f"realised volatility {tv.hv:.0%}: room for surprise moves", "OTS-032-b", "OTS-096-e")
    return row


def screen(symbols: list[str], history_fn, opts: dict | None = None,
           sector_benchmarks: dict[str, str] | None = None, workers: int = 8) -> list[ScreenRow]:
    """``history_fn(symbol) -> PriceHistory`` (raises on failure)."""
    o = dict(DEFAULTS)
    o.update(opts or {})
    sector_benchmarks = sector_benchmarks or {}
    bench_syms = {sector_benchmarks.get(s, o["benchmark"]) for s in symbols} - {""}
    cache: dict[str, PriceHistory | Exception] = {}

    def fetch(sym):
        try:
            return sym, history_fn(sym)
        except Exception as e:  # recorded per symbol
            return sym, e

    with ThreadPoolExecutor(max_workers=workers) as ex:
        for sym, h in ex.map(fetch, sorted(set(symbols) | bench_syms)):
            cache[sym] = h

    rows = []
    for sym in symbols:
        h = cache.get(sym)
        if isinstance(h, Exception) or h is None:
            rows.append(ScreenRow(sym, False, str(h)))
            continue
        b = cache.get(sector_benchmarks.get(sym, o["benchmark"]))
        try:
            rows.append(score_one(sym, h, b if isinstance(b, PriceHistory) else None, o))
        except Exception as e:
            rows.append(ScreenRow(sym, False, f"{type(e).__name__}: {e}"))
    rows.sort(key=lambda r: (not r.ok, not r.passed_filters, r.bias == "wait", -r.score))
    return rows


def parse_symbols(text: str) -> tuple[list[str], dict[str, str]]:
    """'ABC, DEF=XLF  GHI' -> (['ABC','DEF','GHI'], {'DEF': 'XLF'})."""
    syms, bench = [], {}
    for tok in text.replace(",", " ").replace(";", " ").split():
        sym, _, b = tok.partition("=")
        sym = sym.strip().upper()
        if not sym:
            continue
        if sym not in syms:
            syms.append(sym)
        if b.strip():
            bench[sym] = b.strip().upper()
    return syms, bench


def rows_to_json(rows: list[ScreenRow]) -> list[dict]:
    return [asdict(r) for r in rows]
