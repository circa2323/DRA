"""Computes every number the rule checks look at, once, for a trade."""

from __future__ import annotations

from dataclasses import dataclass, field

from . import indicators, pricing
from .data import PriceHistory
from .trade import CONTRACT_SIZE, Leg, Trade


@dataclass
class LegMetrics:
    leg: Leg
    dte: int | None
    market: float              # per-share premium used for the trade
    fair: float | None         # model value at historical volatility
    value_ratio: float | None  # market / fair  (<1 undervalued, >1 overvalued)
    iv: float | None
    delta: float | None
    moneyness: float | None    # (strike - spot)/spot for calls, (spot - strike)/spot for puts; >0 = OTM
    bid_ask_pct: float | None


@dataclass
class Context:
    trade: Trade
    settings: dict
    history: PriceHistory | None = None
    technical: object | None = None        # technical.TechnicalView when history given
    plan: dict = field(default_factory=dict)
    hv_short: float | None = None
    hv_long: float | None = None
    vol: float | None = None               # volatility used for fair value / odds
    vol_source: str = ""
    iv_percentile: float | None = None
    legs: list[LegMetrics] = field(default_factory=list)
    strategy: str = ""
    net_debit: float = 0.0
    max_profit: float = 0.0
    max_loss: float = 0.0
    profit_unlimited: bool = False
    loss_unlimited: bool = False
    breakevens: list[float] = field(default_factory=list)
    prob_profit: float | None = None
    greeks: dict = field(default_factory=dict)
    is_index: bool = False

    # ---- convenience ---------------------------------------------------------
    @property
    def long_options(self) -> list[LegMetrics]:
        return [m for m in self.legs if m.leg.action == "buy"]

    @property
    def short_options(self) -> list[LegMetrics]:
        return [m for m in self.legs if m.leg.action == "sell"]

    @property
    def min_dte(self) -> int | None:
        d = [m.dte for m in self.legs if m.dte is not None]
        return min(d) if d else None

    @property
    def max_dte(self) -> int | None:
        d = [m.dte for m in self.legs if m.dte is not None]
        return max(d) if d else None

    @property
    def is_credit(self) -> bool:
        return self.net_debit < 0

    @property
    def capital_at_risk(self) -> float:
        """Dollars that can be lost (max loss, or the debit when loss is capped)."""
        return abs(self.max_loss)

    def prob_touch(self, level: float) -> float | None:
        if self.vol is None or self.min_dte is None:
            return None
        return pricing.prob_touch(self.trade.spot, level, self.min_dte / 365.0, self.vol)


def build(trade: Trade, settings: dict, history: PriceHistory | None = None,
          plan: dict | None = None, iv_history: list[float] | None = None,
          benchmark: PriceHistory | None = None, technical=None) -> Context:
    ctx = Context(trade=trade, settings=settings, history=history, plan=plan or {})
    ctx.strategy = trade.strategy()
    ctx.is_index = trade.underlying.upper() in settings["index_symbols"]

    # Volatility input: historical volatility, lookback matched to the holding
    # period (OTS-056-a, OTS-056-b).
    if history is not None and len(history) > 30:
        hv20 = indicators.historical_volatility(history.closes, 20)[-1]
        hv30 = indicators.historical_volatility(history.closes, 30)[-1]
        ctx.hv_short = hv30 or hv20
        if len(history) > 101:
            ctx.hv_long = indicators.historical_volatility(history.closes, 100)[-1]
    elif trade.historical_vol:
        ctx.hv_short = trade.historical_vol

    dte_for_vol = trade.min_dte() or 0
    if dte_for_vol > settings["long_hold_dte"] and ctx.hv_long:
        ctx.vol, ctx.vol_source = ctx.hv_long, "100-day historical volatility"
    elif ctx.hv_short:
        ctx.vol, ctx.vol_source = ctx.hv_short, "20-30 day historical volatility"

    for leg in trade.option_legs:
        ctx.legs.append(_leg_metrics(trade, leg, ctx.vol))

    if ctx.vol is None:
        ivs = [m.iv for m in ctx.legs if m.iv]
        if ivs:
            ctx.vol, ctx.vol_source = sum(ivs) / len(ivs), "implied volatility (no history supplied)"

    if iv_history and ctx.legs:
        current = [m.iv for m in ctx.legs if m.iv]
        if current:
            iv_now = sum(current) / len(current)
            below = sum(1 for v in iv_history if v < iv_now)
            ctx.iv_percentile = 100.0 * below / len(iv_history)

    ctx.net_debit = trade.net_debit()
    ctx.max_profit, ctx.max_loss, ctx.profit_unlimited, ctx.loss_unlimited = trade.max_profit_loss()
    ctx.breakevens = trade.breakevens()
    if ctx.vol:
        ctx.prob_profit = trade.prob_profit(ctx.vol)
        ctx.greeks = trade.net_greeks(lambda l: ctx.vol)

    if technical is not None:
        ctx.technical = technical
    elif history is not None:
        from . import technical as tech
        ctx.technical = tech.analyze(history, benchmark)
    return ctx


def _leg_metrics(trade: Trade, leg: Leg, vol: float | None) -> LegMetrics:
    dte = leg.days_to_expiration(trade.as_of)
    years = max(dte or 0, 0) / 365.0
    market = leg.premium()
    iv = leg.iv
    if iv is None and years > 0:
        iv = pricing.implied_volatility(leg.type, market, trade.spot, leg.strike, years,
                                        trade.rate, trade.dividend_yield)
    fair = ratio = delta = None
    if vol:
        g = pricing.black_scholes(leg.type, trade.spot, leg.strike, years, vol,
                                  trade.rate, trade.dividend_yield)
        fair, delta = g.price, g.delta
        ratio = market / fair if fair > 1e-9 else None
    elif iv:
        delta = pricing.black_scholes(leg.type, trade.spot, leg.strike, years, iv,
                                      trade.rate, trade.dividend_yield).delta
    if leg.type == "call":
        moneyness = (leg.strike - trade.spot) / trade.spot
    else:
        moneyness = (trade.spot - leg.strike) / trade.spot
    spread = None
    if leg.bid is not None and leg.ask is not None and leg.ask > 0:
        mid = (leg.bid + leg.ask) / 2
        spread = (leg.ask - leg.bid) / mid if mid > 0 else None
    return LegMetrics(leg, dte, market, fair, ratio, iv, delta, moneyness, spread)
