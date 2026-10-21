"""Plain-text and JSON rendering of decisions."""

from __future__ import annotations

import json

from .engine import Decision
from .knowledge import Library

ICON = {"BLOCK": "✖", "WARN": "!", "PASS": "✔", "INFO": "·", "PLAN": "→"}


def _money(x: float) -> str:
    return f"-${-x:,.0f}" if x < 0 else f"${x:,.0f}"


def text(decision: Decision, library: Library, show_sources: bool = True) -> str:
    c = decision.context
    t = c.trade
    lines = [f"{t.underlying} @ {t.spot:g} — {c.strategy.replace('_', ' ')}   (as of {t.as_of})", ""]
    debit = "debit" if c.net_debit >= 0 else "credit"
    mp = "unlimited" if c.profit_unlimited else _money(c.max_profit)
    ml = "unlimited" if c.loss_unlimited else _money(c.max_loss)
    lines.append(f"  Net {debit}: {_money(abs(c.net_debit))}   Max profit: {mp}   Max loss: {ml}")
    if c.breakevens:
        lines.append(f"  Break-even(s) at expiration: {', '.join(f'{b:g}' for b in c.breakevens)}")
    if c.prob_profit is not None:
        lines.append(f"  Probability of profit at expiration: {c.prob_profit:.0%}  (vol: {c.vol_source})")
    for m in c.legs:
        fv = f"fair {m.fair:.2f} ({m.value_ratio:.2f}x)" if m.fair else "fair n/a"
        iv = f"IV {m.iv:.0%}" if m.iv else "IV n/a"
        d = f"Δ {m.delta:+.2f}" if m.delta is not None else ""
        lines.append(f"  {m.leg.action:4} {m.leg.quantity:>3} {m.leg.strike:g} {m.leg.type:4} "
                     f"{m.leg.expiration} ({m.dte}d)  @ {m.market:.2f}  {fv}  {iv}  {d}")
    if c.greeks:
        g = c.greeks
        lines.append(f"  Position delta {g['delta']:+.0f} sh   theta {g['theta']:+.2f}/day   vega {g['vega']:+.2f}/pt")
    if c.technical is not None:
        lines.append("")
        lines.extend("  " + s for s in c.technical.summary_lines())
    lines += ["", f"VERDICT: {decision.verdict}", ""]
    for f in decision.findings:
        lines.append(f"  {ICON[f.severity]} {f.severity:5} {f.message}  [{', '.join(f.principles)}]")
    if decision.management:
        lines += ["", "MANAGEMENT PLAN"]
        for f in decision.management:
            lines.append(f"  {ICON['PLAN']} {f.message}  [{', '.join(f.principles)}]")
    if show_sources:
        lines += ["", "SOURCES"]
        for pid in decision.cited():
            p = library.get(pid)
            lines.append(f"  {p.citation(library)}")
            if p.quote:
                lines.append(f"      \"{p.quote}\"")
    return "\n".join(lines)


def as_json(decision: Decision, library: Library) -> str:
    c = decision.context
    return json.dumps({
        "underlying": c.trade.underlying,
        "strategy": c.strategy,
        "verdict": decision.verdict,
        "net_debit": c.net_debit,
        "max_profit": None if c.profit_unlimited else c.max_profit,
        "max_loss": None if c.loss_unlimited else c.max_loss,
        "breakevens": c.breakevens,
        "prob_profit": c.prob_profit,
        "volatility": c.vol,
        "volatility_source": c.vol_source,
        "findings": [{"severity": f.severity, "message": f.message, "principles": list(f.principles),
                      "check": f.check} for f in decision.findings],
        "management": [{"message": f.message, "principles": list(f.principles)} for f in decision.management],
        "sources": {pid: {"citation": library.get(pid).citation(library), "quote": library.get(pid).quote}
                    for pid in decision.cited()},
    }, indent=2)
