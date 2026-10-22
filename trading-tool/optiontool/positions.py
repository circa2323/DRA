"""My trades: positions you actually placed, checked against the books'
management rules on every pull.

Each check returns HOLD, TAKE PROFIT, BUY BACK or EXIT with the reasons and
the principles behind them.
"""

from __future__ import annotations

import json
import uuid
from datetime import date
from pathlib import Path

EXIT, BUYBACK, PROFIT, HOLD, DONE = "EXIT", "BUY BACK", "TAKE PROFIT", "HOLD", "EXPIRED"
PRIORITY = {EXIT: 0, BUYBACK: 1, PROFIT: 2, DONE: 3, HOLD: 4}


def path(cfg: dict) -> Path:
    return Path(cfg["data_dir"]).expanduser() / "positions.json"


def load(cfg: dict) -> list[dict]:
    p = path(cfg)
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else []


def save(cfg: dict, positions: list[dict]) -> None:
    p = path(cfg)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(positions, indent=2), encoding="utf-8")


def open_symbols(cfg: dict) -> list[str]:
    return sorted({p["symbol"] for p in load(cfg) if p.get("status") == "open"})


def new_position(symbol: str, contracts: list[dict], fill: float, opened: str, stop=None, target=None,
                 close_by=None, strategy: str = "", spot: float | None = None) -> dict:
    """``fill`` is the net price per share you actually got: positive for a debit, negative for a credit."""
    return {"id": uuid.uuid4().hex[:8], "symbol": symbol.upper(), "strategy": strategy, "contracts": contracts,
            "entry": fill, "opened": opened, "stop": stop, "target": target, "close_by": close_by,
            "entry_spot": spot, "high_water": spot, "low_water": spot, "status": "open",
            "bias": _bias(contracts, spot) if spot else 0}


def _bias(contracts: list[dict], spot: float) -> int:
    def payoff(s):
        tot = 0.0
        for c in contracts:
            v = max(0.0, s - c["strike"]) if c["type"] == "call" else max(0.0, c["strike"] - s)
            tot += (1 if c["action"] == "buy" else -1) * v * c["qty"]
        return tot
    up, dn = payoff(spot * 1.5), payoff(spot * 0.5)   # far enough to clear out-of-the-money strikes
    if abs(up - dn) < 1e-9 or (up > 0 and dn > 0):
        return 0
    return 1 if up > dn else -1


def _qty(pos: dict) -> int:
    return max(c["qty"] for c in pos["contracts"])


def value_now(pos: dict, chain) -> float | None:
    """Net per-share value to close now: sell longs at the bid, buy back shorts at the ask."""
    if not chain:
        return None
    idx = {(l.type, round(l.strike, 3), l.expiration): l for l in chain}
    total = 0.0
    for c in pos["contracts"]:
        l = idx.get((c["type"], round(float(c["strike"]), 3), c["expiration"]))
        if l is None:
            return None
        px = l.bid if c["action"] == "buy" else l.ask
        if px is None:
            return None
        total += (1 if c["action"] == "buy" else -1) * px * c["qty"] / _qty(pos)
    return total


