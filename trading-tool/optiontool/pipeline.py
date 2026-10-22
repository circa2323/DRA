"""The "Pull" button: fetch data, save a dated snapshot, run the books' scan
on every ticker in the watchlist, and return results ready to display."""

from __future__ import annotations

import csv
import json
import traceback
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

from . import advisor, report, ticket
from . import settings as settings_mod
from .data import PriceHistory, load_csv
from .knowledge import Library
from .providers import ProviderError, make_provider

try:
    from zoneinfo import ZoneInfo
    NY = ZoneInfo("America/New_York")
except Exception:  # pragma: no cover - no tz database
    NY = None

DEFAULT_CONFIG = {
    "provider": "csv",                      # "schwab" or "csv"
    "csv_folder": "data/inbox",
    "fetch_history": True,                  # CSV mode: download price history from Yahoo if no <SYM>.csv
    "data_dir": "data",
    "watchlist": ["SPY", "XLK", "AAPL", "NVDA"],
    "benchmarks": {"XLK": "SPY", "AAPL": "XLK", "NVDA": "XLK"},
    "holdings": [],                         # tickers you own (covered-call / hedge ideas)
    "account_value": None,
    "option_budget": None,                  # option-buying portfolio (OTS-025-a)
    "small_account_mode": False,            # book % limits warn instead of block; max_trade_risk enforced
    "max_trade_risk": None,                 # dollars you allow one trade to lose (user setting)
    "min_dte": 20, "max_dte": 160, "expiration_count": 4, "strike_band": 0.15,
    "top": 5,
    "schwab": {"app_key": "", "app_secret": "", "callback_url": "https://127.0.0.1",
               "token_path": "~/.optiontool/schwab_token.json"},
}


def load_config(path: str | Path) -> dict:
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))
    p = Path(path)
    if p.exists():
        user = json.loads(p.read_text(encoding="utf-8"))
        cfg.update({k: v for k, v in user.items() if k != "schwab"})
        cfg["schwab"].update(user.get("schwab", {}))
    return cfg


def save_config(path: str | Path, cfg: dict) -> None:
    Path(path).write_text(json.dumps(cfg, indent=2), encoding="utf-8")


def market_status(now: datetime | None = None) -> tuple[bool, str]:
    """Regular session is 9:30-16:00 ET on weekdays (holidays not tracked)."""
    if NY is None:
        return True, "time zone data unavailable"
    now = (now or datetime.now(NY)).astimezone(NY)
    if now.weekday() >= 5:
        return False, "market closed (weekend) — quotes are stale"
    t = now.hour * 60 + now.minute
    if t < 9 * 60 + 35:
        return False, "before the open — pre-market option spreads are wide and will trip the liquidity and fair-value checks"
    if t >= 16 * 60:
        return False, "after the close — quotes are end-of-day"
    return True, "market open"


@dataclass
class TickerResult:
    symbol: str
    ok: bool
    error: str = ""
    spot: float | None = None
    benchmark: str | None = None
    technical: list[str] = field(default_factory=list)
    regime: str | None = None
    ideas: list[dict] = field(default_factory=list)
    candidates: list[dict] = field(default_factory=list)


@dataclass
class RunResult:
    snapshot: str
    as_of: str
    provider: str
    market_note: str
    tickers: list[TickerResult]

    def to_json(self) -> str:
        return json.dumps(self, default=lambda o: o.__dict__, indent=2)


# ---------------------------------------------------------------- pull
def pull(cfg: dict, now: datetime | None = None) -> Path:
    """Fetch history + chain for every watchlist and benchmark symbol into a new snapshot folder."""
    provider = make_provider(cfg)
    now = now or (datetime.now(NY) if NY else datetime.now())
    as_of = now.date()
    snap = Path(cfg["data_dir"]).expanduser() / "snapshots" / now.strftime("%Y-%m-%d_%H%M")
    snap.mkdir(parents=True, exist_ok=True)
    symbols = list(dict.fromkeys(cfg["watchlist"] + [b for b in cfg["benchmarks"].values() if b]))
    errors = {}
    for sym in symbols:
        try:
            _write_history(snap / f"{sym}.csv", provider.history(sym))
        except ProviderError as e:
            errors[sym] = str(e)
            continue
        if sym not in cfg["watchlist"]:
            continue
        try:
            ch = provider.chain(sym, as_of, cfg)
            _write_chain(snap / f"{sym}_chain.csv", ch.legs)
            _log_iv(cfg, as_of, sym, ch)
            (snap / f"{sym}_spot.txt").write_text(str(ch.spot), encoding="utf-8")
        except ProviderError as e:
            errors[sym] = str(e)
    if getattr(provider, "stale_spot", False):
        errors_note = "no live price given for some chains (add <SYM>_spot.txt) — using last close"
    else:
        errors_note = ""
    open_, note = market_status(now)
    if errors_note:
        note = f"{note}; {errors_note}"
    (snap / "meta.json").write_text(json.dumps({
        "as_of": as_of.isoformat(), "pulled_at": now.isoformat(), "provider": provider.name,
        "market_note": note, "errors": errors}, indent=2), encoding="utf-8")
    return snap


def _write_history(path: Path, h: PriceHistory) -> None:
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["Date", "Open", "High", "Low", "Close", "Volume"])
        for row in zip(h.dates, h.opens, h.highs, h.lows, h.closes, h.volumes):
            w.writerow(row)


def _write_chain(path: Path, legs) -> None:
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["expiration", "strike", "type", "bid", "ask", "iv", "open_interest", "volume"])
        for l in sorted(legs, key=lambda l: (l.expiration, l.strike, l.type)):
            w.writerow([l.expiration, l.strike, l.type, l.bid, l.ask,
                        "" if l.iv is None else round(l.iv, 4),
                        "" if l.open_interest is None else l.open_interest,
                        "" if l.volume is None else l.volume])


