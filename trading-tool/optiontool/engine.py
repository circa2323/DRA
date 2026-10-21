"""Runs every rule check on a trade and turns the findings into a decision."""

from __future__ import annotations

from dataclasses import dataclass, field

from . import rules
from .context import Context
from .knowledge import Library, UnknownPrinciple
from .rules import BLOCK, INFO, PASS, SEVERITY_ORDER, WARN, Finding


@dataclass
class Decision:
    verdict: str                # REJECT / CAUTION / ACCEPT
    findings: list[Finding]
    management: list[Finding] = field(default_factory=list)
    context: Context | None = None

    @property
    def blocks(self):
        return [f for f in self.findings if f.severity == BLOCK]

    @property
    def warnings(self):
        return [f for f in self.findings if f.severity == WARN]

    def cited(self) -> list[str]:
        seen: list[str] = []
        for f in self.findings + self.management:
            for pid in f.principles:
                if pid not in seen:
                    seen.append(pid)
        return seen


def evaluate(ctx: Context, library: Library) -> Decision:
    findings: list[Finding] = []
    for fn in rules.CHECKS:
        for f in fn(ctx) or ():
            f.check = fn.__name__
            findings.append(f)
    for f in findings:
        _verify(f, library)
    findings.sort(key=lambda f: SEVERITY_ORDER[f.severity])
    if any(f.severity == BLOCK for f in findings):
        verdict = "REJECT"
    elif any(f.severity == WARN for f in findings):
        verdict = "CAUTION"
    else:
        verdict = "ACCEPT"
    mgmt = management_plan(ctx)
    for f in mgmt:
        _verify(f, library)
    return Decision(verdict, findings, mgmt, ctx)


def _verify(f: Finding, library: Library) -> None:
    """Every decision must trace back to a loaded book."""
    for pid in f.principles:
        if pid not in library:
            raise UnknownPrinciple(f"check {f.check!r} cites {pid}, which is not in the library")


def management_plan(ctx: Context) -> list[Finding]:
    """How to manage the position after entry, per the books."""
    out: list[Finding] = []
    s = ctx.strategy
    dte = ctx.min_dte
    add = lambda msg, *ids: out.append(Finding("PLAN", msg, ids, "management_plan"))

    if s in ("long_call", "long_put", "straddle", "strangle"):
        cost = ctx.long_options[0].market if ctx.long_options else None
        if cost:
            add(f"Sell half when the option doubles (≈ {2 * cost:.2f}); sell more at 3x "
                f"(≈ {3 * cost:.2f}); let a few ride for a home run.", "OTS-021", "OTS-025-c", "OTS-020")
        add("Once in profit, trail a stop on the underlying about 5% away.", "OTS-022-a", "OTS-087-c")
        if cost and cost > 2.0 or any((m.moneyness or 0) < 0 for m in ctx.long_options):
            add("Expensive / in-the-money option: hold no more than 3 weeks (21 days).", "OTS-023-b")
        if dte is not None and dte >= 30:
            when = "now" if dte == 30 else f"within {dte - 30} days"
            add(f"Exit before the option's last month: {when}.", "OTS-024-a")
        add("Exit if the underlying leaves your profit box or the trend turns against you; "
            "ask 'would I buy this today?'.", "OTS-029", "OTS-085", "OTS-086")
    if s == "vertical_spread" and not ctx.is_credit:
        leaps = (dte or 0) >= ctx.settings["leaps_min_dte"]
        if leaps:
            add("Take profits on half if the spread doubles; trail a stop on the rest.", "OTS-101-d")
        else:
            add("Debit spread caps profits: take them as the spread approaches max value.", "OTS-072-a")
    if ctx.short_options and (ctx.is_credit or s in ("short_call", "short_put", "covered_call")):
        pct = ctx.settings["writer_buyback_captured"]
        add(f"Buy back when most of the premium is gone (≥ {pct:.0%} captured, user setting) — get out of "
            "the hot seat.", "OTS-046-a", "OTS-097-f")
        add("Stops are placed on the underlying's price (if on the option, use bid/ask, never last) "
            "and are always obeyed.", "OTS-087-b", "OTS-087-d")
        add("Exit at once on an unplanned major change in the stock or the market.", "OTS-046-b")
    add("Never repair a losing trade by adding legs — cut the loss.", "OTS-071")
    if ctx.technical is not None:
        out.extend(ctx.technical.management_notes(ctx))
    return out
