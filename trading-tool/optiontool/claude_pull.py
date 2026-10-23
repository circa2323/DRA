"""Pull data through Claude in Chrome (reading Schwab's website) while the
Schwab API is pending.

The app writes ``_PULL_REQUEST.json`` into the CSV inbox: which tickers,
expirations and strikes to copy, and the exact file format. You paste one
generated instruction into Claude in Chrome; Claude writes the files plus
``_PULL_DONE.txt``; the app notices and runs the analysis by itself.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from pathlib import Path

from . import positions

REQUEST, DONE = "_PULL_REQUEST.json", "_PULL_DONE.txt"
COLUMNS = "expiration,strike,type,bid,ask,iv,open_interest,volume"


def inbox(cfg: dict) -> Path:
    return Path(cfg["csv_folder"]).expanduser().resolve()


def _fridays(start: date, end: date):
    d = start + timedelta(days=(4 - start.weekday()) % 7)
    while d <= end:
        yield d
        d += timedelta(days=7)


def expirations(cfg: dict, as_of: date) -> list[str]:
    """Standard monthlies (third Friday) in the configured window, plus weekly
    Fridays out to 30 days when weeklies are on."""
    lo = as_of + timedelta(days=cfg.get("min_dte", 20))
    hi = as_of + timedelta(days=cfg.get("max_dte", 160))
    monthly = [d for d in _fridays(lo, hi) if 15 <= d.day <= 21][: cfg.get("expiration_count", 4)]
    out = set(monthly)
    if cfg.get("include_weeklies"):
        out |= set(_fridays(lo, min(hi, as_of + timedelta(days=30))))
    return [d.isoformat() for d in sorted(out)]


def build_request(cfg: dict, now: datetime | None = None) -> dict:
    now = now or datetime.now()
    as_of = now.date()
    folder = inbox(cfg)
    folder.mkdir(parents=True, exist_ok=True)
    exps = expirations(cfg, as_of)
    band = cfg.get("strike_band", 0.15)
    tickers = [{"symbol": s, "expirations": exps, "strike_band_pct": round(band * 100),
                "chain_file": f"{s}_chain.csv", "spot_file": f"{s}_spot.txt"} for s in cfg["watchlist"]]
    held = {}
    for p in positions.load(cfg):
        if p.get("status") == "open":
            held.setdefault(p["symbol"], set()).update(c["expiration"] for c in p["contracts"])
    for sym, held_exps in held.items():
        t = next((t for t in tickers if t["symbol"] == sym), None)
        if t is None:
            tickers.append({"symbol": sym, "expirations": sorted(held_exps), "strike_band_pct": round(band * 100),
                            "chain_file": f"{sym}_chain.csv", "spot_file": f"{sym}_spot.txt", "held_only": True})
        else:
            t["expirations"] = sorted(set(t["expirations"]) | held_exps)
    req = {"created_at": now.isoformat(timespec="seconds"), "inbox": str(folder), "columns": COLUMNS,
           "done_file": DONE, "tickers": tickers, "analyzed": False}
    (folder / DONE).unlink(missing_ok=True)
    (folder / REQUEST).write_text(json.dumps(req, indent=2), encoding="utf-8")
    return req


def load_request(cfg: dict) -> dict | None:
    p = inbox(cfg) / REQUEST
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


def mark_analyzed(cfg: dict) -> None:
    req = load_request(cfg)
    if req:
        req["analyzed"] = True
        (inbox(cfg) / REQUEST).write_text(json.dumps(req, indent=2), encoding="utf-8")


def cancel(cfg: dict) -> None:
    (inbox(cfg) / REQUEST).unlink(missing_ok=True)


def status(cfg: dict, req: dict) -> dict:
    folder = Path(req["inbox"])
    since = datetime.fromisoformat(req["created_at"]).timestamp() - 5
    fresh = lambda f: (folder / f).exists() and (folder / f).stat().st_mtime >= since
    rows = [{"symbol": t["symbol"], "chain": fresh(t["chain_file"]), "spot": fresh(t["spot_file"])}
            for t in req["tickers"]]
    have = sum(r["chain"] and r["spot"] for r in rows)
    return {"rows": rows, "have": have, "need": len(rows), "done": fresh(DONE),
            "complete": fresh(DONE) or (rows and have == len(rows))}


def prompt(req: dict) -> str:
    """The instruction to paste into Claude in Chrome."""
    lines = []
    for t in req["tickers"]:
        exps = ", ".join(date.fromisoformat(e).strftime("%b %d %Y") for e in t["expirations"])
        lines.append(f"- {t['symbol']}: expirations {exps}; strikes within ±{t['strike_band_pct']}% of the "
                     f"stock price → save as {t['chain_file']} and {t['spot_file']}")
    return f"""Please pull option-chain data from Schwab's website for my trading tool. Read-only: do NOT place,
stage or preview any orders, and don't change any account settings.

Save every file into this folder (overwrite existing files):
{req['inbox']}

For each ticker below, open Schwab's option chain for it (Trade → Options, or the ticker's Options tab):
{chr(10).join(lines)}

1. The stock's current price → write just the number (e.g. 343.99) into the spot file.
2. The chain → a CSV file with exactly this header row:
   {req['columns']}
   - one row per contract: calls and puts, every listed strike in the range, each requested expiration
     (if an expiration isn't listed, use the nearest listed one and say so)
   - expiration as YYYY-MM-DD; type as call or put; bid and ask as plain numbers
   - iv as a percent number (e.g. 28.5); Schwab's Greeks view shows bid, ask, IV and open interest,
     the regular chain view shows volume; leave a cell blank if a value isn't shown
3. Check each file: bid ≤ ask on every row, no $ signs or thousands commas, the strikes bracket the
   stock price. Fix anything that's off.
4. Only after all tickers are saved, create {req['done_file']} in the same folder containing the word done.

Do this during market hours (after 9:35 am ET) so the quotes are live. If Schwab asks me to log in, stop and
tell me. When finished, list each file you wrote and anything you couldn't get.

(Request created {req['created_at']}; details are also in {req['inbox']}\\{REQUEST}.)"""
