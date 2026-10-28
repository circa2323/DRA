"""Your brokerage portfolio (e.g. Robinhood): import, then book-based review.

Robinhood has no public API, so holdings come in as a CSV: written by Claude
in Chrome from Robinhood's portfolio page, pasted by hand, or exported.

Stocks get a recommendation each (hold, trim/sell, hedge, covered call,
stock replacement...) from the books' rules; options go into My trades so the
exit rules check them on every pull; and the portfolio as a whole is checked
for concentration, option exposure and put/call balance.
"""

from __future__ import annotations

import csv
import io
import json
from datetime import date
from pathlib import Path

from . import positions

FILE = "PORTFOLIO.csv"
COLUMNS = "symbol,kind,quantity,avg_price,option_type,strike,expiration,side"


def path(cfg: dict) -> Path:
    return Path(cfg["data_dir"]).expanduser() / "portfolio.json"


def load(cfg: dict) -> dict | None:
    p = path(cfg)
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


def _num(v):
    if v is None:
        return None
    v = str(v).strip().replace("$", "").replace(",", "")
    if v in ("", "-", "--"):
        return None
    neg = v.startswith("(") and v.endswith(")")
    v = v.strip("()")
    x = float(v.rstrip("%"))
    return -x if neg else x


def parse(text: str) -> dict:
    """CSV with columns like COLUMNS (case-insensitive; extra columns ignored).

    kind: stock | option | cash.  For options: option_type call/put, strike,
    expiration (YYYY-MM-DD or MM/DD/YYYY), side long/short (or negative quantity).
    """
    from .advisor import _iso
    rows = list(csv.DictReader(io.StringIO(text.strip().lstrip("﻿"))))
    out = {"stocks": [], "options": [], "cash": None, "imported": date.today().isoformat()}
    for raw in rows:
        r = {(k or "").strip().lower().replace(" ", "_"): (v or "").strip() for k, v in raw.items()}
        kind = (r.get("kind") or r.get("type") or "").lower()
        sym = (r.get("symbol") or r.get("ticker") or "").upper()
        if kind == "cash" or sym in ("CASH", "BUYING_POWER"):
            out["cash"] = _num(r.get("quantity") or r.get("value") or r.get("avg_price"))
            continue
        if not sym:
            continue
        qty = _num(r.get("quantity") or r.get("shares") or r.get("contracts")) or 0
        avg = _num(r.get("avg_price") or r.get("average_cost") or r.get("avg_cost") or r.get("cost"))
        otype = (r.get("option_type") or "").lower()
        if kind == "option" or otype in ("call", "put"):
            side = (r.get("side") or "").lower()
            short = side.startswith("short") or side.startswith("sell") or qty < 0
            out["options"].append({"symbol": sym, "type": otype, "strike": _num(r.get("strike")),
                                   "expiration": _iso(r.get("expiration") or r.get("expiry")),
                                   "qty": int(abs(qty)), "avg_price": abs(avg or 0), "short": short})
        else:
            out["stocks"].append({"symbol": sym, "shares": qty, "avg_cost": avg})
    if not out["stocks"] and not out["options"]:
        raise ValueError("no holdings found — check the header row: " + COLUMNS)
    return out


def save_import(cfg: dict, pf: dict) -> dict:
    """Store the portfolio; sync options into My trades and 100+-share stocks into holdings."""
    p = path(cfg)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(pf, indent=2), encoding="utf-8")
    book = positions.load(cfg)
    have = {(x["symbol"], c["type"], float(c["strike"]), c["expiration"], c["action"])
            for x in book if x.get("status") == "open" for c in x["contracts"]}
    added = 0
    for o in pf["options"]:
        action = "sell" if o["short"] else "buy"
        key = (o["symbol"], o["type"], float(o["strike"]), o["expiration"], action)
        if key in have:
            continue
        c = {"action": action, "type": o["type"], "strike": float(o["strike"]), "expiration": o["expiration"],
             "qty": o["qty"], "price": o["avg_price"]}
        pos = positions.new_position(o["symbol"], [c], -o["avg_price"] if o["short"] else o["avg_price"],
                                     pf["imported"], strategy=f"{'short' if o['short'] else 'long'}_{o['type']}")
        pos["bias"] = (1 if o["type"] == "call" else -1) * (-1 if o["short"] else 1)
        pos["source"] = "portfolio import"
        book.append(pos)
        added += 1
    positions.save(cfg, book)
    return {"options_added": added,
            "covered_call_eligible": [s["symbol"] for s in pf["stocks"] if s["shares"] >= 100]}


def symbols(cfg: dict) -> list[str]:
    pf = load(cfg)
    return sorted({s["symbol"] for s in pf["stocks"]}) if pf else []


