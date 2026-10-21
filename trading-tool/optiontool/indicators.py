"""Technical indicators used by the trend, pattern and oscillator rules.

Increasing Alpha with Options (IAO) ch. 2-7 builds its strategy selection on
trends, moving averages, oscillators (stochastics, CCI, Bollinger Bands),
volume (OBV, money flow) and relative performance. All functions take plain
lists ordered oldest -> newest and return lists of the same length, padded
with None where there is not enough history.
"""

from __future__ import annotations

import math
from typing import Optional, Sequence

Series = list[Optional[float]]


def sma(values: Sequence[float], period: int) -> Series:
    out: Series = [None] * len(values)
    total = 0.0
    for i, v in enumerate(values):
        total += v
        if i >= period:
            total -= values[i - period]
        if i >= period - 1:
            out[i] = total / period
    return out


def ema(values: Sequence[float], period: int) -> Series:
    out: Series = [None] * len(values)
    if len(values) < period:
        return out
    k = 2.0 / (period + 1)
    prev = sum(values[:period]) / period
    out[period - 1] = prev
    for i in range(period, len(values)):
        prev = values[i] * k + prev * (1 - k)
        out[i] = prev
    return out


def rsi(closes: Sequence[float], period: int = 14) -> Series:
    """Wilder's RSI."""
    out: Series = [None] * len(closes)
    if len(closes) <= period:
        return out
    gains = losses = 0.0
    for i in range(1, period + 1):
        ch = closes[i] - closes[i - 1]
        gains += max(ch, 0.0)
        losses += max(-ch, 0.0)
    avg_g, avg_l = gains / period, losses / period
    out[period] = 100.0 if avg_l == 0 else 100.0 - 100.0 / (1 + avg_g / avg_l)
    for i in range(period + 1, len(closes)):
        ch = closes[i] - closes[i - 1]
        avg_g = (avg_g * (period - 1) + max(ch, 0.0)) / period
        avg_l = (avg_l * (period - 1) + max(-ch, 0.0)) / period
        out[i] = 100.0 if avg_l == 0 else 100.0 - 100.0 / (1 + avg_g / avg_l)
    return out


def stochastic(highs: Sequence[float], lows: Sequence[float], closes: Sequence[float],
               k_period: int = 14, d_period: int = 3, slowing: int = 3) -> tuple[Series, Series]:
    """Slow stochastic %K and %D."""
    n = len(closes)
    raw: Series = [None] * n
    for i in range(k_period - 1, n):
        hh = max(highs[i - k_period + 1:i + 1])
        ll = min(lows[i - k_period + 1:i + 1])
        raw[i] = 50.0 if hh == ll else 100.0 * (closes[i] - ll) / (hh - ll)
    k = _sma_optional(raw, slowing)
    d = _sma_optional(k, d_period)
    return k, d


def cci(highs: Sequence[float], lows: Sequence[float], closes: Sequence[float],
        period: int = 20) -> Series:
    """Commodity Channel Index (Lambert), constant 0.015."""
    tp = [(h + l + c) / 3.0 for h, l, c in zip(highs, lows, closes)]
    out: Series = [None] * len(tp)
    for i in range(period - 1, len(tp)):
        window = tp[i - period + 1:i + 1]
        mean = sum(window) / period
        mad = sum(abs(x - mean) for x in window) / period
        out[i] = 0.0 if mad == 0 else (tp[i] - mean) / (0.015 * mad)
    return out


def bollinger(closes: Sequence[float], period: int = 20, width: float = 2.0
              ) -> tuple[Series, Series, Series]:
    """Middle, upper and lower Bollinger Bands."""
    mid = sma(closes, period)
    upper: Series = [None] * len(closes)
    lower: Series = [None] * len(closes)
    for i in range(period - 1, len(closes)):
        window = closes[i - period + 1:i + 1]
        m = mid[i]
        sd = math.sqrt(sum((x - m) ** 2 for x in window) / period)
        upper[i] = m + width * sd
        lower[i] = m - width * sd
    return mid, upper, lower


def obv(closes: Sequence[float], volumes: Sequence[float]) -> Series:
    out: Series = [None] * len(closes)
    if not closes:
        return out
    total = 0.0
    out[0] = 0.0
    for i in range(1, len(closes)):
        if closes[i] > closes[i - 1]:
            total += volumes[i]
        elif closes[i] < closes[i - 1]:
            total -= volumes[i]
        out[i] = total
    return out


