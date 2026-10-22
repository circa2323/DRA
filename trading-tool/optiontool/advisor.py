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


def ideas(tv: TechnicalView, iv_regime: str | None, holding_stock: bool = False,
          event_date: str | None = None, short_dated: bool = False, is_index: bool = False) -> list[Idea]:
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
                add("vertical_spread:bull_put", "Uptrend with high IV: put credit spread — sell an OTM put and "
                    "buy a lower one, so the risk is capped.", "IAO-16-04", "IAO-15-13", "OTS-067", "OTS-097-b")
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
            add("vertical_spread:bull_put", "Sideways with high IV: put credit spread with capped risk.",
                "IAO-02-37", "OTS-067", "OTS-097-b")
    if event_date and iv_regime != "high":
        add("strangle", f"Event on {event_date} with premiums not yet inflated: buy a strangle (OTM call + "
            "OTM put) weeks ahead, before IV rises.", "IAO-16-30", "IAO-06-14", "IAO-11-08")
    if short_dated and is_index and t in ("up", "down"):
        side = "call" if t == "up" else "put"
        add(f"long_shot:{side}", f"Expiration-week long shot: a near-the-money index {side} under $1 with "
            "days left — small, fast, take profits immediately.", "OTS-039-a", "OTS-039-b")
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
def _num(v):
    """Parse '1,234.50', '$2.10', '28.5%', '--' and blanks."""
    if v is None:
        return None
    v = str(v).strip().replace(",", "").replace("$", "")
    pct = v.endswith("%")
    v = v.rstrip("%")
    if v in ("", "-", "--", "N/A", "n/a"):
        return None
    x = float(v)
    return x / 100.0 if pct else x


def _iso(d: str) -> str:
    """Accept YYYY-MM-DD, MM/DD/YYYY, M/D/YY or 'Nov 20 2026' style dates."""
    from datetime import datetime
    d = d.strip()
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y", "%b %d %Y", "%d %b %Y", "%b %d, %Y", "%Y%m%d"):
        try:
            return datetime.strptime(d, fmt).date().isoformat()
        except ValueError:
            pass
    raise ValueError(f"unrecognised expiration date {d!r}")


def load_chain(path: str | Path) -> list[Leg]:
    """CSV columns: expiration,strike,type,bid,ask[,iv,open_interest,volume].

    Header names are case-insensitive; type may be call/put/C/P; IV may be a
    decimal (0.28), a percent number (28) or a percent string ('28%').
    """
    legs = []
    with Path(path).open(newline="", encoding="utf-8-sig") as fh:
        for raw in csv.DictReader(fh):
            r = {(k or "").strip().lower().replace(" ", "_"): v for k, v in raw.items()}
            typ = (r.get("type") or "").strip().lower()
            typ = {"c": "call", "p": "put", "calls": "call", "puts": "put"}.get(typ, typ)
            if typ not in ("call", "put"):
                continue
            iv = _num(r.get("iv"))
            if iv is not None and iv > 3:
                iv /= 100.0
            oi, vol = _num(r.get("open_interest")), _num(r.get("volume"))
            legs.append(Leg("buy", typ, 1, strike=_num(r["strike"]), expiration=_iso(r["expiration"]),
                            bid=_num(r.get("bid")), ask=_num(r.get("ask")), iv=iv or None,
                            open_interest=int(oi) if oi is not None else None,
                            volume=int(vol) if vol is not None else None))
    return legs


def _clone(leg: Leg, action: str, qty: int) -> Leg:
    price = leg.ask if action == "buy" else leg.bid
    return Leg(action, leg.type, qty, leg.strike, leg.expiration, price, leg.bid, leg.ask,
               leg.iv, leg.open_interest, leg.volume)