def check(pos: dict, spot: float | None, chain, as_of: date, trend: str | None = None) -> dict:
    """Apply the management rules. Updates the position's high/low-water marks in place."""
    reasons: list[tuple[str, str, tuple[str, ...]]] = []   # (action, text, principles)
    add = lambda a, text, *ids: reasons.append((a, text, ids))
    entry, qty, bias = pos["entry"], _qty(pos), pos.get("bias", 0)
    credit = entry < 0
    val = value_now(pos, chain)
    pnl = None if val is None else (val - entry) * 100 * qty

    last_exp = max(date.fromisoformat(c["expiration"]) for c in pos["contracts"])
    first_exp = min(date.fromisoformat(c["expiration"]) for c in pos["contracts"])
    if as_of > last_exp:
        add(DONE, "All options have expired — record the result and close it out here.", "OTS-088")
    days_left = (first_exp - as_of).days

    if spot is not None:
        if bias > 0:
            pos["high_water"] = max(pos.get("high_water") or spot, spot)
        elif bias < 0:
            pos["low_water"] = min(pos.get("low_water") or spot, spot)
        stop = pos.get("stop")
        if stop is not None and ((bias > 0 and spot <= stop) or (bias < 0 and spot >= stop)):
            add(EXIT, f"Stop hit: {pos['symbol']} at {spot:.2f} vs stop {stop:.2f}. Close it — stops are always obeyed.",
                "OTS-087-a", "OTS-087-d")
        target = pos.get("target")
        if target is not None and ((bias > 0 and spot >= target) or (bias < 0 and spot <= target)):
            add(PROFIT, f"Stock reached the {target:.2f} target — take profits or tighten the stop.", "OTS-022-b")
        in_profit = val is not None and ((val > entry) if not credit else (val > entry))
        if not credit and in_profit:
            if bias > 0 and spot <= pos["high_water"] * 0.95:
                add(EXIT, f"Trailing stop: {pos['symbol']} fell 5% from its high of {pos['high_water']:.2f} while "
                          "you're in profit — lock it in.", "OTS-022-a", "OTS-087-c")
            if bias < 0 and spot >= pos["low_water"] * 1.05:
                add(EXIT, f"Trailing stop: {pos['symbol']} rose 5% from its low of {pos['low_water']:.2f} while "
                          "you're in profit — lock it in.", "OTS-022-a", "OTS-087-c")

    if val is not None and not credit and entry > 0:
        mult = val / entry
        if mult >= 3:
            add(PROFIT, f"Option is worth {mult:.1f}× what you paid — sell more (scale out at 2×, 3×…).", "OTS-025-c")
        elif mult >= 2:
            what = "sell half and let the rest ride" if qty >= 2 else "sell it, or hold with a 5% trailing stop"
            add(PROFIT, f"Option has doubled ({val:.2f} vs {entry:.2f}) — {what}.", "OTS-021")
    if val is not None and credit:
        cost_to_close = -val
        if cost_to_close <= 0.2 * (-entry):
            add(BUYBACK, f"Buy it back for {cost_to_close:.2f}: most of the {-entry:.2f} premium is captured — "
                         "get out of the hot seat.", "OTS-046-a", "OTS-097-f")

    close_by = pos.get("close_by")
    if close_by and as_of >= date.fromisoformat(close_by) and as_of <= last_exp:
        add(EXIT, f"Time limit reached (close by {close_by}).", "OTS-024-a", "OTS-023-b")
    elif 0 <= days_left <= 2:
        add(EXIT, f"{days_left} day(s) to expiration — don't hold into expiration.", "OTS-024-a", "OTS-039-b")
    if trend and bias and ((bias > 0 and trend == "down") or (bias < 0 and trend == "up")):
        add(EXIT, f"The trend has turned {trend} — against the position.", "OTS-085", "IAO-02-09")

    action = min((r[0] for r in reasons), key=lambda a: PRIORITY[a], default=HOLD)
    if action == HOLD:
        reasons.append((HOLD, "No exit rule triggered — hold and re-check next pull.", ("OTS-093",)))
    return {"id": pos["id"], "symbol": pos["symbol"], "action": action, "value": val, "pnl": pnl,
            "spot": spot, "days_left": days_left,
            "reasons": [{"action": a, "text": t, "principles": list(ids)} for a, t, ids in
                        sorted(reasons, key=lambda r: PRIORITY[r[0]])]}


def describe(pos: dict) -> str:
    def leg(c):
        d = date.fromisoformat(c["expiration"]).strftime("%b %d, %Y").replace(" 0", " ")
        return f'{"Long" if c["action"] == "buy" else "Short"} {c["qty"]} × {pos["symbol"]} {d} ${c["strike"]:g} {c["type"]}'
    return " + ".join(leg(c) for c in pos["contracts"])
