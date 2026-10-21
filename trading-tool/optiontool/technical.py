"""Technical reading of a price history, following Increasing Alpha with
Options (IAO): trend (ch. 2), patterns/breakouts (ch. 1, 4), oscillators
(ch. 5-6), relative performance (ch. 6-8) and seasonality (ch. 2, 15).

Periods/levels the book does not specify are marked "(default)" below:
ADX period 14, stochastic %D 3, CCI period 20, volume spike 1.5x average.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from . import indicators as ind
from .data import PriceHistory


@dataclass
class Signal:
    name: str
    detail: str
    principles: tuple[str, ...]


@dataclass
class TechnicalView:
    symbol: str
    last: float
    primary_trend: str = "unknown"        # up / down / sideways
    trend_strength: str = "unknown"       # strong / trending / weak / consolidating
    trend_principles: tuple[str, ...] = ("IAO-02-06", "IAO-02-39", "IAO-02-41")
    swing_structure: str = "unknown"
    sma21: float | None = None
    sma50: float | None = None
    sma200: float | None = None
    sma200_rising: bool | None = None
    adx: float | None = None
    rsi: float | None = None
    rsi_overbought: float = 70
    rsi_oversold: float = 30
    weekly_rsi: float | None = None
    stoch_k: float | None = None
    stoch_d: float | None = None
    cci: float | None = None
    boll_upper: float | None = None
    boll_lower: float | None = None
    band_width_contracting: bool | None = None
    momentum: float | None = None
    momentum_fading: bool = False
    hv: float | None = None
    rel_perf_slope: float | None = None
    overbought_daily: bool = False
    overbought_weekly: bool = False
    oversold_daily: bool = False
    oversold_weekly: bool = False
    breakout: bool = False
    support: float | None = None
    resistance: float | None = None
    signals: list[Signal] = field(default_factory=list)

    def add(self, name, detail, *ids):
        self.signals.append(Signal(name, detail, ids))

    # ------------------------------------------------------------------ text
    def summary_lines(self) -> list[str]:
        f = lambda x, n=2: "n/a" if x is None else f"{x:.{n}f}"
        out = [f"TECHNICALS ({self.symbol})",
               f"  Trend: {self.primary_trend} ({self.trend_strength}); swings: {self.swing_structure}",
               f"  SMA21 {f(self.sma21)}  SMA50 {f(self.sma50)}  SMA200 {f(self.sma200)}  ADX {f(self.adx, 1)}",
               f"  RSI {f(self.rsi, 1)} (OB {self.rsi_overbought:g}/OS {self.rsi_oversold:g})  weekly RSI {f(self.weekly_rsi, 1)}"
               f"  %K {f(self.stoch_k, 1)} %D {f(self.stoch_d, 1)}  CCI {f(self.cci, 0)}",
               f"  Bollinger {f(self.boll_lower)}–{f(self.boll_upper)}  momentum {f(self.momentum)}"
               f"  HV {'n/a' if self.hv is None else f'{self.hv:.0%}'}"
               + ("" if self.rel_perf_slope is None else f"  rel. perf. slope {self.rel_perf_slope:+.1%}")]
        for s in self.signals:
            out.append(f"  • {s.name}: {s.detail}  [{', '.join(s.principles)}]")
        return out

    def management_notes(self, ctx) -> list:
        from .rules import Finding
        notes = []
        add = lambda msg, *ids: notes.append(Finding("PLAN", msg, ids, "technical"))
        if self.support is not None:
            add(f"Use consolidation support ≈ {self.support:.2f} as a mental stop.", "IAO-03-13", "OTS-012-a")
        bullish = ctx.strategy in ("long_call", "covered_call", "short_put") or (
            ctx.strategy == "vertical_spread" and all(l.type == "call" for l in ctx.trade.option_legs)
            and not ctx.is_credit)
        if ctx.strategy == "long_call":
            add("If the stock rises 10%+, roll the call up to a higher strike (same expiration) or write a "
                "nearer-term OTM call against it.", "IAO-01-11", "IAO-01-12", "IAO-16-11")
        if self.breakout and bullish:
            add("Exit if price falls back below the breakout level into the old range.", "IAO-01-09")
        add("Momentum retreating toward zero is the early warning of a trend change — plan the exit or hedge.",
            "IAO-06-08", "IAO-07-07")
        return notes


def analyze(hist: PriceHistory, benchmark: PriceHistory | None = None,
            as_of: date | None = None) -> TechnicalView:
    c, h, l, v = hist.closes, hist.highs, hist.lows, hist.volumes
    tv = TechnicalView(hist.symbol, c[-1])
    n = len(c)

    # ---- trend (IAO-02-06, 02-39, 02-40, 02-41) ------------------------------
    s21, s50, s200 = ind.sma(c, 21), ind.sma(c, 50), ind.sma(c, 200)
    tv.sma21, tv.sma50, tv.sma200 = s21[-1], s50[-1], s200[-1]
    if n > 220 and s200[-21] is not None:
        tv.sma200_rising = s200[-1] > s200[-21]
    sh, sl = ind.swing_points(h, l, order=5)
    if len(sh) >= 2 and len(sl) >= 2:
        hh, hl = h[sh[-1]] > h[sh[-2]], l[sl[-1]] > l[sl[-2]]
        lh, ll = h[sh[-1]] < h[sh[-2]], l[sl[-1]] < l[sl[-2]]
        tv.swing_structure = ("higher highs & higher lows" if hh and hl else
                              "lower highs & lower lows" if lh and ll else "mixed")
    votes = 0
    if tv.sma200 is not None:
        votes += 1 if c[-1] > tv.sma200 else -1
        if tv.sma200_rising is not None:
            votes += 1 if tv.sma200_rising else -1
    if tv.sma50 is not None and tv.sma200 is not None:
        votes += 1 if tv.sma50 > tv.sma200 else -1
    if tv.swing_structure.startswith("higher"):
        votes += 1
    elif tv.swing_structure.startswith("lower"):
        votes -= 1
    if tv.sma200 is None and tv.sma50 is not None:   # short history: intermediate trend only
        votes += 1 if c[-1] > tv.sma50 else -1
        tv.add("Short history", "under 200 bars: trend read from the 50-day (intermediate) average",
               "IAO-02-40", "IAO-02-08")
    tv.primary_trend = "up" if votes >= 2 else "down" if votes <= -2 else "sideways"
    tv.add("Trend", f"{tv.primary_trend} — price vs rising/falling long-term MA, 50/200 relationship, "
                    f"swing structure ({tv.swing_structure})", "IAO-02-06", "IAO-02-39", "IAO-02-41")

    adx, pdi, mdi = ind.adx(h, l, c, 14)
    tv.adx = adx[-1]
    if tv.adx is not None:
        if tv.adx < 10:
            tv.trend_strength = "consolidating"
            tv.add("ADX < 10", "long sideways consolidation — options tend to be cheap", "IAO-06-12", "IAO-06-13")
        elif tv.adx < 22:
            tv.trend_strength = "weak"
            tv.add("ADX < 22", "no confirmed trend", "IAO-06-11")
        else:
            tv.trend_strength = "strong" if tv.adx >= 40 else "trending"
            tv.add("ADX ≥ 22", f"trend confirmed (ADX {tv.adx:.0f})", "IAO-06-11")
            if tv.trend_strength == "strong":
                tv.add("Strength", "ADX ≥ 40 treated as a strong trend (default)", "IAO-06-11")

    # ---- oscillators (IAO-05-04..05-16) --------------------------------------
    r = ind.rsi(c, 14)
    tv.rsi = r[-1]
    if tv.primary_trend == "up":
        tv.rsi_overbought = 80
    elif tv.primary_trend == "down":
        tv.rsi_oversold, tv.rsi_overbought = 20, 70
    k, d = ind.stochastic(h, l, c, 14, 3, 3)
    tv.stoch_k, tv.stoch_d = k[-1], d[-1]
    tv.cci = ind.cci(h, l, c, 20)[-1]
    mid, up, lo = ind.bollinger(c, 20, 2.0)
    tv.boll_upper, tv.boll_lower = up[-1], lo[-1]
    if n > 40 and up[-21] is not None:
        tv.band_width_contracting = (up[-1] - lo[-1]) < (up[-21] - lo[-21])

    ob = [tv.rsi is not None and tv.rsi > tv.rsi_overbought,
          tv.stoch_k is not None and tv.stoch_k > 80,
          tv.cci is not None and tv.cci > 100]
    os_ = [tv.rsi is not None and tv.rsi < tv.rsi_oversold,
           tv.stoch_k is not None and tv.stoch_k < 20,
           tv.cci is not None and tv.cci < -100]
    # require two oscillators to agree (IAO-05-03)
    tv.overbought_daily = sum(ob) >= 2
    tv.oversold_daily = sum(os_) >= 2
    if tv.overbought_daily:
        tv.add("Overbought (daily)", "RSI/stochastic/CCI agree", "IAO-05-03", "IAO-05-05", "IAO-05-07",
               "IAO-05-09", "IAO-05-12")
    if tv.oversold_daily:
        tv.add("Oversold (daily)", "RSI/stochastic/CCI agree", "IAO-05-03", "IAO-05-05", "IAO-05-06",
               "IAO-05-09", "IAO-05-12")
    if len(r) > 1 and r[-2] is not None and tv.rsi is not None:
        if r[-2] < 30 <= tv.rsi:
            tv.add("RSI buy signal", "RSI crossed back above 30", "IAO-05-05", "IAO-16-16")
        if r[-2] > 70 >= tv.rsi:
            tv.add("RSI sell signal", "RSI crossed below 70", "IAO-05-05")
    if len(k) > 1 and None not in (k[-2], d[-2], tv.stoch_k, tv.stoch_d):
        if k[-2] < d[-2] and tv.stoch_k > tv.stoch_d:
            tv.add("Stochastic buy", "%K crossed above %D", "IAO-05-10")
        if k[-2] > d[-2] and tv.stoch_k < tv.stoch_d:
            tv.add("Stochastic sell", "%K crossed below %D", "IAO-05-10")
    if tv.boll_upper is not None:
        if c[-1] > tv.boll_upper:
            tv.add("Above upper band", "likely to retrace toward the 20-day average", "IAO-05-14", "IAO-05-16")
        elif c[-1] < tv.boll_lower:
            tv.add("Below lower band", "likely to retrace toward the 20-day average", "IAO-05-14")
    if tv.band_width_contracting:
        tv.add("Bands contracting", "volatility falling — premiums cheaper", "IAO-05-15")

    # ---- weekly confirmation (IAO-04-01, 04-03, 06-01) ------------------------
    wk = hist.weekly()
    if len(wk) > 20:
        wr = ind.rsi(wk.closes, 13)[-1]
        wkk, _ = ind.stochastic(wk.highs, wk.lows, wk.closes, 14, 3, 3)
        tv.weekly_rsi = wr
        w_ob = [wr is not None and wr > 70, wkk[-1] is not None and wkk[-1] > 80]
        w_os = [wr is not None and wr < 30, wkk[-1] is not None and wkk[-1] < 20]
        tv.overbought_weekly = any(w_ob)
        tv.oversold_weekly = any(w_os)
        if tv.overbought_daily and tv.overbought_weekly:
            tv.add("Overbought daily AND weekly", "concurring timeframes", "IAO-04-01", "IAO-06-01")
        if tv.oversold_daily and tv.oversold_weekly:
            tv.add("Oversold daily AND weekly", "concurring timeframes", "IAO-04-01")

    # ---- momentum (IAO-06-07, 06-08, 07-07) ----------------------------------
    m = ind.momentum(c, 10)
    tv.momentum = m[-1]
    if n > 30 and None not in (m[-1], m[-2]):
        recent = [x for x in m[-20:] if x is not None]
        if recent:
            peak = max(recent, key=abs)
            if peak and abs(m[-1]) < 0.5 * abs(peak) and (m[-1] > 0) == (peak > 0):
                tv.momentum_fading = True
                tv.add("Momentum fading", "retreating toward zero — early warning of a trend change",
                       "IAO-06-08", "IAO-02-18")
        if (m[-2] < 0) != (m[-1] < 0):
            tv.add("Momentum zero cross", "often the first sign of a reversal", "IAO-07-07")

    # ---- volume & breakouts (IAO-04-23, 06-23, 06-24) ------------------------
    if n > 60 and v[-1] > 0:
        avg_v = sum(v[-51:-1]) / 50
        spike = avg_v > 0 and v[-1] > 1.5 * avg_v
        if spike and tv.sma21 and tv.boll_upper and c[-1] > tv.sma21 and c[-1] > tv.boll_upper:
            tv.breakout = True
            tv.add("Breakout", "close above the 21-day MA and upper band on a volume spike", "IAO-04-23")
        o = ind.obv(c, v)
        if n > 25 and c[-1] > c[-21] and o[-1] < o[-21]:
            tv.add("Volume divergence", "price up while on-balance volume falls — plan a covered call",
                   "IAO-06-24")
        if n > 25 and c[-1] < c[-21] and o[-1] < o[-21]:
            tv.add("Volume confirms decline", "price and on-balance volume both lower", "IAO-06-23")

    # support / resistance from the last 60 bars (OTS-012-a)
    # nearest swing low below / swing high above the price, else the 60-bar range
    look = min(60, n)
    lows_below = [l[i] for i in sl if l[i] < c[-1]]
    highs_above = [h[i] for i in sh if h[i] > c[-1]]
    tv.support = lows_below[-1] if lows_below else min(l[-look:])
    tv.resistance = highs_above[-1] if highs_above else max(h[-look:])

    tv.hv = ind.historical_volatility(c, 20)[-1]

    # ---- relative performance (IAO-06-15..17, 08-10) --------------------------
    if benchmark is not None and len(benchmark) >= 15:
        bmap = dict(zip(benchmark.dates, benchmark.closes))
        pairs = [(a, bmap[dt]) for dt, a in zip(hist.dates, c) if dt in bmap]
        if len(pairs) > 15:
            rs = ind.relative_performance([p[0] for p in pairs], [p[1] for p in pairs])
            tv.rel_perf_slope = ind.slope(rs, 14)
            label = "outperforming" if tv.rel_perf_slope > 0 else "lagging"
            tv.add("Relative performance", f"{label} {benchmark.symbol} over 14 days", "IAO-06-16", "IAO-08-10")

    # ---- seasonality (IAO-02-29, 02-30) ---------------------------------------
    as_of = as_of or (date.fromisoformat(hist.dates[-1][:10]) if hist.dates else date.today())
    md = (as_of.month, as_of.day)
    if (4, 15) <= md < (10, 31):
        tv.add("Season", "mid-April to late October: seasonally weak (Sep/Oct weakest)", "IAO-02-30", "OTS-014-a")
    else:
        tv.add("Season", "late October to mid-April: seasonally strong", "IAO-02-29", "OTS-014-b")
    return tv
