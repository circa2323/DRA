"""Turn an evaluated candidate into a plain-language action label and an
order ticket you can type into a broker."""

from __future__ import annotations

from datetime import date, timedelta

from .engine import Decision
from .rules import BLOCK, WARN

BUY, SMALL, CONSIDER, REJECT = "BUY", "Buy – Small Budget Logic", "Consider", "Reject"
ORDER = {BUY: 0, SMALL: 1, CONSIDER: 2, REJECT: 3}


def action_label(dec: Decision) -> str:
    """BUY = passes every rule. Small Budget = passes every rule except the book
    sizing limits that small-account mode relaxes. Consider = other warnings.
    Reject = breaks a hard rule."""
    if any(f.severity == BLOCK for f in dec.findings):
        return REJECT
    warns = [f for f in dec.findings if f.severity == WARN]
    if not warns:
        return BUY
    if all(f.small_budget for f in warns):
        return SMALL
    return CONSIDER


def _exp(e: str) -> str:
    return date.fromisoformat(e).strftime("%b %d, %Y").replace(" 0", " ")


def _mid(leg) -> float:
    if leg.bid is not None and leg.ask is not None and leg.ask > 0:
        return (leg.bid + leg.ask) / 2
    return leg.premium()


def build(dec: Decision) -> dict:
    c = dec.context
    t = c.trade
    sym = t.underlying
    lines, net_mid, net_worst = [], 0.0, 0.0
    contracts = 0
    for l in t.legs:
        if l.type == "stock":
            verb = "BUY" if l.action == "buy" else "SELL"
            lines.append(f"{verb} {l.quantity} shares of {sym} (about ${l.price:,.2f} each)")
            continue
        verb = "BUY TO OPEN" if l.action == "buy" else "SELL TO OPEN"
        lines.append(f"{verb} {l.quantity} × {sym} {_exp(l.expiration)} ${l.strike:g} {l.type.upper()}")
        contracts = l.quantity
        net_mid += l.sign * _mid(l)
        net_worst += l.sign * l.premium()   # ask for buys, bid for sells: the price the tool judged
    opt_legs = t.option_legs
    debit = net_worst >= 0
    limit_px = round(abs(net_mid), 2)
    worst_px = round(abs(net_worst), 2)
    if debit:
        limit_px = min(limit_px, worst_px)
    else:
        limit_px = max(limit_px, worst_px)
    single = len(opt_legs) == 1
    order = "Limit" if single else "Limit (one multi-leg order — don't leg in)"
    price_rule = (f"Limit price ${limit_px:.2f} (mid-point). Never pay more than ${worst_px:.2f}."
                  if debit else
                  f"Limit price ${limit_px:.2f} credit (mid-point). Never accept less than ${worst_px:.2f}.")
    cost = worst_px * 100 * max(contracts, 1)

    exits = []
    stop, target = c.plan.get("stop"), c.plan.get("target")
    if stop is not None:
        exits.append(f"STOP: set a price alert on {sym} at ${stop:,.2f}. If it trades there, close the "
                     "whole position (stops go on the stock price — OTS-087-b).")
    long_premium = debit and c.strategy in ("long_call", "long_put", "straddle", "strangle", "vertical_spread")
    if long_premium:
        double = worst_px * 2
        if contracts >= 2:
            exits.append(f"PROFIT: when the option is worth ${double:.2f} (2× cost), sell half; let the rest "
                         "ride with a 5% trailing stop on the stock (OTS-021, OTS-022-a).")
        else:
            exits.append(f"PROFIT: when the option is worth ${double:.2f} (2× cost), sell it — or hold for "
                         "more with a 5% trailing stop on the stock (OTS-021, OTS-022-a).")
    if not debit:
        buyback = round(worst_px * 0.2, 2)
        exits.append(f"PROFIT: buy it back when it costs ${buyback:.2f} or less (most of the premium "
                     "captured — OTS-046-a).")
    if target is not None:
        exits.append(f"Stock target: ${target:,.2f}.")
    if long_premium and opt_legs:
        exp = min(date.fromisoformat(l.expiration) for l in opt_legs)
        exit_by = exp - timedelta(days=30)
        if worst_px > 2.0 or any((m.moneyness or 0) < 0 for m in c.long_options):
            exit_by = min(exit_by, t.as_of + timedelta(days=21))
            why = "3-week limit for expensive / in-the-money options and the last-month rule (OTS-023-b, OTS-024-a)"
        else:
            why = "out before the option's last month (OTS-024-a)"
        if exit_by >= t.as_of:
            exits.append(f"TIME: close by {exit_by.strftime('%b %d, %Y').replace(' 0', ' ')} — {why}.")
    return {
        "headline": " + ".join(lines),
        "legs": lines,
        "order": order,
        "price_rule": price_rule,
        "debit": debit,
        "limit_price": limit_px,
        "worst_price": worst_px,
        "total": round(cost, 2),
        "max_loss": None if c.loss_unlimited else round(abs(c.max_loss), 2),
        "max_profit": None if c.profit_unlimited else round(c.max_profit, 2),
        "breakevens": c.breakevens,
        "prob_profit": c.prob_profit,
        "exits": exits,
        "time_in_force": "Day — today's quotes only; re-run tomorrow before re-entering.",
    }
