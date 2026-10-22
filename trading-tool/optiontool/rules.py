"""Rule checks. Each check cites the principle ids it enforces.

A check returns zero or more ``Finding``s. Severity:
  BLOCK - violates a rule the book states as a must / never
  WARN  - goes against the book's guidance
  PASS  - satisfies a rule (shown so you can see why a trade is acceptable)
  INFO  - context the book says to look at
Numbers in this file are the books' own; numbers the books leave open come
from ``settings`` and the finding says "(user setting)".
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from .context import Context

BLOCK, WARN, PASS, INFO = "BLOCK", "WARN", "PASS", "INFO"
SEVERITY_ORDER = {BLOCK: 0, WARN: 1, INFO: 2, PASS: 3}


@dataclass
class Finding:
    severity: str
    message: str
    principles: tuple[str, ...]
    check: str = ""
    small_budget: bool = False   # a book sizing limit relaxed by small-account mode


CHECKS: list = []


def check(*principle_ids: str):
    """Register a rule check and record which principles it enforces."""
    def wrap(fn):
        fn.principles = principle_ids
        CHECKS.append(fn)
        return fn
    return wrap


def _f(sev, msg, *ids):
    return Finding(sev, msg, ids)


def pct(x: float) -> str:
    return f"{x * 100:.1f}%" if abs(x) < 0.10 else f"{x * 100:.0f}%"


LONG_SINGLE = ("long_call", "long_put")
NAKED_SHORT = ("short_call", "short_put", "short_straddle", "short_strangle")


# ---------------------------------------------------------------- preparation
@check("OTS-055-a", "OTS-055-b", "OTS-005", "OTS-056-a")
def fair_value_available(ctx: Context):
    if ctx.vol is None:
        yield _f(BLOCK, "No volatility input: cannot compute fair value or probability of profit. "
                        "Supply price history (CSV) or historical_vol before trading.",
                 "OTS-005", "OTS-055-a", "OTS-056-a")
    elif "implied" in ctx.vol_source:
        yield _f(WARN, "Fair value computed from implied volatility only, so under/over-valuation "
                       "cannot be judged. Supply price history for historical volatility.",
                 "OTS-055-a", "OTS-056-a")
    else:
        yield _f(INFO, f"Volatility input: {ctx.vol_source} = {pct(ctx.vol)}.", "OTS-056-a", "OTS-056-b")


@check("OTS-093", "OTS-060-c", "OTS-087-a", "OTS-044-b")
def game_plan(ctx: Context):
    plan = ctx.plan
    has_stop = plan.get("stop") is not None
    has_target = plan.get("target") is not None
    writer = bool(ctx.short_options) and (ctx.is_credit or ctx.strategy in NAKED_SHORT)
    if not has_stop:
        sev = BLOCK if writer else WARN
        why = "stop-losses are a MUST for writers and credit spreads" if writer else \
              "every position needs a written stop"
        yield _f(sev, f"No stop-loss in the plan ({why}). Set it on the underlying's price.",
                 "OTS-087-a", "OTS-044-b", "OTS-093")
    if not has_target:
        yield _f(WARN, "No profit goal in the plan. Write the game plan (max risk, goal, stop) before entry.",
                 "OTS-093", "OTS-060-c")
    if has_stop and has_target:
        yield _f(PASS, f"Game plan defined: stop {plan['stop']}, target {plan['target']}.", "OTS-093")


# ---------------------------------------------------------------- structure
@check("OTS-069", "OTS-070", "OTS-071")
def avoid_complex_structures(ctx: Context):
    n = len(ctx.trade.option_legs)
    distinct = {(l.type, l.strike, l.expiration) for l in ctx.trade.option_legs}
    if len(distinct) >= 3:
        yield _f(BLOCK, f"{len(distinct)}-leg structure: avoid 'alligator spreads' that need three or "
                        "more trades to enter (commissions and slippage eat the profit).", "OTS-069")
    if ctx.plan.get("adjustment") in ("repair", "delta_neutral"):
        yield _f(BLOCK, "Repair / delta-neutral adjustments: cut the loss instead of adding legs.",
                 "OTS-070", "OTS-071")


@check("OTS-081", "OTS-075-b")
def execution_costs(ctx: Context):
    for l in ctx.trade.option_legs:
        if l.quantity > 10:
            yield _f(WARN, f"{l.quantity} contracts in one order: quotes are typically good for 10; "
                           "split the order so you don't tip your hand.", "OTS-081")
            break
    comm = ctx.plan.get("commission_per_contract")
    if comm is not None:
        if comm > 5:
            yield _f(BLOCK, f"Commission ${comm:.2f}/contract: over $5 per option is real trouble.", "OTS-075-b")
        elif comm > 3:
            yield _f(WARN, f"Commission ${comm:.2f}/contract: try not to pay more than $3.", "OTS-075-b")


@check("OTS-075-a", "OTS-TUT-a", "OTS-076", "OTS-077")
def liquidity(ctx: Context):
    s = ctx.settings
    for m in ctx.legs:
        name = _leg_name(m.leg)
        if m.bid_ask_pct is not None and m.bid_ask_pct > s["max_bid_ask_pct"]:
            yield _f(WARN, f"{name}: bid-ask spread is {pct(m.bid_ask_pct)} of mid (limit "
                           f"{pct(s['max_bid_ask_pct'])}, user setting). Slippage is the hidden cost; "
                           "use a limit order between bid and ask.", "OTS-075-a", "OTS-077")
        oi = m.leg.open_interest
        if oi is not None and oi < s["min_open_interest"]:
            yield _f(WARN, f"{name}: open interest {oi} is thin (minimum {s['min_open_interest']}, user setting).",
                     "OTS-TUT-a")


# ---------------------------------------------------------------- option buying
@check("OTS-019-a", "OTS-096-a", "OTS-095-b", "OTS-028-a")
def buy_only_undervalued(ctx: Context):
    if ctx.strategy not in LONG_SINGLE + ("straddle", "strangle", "protective_put"):
        return
    for m in ctx.long_options:
        if m.value_ratio is None:
            continue
        name = _leg_name(m.leg)
        if m.value_ratio > 1.0:
            yield _f(BLOCK, f"{name} costs {m.market:.2f} vs fair value {m.fair:.2f} "
                            f"({pct(m.value_ratio - 1)} overvalued). Buy only underpriced options.",
                     "OTS-019-a", "OTS-096-a")
        else:
            yield _f(PASS, f"{name} costs {m.market:.2f} vs fair value {m.fair:.2f} "
                           f"({pct(1 - m.value_ratio)} undervalued).", "OTS-019-a", "OTS-096-a")


@check("OTS-017-a", "OTS-096-b", "OTS-018-a", "OTS-023-b")
def option_price_level(ctx: Context):
    if ctx.strategy not in LONG_SINGLE:
        return
    leaps = (ctx.min_dte or 0) >= ctx.settings["leaps_min_dte"]
    for m in ctx.long_options:
        name = _leg_name(m.leg)
        if m.market > 2.0:
            yield _f(WARN, f"{name} costs {m.market:.2f}: avoid paying more than 2 ($200) for a stock "
                           "option. If you do, set a stop on the underlying and a 3-week holding limit.",
                     "OTS-017-a", "OTS-018-a", "OTS-023-b")
        cheap = 2.5 if leaps else 1.0
        if m.market < cheap:
            yield _f(PASS, f"{name} is cheap ({m.market:.2f} < {cheap}).", "OTS-096-b")


@check("OTS-024-a", "OTS-024-b", "OTS-039-a")
def avoid_last_month(ctx: Context):
    if ctx.strategy not in LONG_SINGLE + ("straddle", "strangle", "vertical_spread") or ctx.is_credit:
        return
    dte = ctx.min_dte
    if dte is None:
        return
    if dte < 30:
        m = ctx.long_options[0] if ctx.long_options else None
        cheap = m and m.value_ratio is not None and m.value_ratio <= ctx.settings["extremely_cheap_ratio"]
        near = m and m.moneyness is not None and abs(m.moneyness) <= 0.03
        if ctx.is_index and dte <= 7 and m and m.market < 1.0 and near:
            yield _f(WARN, f"{dte} days left: acceptable only as an expiration-week long shot "
                           "(near-the-money index option under 1). Take profits fast.", "OTS-039-a")
        elif cheap and near:
            yield _f(WARN, f"{dte} days left, but the option is extremely cheap, undervalued and near the "
                           "strike — the one exception to avoiding the last month.", "OTS-024-b")
        else:
            yield _f(BLOCK, f"Only {dte} days to expiration: avoid holding long options in their last "
                            "month, when time decay is fastest.", "OTS-024-a")
    else:
        yield _f(PASS, f"{dte} days to expiration (outside the last month).", "OTS-024-a")


@check("OTS-096-i", "OTS-007-a", "OTS-059-b", "OTS-073-c")
def long_probability(ctx: Context):
    if ctx.prob_profit is None or ctx.is_credit:
        return
    p = ctx.prob_profit
    if ctx.strategy in LONG_SINGLE:
        if p < 0.20:
            yield _f(WARN, f"Probability of profit at expiration {pct(p)} is below 20%.", "OTS-096-i")
        else:
            yield _f(PASS, f"Probability of profit at expiration {pct(p)} (≥ 20%).", "OTS-096-i")
        yield _f(INFO, "A long option's odds are below 50% in a random market — be active and take "
                       "profits before expiration.", "OTS-007-a", "OTS-059-b")


@check("OTS-025-d")
def scaling_size(ctx: Context):
    if ctx.strategy in LONG_SINGLE:
        q = ctx.long_options[0].leg.quantity
        if q < 3:
            f = _f(WARN, f"{q} contract(s): buy at least 3 per position so you can scale out.", "OTS-025-d")
            f.small_budget = bool(ctx.plan.get("small_account_mode"))
            yield f


@check("OTS-030-a", "OTS-030-b")
def straddle_rules(ctx: Context):
    if ctx.strategy not in ("straddle", "strangle"):
        return
    target_up, target_dn = ctx.plan.get("target_up"), ctx.plan.get("target_down")
    levels = [x for x in (target_up, target_dn) if x] or ctx.breakevens
    if ctx.vol is None or not levels:
        return
    ps = [ctx.prob_touch(x) for x in levels]
    p = min(1.0, sum(ps))
    if p < 0.80:
        yield _f(BLOCK, f"Chance of touching a profit point during the trade ≈ {pct(p)}: straddles "
                        "need 80% or better.", "OTS-030-a")
    else:
        yield _f(PASS, f"Chance of touching a profit point ≈ {pct(p)} (≥ 80%).", "OTS-030-a")


# ---------------------------------------------------------------- option writing
@check("OTS-048-c", "OTS-055-b")
def write_only_overvalued(ctx: Context):
    if not ctx.short_options or not (ctx.is_credit or ctx.strategy in NAKED_SHORT + ("covered_call", "collar")):
        return
    for m in ctx.short_options:
        if m.value_ratio is None:
            continue
        name = _leg_name(m.leg)
        if m.value_ratio < 1.0:
            yield _f(BLOCK, f"Writing {name} at {m.market:.2f} below fair value {m.fair:.2f}: only write "
                            "overvalued options.", "OTS-048-c", "OTS-055-b")
        else:
            yield _f(PASS, f"{name} written at {m.market:.2f} vs fair {m.fair:.2f} "
                           f"({pct(m.value_ratio - 1)} overvalued).", "OTS-048-c")


@check("OTS-044-a", "OTS-043", "OTS-041")
def naked_probability(ctx: Context):
    if ctx.strategy not in NAKED_SHORT or ctx.prob_profit is None:
        return
    p = ctx.prob_profit
    if p <= 0.90:
        yield _f(BLOCK, f"Naked write with {pct(p)} probability of success: only write naked when it "
                        "exceeds 90% (far out-of-the-money).", "OTS-044-a", "OTS-043")
    else:
        yield _f(PASS, f"Probability of success {pct(p)} (> 90%).", "OTS-044-a")


@check("OTS-045-a", "OTS-045-b", "OTS-098-c")
def writer_time(ctx: Context):
    if not ctx.short_options or not (ctx.is_credit or ctx.strategy in NAKED_SHORT + ("covered_call",)):
        return
    dte = min(m.dte for m in ctx.short_options if m.dte is not None)
    if dte > 60:
        yield _f(BLOCK, f"Writing an option with {dte} days left: avoid writing with more than two "
                        "months to expiration.", "OTS-045-b")
    elif dte >= 30:
        yield _f(WARN, f"{dte} days left: writers do best with under one month to expiration.", "OTS-045-a")
    else:
        yield _f(PASS, f"{dte} days to expiration for the written option (< 1 month).", "OTS-045-a")
    if ctx.strategy == "vertical_spread" and ctx.is_credit and ctx.is_index and dte >= 21:
        yield _f(BLOCK, f"Index credit spread with {dte} days left: enter with less than three weeks.",
                 "OTS-098-c")


@check("OTS-052-a")
def cash_for_assignment(ctx: Context):
    if ctx.strategy != "short_put":
        return
    leg = ctx.short_options[0].leg
    need = leg.strike * 100 * leg.quantity
    cash = ctx.plan.get("cash_available", ctx.trade.account_value)
    if cash is None:
        yield _f(WARN, f"Put writing needs ${need:,.0f} to take assignment; account size not given.", "OTS-052-a")
    elif cash < need:
        yield _f(BLOCK, f"Put writing needs ${need:,.0f} to buy the stock at the strike; only "
                        f"${cash:,.0f} available.", "OTS-052-a")
    else:
        yield _f(PASS, f"Cash covers assignment (${need:,.0f}).", "OTS-052-a")


@check("OTS-083", "OTS-097-e")
def no_falling_knife(ctx: Context):
    tv = ctx.technical
    if tv is None:
        return
    bullish_writer = ctx.strategy in ("short_put", "short_straddle", "short_strangle") or \
        (ctx.strategy == "vertical_spread" and ctx.is_credit and all(l.type == "put" for l in ctx.trade.option_legs))
    if bullish_writer and tv.primary_trend == "down":
        yield _f(BLOCK, "Writing puts / put credit spreads into a major downtrend: don't catch a "
                        "falling knife.", "OTS-083", "OTS-097-e", *tv.trend_principles)


@check("OTS-049-c", "OTS-051")
def covered_call_rules(ctx: Context):
    if ctx.strategy != "covered_call":
        return
    tv = ctx.technical
    if tv is not None and tv.primary_trend == "up" and tv.trend_strength == "strong":
        yield _f(WARN, "Covered call on a stock in a strong uptrend caps the gain; the book avoids it.",
                 "OTS-049-c", *tv.trend_principles)
    if ctx.trade.spot < 15:
        yield _f(INFO, "Low-priced stock (< $15): covered-call premiums are high relative to price.", "OTS-051")


# ---------------------------------------------------------------- spreads
@check("OTS-073-a", "OTS-073-b", "OTS-073-d", "OTS-101-b", "OTS-072-b", "OTS-TUT-c")
def debit_spread_rules(ctx: Context):
    if ctx.strategy != "vertical_spread" or ctx.is_credit:
        return
    debit = ctx.net_debit
    ret = ctx.max_profit / debit if debit > 0 else 0
    long_m, short_m = ctx.long_options[0], ctx.short_options[0]
    leaps = (ctx.min_dte or 0) >= ctx.settings["leaps_min_dte"]
    need = 4.0 if leaps else 2.0
    ids = ("OTS-101-b",) if leaps else ("OTS-073-a",)
    if ret < need:
        yield _f(BLOCK, f"Maximum profit is {pct(ret)} of the debit; needs at least {pct(need)}.", *ids, "OTS-TUT-c")
    else:
        yield _f(PASS, f"Maximum profit {pct(ret)} of the debit (≥ {pct(need)}).", *ids)
    if leaps:
        width = abs(long_m.leg.strike - short_m.leg.strike)
        if width < 10:
            yield _f(BLOCK, f"LEAPS debit spread is {width:g} points wide; use 10 points or more.", "OTS-101-b")
    if short_m.market < 0.40:
        yield _f(BLOCK, f"Short leg sold for {short_m.market:.2f}: don't sell an option for less than .40.", "OTS-073-b")
    if (long_m.moneyness or 0) > (short_m.moneyness or 0):
        yield _f(BLOCK, "The bought leg must be closer to the money than the sold leg.", "OTS-073-d")
    if long_m.iv and short_m.iv:
        if short_m.iv > long_m.iv:
            yield _f(PASS, f"Sold leg IV {pct(short_m.iv)} > bought leg IV {pct(long_m.iv)}.", "OTS-072-b")
        else:
            yield _f(INFO, f"Sold leg IV {pct(short_m.iv)} ≤ bought leg IV {pct(long_m.iv)}; debit spreads "
                           "work best the other way round.", "OTS-072-b")
    if ctx.prob_profit is not None:
        if ctx.prob_profit < 0.35:
            yield _f(WARN, f"Probability of profit {pct(ctx.prob_profit)}; try for 35% or better.", "OTS-073-c")
        else:
            yield _f(PASS, f"Probability of profit {pct(ctx.prob_profit)} (≥ 35%).", "OTS-073-c")


@check("OTS-098-a", "OTS-098-b", "OTS-097-a", "OTS-097-b", "OTS-TUT-d")
def credit_spread_rules(ctx: Context):
    if ctx.strategy != "vertical_spread" or not ctx.is_credit:
        return
    legs = ctx.trade.option_legs
    width = abs(legs[0].strike - legs[1].strike)
    credit = -ctx.net_debit / (legs[0].quantity * 100)
    yield _f(INFO, f"Credit {credit:.2f}, width {width:g}, max risk {width - credit:.2f} per share.", "OTS-097-b", "OTS-TUT-d")
    if not ctx.is_index:
        yield _f(INFO, "Index credit spreads are preferred: indexes dampen single-stock surprises.", "OTS-097-a")
        return
    if width not in (5, 10, 15):
        yield _f(BLOCK, f"Index credit spread {width:g} points wide; use 5, 10 or 15.", "OTS-098-a")
    if credit < 0.45:
        yield _f(BLOCK, f"Credit {credit:.2f}: get at least .45.", "OTS-098-b")
    else:
        yield _f(PASS, f"Credit {credit:.2f} (≥ .45).", "OTS-098-b")


@check("OTS-097-c", "OTS-097-d", "OTS-098-d")
def credit_stop_odds(ctx: Context):
    if not (ctx.is_credit and ctx.short_options) or ctx.plan.get("stop") is None:
        return
    stop = float(ctx.plan["stop"])
    p = ctx.prob_touch(stop)
    if p is None:
        return
    short = ctx.short_options[0].leg
    otm_side = stop < short.strike if short.type == "call" else stop > short.strike
    if not otm_side:
        yield _f(WARN, f"Stop {stop} is beyond the short strike {short.strike}; set it out-of-the-money, "
                       "before the short strike.", "OTS-097-c")
    if p > 0.20:
        yield _f(BLOCK, f"Chance of hitting the stop ({stop}) during the trade ≈ {pct(p)}: pass if it "
                        "exceeds 20% (index spreads want ≥ 85% chance of not being touched).",
                 "OTS-097-d", "OTS-098-d")
    else:
        yield _f(PASS, f"Chance of hitting the stop ≈ {pct(p)} (≤ 20%).", "OTS-097-d", "OTS-098-d")


# ---------------------------------------------------------------- money management
@check("OTS-025-a", "OTS-027", "OTS-094-a", "OTS-084", "OTS-048-b", "OTS-004-b", "OTS-001-b")
def position_size(ctx: Context):
    acct = ctx.trade.account_value
    if not acct:
        yield _f(WARN, "Account value not given: cannot check position size.", "OTS-025-a")
        return
    risk = ctx.capital_at_risk
    if ctx.loss_unlimited:
        yield _f(WARN, "Loss is open-ended; size from the stop, keep positions small.", "OTS-048-b")
        return
    small = bool(ctx.plan.get("small_account_mode"))
    # small-account mode: the books' percentage limits become warnings, and the
    # user's own dollar cap per trade is enforced instead (user setting)
    limit = WARN if small else BLOCK
    tag = " (small-account mode: book limit exceeded)" if small else ""
    cap = ctx.plan.get("max_trade_risk")
    if cap is not None:
        if risk > cap:
            yield _f(BLOCK, f"Risking ${risk:,.0f} exceeds your ${cap:,.0f} per-trade cap (user setting). "
                            "Start with very small positions.", "OTS-004-b", "OTS-084")
        else:
            yield _f(PASS, f"Risk ${risk:,.0f} is within your ${cap:,.0f} per-trade cap (user setting).", "OTS-004-b")
    if risk > acct:
        yield _f(BLOCK, f"Risking ${risk:,.0f} is more than the ${acct:,.0f} account.", "OTS-001-b")
    buying_book = ctx.plan.get("option_buying_portfolio")
    if ctx.long_options and not ctx.is_credit and buying_book:
        share = risk / buying_book
        if share >= 0.05:
            f = _f(limit, f"Risking ${risk:,.0f} = {pct(share)} of the option-buying portfolio; each "
                          f"position must be under 5%{tag}.", "OTS-025-a", "OTS-027")
            f.small_budget = small
            yield f
        else:
            yield _f(PASS, f"Position is {pct(share)} of the option-buying portfolio (< 5%).", "OTS-025-a")
    share = (risk + ctx.trade.capital_at_risk_elsewhere) / acct
    if ctx.long_options and not ctx.is_credit and share > 0.10:
        f = _f(limit, f"Speculative option buying would be {pct(share)} of the portfolio; never more "
                      f"than 5–10%{tag}.", "OTS-094-a", "OTS-084")
        f.small_budget = small
        yield f
    elif ctx.long_options and not ctx.is_credit and share > 0.05:
        f = _f(WARN, f"Speculative option buying would be {pct(share)} of the portfolio (book range 5–10%).",
               "OTS-094-a")
        f.small_budget = small
        yield f


# ---------------------------------------------------------------- volatility & prediction
@check("OTS-057-a", "OTS-057-b", "OTS-008-a", "OTS-012-d", "OTS-044-c")
def implied_vs_historical(ctx: Context):
    s = ctx.settings
    hv = ctx.hv_short
    for m in ctx.legs:
        if not (m.iv and hv):
            continue
        ratio = m.iv / hv
        name = _leg_name(m.leg)
        yield _f(INFO, f"{name}: IV {pct(m.iv)} vs HV {pct(hv)} (ratio {ratio:.2f}).", "OTS-057-a")
        if ratio >= s["iv_spike_ratio"]:
            yield _f(WARN, f"{name}: options are very expensive relative to the stock's movement "
                           f"(IV/HV ≥ {s['iv_spike_ratio']}, user setting) — possible undisclosed news; "
                           "investigate before trading.", "OTS-012-d", "OTS-044-c")
        break
    if ctx.iv_percentile is not None:
        p = ctx.iv_percentile
        if p <= s["iv_extreme_low_pct"]:
            yield _f(INFO, f"IV at the {p:.0f}th percentile of its history: options cheap — favors buying.",
                     "OTS-057-b", "OTS-008-a")
        elif p >= s["iv_extreme_high_pct"]:
            yield _f(INFO, f"IV at the {p:.0f}th percentile of its history: options expensive — favors writing.",
                     "OTS-057-b", "OTS-008-a")


@check("OTS-015-a", "OTS-064-a", "OTS-064-b")
def sixty_percent_rule(ctx: Context):
    conf = ctx.plan.get("confidence")
    if conf is not None and conf > 0.60:
        yield _f(WARN, f"Stated confidence {pct(conf)} capped at 60%: assume you'll be right at most 60% "
                       "of the time. The trade must still work at that rate.", "OTS-015-a", "OTS-064-b")


@check("OTS-014-a", "OTS-014-b")
def seasonality(ctx: Context):
    bullish = (ctx.trade.outlook == "bullish") or ctx.strategy in ("long_call", "short_put", "covered_call")
    if not bullish:
        return
    m = ctx.trade.as_of.month
    if m in (5, 6, 9, 10) and not (m == 10 and ctx.trade.as_of.day >= 20):
        yield _f(INFO, f"Bullish trade in a historically weak month ({date(2000, m, 1):%B}).", "OTS-014-a")
    if m == 10 and ctx.trade.as_of.day >= 20:
        yield _f(INFO, "Late October has historically been the best time to buy stocks.", "OTS-014-b")


def _leg_name(leg) -> str:
    verb = "long" if leg.action == "buy" else "short"
    return f"{verb} {leg.strike:g} {leg.type} {leg.expiration}"


def all_principle_ids(library=None) -> set[str]:
    """Ids registered on checks, plus (given a library) every id cited anywhere in the code."""
    out = set()
    for fn in CHECKS:
        out.update(fn.principles)
    if library is not None:
        from pathlib import Path
        src = "".join(f.read_text(encoding="utf-8") for f in Path(__file__).parent.glob("*.py"))
        out.update(pid for pid in library.principles if f'"{pid}"' in src)
    return out


# ================================================================ IAO (Fullman)
BULLISH = ("long_call", "short_put", "covered_call")
BEARISH = ("long_put", "short_call")


def direction(ctx: Context) -> str | None:
    s = ctx.strategy
    if s in BULLISH:
        return "bull"
    if s in BEARISH:
        return "bear"
    if s == "vertical_spread":
        t = ctx.trade.option_legs[0].type
        # call debit / put credit = bullish; put debit / call credit = bearish
        bull = (t == "call") != ctx.is_credit
        return "bull" if bull else "bear"
    return None


def iv_regime(ctx: Context) -> tuple[str | None, str]:
    """'high' / 'low' premium regime, judged against IV history or realised vol."""
    s = ctx.settings
    if ctx.iv_percentile is not None:
        if ctx.iv_percentile >= s["iv_extreme_high_pct"]:
            return "high", f"IV at {ctx.iv_percentile:.0f}th percentile of its history"
        if ctx.iv_percentile <= s["iv_extreme_low_pct"]:
            return "low", f"IV at {ctx.iv_percentile:.0f}th percentile of its history"
        return None, f"IV at {ctx.iv_percentile:.0f}th percentile (mid-range)"
    ivs = [m.iv for m in ctx.legs if m.iv]
    if ivs and ctx.hv_short:
        iv = sum(ivs) / len(ivs)
        return ("high" if iv > ctx.hv_short else "low"), f"IV {iv:.0%} vs HV {ctx.hv_short:.0%}"
    return None, "IV regime unknown"


@check("IAO-02-09", "IAO-02-07", "OTS-085")
def trade_with_the_trend(ctx: Context):
    tv, d = ctx.technical, direction(ctx)
    if tv is None or d is None:
        return
    t = tv.primary_trend
    if (d == "bull" and t == "down") or (d == "bear" and t == "up"):
        yield _f(WARN, f"{'Bullish' if d == 'bull' else 'Bearish'} position against the {t}trend: the stock "
                       "is likely to resume its primary trend — treat as a short-term countertrend trade.",
                 "IAO-02-09", "IAO-02-07", "OTS-085", *tv.trend_principles)
    elif t in ("up", "down"):
        yield _f(PASS, f"Position is aligned with the {t}trend.", "IAO-02-09", *tv.trend_principles)


@check("IAO-00-03", "IAO-02-02", "IAO-12-02", "IAO-07-11", "OTS-057-b")
def premium_regime(ctx: Context):
    regime, why = iv_regime(ctx)
    if regime is None:
        return
    buyer = not ctx.is_credit and bool(ctx.long_options) and not ctx.short_options
    seller = ctx.is_credit or ctx.strategy in NAKED_SHORT + ("covered_call",)
    if buyer and regime == "high":
        yield _f(WARN, f"Buying premium while it is high ({why}): professionals sell when IV is high "
                       "and buy when it is low.", "IAO-00-03", "IAO-07-11", "OTS-057-b")
    elif seller and regime == "low":
        yield _f(WARN, f"Selling premium while it is low ({why}): below-normal premiums; consider buying instead.",
                 "IAO-00-03", "IAO-12-02", "OTS-057-b")
    elif buyer or seller:
        yield _f(PASS, f"{'Buying' if buyer else 'Selling'} premium in a {regime}-IV regime ({why}).",
                 "IAO-00-03", "IAO-07-11")


@check("IAO-11-08", "IAO-06-14", "IAO-16-30")
def straddle_iv(ctx: Context):
    if ctx.strategy not in ("straddle", "strangle"):
        return
    regime, why = iv_regime(ctx)
    if regime == "high":
        yield _f(BLOCK, f"Long straddle/strangle with high IV ({why}): buy them only when IV is low — "
                        "high premiums need bigger moves.", "IAO-11-08")
    elif regime == "low":
        tv = ctx.technical
        if tv is not None and tv.trend_strength in ("weak", "consolidating"):
            yield _f(PASS, "Low IV in a narrowing consolidation — the setup for straddles.", "IAO-06-14", "IAO-06-13")


@check("IAO-06-01", "IAO-06-02", "IAO-05-16", "IAO-02-21", "IAO-02-19", "IAO-12-01")
def covered_call_timing(ctx: Context):
    if ctx.strategy != "covered_call":
        return
    m = ctx.short_options[0]
    if m.leg.strike <= ctx.trade.spot:
        yield _f(WARN, "Overwrite strike at or below the stock price: pick a strike above it to keep some upside.",
                 "IAO-02-21")
    tv = ctx.technical
    if tv is None:
        return
    if tv.overbought_daily and tv.overbought_weekly:
        lo, hi = 21, 63
        ok = lo <= (m.dte or 0) <= hi
        yield _f(PASS if ok else INFO, f"Overbought on daily and weekly: classic overwrite; book uses 3–9 weeks "
                                       f"(this one {m.dte}d).", "IAO-06-01")
    elif tv.overbought_daily:
        ok = 15 <= (m.dte or 0) <= 45
        yield _f(PASS if ok else INFO, f"Overbought on daily only: book writes 15–45 day calls (this one {m.dte}d).",
                 "IAO-06-02")
    elif tv.primary_trend == "up" and not tv.momentum_fading:
        yield _f(INFO, "Uptrend without overbought readings or fading momentum: no technical reason to cap upside yet.",
                 "IAO-06-01", "IAO-16-17")
    prem = m.market / ctx.trade.spot
    upside = (m.leg.strike - ctx.trade.spot) / ctx.trade.spot
    yield _f(INFO, f"Premium {prem:.1%} of the stock, {upside:.1%} upside to the strike (book's example fund "
                   "guideline: ≥ 4% and ≥ 2.5%).", "IAO-12-01")


@check("IAO-06-04", "IAO-05-01", "IAO-02-17")
def oscillator_timing(ctx: Context):
    tv, d = ctx.technical, direction(ctx)
    if tv is None or d is None or ctx.is_credit:
        return
    if d == "bull" and ctx.strategy != "covered_call" and tv.overbought_daily and tv.overbought_weekly:
        yield _f(WARN, "Buying bullish exposure while overbought on daily and weekly charts: the book's play "
                       "here is to overwrite, not buy.", "IAO-06-01", "IAO-05-01")
    if d == "bear" and tv.oversold_daily:
        yield _f(WARN, "Buying puts while oversold: countertrend rallies are likely.", "IAO-06-06", "IAO-05-01")
    if d == "bear" and tv.overbought_daily and not any(s.name.endswith("sell") or "sell" in s.name
                                                        for s in tv.signals):
        yield _f(INFO, "Overbought but no sell signal yet: the book waits for the oscillator to turn "
                       "(preferably on the weekly) before buying puts.", "IAO-06-04", "IAO-02-17")


@check("IAO-14-07", "IAO-14-09", "IAO-14-10", "IAO-14-14", "IAO-14-11", "IAO-14-06", "IAO-14-08",
       "OTS-037-c", "OTS-037-b")
def hedge_rules(ctx: Context):
    if ctx.strategy not in ("protective_put", "collar"):
        return
    put = next(m for m in ctx.long_options if m.leg.type == "put")
    if put.value_ratio is not None and put.value_ratio > 1.0:
        yield _f(WARN, f"Insurance put costs {put.market:.2f} vs fair value {put.fair:.2f} "
                       f"({pct(put.value_ratio - 1)} overvalued): insurance puts should still be undervalued.",
                 "OTS-037-c")
    if put.dte is not None and put.dte < 45:
        yield _f(WARN, f"{put.dte}-day hedge: avoid ~1-month hedges; two-month-plus contracts cost less per unit "
                       "of time.", "IAO-14-09", "IAO-14-10")
    if put.delta is not None:
        d = abs(put.delta)
        if 0.40 <= d <= 0.55:
            yield _f(PASS, f"Put delta {d:.2f}: ATM puts (0.40–0.55) are the best hedge value.", "IAO-14-07")
        elif d > 0.55:
            yield _f(INFO, f"Put delta {d:.2f}: in-the-money hedges are expensive.", "IAO-14-06")
        else:
            yield _f(INFO, f"Put delta {d:.2f}: out-of-the-money puts are catastrophe protection only.", "IAO-14-08")
    if ctx.strategy == "protective_put":
        if put.market / ctx.trade.spot > 0.10:
            yield _f(WARN, "Put costs more than 10% of the stock price: switch to a collar.", "IAO-14-14")
        regime, why = iv_regime(ctx)
        if regime == "high":
            yield _f(WARN, f"Protective puts work best when premiums are low ({why}); consider a collar.",
                     "IAO-14-11", "IAO-15-01")


@check("IAO-14-17", "IAO-02-04", "IAO-08-07")
def short_option_structure(ctx: Context):
    if ctx.strategy == "ratio_spread" and (ctx.min_dte or 0) > 60:
        yield _f(WARN, "Ratio spreads only within a month or two of expiration.", "IAO-14-17")
    if ctx.strategy == "short_call":
        yield _f(WARN, "Naked call writing is inherently risky (short squeezes).", "IAO-02-04", "OTS-043")
    if ctx.strategy == "short_put" and (ctx.min_dte or 0) >= 60:
        yield _f(WARN, "Write puts with less than two months to expiration to maximise time decay.", "IAO-08-07")


@check("IAO-09-05", "IAO-11-07", "IAO-16-28", "IAO-11-02")
def event_risk(ctx: Context):
    t = ctx.trade
    exp = max((l.expiration for l in t.option_legs if l.expiration), default=None)
    if t.ex_dividend_date and exp and t.ex_dividend_date <= exp:
        for m in ctx.short_options:
            if m.leg.type == "call" and (m.moneyness or 0) < 0:
                yield _f(WARN, f"Short in-the-money call before ex-dividend {t.ex_dividend_date}: early "
                               "assignment can cost the dividend.", "IAO-09-05", "IAO-11-07")
    if t.earnings_date and exp and t.as_of.isoformat() <= t.earnings_date <= exp:
        if ctx.short_options and not ctx.long_options:
            yield _f(WARN, f"Short premium through earnings on {t.earnings_date}.", "IAO-16-28")
        elif ctx.strategy in ("straddle", "strangle"):
            yield _f(INFO, f"Earnings {t.earnings_date} inside the trade: buy before IV inflates; weigh theta "
                           "against the expected pre-event move.", "IAO-16-30", "IAO-11-02")


@check("IAO-02-11", "IAO-06-03")
def horizon_in_trends(ctx: Context):
    tv = ctx.technical
    if tv is None or ctx.strategy not in ("long_call", "long_put") or ctx.min_dte is None:
        return
    if tv.primary_trend in ("up", "down") and direction(ctx) == ("bull" if tv.primary_trend == "up" else "bear"):
        if ctx.min_dte < 90:
            yield _f(INFO, f"{ctx.min_dte} days: in established trends the book works a 3–6 month horizon.",
                     "IAO-02-11", "OTS-096-d")


@check("IAO-02-12", "IAO-10-01")
def volatile_market_sizing(ctx: Context):
    tv = ctx.technical
    if tv is not None and tv.hv is not None and tv.hv > 0.50:
        yield _f(INFO, f"Realised volatility {tv.hv:.0%}: in volatile markets take smaller positions with "
                       "nearer targets and stops.", "IAO-02-12", "IAO-10-01")