def money_flow_index(highs, lows, closes, volumes, period: int = 14) -> Series:
    tp = [(h + l + c) / 3.0 for h, l, c in zip(highs, lows, closes)]
    out: Series = [None] * len(tp)
    for i in range(period, len(tp)):
        pos = neg = 0.0
        for j in range(i - period + 1, i + 1):
            flow = tp[j] * volumes[j]
            if tp[j] > tp[j - 1]:
                pos += flow
            elif tp[j] < tp[j - 1]:
                neg += flow
        out[i] = 100.0 if neg == 0 else 100.0 - 100.0 / (1 + pos / neg)
    return out


def historical_volatility(closes: Sequence[float], period: int = 20,
                          trading_days: int = 252) -> Series:
    """Annualised close-to-close volatility of log returns."""
    out: Series = [None] * len(closes)
    rets = [math.log(closes[i] / closes[i - 1]) for i in range(1, len(closes))]
    for i in range(period, len(closes)):
        window = rets[i - period:i]
        mean = sum(window) / period
        var = sum((r - mean) ** 2 for r in window) / (period - 1)
        out[i] = math.sqrt(var * trading_days)
    return out


def relative_performance(closes: Sequence[float], benchmark: Sequence[float]) -> Series:
    """Ratio line of a security versus a benchmark, rebased to 1.0 at the start."""
    if not closes or not benchmark:
        return []
    base = closes[0] / benchmark[0]
    return [(c / b) / base for c, b in zip(closes, benchmark)]


def slope(values: Series, lookback: int) -> Optional[float]:
    """Percent change of a series over ``lookback`` bars (None if unavailable)."""
    if len(values) <= lookback:
        return None
    a, b = values[-1 - lookback], values[-1]
    if a is None or b is None or a == 0:
        return None
    return (b - a) / abs(a)


def swing_points(highs: Sequence[float], lows: Sequence[float], order: int = 5
                 ) -> tuple[list[int], list[int]]:
    """Indices of swing highs and swing lows (local extrema over +/- ``order`` bars)."""
    sh, sl = [], []
    for i in range(order, len(highs) - order):
        if highs[i] == max(highs[i - order:i + order + 1]):
            sh.append(i)
        if lows[i] == min(lows[i - order:i + order + 1]):
            sl.append(i)
    return sh, sl


def _sma_optional(values: Series, period: int) -> Series:
    out: Series = [None] * len(values)
    for i in range(len(values)):
        window = values[max(0, i - period + 1):i + 1]
        if len(window) == period and all(v is not None for v in window):
            out[i] = sum(window) / period
    return out


def momentum(closes: Sequence[float], period: int = 10) -> Series:
    """Close minus the close ``period`` bars ago (IAO-06-07)."""
    out: Series = [None] * len(closes)
    for i in range(period, len(closes)):
        out[i] = closes[i] - closes[i - period]
    return out


def adx(highs: Sequence[float], lows: Sequence[float], closes: Sequence[float],
        period: int = 14) -> tuple[Series, Series, Series]:
    """Wilder's ADX with +DI and -DI."""
    n = len(closes)
    plus_di: Series = [None] * n
    minus_di: Series = [None] * n
    out: Series = [None] * n
    if n <= 2 * period:
        return out, plus_di, minus_di
    tr, pdm, mdm = [0.0] * n, [0.0] * n, [0.0] * n
    for i in range(1, n):
        up, dn = highs[i] - highs[i - 1], lows[i - 1] - lows[i]
        pdm[i] = up if up > dn and up > 0 else 0.0
        mdm[i] = dn if dn > up and dn > 0 else 0.0
        tr[i] = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
    atr, sp, sm = sum(tr[1:period + 1]), sum(pdm[1:period + 1]), sum(mdm[1:period + 1])
    dx: list[float] = []
    for i in range(period, n):
        if i > period:
            atr = atr - atr / period + tr[i]
            sp = sp - sp / period + pdm[i]
            sm = sm - sm / period + mdm[i]
        pdi = 100 * sp / atr if atr else 0.0
        mdi = 100 * sm / atr if atr else 0.0
        plus_di[i], minus_di[i] = pdi, mdi
        dx.append(100 * abs(pdi - mdi) / (pdi + mdi) if pdi + mdi else 0.0)
        if len(dx) == period:
            out[i] = sum(dx) / period
        elif len(dx) > period:
            out[i] = (out[i - 1] * (period - 1) + dx[-1]) / period
    return out, plus_di, minus_di
