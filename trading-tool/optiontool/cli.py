"""Command line: python -m optiontool <command> ..."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

from . import advisor, context, engine, report, rules
from . import settings as settings_mod
from .data import load_csv
from .knowledge import DEFAULT_DIR, Library
from .technical import analyze
from .trade import Trade


def _iv_history(path):
    if not path:
        return None
    vals = [float(x) for x in Path(path).read_text().replace(",", "\n").split() if x.strip()]
    return [v / 100 if v > 3 else v for v in vals]


def cmd_evaluate(a, lib, cfg):
    spec = json.loads(Path(a.trade).read_text())
    base = Path(a.trade).parent
    trade = Trade.from_dict(spec)
    hist_path = a.history or spec.get("history_csv")
    bench_path = a.benchmark or spec.get("benchmark_csv")
    resolve = lambda p: p if p is None or Path(p).is_absolute() or Path(p).exists() else str(base / p)
    hist = load_csv(resolve(hist_path), trade.underlying) if hist_path else None
    bench = load_csv(resolve(bench_path)) if bench_path else None
    ctx = context.build(trade, cfg, hist, spec.get("plan"), _iv_history(resolve(a.iv_history or spec.get("iv_history"))), bench)
    dec = engine.evaluate(ctx, lib)
    print(report.as_json(dec, lib) if a.json else report.text(dec, lib, not a.no_sources))
    return {"REJECT": 2, "CAUTION": 1, "ACCEPT": 0}[dec.verdict] if a.exit_code else 0


def cmd_analyze(a, lib, cfg):
    hist = load_csv(a.history, a.symbol)
    bench = load_csv(a.benchmark) if a.benchmark else None
    tv = analyze(hist, bench)
    print("\n".join(tv.summary_lines()))
    print()
    print(f"STRATEGY IDEAS (premium regime: {a.iv_regime or 'unknown — pass --iv-regime or use scan'})")
    for i in advisor.ideas(tv, a.iv_regime, a.holding):
        print(f"  → {i.strategy}: {i.rationale}  [{', '.join(i.principles)}]")
    return 0


def cmd_scan(a, lib, cfg):
    hist = load_csv(a.history, a.symbol)
    bench = load_csv(a.benchmark) if a.benchmark else None
    chain = advisor.load_chain(a.chain)
    as_of = date.fromisoformat(a.as_of) if a.as_of else date.fromisoformat(hist.dates[-1][:10])
    plan = {"option_buying_portfolio": a.option_budget} if a.option_budget else {}
    tv, regime, found, best = advisor.scan(a.symbol, hist, chain, lib, cfg, as_of, a.account,
                                           plan, a.holding, bench, _iv_history(a.iv_history), a.top)
    print("\n".join(tv.summary_lines()))
    print(f"\nPremium regime: {regime or 'unknown'}\n\nSTRATEGY IDEAS")
    for i in found:
        print(f"  → {i.strategy}: {i.rationale}  [{', '.join(i.principles)}]")
    if not best:
        print("\nNo candidates could be built from the chain for these ideas.")
        return 0
    print(f"\nTOP {len(best)} CANDIDATES")
    for n, s in enumerate(best, 1):
        print(f"\n#{n}  score {s.score:.0f}  — idea: {s.idea.strategy}")
        print("\n".join("  " + line for line in
                        report.text(s.decision, lib, show_sources=False).splitlines()))
    return 0


def cmd_principles(a, lib, cfg):
    found = lib.search(" ".join(a.text), a.category, a.applies_to, a.book)
    enforced = rules.all_principle_ids(lib)
    for p in found:
        mark = "*" if p.id in enforced else " "
        print(f"{mark} {p.id:11} p.{p.page!s:5} [{p.category}] {p.principle}")
    print(f"\n{len(found)} principle(s). * = enforced by a rule check.")
    return 0


def cmd_explain(a, lib, cfg):
    for pid in a.ids:
        p = lib.get(pid)
        print(p.citation(lib))
        print(f"  {p.principle}")
        if p.quote:
            print(f"  \"{p.quote}\"")
        if p.rule:
            print(f"  rule: {json.dumps(p.rule)}")
        users = [fn.__name__ for fn in rules.CHECKS if pid in fn.principles]
        if not users and pid in rules.all_principle_ids(lib):
            users = ['technical analysis / advisor / management plan']
        print(f"  enforced by: {', '.join(users) or '(not yet enforced by a check)'}\n")
    return 0


def cmd_books(a, lib, cfg):
    enforced = rules.all_principle_ids(lib)
    for bid, b in lib.books.items():
        ps = [p for p in lib.principles.values() if p.book_id == bid]
        e = sum(1 for p in ps if p.id in enforced)
        print(f"{bid}: {b['title']} — {b.get('author', '')} ({b.get('year', '')})")
        print(f"     {len(ps)} principles, {e} enforced by rule checks, "
              f"{sum(p.machine_checkable for p in ps)} marked machine-checkable")
    return 0


def cmd_serve(a, lib, cfg):
    from .webapp import serve
    serve(a.config, a.port)
    return 0


def cmd_pull(a, lib, cfg):
    from . import pipeline
    conf = pipeline.load_config(a.config)
    snap = pipeline.latest_snapshot(conf) if a.no_fetch else pipeline.pull(conf)
    if snap is None:
        print("no snapshot to analyze")
        return 1
    res = pipeline.analyze(conf, snap, lib)
    print(f"snapshot {snap}  ({res.market_note})")
    for t in res.tickers:
        if not t.ok:
            print(f"  {t.symbol}: ERROR {t.error.splitlines()[0]}")
            continue
        best = t.candidates[0] if t.candidates else None
        ideas = ", ".join(i["strategy"] for i in t.ideas)
        top = f"top: {best['strategy']} {best['verdict']} (score {best['score']})" if best else "no candidates"
        print(f"  {t.symbol} @ {t.spot:.2f}  regime={t.regime}  ideas: {ideas}  {top}")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="optiontool", description=__doc__)
    ap.add_argument("--knowledge", default=str(DEFAULT_DIR), help="folder of book JSON files")
    ap.add_argument("--settings", help="JSON file overriding user settings")
    sub = ap.add_subparsers(dest="cmd", required=True)

    e = sub.add_parser("evaluate", help="judge a proposed trade against the books")
    e.add_argument("trade", help="trade JSON file")
    e.add_argument("--history", help="price history CSV for the underlying")
    e.add_argument("--benchmark", help="benchmark price history CSV (relative performance)")
    e.add_argument("--iv-history", help="file of past implied volatility readings")
    e.add_argument("--json", action="store_true")
    e.add_argument("--no-sources", action="store_true")
    e.add_argument("--exit-code", action="store_true", help="exit 2 on REJECT, 1 on CAUTION")
    e.set_defaults(fn=cmd_evaluate)

    an = sub.add_parser("analyze", help="technical reading and strategy ideas for a symbol")
    an.add_argument("symbol")
    an.add_argument("--history", required=True)
    an.add_argument("--benchmark")
    an.add_argument("--iv-regime", choices=["high", "low"])
    an.add_argument("--holding", action="store_true", help="you own the stock (hedge/overwrite ideas)")
    an.set_defaults(fn=cmd_analyze)

    sc = sub.add_parser("scan", help="build and rank candidate trades from an option chain")
    sc.add_argument("symbol")
    sc.add_argument("--history", required=True)
    sc.add_argument("--chain", required=True, help="CSV: expiration,strike,type,bid,ask[,iv,open_interest,volume]")
    sc.add_argument("--benchmark")
    sc.add_argument("--iv-history")
    sc.add_argument("--account", type=float, help="total account value")
    sc.add_argument("--option-budget", type=float, help="option-buying portfolio size (OTS-025-a)")
    sc.add_argument("--holding", action="store_true")
    sc.add_argument("--as-of")
    sc.add_argument("--top", type=int, default=5)
    sc.set_defaults(fn=cmd_scan)

    pr = sub.add_parser("principles", help="search the principle library")
    pr.add_argument("text", nargs="*")
    pr.add_argument("--book")
    pr.add_argument("--category")
    pr.add_argument("--applies-to")
    pr.set_defaults(fn=cmd_principles)

    ex = sub.add_parser("explain", help="show principles by id")
    ex.add_argument("ids", nargs="+")
    ex.set_defaults(fn=cmd_explain)

    bk = sub.add_parser("books", help="list loaded books and rule coverage")
    bk.set_defaults(fn=cmd_books)

    sv = sub.add_parser("serve", help="run the local web app (Pull button, ideas, settings)")
    sv.add_argument("--config", default="config.json")
    sv.add_argument("--port", type=int, default=8765)
    sv.set_defaults(fn=cmd_serve)

    pl = sub.add_parser("pull", help="pull data for the watchlist and analyze it (for schedulers)")
    pl.add_argument("--config", default="config.json")
    pl.add_argument("--no-fetch", action="store_true", help="re-analyze the latest snapshot only")
    pl.set_defaults(fn=cmd_pull)

    a = ap.parse_args(argv)
    lib = Library(a.knowledge)
    cfg = settings_mod.load(a.settings)
    return a.fn(a, lib, cfg)


if __name__ == "__main__":
    sys.exit(main())
