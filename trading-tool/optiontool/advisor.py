"""Strategy selection: from the market's condition to candidate trades.

The mapping from technical condition and premium level to strategy is IAO's
(ch. 2, 4, 6, 8, 14-16); the filters that every candidate must then survive
are OTS's buying/writing rules, applied by running each candidate through the
same engine used for ``evaluate``.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from . import context, engine
from .data import PriceHistory
from .knowledge import Library
from .technical import TechnicalView
from .trade import Leg, Trade


@dataclass
class Idea:
    strategy: str          # trade.classify() name, or 'hedge:<name>' for holders
    rationale: str
    principles: tuple[str, ...]


def ideas(tv: TechnicalView, iv_regime: str | None, holding_stock: bool = False) -> list[Idea]:
    out: list[Idea] = []
    add = lambda s, why, *ids: out.append(Idea(s, why, ids))
    t, strength = tv.primary_trend, tv.trend_strength
    hi, lo = iv_regime == "high", iv_regime == "low"

    if tv.breakout and not hi:
        add("long_call", "Breakout from a base with modest IV: long-dated or ITM calls instead of stock; "
            "exit if the breakout fails.", "IAO-01-05", "IAO-04-23", "IAO-16-10", "IAO-01-09")
    if t == "up":
        if tv.overbought_daily and tv.overbought_weekly:
            if holding_stock:
                add("covered_call", "Uptrend, overbought on daily and weekly: overwrite 3–9 weeks out.",
                    "IAO-06-01", "IAO-02-19")
            if lo:
                add("long_put", "Overbought with low IV: slightly OTM put ≥ 4 weeks once the weekly gives a "
                    "sell signal (short-term countertrend).", "IAO-06-04", "IAO-02-25")
        elif tv.overbought_daily and holding_stock:
            add("covered_call", "Overbought on the daily only: 15–45 day covered call.", "IAO-06-02")
        elif tv.momentum_fading:
            if holding_stock:
                add("covered_call", "Rising trend with fading momentum: write OTM calls.", "IAO-16-17", "IAO-06-08")
            else:
                add("none", "Rising trend but momentum is fading toward zero — the early warning of a trend "
                    "change. Wait for momentum to turn up before buying.", "IAO-06-08", "IAO-07-06", "OTS-031")
        else:
            if hi:
                add("short_put", "Uptrend with high IV: sell OTM puts instead of buying shares.",
                    "IAO-16-04", "IAO-15-13", "IAO-00-03")
                add("vertical_spread:bull_call", "Uptrend with high IV: bull call spread (sell the OTM leg "
                    "against the one bought).", "IAO-08-02", "OTS-072-b")
            else:
                add("long_call", "Uptrend with low premiums: buy calls (3–6 month horizon).",
                    "IAO-02-02", "IAO-02-11", "IAO-00-03")
                add("vertical_spread:bull_call", "Uptrend channel, rising momentum, IV below HV: bull call "
                    "spread.", "IAO-08-06")
    elif t == "down":
        if tv.oversold_daily:
            add("none", "Downtrend but oversold: countertrend rallies likely — wait.", "IAO-06-06")
        elif hi:
            add("vertical_spread:bear_put", "Downtrend with high IV: bear put spread to offset premium.",
                "IAO-02-03", "IAO-14-15")
            add("vertical_spread:bear_call", "Downtrend with high IV: call credit spread (sell premium).",
                "IAO-02-03", "IAO-00-03", "OTS-097-a")
        else:
            add("long_put", "Downtrend with low premiums: buy puts rather than shorting stock.",
                "IAO-01-14", "IAO-01-15", "IAO-13-02", "OTS-034")
    else:  # sideways
        if strength in ("weak", "consolidating") and lo:
            add("straddle", "Narrowing consolidation with low IV: straddle/strangle ahead of the breakout "
                "or an event.", "IAO-06-13", "IAO-06-14", "IAO-11-08")
        if tv.support is not None and tv.last <= tv.support * 1.03:
            add("long_call", "Range trade: short-term call on a test of support.", "IAO-02-38")
        if tv.resistance is not None and tv.last >= tv.resistance * 0.97:
            add("long_put", "Range trade: short-term put on a test of resistance.", "IAO-02-38")
        if hi and strength != "consolidating":
            add("short_put", "Sideways with high IV: sell premium early in the range, not late in a narrowing "
                "triangle.", "IAO-02-37", "IAO-16-13")
    if holding_stock:
        if lo:
            add("protective_put", "Hedging with low IV: 2–3 month ATM protective put.", "IAO-14-10",
                "IAO-14-11", "IAO-02-24", "OTS-037-a")
        elif hi:
            add("collar", "Hedging with high IV: collar (sell an OTM call to pay for the put).",
                "IAO-14-12", "IAO-15-01")
    if not out:
        add("none", "No setup from the books' playbook fits right now. No edge, no trade.", "OTS-055-b", "OTS-031")
    return out


# ---------------------------------------------------------------- chain scanning
def load_chain(path: str | Path) -> list[Leg]:
    """CSV columns: expiration,strike,type,bid,ask[,iv,open_interest,volume]."""
    legs = []
    with Path(path).open(newline="") as fh:
        for r in csv.DictReader(fh):
            num = lambda k: float(r[k]) if r.get(k) not in (None, "") else None
            iv = num("iv")
            if iv is not None and iv > 3:
                iv /= 100.0
            legs.append(Leg("buy", r["type"].strip().lower(), 1, strike=float(r["strike"]),
                            expiration=r["expiration"].strip(), bid=num("bid"), ask=num("ask"), iv=iv,
                            open_interest=int(num("open_interest") or 0) if r.get("open_interest") else None,
                            volume=int(num("volume") or 0) if r.get("volume") else None))
    return legs


def _clone(leg: Leg, action: str, qty: int) -> Leg:
    price = leg.ask if action == "buy" else leg.bid
    return Leg(action, leg.type, qty, leg.strike, leg.expiration, price, leg.bid, leg.ask,
               leg.iv, leg.open_interest, leg.volume)


def candidates(strategy: str, chain: list[Leg], spot: float, qty: int = 3) -> list[list[Leg]]:
    """Concrete leg sets for a strategy name from ``ideas``."""
    calls = [l for l in chain if l.type == "call" and l.bid is not None and l.ask]
    puts = [l for l in chain if l.type == "put" and l.bid is not None and l.ask]
    out: list[list[Leg]] = []
    near = lambda l: abs(l.strike - spot) / spot <= 0.10
    if strategy == "long_call":
        out = [[_clone(c, "buy", qty)] for c in calls if near(c)]
    elif strategy == "long_put":
        out = [[_clone(p, "buy", qty)] for p in puts if near(p)]
    elif strategy == "short_put":
        out = [[_clone(p, "sell", 1)] for p in puts if p.strike < spot]
    elif strategy == "straddle":
        for c in calls:
            for p in puts:
                if c.expiration == p.expiration and c.strike == p.strike and near(c):
                    out.append([_clone(c, "buy", 1), _clone(p, "buy", 1)])
    elif strategy in ("covered_call", "protective_put", "collar"):
        stock = Leg("buy", "stock", 100, price=spot)
        otm_calls = [c for c in calls if spot < c.strike <= spot * 1.15]
        hedge_puts = [p for p in puts if spot * 0.90 <= p.strike <= spot * 1.02]
        if strategy == "covered_call":
            out = [[stock, _clone(c, "sell", 1)] for c in otm_calls]
        elif strategy == "protective_put":
            out = [[stock, _clone(p, "buy", 1)] for p in hedge_puts]
        else:
            out = [[stock, _clone(p, "buy", 1), _clone(c, "sell", 1)]
                   for p in hedge_puts for c in otm_calls if c.expiration == p.expiration]
    elif strategy.startswith("vertical_spread:"):
        kind = strategy.split(":")[1]
        pool = calls if kind in ("bull_call", "bear_call") else puts
        by_exp: dict[str, list[Leg]] = {}
        for l in pool:
            by_exp.setdefault(l.expiration, []).append(l)
        for exp, ls in by_exp.items():
            ls.sort(key=lambda l: l.strike)
            for i, a in enumerate(ls):
                for b in ls[i + 1:]:
                    lo_leg, hi_leg = a, b
                    if kind == "bull_call" and near(lo_leg):
                        out.append([_clone(lo_leg, "buy", 1), _clone(hi_leg, "sell", 1)])
                    elif kind == "bear_put" and near(hi_leg):
                        out.append([_clone(hi_leg, "buy", 1), _clone(lo_leg, "sell", 1)])
                    elif kind == "bear_call" and lo_leg.strike > spot:
                        out.append([_clone(lo_leg, "sell", 1), _clone(hi_leg, "buy", 1)])
    return out


@dataclass
class Scored:
    idea: Idea
    decision: engine.Decision
    score: float


def scan(symbol: str, hist: PriceHistory, chain: list[Leg], library: Library, settings: dict,
         as_of: date, account_value: float | None = None, plan_defaults: dict | None = None,
         holding_stock: bool = False, benchmark: PriceHistory | None = None,
         iv_history: list[float] | None = None, limit: int = 5):
    """Build candidates for each idea, evaluate them all, return the best."""
    from .technical import analyze
    from .rules import iv_regime
    spot = hist.last
    tv = analyze(hist, benchmark, as_of)
    # Premium regime from an ATM option of the chain.
    atm = min(chain, key=lambda l: abs(l.strike - spot)) if chain else None
    probe = Trade(symbol, spot, [_clone(atm, "buy", 1)], as_of) if atm else None
    regime = None
    if probe:
        regime, _ = iv_regime(context.build(probe, settings, hist, iv_history=iv_history, benchmark=benchmark))
    found = ideas(tv, regime, holding_stock)
    results: list[Scored] = []
    for idea in found:
        for legs in candidates(idea.strategy, chain, spot):
            trade = Trade(symbol, spot, legs, as_of, account_value=account_value)
            plan = dict(plan_defaults or {})
            plan.setdefault("stop", _default_stop(trade, tv))
            plan.setdefault("target", _default_target(trade, tv))
            ctx = context.build(trade, settings, hist, plan, iv_history, benchmark)
            ctx.technical = tv
            dec = engine.evaluate(ctx, library)
            results.append(Scored(idea, dec, _score(dec)))
    results.sort(key=lambda r: -r.score)
    return tv, regime, found, results[:limit]


def _default_stop(trade: Trade, tv: TechnicalView) -> float:
    """Stop on the underlying at chart support/resistance (OTS-012-a, IAO-03-13)."""
    long_stock = any(l.type == "stock" and l.action == "buy" for l in trade.legs)
    bear = not long_stock and (any(l.type == "put" and l.action == "buy" for l in trade.legs) or
                               any(l.type == "call" and l.action == "sell" for l in trade.legs))
    if bear:
        return round(max(tv.resistance or trade.spot * 1.05, trade.spot * 1.02), 2)
    return round(min(tv.support or trade.spot * 0.95, trade.spot * 0.98), 2)


def _default_target(trade: Trade, tv: TechnicalView) -> float:
    long_stock = any(l.type == "stock" and l.action == "buy" for l in trade.legs)
    bear = not long_stock and any(l.type == "put" and l.action == "buy" for l in trade.legs)
    rng = (tv.resistance or trade.spot) - (tv.support or trade.spot)
    return round(trade.spot - rng if bear else trade.spot + rng, 2)


def _score(dec: engine.Decision) -> float:
    s = {"ACCEPT": 100, "CAUTION": 50, "REJECT": 0}[dec.verdict]
    s -= 15 * len(dec.blocks) + 5 * len(dec.warnings)
    c = dec.context
    s += 10 * sum(1 for f in dec.findings if f.severity == "PASS")
    if c.prob_profit is not None:
        s += 20 * c.prob_profit
    for m in c.long_options:
        if m.value_ratio:
            s += 20 * (1 - m.value_ratio)
    for m in c.short_options:
        if m.value_ratio:
            s += 20 * (m.value_ratio - 1)
    return s