def _log_iv(cfg: dict, as_of: date, sym: str, ch) -> None:
    """Record one at-the-money IV per symbol per day. Over time this builds the
    IV history needed to judge "extremely" high or low IV (OTS-057-b, IAO-17-05)."""
    exps = sorted({l.expiration for l in ch.legs})
    if not exps:
        return
    near = [l for l in ch.legs if l.expiration == exps[0] and l.iv]
    if not near:
        return
    atm = min(near, key=lambda l: abs(l.strike - ch.spot))
    path = Path(cfg["data_dir"]).expanduser() / "iv_log.csv"
    rows = {}
    if path.exists():
        with path.open(newline="", encoding="utf-8-sig") as fh:
            rows = {(r["date"], r["symbol"]): r["atm_iv"] for r in csv.DictReader(fh)}
    rows[(as_of.isoformat(), sym)] = f"{atm.iv:.4f}"
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["date", "symbol", "atm_iv"])
        for (d, s), v in sorted(rows.items()):
            w.writerow([d, s, v])


def iv_history(cfg: dict, sym: str, min_readings: int = 60) -> list[float] | None:
    path = Path(cfg["data_dir"]).expanduser() / "iv_log.csv"
    if not path.exists():
        return None
    with path.open(newline="", encoding="utf-8-sig") as fh:
        vals = [float(r["atm_iv"]) for r in csv.DictReader(fh) if r["symbol"] == sym]
    return vals if len(vals) >= min_readings else None


# ---------------------------------------------------------------- analyze
def latest_snapshot(cfg: dict) -> Path | None:
    root = Path(cfg["data_dir"]).expanduser() / "snapshots"
    snaps = sorted(p for p in root.glob("*") if (p / "meta.json").exists()) if root.exists() else []
    return snaps[-1] if snaps else None


def analyze(cfg: dict, snap: Path, library: Library | None = None) -> RunResult:
    library = library or Library()
    settings = settings_mod.load(cfg.get("settings_file"))
    meta = json.loads((snap / "meta.json").read_text(encoding="utf-8"))
    as_of = date.fromisoformat(meta["as_of"])
    out = []
    for sym in cfg["watchlist"]:
        out.append(_analyze_one(cfg, snap, sym, as_of, meta, library, settings))
    result = RunResult(snap.name, meta["as_of"], meta["provider"], meta["market_note"], out)
    (snap / "results.json").write_text(result.to_json(), encoding="utf-8")
    return result


def _analyze_one(cfg, snap, sym, as_of, meta, library, settings) -> TickerResult:
    if sym in meta.get("errors", {}):
        return TickerResult(sym, False, meta["errors"][sym])
    try:
        hist = load_csv(snap / f"{sym}.csv", sym)
        spot_file = snap / f"{sym}_spot.txt"
        if spot_file.exists():           # live quote beats yesterday's close
            spot = float(spot_file.read_text(encoding="utf-8"))
            if hist.dates and hist.dates[-1] < as_of.isoformat():
                hist.dates.append(as_of.isoformat())
                for arr in (hist.opens, hist.highs, hist.lows, hist.closes):
                    arr.append(spot)
                hist.highs[-1] = max(spot, hist.closes[-2])
                hist.lows[-1] = min(spot, hist.closes[-2])
                hist.volumes.append(0.0)
        bsym = cfg["benchmarks"].get(sym)
        bench = load_csv(snap / f"{bsym}.csv", bsym) if bsym and (snap / f"{bsym}.csv").exists() else None
        chain = advisor.load_chain(snap / f"{sym}_chain.csv")
        plan = {"option_buying_portfolio": cfg["option_budget"]} if cfg.get("option_budget") else {}
        small = bool(cfg.get("small_account_mode"))
        if small:
            plan["small_account_mode"] = True
        if cfg.get("max_trade_risk"):
            plan["max_trade_risk"] = float(cfg["max_trade_risk"])
        tv, regime, found, best = advisor.scan(
            sym, hist, chain, library, settings, as_of, cfg.get("account_value"), plan,
            sym in cfg.get("holdings", []), bench, iv_history(cfg, sym), cfg.get("top", 5),
            contracts=1 if small else 3)
        return TickerResult(
            sym, True, spot=hist.last, benchmark=bsym, technical=tv.summary_lines(), regime=regime,
            ideas=[{"strategy": i.strategy, "rationale": i.rationale, "principles": list(i.principles)}
                   for i in found],
            candidates=sorted([{"score": round(s.score), "idea": s.idea.strategy,
                         "label": ticket.action_label(s.decision), "ticket": ticket.build(s.decision),
                         **json.loads(report.as_json(s.decision, library)),
                         "legs": [{"action": m.leg.action, "qty": m.leg.quantity, "type": m.leg.type,
                                   "strike": m.leg.strike, "expiration": m.leg.expiration, "dte": m.dte,
                                   "price": m.market, "fair": m.fair, "iv": m.iv, "delta": m.delta}
                                  for m in s.decision.context.legs],
                         "stock_legs": [{"action": l.action, "qty": l.quantity, "price": l.price}
                                        for l in s.decision.context.trade.legs if l.type == "stock"]}
                        for s in best], key=lambda c: (ticket.ORDER[c["label"]], -c["score"])))
    except Exception as e:  # keep the other tickers going
        return TickerResult(sym, False, f"{type(e).__name__}: {e}\n{traceback.format_exc(limit=3)}")