# ---------------------------------------------------------------- review
def review(pf: dict, prices: dict, techs: dict, regimes: dict | None = None) -> dict:
    """Recommendations per stock and for the portfolio.

    prices: {SYM: last price}; techs: {SYM: TechnicalView}; regimes: {SYM: 'high'/'low'/None}.
    """
    regimes = regimes or {}
    stocks = []
    total_stock = 0.0
    for s in pf["stocks"]:
        px = prices.get(s["symbol"])
        value = px * s["shares"] if px else None
        total_stock += value or 0
        stocks.append({**s, "price": px, "value": value})
    opt_value = sum(o["avg_price"] * 100 * o["qty"] for o in pf["options"] if not o["short"])
    cash = pf.get("cash") or 0.0
    total = total_stock + opt_value + cash

    out = []
    for s in stocks:
        sym, px, tv = s["symbol"], s["price"], techs.get(s["symbol"])
        recs = []
        add = lambda action, text, *ids: recs.append({"action": action, "text": text, "principles": list(ids)})
        gain = (px / s["avg_cost"] - 1) if px and s["avg_cost"] else None
        lots = s["shares"] >= 100
        if tv is None:
            add("REVIEW", "No price history — can't read the trend.", "OTS-005")
        else:
            regime = regimes.get(sym)
            if tv.primary_trend == "down":
                add("TRIM / SELL", "Trend has turned down — when the trend goes against you, get out or reduce.",
                    "OTS-085", "IAO-02-09")
                if gain is not None and gain < -0.20:
                    add("TRIM / SELL", f"Down {abs(gain):.0%} from your cost — ignore the sunk cost: would you buy it "
                        "today at this price? Don't average down.", "OTS-086", "IAO-13-03")
                if lots:
                    if regime == "high":
                        add("HEDGE", "If you keep it: premiums are high — a collar (sell an OTM call to pay for "
                            "the put).", "IAO-14-12", "IAO-15-01")
                    else:
                        add("HEDGE", "If you keep it: buy a 2–3 month at-the-money put as protection.",
                            "IAO-14-10", "IAO-14-11", "OTS-037-a")
            elif tv.primary_trend == "up":
                if tv.overbought_daily and tv.overbought_weekly:
                    add("COVERED CALL" if lots else "HOLD",
                        "Overbought on daily and weekly charts — " +
                        ("write a covered call 3–9 weeks out, strike above the price." if lots else
                         "a pullback is likely; covered calls need 100 shares, so tighten your stop instead."),
                        "IAO-06-01", "IAO-02-19", "IAO-02-21")
                elif tv.momentum_fading:
                    add("COVERED CALL" if lots else "HOLD",
                        "Uptrend but momentum is fading — " +
                        ("write an out-of-the-money call." if lots else "tighten your stop."),
                        "IAO-16-17", "IAO-06-08")
                else:
                    add("HOLD", "Uptrend intact — let it run; the trend is your friend until it breaks.",
                        "IAO-02-07", "IAO-02-09")
                if gain is not None and 0.10 <= gain <= 0.50:
                    add("REVIEW", f"Up {gain:.0%}: if bought within the last 6 months, consider stock replacement — "
                        "sell the shares, buy an in-the-money call to keep the upside with less capital at risk.",
                        "IAO-14-20", "IAO-14-21")
                if gain is not None and gain > 0:
                    add("HOLD", f"In profit — trail a stop about 5% below the price (≈ {px * 0.95:.2f}).",
                        "OTS-022-a", "OTS-087-c")
            else:
                add("HOLD", "Sideways — no trend to act on; keep a stop at support"
                    + (f" (≈ {tv.support:.2f})." if tv.support else "."), "IAO-03-13", "OTS-012-a")
        weight = (s["value"] / total) if s["value"] and total else None
        if weight is not None and weight > 0.25 and len(stocks) > 1:
            add("TRIM / SELL", f"{weight:.0%} of the portfolio in one stock — don't plunge into one 'sure thing'.",
                "OTS-027", "OTS-035-a")
        order = ["TRIM / SELL", "HEDGE", "COVERED CALL", "REVIEW", "HOLD"]
        action = min((r["action"] for r in recs), key=order.index, default="HOLD")
        out.append({**s, "gain": gain, "weight": weight, "action": action, "recommendations": recs,
                    "trend": tv.primary_trend if tv else None})

    port = []
    padd = lambda text, *ids: port.append({"text": text, "principles": list(ids)})
    if total:
        share = opt_value / total
        if share > 0.10:
            padd(f"Long options are {share:.0%} of the portfolio — the books keep speculative buying at 5–10%.",
                 "OTS-094-a", "OTS-084")
    calls = sum(o["qty"] for o in pf["options"] if o["type"] == "call" and not o["short"])
    puts = sum(o["qty"] for o in pf["options"] if o["type"] == "put" and not o["short"])
    if calls and not puts:
        padd("All long calls, no puts — Trester biases the option book toward puts (and both sides for balance).",
             "OTS-034", "OTS-035-a")
    if stocks and not puts:
        padd("No puts protecting the stock holdings — keep some cheap insurance puts.", "OTS-037-a", "OTS-037-b")
    if 0 < len(stocks) + len(pf["options"]) < 3:
        padd("Very few positions — diversify across more names and over time.", "OTS-035-a", "OTS-035-b")
    if total and cash / total < 0.10 and total > 0:
        padd("Little cash left — keep reserves for new opportunities and losing streaks.", "OTS-094-b", "OTS-035-b")
    return {"stocks": out, "portfolio": port, "total": total, "cash": cash, "stock_value": total_stock,
            "option_cost": opt_value}


def claude_prompt(cfg: dict) -> str:
    folder = Path(cfg["csv_folder"]).expanduser().resolve()
    return f"""Please read my Robinhood portfolio for my trading tool. Read-only: do NOT place, cancel or change
any orders, transfers or settings.

1. Open robinhood.com (I'm logged in) and go to my account / investing portfolio.
2. For every stock/ETF holding, note: symbol, number of shares, average cost per share.
3. For every option position, note: symbol, call or put, strike, expiration date, number of contracts,
   long or short, and the average price per share (e.g. 0.45, not $45).
4. Note my buying power / cash.
5. Save a CSV file named {FILE} into this folder (overwrite if it exists):
   {folder}
   with exactly this header row:
   {COLUMNS}
   - stocks: kind = stock, quantity = shares, avg_price = average cost; leave option columns blank
   - options: kind = option, quantity = contracts, avg_price = average price per share,
     option_type = call or put, strike, expiration as YYYY-MM-DD, side = long or short
   - one row: symbol = CASH, kind = cash, quantity = cash / buying power
   - plain numbers only (no $ or commas)
6. Tell me how many stock and option rows you saved. If Robinhood asks me to log in or verify, stop and tell me."""