def _qty(leg: Leg, qty: int, cap: float | None) -> int:
    """Up to ``qty`` contracts (3 per OTS-025-d), as many as fit the per-trade cap."""
    if not cap or not leg.ask:
        return qty
    return max(1, min(qty, int(cap // (leg.ask * 100))))


def candidates(strategy: str, chain: list[Leg], spot: float, qty: int = 3, cap: float | None = None,
               max_price: float | None = None, as_of: date | None = None) -> list[list[Leg]]:
    """Concrete leg sets for a strategy name from ``ideas``.

    Pruned with the books' structural rules so dense chains stay tractable:
    bought legs close to the money (OTS-073-d, OTS-096-c), sold debit-spread
    legs worth at least .40 (OTS-073-b), hedge puts near the money (IAO-14-07),
    covered/collar calls 2.5-15% out of the money (IAO-02-19, IAO-12-01).
    """
    live = [l for l in chain if l.bid is not None and l.ask and l.ask > 0]
    calls = sorted((l for l in live if l.type == "call"), key=lambda l: (l.expiration, l.strike))
    puts = sorted((l for l in live if l.type == "put"), key=lambda l: (l.expiration, l.strike))
    out: list[list[Leg]] = []
    dist = lambda l: abs(l.strike - spot) / spot
    near = lambda l: dist(l) <= 0.10
    atm = lambda l: dist(l) <= 0.03
    cheap = lambda l: max_price is None or l.ask <= max_price
    wide = lambda l: dist(l) <= 0.15
    if strategy == "long_call":
        out = [[_clone(c, "buy", _qty(c, qty, cap))] for c in calls if wide(c) and cheap(c)]
    elif strategy == "long_put":
        out = [[_clone(p, "buy", _qty(p, qty, cap))] for p in puts if wide(p) and cheap(p)]
    elif strategy.startswith("long_shot:"):
        side = calls if strategy.endswith("call") else puts
        today = as_of or date.today()
        out = [[_clone(l, "buy", _qty(l, qty, cap))] for l in side
               if 0 < (date.fromisoformat(l.expiration) - today).days <= 7 and atm(l) and l.ask < 1.0]
    elif strategy == "strangle":
        cs = [c for c in calls if spot * 1.02 <= c.strike <= spot * 1.10]
        ps = [p for p in puts if spot * 0.90 <= p.strike <= spot * 0.98]
        out = [[_clone(c, "buy", 1), _clone(p, "buy", 1)] for c in cs for p in ps if c.expiration == p.expiration]
    elif strategy == "short_put":
        out = [[_clone(p, "sell", 1)] for p in puts if spot * 0.80 <= p.strike < spot]
    elif strategy == "straddle":
        pmap = {(p.expiration, p.strike): p for p in puts}
        out = [[_clone(c, "buy", 1), _clone(pmap[(c.expiration, c.strike)], "buy", 1)]
               for c in calls if atm(c) and (c.expiration, c.strike) in pmap]
    elif strategy in ("covered_call", "protective_put", "collar"):
        stock = Leg("buy", "stock", 100, price=spot)
        otm_calls = [c for c in calls if spot * 1.025 <= c.strike <= spot * 1.15]
        hedge_puts = [p for p in puts if spot * 0.95 <= p.strike <= spot * 1.02]
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
        for ls in by_exp.values():
            for a in ls:
                for b in ls:
                    if b.strike <= a.strike or (b.strike - a.strike) / spot > 0.15:
                        continue
                    if kind == "bull_call" and atm(a) and b.bid >= 0.40:
                        out.append([_clone(a, "buy", 1), _clone(b, "sell", 1)])
                    elif kind == "bear_put" and atm(b) and a.bid >= 0.40:
                        out.append([_clone(b, "buy", 1), _clone(a, "sell", 1)])
                    elif kind == "bear_call" and a.strike > spot and (b.strike - a.strike) / spot <= 0.05:
                        out.append([_clone(a, "sell", 1), _clone(b, "buy", 1)])
                    elif kind == "bull_put" and b.strike < spot and (b.strike - a.strike) / spot <= 0.05:
                        out.append([_clone(b, "sell", 1), _clone(a, "buy", 1)])
    return out


def quick_pl(legs: list[Leg], spot: float) -> tuple[float, float, bool]:
    """Exact expiration max profit / max loss of a same-expiry structure, cheaply:
    the payoff is piecewise linear with kinks only at the strikes."""
    t = Trade("_", spot, legs)
    strikes = [l.strike for l in legs if l.is_option]
    far = max([spot] + strikes) * 3
    pts = [0.0] + strikes + [far]
    vals = [t.payoff_at(x) for x in pts]
    loss_unlimited = t.payoff_at(far * 2) < vals[-1] - 1e-9
    return max(vals), min(vals), loss_unlimited


def _feasible(legs: list[Leg], spot: float, cap: float | None, account: float | None) -> bool:
    """Drop candidates that cannot make money, or that the account cannot hold:
    loss above the per-trade cap, more than the account, or a short put the
    account cannot pay for if assigned (OTS-052-a)."""
    if any(l.type == "stock" for l in legs):
        return True
    if len({l.expiration for l in legs if l.is_option}) > 1:
        return True
    best, worst, unlimited = quick_pl(legs, spot)
    if best <= 0:
        return False
    if unlimited:
        return cap is None and account is None
    loss = -worst
    if cap is not None and loss > cap:
        return False
    if account is not None and loss > account:
        return False
    if account is not None:
        for l in legs:
            if l.type == "put" and l.action == "sell" and len(legs) == 1 and l.strike * 100 * l.quantity > account:
                return False
    return True


def _prescore(legs: list[Leg], spot: float, as_of: date, vol: float | None) -> float:
    """Cheap edge estimate (model value minus price, signed) used to trim candidates."""
    from .pricing import black_scholes
    if not vol:
        return 0.0
    edge = 0.0
    for l in legs:
        if not l.is_option:
            continue
        t = max((date.fromisoformat(l.expiration) - as_of).days, 0) / 365
        fair = black_scholes(l.type, spot, l.strike, t, vol).price
        edge += l.sign * (fair - l.premium()) * l.quantity
    return edge


@dataclass
class Scored:
    idea: Idea
    decision: engine.Decision
    score: float


def scan(symbol: str, hist: PriceHistory, chain: list[Leg], library: Library, settings: dict,
         as_of: date, account_value: float | None = None, plan_defaults: dict | None = None,
         holding_stock: bool = False, benchmark: PriceHistory | None = None,
         iv_history: list[float] | None = None, limit: int = 5, per_idea: int = 150,
         contracts: int = 3, event_date: str | None = None, short_dated: bool = False,
         max_option_price: float | None = None):
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
        regime, _ = iv_regime(context.build(probe, settings, hist, iv_history=iv_history,
                                            benchmark=benchmark, technical=tv))
    found = ideas(tv, regime, holding_stock, event_date, short_dated,
                  symbol.upper() in settings.get("index_symbols", []))
    results: list[Scored] = []
    for idea in found:
        cap = (plan_defaults or {}).get("max_trade_risk")
        cands = candidates(idea.strategy, chain, spot, contracts, cap, max_option_price, as_of)
        cands = [c for c in cands if _feasible(c, spot, cap, account_value)]
        if len(cands) > per_idea:
            cands.sort(key=lambda legs: -_prescore(legs, spot, as_of, tv.hv))
            cands = cands[:per_idea]
        for legs in cands:
            trade = Trade(symbol, spot, legs, as_of, account_value=account_value)
            plan = dict(plan_defaults or {})
            if "stop" not in plan:
                plan["stop"] = _default_stop(trade, tv)
            if "target" not in plan:
                plan["target"] = _default_target(trade, tv)
            if plan["stop"] is None and trade.strategy() in ("straddle", "strangle"):
                be = trade.breakevens()
                if len(be) == 2:
                    plan["target_down"], plan["target_up"] = be
            ctx = context.build(trade, settings, hist, plan, iv_history, benchmark, technical=tv)
            dec = engine.evaluate(ctx, library)
            results.append(Scored(idea, dec, _score(dec)))
    results.sort(key=lambda r: -r.score)
    # best candidate of each idea first, so one strategy can't crowd out the others
    picked, seen = [], set()
    for r in results:
        if r.idea.strategy not in seen:
            seen.add(r.idea.strategy)
            picked.append(r)
    picked.sort(key=lambda r: -r.score)
    picked += [r for r in results if r not in picked]
    return tv, regime, found, picked[:limit]


def _bias(trade: Trade) -> int:
    """+1 bullish, -1 bearish, 0 neutral — from how the payoff moves with the stock."""
    up, dn = trade.payoff_at(trade.spot * 1.5), trade.payoff_at(trade.spot * 0.5)
    if abs(up - dn) < 1e-6 or (up > 0 and dn > 0 and min(up, dn) > 0.3 * max(up, dn)):
        return 0
    return 1 if up > dn else -1


def _default_stop(trade: Trade, tv: TechnicalView) -> float | None:
    """Stop on the underlying at chart support/resistance (OTS-012-a, IAO-03-13).
    None for neutral long-premium positions, whose risk is the premium paid."""
    b = _bias(trade)
    if b == 0:
        return None
    shorts = [l for l in trade.option_legs if l.action == "sell"]
    if shorts and trade.net_debit() < 0:
        # credit trades: stop on the out-of-the-money side of the short strike (OTS-097-c)
        k = shorts[0].strike
        if b > 0:   # short put
            return round(min(max(k * 1.01, (k + trade.spot) / 2), trade.spot * 0.995), 2)
        return round(max(min(k * 0.99, (k + trade.spot) / 2), trade.spot * 1.005), 2)
    if b < 0:
        return round(max(tv.resistance or trade.spot * 1.05, trade.spot * 1.02), 2)
    return round(min(tv.support or trade.spot * 0.95, trade.spot * 0.98), 2)


def _default_target(trade: Trade, tv: TechnicalView) -> float | None:
    b = _bias(trade)
    rng = (tv.resistance or trade.spot) - (tv.support or trade.spot)
    if b == 0:
        return None
    return round(trade.spot - rng if b < 0 else trade.spot + rng, 2)


def _score(dec: engine.Decision) -> float:
    s = {"ACCEPT": 100, "CAUTION": 50, "REJECT": 0}[dec.verdict]
    s -= 15 * len(dec.blocks) + 5 * len(dec.warnings)
    c = dec.context
    s += 10 * sum(1 for f in dec.findings if f.severity == "PASS")
    if c.prob_profit is not None:
        s += 20 * c.prob_profit
    # value edge, clamped: a 5-cent option "worth" 0.1 cent is not 5,000% better
    clamp = lambda x: max(-1.0, min(1.0, x))
    for m in c.long_options:
        if m.value_ratio:
            s += 20 * clamp(1 - m.value_ratio)
    for m in c.short_options:
        if m.value_ratio:
            s += 20 * clamp(m.value_ratio - 1)
    return s
