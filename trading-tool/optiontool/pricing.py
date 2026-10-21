"""Option valuation, greeks and probability analysis.

Both books lean on a fair-value model to judge whether an option is cheap or
expensive (OTS secrets 54-60; IAO ch. 9 "Efficient Pricing—Mostly", ch. 10-11
on the greeks). This module implements Black-Scholes-Merton with a continuous
dividend yield, which is the standard model those discussions assume.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

SQRT_2PI = math.sqrt(2.0 * math.pi)


def norm_pdf(x: float) -> float:
    return math.exp(-0.5 * x * x) / SQRT_2PI


def norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


@dataclass(frozen=True)
class Greeks:
    price: float
    delta: float
    gamma: float
    theta: float  # per calendar day
    vega: float   # per 1 volatility point (0.01)
    rho: float    # per 1 rate point (0.01)


def _d1_d2(spot, strike, years, vol, rate, div):
    vs = vol * math.sqrt(years)
    d1 = (math.log(spot / strike) + (rate - div + 0.5 * vol * vol) * years) / vs
    return d1, d1 - vs


def black_scholes(kind: str, spot: float, strike: float, years: float,
                  vol: float, rate: float = 0.0, div: float = 0.0) -> Greeks:
    """Price and greeks for a European call ("call") or put ("put")."""
    kind = kind.lower()
    if kind not in ("call", "put"):
        raise ValueError(f"kind must be 'call' or 'put', got {kind!r}")
    if years <= 0 or vol <= 0:
        intrinsic = max(0.0, spot - strike) if kind == "call" else max(0.0, strike - spot)
        delta = (1.0 if spot > strike else 0.0) if kind == "call" else (-1.0 if spot < strike else 0.0)
        return Greeks(intrinsic, delta, 0.0, 0.0, 0.0, 0.0)

    d1, d2 = _d1_d2(spot, strike, years, vol, rate, div)
    df_r = math.exp(-rate * years)
    df_q = math.exp(-div * years)
    pdf = norm_pdf(d1)
    gamma = df_q * pdf / (spot * vol * math.sqrt(years))
    vega = spot * df_q * pdf * math.sqrt(years) / 100.0
    common_theta = -spot * df_q * pdf * vol / (2.0 * math.sqrt(years))
    if kind == "call":
        price = spot * df_q * norm_cdf(d1) - strike * df_r * norm_cdf(d2)
        delta = df_q * norm_cdf(d1)
        theta = common_theta - rate * strike * df_r * norm_cdf(d2) + div * spot * df_q * norm_cdf(d1)
        rho = strike * years * df_r * norm_cdf(d2) / 100.0
    else:
        price = strike * df_r * norm_cdf(-d2) - spot * df_q * norm_cdf(-d1)
        delta = -df_q * norm_cdf(-d1)
        theta = common_theta + rate * strike * df_r * norm_cdf(-d2) - div * spot * df_q * norm_cdf(-d1)
        rho = -strike * years * df_r * norm_cdf(-d2) / 100.0
    return Greeks(price, delta, gamma, theta / 365.0, vega, rho)


def implied_volatility(kind: str, market_price: float, spot: float, strike: float,
                       years: float, rate: float = 0.0, div: float = 0.0,
                       lo: float = 1e-4, hi: float = 5.0, tol: float = 1e-6) -> float | None:
    """Volatility that makes the model price equal the market price (bisection).

    Returns None when the price is outside the no-arbitrage range.
    """
    if years <= 0 or market_price <= 0:
        return None
    if black_scholes(kind, spot, strike, years, lo, rate, div).price > market_price:
        return None
    if black_scholes(kind, spot, strike, years, hi, rate, div).price < market_price:
        return None
    for _ in range(200):
        mid = 0.5 * (lo + hi)
        if black_scholes(kind, spot, strike, years, mid, rate, div).price < market_price:
            lo = mid
        else:
            hi = mid
        if hi - lo < tol:
            break
    return 0.5 * (lo + hi)


def prob_above(spot: float, level: float, years: float, vol: float,
               drift: float = 0.0) -> float:
    """Probability the price finishes above ``level`` at expiration (lognormal).

    ``drift`` defaults to zero: the books warn against relying on forecasts
    (OTS secret 6), so probability analysis assumes no directional edge.
    """
    if years <= 0 or vol <= 0:
        return 1.0 if spot > level else 0.0
    z = (math.log(level / spot) - (drift - 0.5 * vol * vol) * years) / (vol * math.sqrt(years))
    return 1.0 - norm_cdf(z)


def prob_below(spot: float, level: float, years: float, vol: float, drift: float = 0.0) -> float:
    return 1.0 - prob_above(spot, level, years, vol, drift)


def prob_touch(spot: float, level: float, years: float, vol: float) -> float:
    """Approximate probability of trading through ``level`` before expiration.

    Uses the reflection principle (about twice the finish-beyond probability),
    capped at 1.
    """
    if level >= spot:
        p = prob_above(spot, level, years, vol)
    else:
        p = prob_below(spot, level, years, vol)
    return min(1.0, 2.0 * p)


def expected_move(spot: float, years: float, vol: float, sigmas: float = 1.0) -> float:
    """Size of an n-standard-deviation move by expiration, in price units."""
    return spot * vol * math.sqrt(years) * sigmas
