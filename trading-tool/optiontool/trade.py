"""Trade description, strategy classification and expiration analysis.

"Analyze your option position" (OTS secret 28) and "profit boxes" (secret 29)
call for knowing the payoff, break-evens, maximum risk and odds of every
position before entering it. This module computes those numbers.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date

from . import pricing

CONTRACT_SIZE = 100


@dataclass
class Leg:
    action: str            # "buy" or "sell"
    type: str              # "call", "put" or "stock"
    quantity: int          # contracts (or shares for stock)
    strike: float | None = None
    expiration: str | None = None   # ISO date
    price: float | None = None      # per share premium (or stock cost)
    bid: float | None = None
    ask: float | None = None
    iv: float | None = None         # implied volatility, decimal
    open_interest: int | None = None
    volume: int | None = None

    @property
    def sign(self) -> int:
        return 1 if self.action == "buy" else -1

    @property
    def is_option(self) -> bool:
        return self.type in ("call", "put")

    @property
    def multiplier(self) -> int:
        return CONTRACT_SIZE if self.is_option else 1

    def mid(self) -> float | None:
        if self.bid is not None and self.ask is not None and self.ask > 0:
            return (self.bid + self.ask) / 2.0
        return self.price

    def premium(self) -> float:
        p = self.price if self.price is not None else self.mid()
        if p is None:
            raise ValueError(f"leg {self} needs a price or bid/ask")
        return p

    def days_to_expiration(self, as_of: date) -> int | None:
        if not self.expiration:
            return None
        return (date.fromisoformat(self.expiration) - as_of).days

    def payoff(self, s: float) -> float:
        """Value at expiration per unit, minus cost, times sign and size."""
        if self.type == "stock":
            value = s
        elif self.type == "call":
            value = max(0.0, s - self.strike)
        else:
            value = max(0.0, self.strike - s)
        return self.sign * (value - self.premium()) * self.quantity * self.multiplier


@dataclass
class Trade:
    underlying: str
    spot: float
    legs: list[Leg]
    as_of: date = field(default_factory=date.today)
    account_value: float | None = None
    outlook: str | None = None       # bullish / bearish / neutral / volatile
    rate: float = 0.04
    dividend_yield: float = 0.0
    historical_vol: float | None = None
    open_positions: int = 0          # other positions already in the account
    capital_at_risk_elsewhere: float = 0.0
    earnings_date: str | None = None
    ex_dividend_date: str | None = None
    notes: str = ""

    @classmethod
    def from_dict(cls, d: dict) -> "Trade":
        legs = [Leg(**leg) for leg in d["legs"]]
        as_of = date.fromisoformat(d["as_of"]) if d.get("as_of") else date.today()
        known = {k: v for k, v in d.items() if k in cls.__dataclass_fields__
                 and k not in ("legs", "as_of")}
        return cls(legs=legs, as_of=as_of, **known)

    @property
    def option_legs(self) -> list[Leg]:
        return [l for l in self.legs if l.is_option]

    def min_dte(self) -> int | None:
        days = [l.days_to_expiration(self.as_of) for l in self.option_legs]
        days = [d for d in days if d is not None]
        return min(days) if days else None

    def years(self, leg: Leg) -> float:
        d = leg.days_to_expiration(self.as_of)
        return max(d, 0) / 365.0 if d is not None else 0.0

    def net_debit(self) -> float:
        """Positive = cash paid, negative = credit received."""
        return sum(l.sign * l.premium() * l.quantity * l.multiplier for l in self.legs)

    def strategy(self) -> str:
        return classify(self.legs)

    # ---- expiration analysis -------------------------------------------------
    def _grid(self, n: int = 2001) -> list[float]:
        strikes = [l.strike for l in self.option_legs if l.strike]
        hi = max([self.spot] + strikes) * 3.0
        return [hi * i / (n - 1) for i in range(n)]

    def payoff_at(self, s: float) -> float:
        return sum(l.payoff(s) for l in self.legs)

    def max_profit_loss(self) -> tuple[float, float, bool, bool]:
        """(max profit, max loss, profit unlimited?, loss unlimited?)."""
        grid = self._grid()
        vals = [self.payoff_at(s) for s in grid]
        # slope beyond the last grid point tells whether risk is open-ended
        tail = vals[-1] - vals[-2]
        return (max(vals), min(vals), tail > 1e-9, tail < -1e-9)

    def breakevens(self) -> list[float]:
        grid = self._grid()
        out = []
        prev_s, prev_v = grid[0], self.payoff_at(grid[0])
        for s in grid[1:]:
            v = self.payoff_at(s)
            if (prev_v < 0 <= v) or (prev_v > 0 >= v):
                out.append(prev_s + (s - prev_s) * (-prev_v) / (v - prev_v) if v != prev_v else s)
            prev_s, prev_v = s, v
        return [round(b, 2) for b in out]

    def prob_profit(self, vol: float) -> float:
        """Probability of a profit at the nearest expiration (zero drift)."""
        legs = self.option_legs
        if not legs:
            return float("nan")
        t = min(self.years(l) for l in legs)
        if t <= 0:
            return 1.0 if self.payoff_at(self.spot) > 0 else 0.0
        # integrate the lognormal density over price buckets
        n = 400
        sd = vol * math.sqrt(t)
        mu = math.log(self.spot) - 0.5 * sd * sd
        p = 0.0
        for i in range(n):
            z0 = -6 + 12 * i / n
            z1 = -6 + 12 * (i + 1) / n
            s = math.exp(mu + sd * (z0 + z1) / 2)
            if self.payoff_at(s) > 0:
                p += pricing.norm_cdf(z1) - pricing.norm_cdf(z0)
        return p

    # ---- greeks ---------------------------------------------------------------
    def net_greeks(self, vol_for: callable) -> dict:
        tot = {"delta": 0.0, "gamma": 0.0, "theta": 0.0, "vega": 0.0}
        for l in self.legs:
            if l.type == "stock":
                tot["delta"] += l.sign * l.quantity
                continue
            g = pricing.black_scholes(l.type, self.spot, l.strike, self.years(l),
                                      vol_for(l), self.rate, self.dividend_yield)
            k = l.sign * l.quantity * CONTRACT_SIZE
            tot["delta"] += g.delta * k
            tot["gamma"] += g.gamma * k
            tot["theta"] += g.theta * k
            tot["vega"] += g.vega * k
        return tot


def classify(legs: list[Leg]) -> str:
    """Name the strategy from its legs."""
    opts = [l for l in legs if l.is_option]
    stock = [l for l in legs if l.type == "stock"]
    if not opts:
        return "stock" if stock else "empty"
    if stock:
        s = stock[0]
        if len(opts) == 1:
            o = opts[0]
            if s.action == "buy" and o.action == "sell" and o.type == "call":
                return "covered_call"
            if s.action == "buy" and o.action == "buy" and o.type == "put":
                return "protective_put"
        if len(opts) == 2 and s.action == "buy":
            kinds = {(o.action, o.type) for o in opts}
            if kinds == {("buy", "put"), ("sell", "call")}:
                return "collar"
        return "stock_and_options"
    if len(opts) == 1:
        o = opts[0]
        if o.action == "buy":
            return "long_call" if o.type == "call" else "long_put"
        return "short_call" if o.type == "call" else "short_put"
    if len(opts) == 2:
        a, b = opts
        same_type = a.type == b.type
        same_exp = a.expiration == b.expiration
        if not same_type and a.action == b.action and same_exp:
            straddle = a.strike == b.strike
            base = "straddle" if straddle else "strangle"
            return base if a.action == "buy" else "short_" + base
        if same_type and a.action != b.action:
            if not same_exp:
                return "calendar_spread"
            if a.quantity != b.quantity:
                return "ratio_spread"
            return "vertical_spread"
    return "complex"
