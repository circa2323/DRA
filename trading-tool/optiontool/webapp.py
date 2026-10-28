"""Local web app: one click to pull data and see the books' trade ideas.

    python -m optiontool serve            # then open http://127.0.0.1:8765

Binds to 127.0.0.1 only — it holds your broker login, so it is not meant to
be exposed to a network.
"""

from __future__ import annotations

import html
import json
import threading
import traceback
import urllib.parse
from datetime import date, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import __version__, claude_pull, pipeline, portfolio, positions, screener, ticket
from .knowledge import Library
from .providers import ProviderError, SchwabAuth, make_provider
from .rules import RELAXABLE, RELAXABLE_WARN

RELAXED = list(RELAXABLE.values()) + list(RELAXABLE_WARN.values())

E = html.escape


class App:
    def __init__(self, config_path: str):
        self.config_path = config_path
        self.library = Library()
        self.lock = threading.Lock()
        self.job = {"running": False, "step": "", "error": "", "finished": None, "kind": ""}

    @property
    def cfg(self) -> dict:
        return pipeline.load_config(self.config_path)

    def schwab_auth(self) -> SchwabAuth | None:
        s = self.cfg["schwab"]
        if not (s.get("app_key") and s.get("app_secret")):
            return None
        return SchwabAuth(s["app_key"], s["app_secret"], s["callback_url"], s["token_path"])

    def start(self, do_pull: bool) -> None:
        with self.lock:
            if self.job["running"]:
                return
            self.job = {"running": True, "step": "pulling data" if do_pull else "analyzing",
                        "error": "", "finished": None, "kind": "pull"}
        threading.Thread(target=self._run, args=(do_pull,), daemon=True).start()

    def start_screen(self, text: str, opts: dict) -> None:
        with self.lock:
            if self.job["running"]:
                return
            self.job = {"running": True, "step": "screening tickers", "error": "", "finished": None,
                        "kind": "screen"}
        threading.Thread(target=self._run_screen, args=(text, opts), daemon=True).start()

    def _run_screen(self, text: str, opts: dict) -> None:
        try:
            cfg = self.cfg
            syms, bench = screener.parse_symbols(text)
            if not syms:
                raise ProviderError("enter at least one ticker")
            provider = make_provider(cfg)
            rows = screener.screen(syms, provider.history, opts, bench)
            out = Path(cfg["data_dir"]).expanduser() / "screens"
            out.mkdir(parents=True, exist_ok=True)
            (out / "latest.json").write_text(json.dumps({
                "ran_at": datetime.now().strftime("%Y-%m-%d %H:%M"), "opts": opts,
                "rows": screener.rows_to_json(rows)}, indent=2), encoding="utf-8")
        except Exception as e:
            self.job["error"] = f"{e}" if isinstance(e, ProviderError) else traceback.format_exc(limit=4)
        finally:
            self.job["running"] = False
            self.job["finished"] = datetime.now().strftime("%H:%M:%S")

    def _run(self, do_pull: bool) -> None:
        try:
            cfg = self.cfg
            snap = pipeline.pull(cfg) if do_pull else pipeline.latest_snapshot(cfg)
            if snap is None:
                raise ProviderError("no snapshot yet — press Pull first")
            self.job["step"] = "running the books' rules"
            pipeline.analyze(cfg, snap, self.library)
        except Exception as e:
            self.job["error"] = f"{e}" if isinstance(e, ProviderError) else traceback.format_exc(limit=4)
        finally:
            self.job["running"] = False
            self.job["finished"] = datetime.now().strftime("%H:%M:%S")


# ---------------------------------------------------------------- rendering
CSS = """
:root{--bg:#f7f7f5;--card:#fff;--ink:#1d1d1f;--mute:#6b6b70;--line:#e3e3e0;--acc:#2456d6;
--rej:#c62828;--cau:#b26a00;--acc-ok:#2e7d32;--chip:#eef1f8}
@media (prefers-color-scheme:dark){:root{--bg:#141416;--card:#1d1d20;--ink:#ececee;--mute:#9a9aa2;
--line:#2c2c31;--acc:#7aa2ff;--rej:#ff6b6b;--cau:#ffb84d;--acc-ok:#6fcf73;--chip:#262a36}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);
font:15px/1.45 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif}
header{display:flex;gap:16px;align-items:center;flex-wrap:wrap;padding:14px 20px;
border-bottom:1px solid var(--line);background:var(--card)}
header h1{font-size:17px;margin:0 12px 0 0}header nav a{margin-right:14px;color:var(--acc);text-decoration:none}
main{max-width:1100px;margin:0 auto;padding:18px 16px 60px}
.note,.idea,.f,summary,p,code{overflow-wrap:anywhere}
.bar{display:flex;gap:10px;align-items:center;flex-wrap:wrap;margin-bottom:14px}
button,.btn{background:var(--acc);color:#fff;border:0;border-radius:8px;padding:9px 16px;font-size:15px;cursor:pointer}
button.sec{background:transparent;color:var(--acc);border:1px solid var(--acc)}
.note{color:var(--mute);font-size:13px}.warn{color:var(--cau)}.err{color:var(--rej);white-space:pre-wrap}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:16px;margin:14px 0}
.card h2{margin:0 0 6px;font-size:19px}pre{white-space:pre-wrap;font-size:12.5px;margin:6px 0;color:var(--mute)}
.idea{margin:4px 0}.chip{display:inline-block;background:var(--chip);border-radius:6px;padding:0 6px;
font-size:12px;margin:0 3px 3px 0;color:var(--ink);text-decoration:none}
details{border-top:1px solid var(--line);padding:8px 0}summary{cursor:pointer}
.v{font-weight:700;border-radius:6px;padding:1px 8px;font-size:12px;color:#fff}
.REJECT{background:var(--rej)}.CAUTION{background:var(--cau)}.ACCEPT{background:var(--acc-ok)}
table{border-collapse:collapse;font-size:13px;margin:6px 0;width:100%}td,th{padding:3px 8px;border-bottom:1px solid var(--line);text-align:left}
.f{margin:3px 0;font-size:13.5px}.BLOCK{color:var(--rej)}.WARN{color:var(--cau)}.PASS{color:var(--acc-ok)}.INFO{color:var(--mute)}
input[type=text],input[type=number],textarea{width:100%;padding:7px;border:1px solid var(--line);border-radius:6px;
background:var(--bg);color:var(--ink);font:inherit}label{display:block;margin:10px 0 3px;font-weight:600}
.tablewrap{overflow-x:auto}
.sum{display:flex;gap:12px;align-items:baseline;flex-wrap:wrap;padding:10px 0;border-bottom:1px solid var(--line)}
.sum:last-child{border-bottom:0}.sum .tk{font-weight:700;min-width:52px}.sum .what{flex:1;min-width:220px}
.badge{display:inline-block;min-width:150px;text-align:center;font-weight:700;border-radius:7px;padding:3px 10px;
font-size:13px;color:#fff}.b-BUY{background:var(--acc-ok)}.b-SMALL{background:#2f7f8a}
.b-CONSIDER{background:var(--cau)}.b-AGGR{background:#7b4fc4}
.ticket.aggr{border-color:#7b4fc4}.a-EXIT{background:var(--rej)}.a-BUYBACK,.a-PROFIT{background:var(--acc-ok)}
.a-HOLD{background:#5b6b8a}.a-EXPIRED{background:#8a8a90}
.inline{display:inline;width:auto}input.inline{width:auto;display:inline-block;max-width:120px;margin-right:8px}label.inline{margin:0 6px 0 0}.b-REJECT{background:#8a8a90}
.ticket{border:2px solid var(--acc-ok);border-radius:10px;padding:12px 14px;margin:8px 0 4px;width:100%}
.ticket.small{border-color:#2f7f8a}.ticket h3{margin:0 0 6px;font-size:16px}
.ticket .leg{font:600 16px/1.5 ui-monospace,SFMono-Regular,Consolas,monospace;overflow-wrap:anywhere}
.ticket dl{display:grid;grid-template-columns:max-content 1fr;gap:3px 12px;margin:8px 0;font-size:14px}
.ticket dt{color:var(--mute)}.ticket dd{margin:0}.ticket ol{margin:6px 0 0 18px;padding:0;font-size:14px}
.counts span{margin-right:14px}
"""


def page(title: str, body: str, refresh: bool = False) -> str:
    secs = refresh if isinstance(refresh, int) and not isinstance(refresh, bool) and refresh > 1 else 3
    meta = f'<meta http-equiv="refresh" content="{secs}">' if refresh else ""
    return (f'<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" '
            f'content="width=device-width,initial-scale=1">{meta}<title>{E(title)}</title>'
            f'<style>{CSS}</style></head><body><header><h1>Option Ideas from the Books '
            f'<span class="note">v{__version__}</span></h1><nav>'
            f'<a href="/">Ideas</a><a href="/screener">Screener</a><a href="/trades">My trades</a><a href="/portfolio">Portfolio</a><a href="/history">History</a><a href="/settings">Settings</a>'
            f'<a href="/principles">Principles</a></nav></header><main>{body}</main></body></html>')


def chips(ids) -> str:
    return "".join(f'<a class="chip" href="/principle/{E(i)}">{E(i)}</a>' for i in ids)


def money(x) -> str:
    return "unlimited" if x is None else (f"-${-x:,.0f}" if x < 0 else f"${x:,.0f}")


def fmt(x, spec: str) -> str:
    return "" if x is None else format(x, spec)


BADGE = {ticket.BUY: "BUY", ticket.SMALL: "SMALL", ticket.AGGR: "AGGR", ticket.CONSIDER: "CONSIDER",
         ticket.REJECT: "REJECT"}
ABADGE = {positions.EXIT: "EXIT", positions.BUYBACK: "BUYBACK", positions.PROFIT: "PROFIT",
          positions.HOLD: "HOLD", positions.DONE: "EXPIRED"}


def _label(c: dict) -> str:
    """Older snapshots have no label; derive one from the verdict."""
    if "label" in c:
        return c["label"]
    return {"ACCEPT": ticket.BUY, "CAUTION": ticket.CONSIDER}.get(c["verdict"], ticket.REJECT)


def render_ticket(t: dict, label: str, findings: list | None = None) -> str:
    kind = {ticket.SMALL: " small", ticket.AGGR: " aggr"}.get(label, "")
    note = ""
    if label == ticket.SMALL:
        note = ('<p class="note warn">Small-budget logic: this passes every book rule except the position-size '
                'limits you chose to relax. You can lose the full cost shown above.</p>')
    elif label == ticket.AGGR:
        relaxed = [f["message"].split(" (aggressive mode")[0] for f in (findings or [])
                   if "aggressive mode" in f["message"]]
        items = "".join(f"<li>{E(x)}</li>" for x in relaxed)
        note = ('<p class="note warn"><b>Off-book:</b> aggressive mode let this through even though it breaks '
                f'these book rules:</p><ul class="note warn">{items}</ul>')
    mp = "unlimited" if t["max_profit"] is None else f'${t["max_profit"]:,.0f}'
    ml = "unlimited" if t["max_loss"] is None else f'${t["max_loss"]:,.0f}'
    pp = "" if t["prob_profit"] is None else f'{t["prob_profit"]:.0%}'
    legs = "".join(f'<div class="leg">{E(x)}</div>' for x in t["legs"])
    exits = "".join(f"<li>{E(x)}</li>" for x in t["exits"])
    total = "Cost" if t["debit"] else "Credit received"
    track = ""
    if t.get("contracts"):
        payload = E(json.dumps({k: t.get(k) for k in ("symbol", "strategy", "contracts", "stop", "target",
                                                       "close_by", "spot", "debit")}))
        track = (f'<form method="post" action="/trades/add" class="bar" style="margin-top:10px">'
                 f'<input type="hidden" name="ticket" value="{payload}">'
                 f'<label class="inline note">Your fill price ($/share)</label>'
                 f'<input class="inline" type="number" step="0.01" name="fill" value="{t["limit_price"]:.2f}">'
                 f'<button class="sec">I placed this trade — track it</button></form>')
    return (f'<div class="ticket{kind}"><h3>Order to place</h3>{legs}<dl>'
            f'<dt>Order type</dt><dd>{E(t["order"])}</dd>'
            f'<dt>Price</dt><dd>{E(t["price_rule"])}</dd>'
            f'<dt>{total}</dt><dd>about ${t["total"]:,.0f}</dd>'
            f'<dt>Max loss</dt><dd>{ml}</dd><dt>Max profit</dt><dd>{mp}</dd>'
            f'<dt>Break-even</dt><dd>{E(", ".join(f"${b:,.2f}" for b in t["breakevens"]) or "n/a")}</dd>'
            f'<dt>Chance of profit</dt><dd>{pp} at expiration (model estimate)</dd>'
            f'<dt>Good for</dt><dd>{E(t["time_in_force"])}</dd></dl>'
            f'<b>Before you submit:</b> check your broker\'s bid/ask for this exact contract. If you can\'t get '
            f'your price, pass (OTS-092-b).<p style="margin:8px 0 0"><b>After you\'re filled:</b></p><ol>{exits}</ol>'
            f'{note}{track}</div>')


def render_positions(items: list[dict], compact: bool = True) -> str:
    if not items:
        return ""
    rows = []
    for r in items:
        a = r["action"]
        pnl = "" if r.get("pnl") is None else f' · P/L {"+" if r["pnl"] >= 0 else "-"}${abs(r["pnl"]):,.0f}'
        val = "" if r.get("value") is None else f' · now {abs(r["value"]):.2f} vs entry {abs(r["entry"]):.2f}'
        top = r["reasons"][0]
        rows.append(f'<div class="sum"><span class="badge a-{ABADGE.get(a, "HOLD")}">{E(a)}</span>'
                    f'<span class="what"><b>{E(r["description"])}</b>{E(val)}{E(pnl)}<br>'
                    f'{E(top["text"])} {chips(top["principles"])}</span></div>')
    link = '<p class="note"><a href="/trades">Manage my trades →</a></p>' if compact else ""
    return f'<div class="card"><h2>My trades</h2>{"".join(rows)}{link}</div>'


PBADGE = {"TRIM / SELL": "a-EXIT", "HEDGE": "b-CONSIDER", "COVERED CALL": "b-SMALL", "REVIEW": "b-AGGR",
          "HOLD": "a-HOLD"}


def render_portfolio(res: dict | None, full: bool = False) -> str:
    if not res:
        return ""
    rows = []
    for s in res["stocks"]:
        if not full and s["action"] == "HOLD":
            continue
        gain = "" if s["gain"] is None else f' · {"+" if s["gain"] >= 0 else ""}{s["gain"]:.0%} vs cost'
        val = "" if s["value"] is None else f' · ${s["value"]:,.0f}'
        wt = "" if s["weight"] is None else f' ({s["weight"]:.0%})'
        recs = s["recommendations"] if full else s["recommendations"][:1]
        lines = "".join(f'<div class="f">{E(r["text"])} {chips(r["principles"])}</div>' for r in recs)
        rows.append(f'<div class="sum"><span class="badge {PBADGE.get(s["action"], "a-HOLD")}">{E(s["action"])}</span>'
                    f'<span class="what"><b>{E(s["symbol"])}</b> {s["shares"]:g} sh{E(val)}{E(wt)}{E(gain)}'
                    f'{lines}</span></div>')
    port = "".join(f'<div class="f warn">{E(p["text"])} {chips(p["principles"])}</div>' for p in res["portfolio"])
    if not rows and not port:
        return ""
    head = (f'<p class="note">Total ≈ ${res["total"]:,.0f} · stocks ${res["stock_value"]:,.0f} · '
            f'long options (cost) ${res["option_cost"]:,.0f} · cash ${res["cash"]:,.0f} · imported {E(res.get("imported") or "")}'
            '. Option positions are checked under My trades.</p>') if full else ""
    link = '' if full else '<p class="note"><a href="/portfolio">Full portfolio review →</a></p>'
    title = "Portfolio review" if full else "Portfolio: action needed"
    return f'<div class="card"><h2>{title}</h2>{head}{port}{"".join(rows)}{link}</div>'


def render_summary(res: dict) -> str:
    rows, counts = [], {k: 0 for k in BADGE}
    for t in res["tickers"]:
        if not t["ok"]:
            counts[ticket.REJECT] += 1
            rows.append(f'<div class="sum"><span class="badge b-REJECT">Reject</span><span class="tk">{E(t["symbol"])}</span>'
                        f'<span class="what note">No data: {E(t["error"].splitlines()[0][:120])}</span></div>')
            continue
        if not t["candidates"]:
            counts[ticket.REJECT] += 1
            real = [i for i in t["ideas"] if i["strategy"] != "none"]
            if real:
                why = ("no trade from these ideas fits your account and per-trade cap ("
                       + ", ".join(i["strategy"].replace("vertical_spread:", "").replace("_", " ") for i in real) + ")")
            else:
                why = "; ".join(i["rationale"] for i in t["ideas"]) or "no setup"
            rows.append(f'<div class="sum"><span class="badge b-REJECT">Reject</span><span class="tk">{E(t["symbol"])}</span>'
                        f'<span class="what">No trade today — {E(why)}</span></div>')
            continue
        best = t["candidates"][0]
        lab = _label(best)
        counts[lab] += 1
        tk = best.get("ticket")
        what = E(tk["headline"]) if tk else E(best["strategy"].replace("_", " "))
        if lab == ticket.REJECT:
            first = next((f["message"] for f in best["findings"] if f["severity"] == "BLOCK"), "")
            what = f"Best idea fails a hard rule — {E(first[:140])}"
        elif lab == ticket.CONSIDER:
            n = sum(1 for f in best["findings"] if f["severity"] == "WARN")
            what += f' <span class="note">({n} book warning{"s" if n != 1 else ""} — see details)</span>'
        cost = f'<span class="note">≈ ${tk["total"]:,.0f}</span>' if tk and lab != ticket.REJECT else ""
        row = (f'<div class="sum"><span class="badge b-{BADGE[lab]}">{E(lab)}</span>'
               f'<span class="tk">{E(t["symbol"])}</span><span class="what">{what}</span>{cost}')
        if lab in (ticket.BUY, ticket.SMALL, ticket.AGGR) and tk:
            row += render_ticket(tk, lab, best["findings"])
        rows.append(row + "</div>")
    order = [ticket.BUY, ticket.SMALL, ticket.AGGR, ticket.CONSIDER, ticket.REJECT]
    rows.sort(key=lambda r: next(i for i, k in enumerate(order) if f"b-{BADGE[k]}" in r))
    tally = "".join(f'<span><span class="badge b-{BADGE[k]}" style="min-width:0">{counts[k]}</span> {E(k)}</span>'
                    for k in order)
    return (f'<div class="card"><h2>Today\'s summary</h2><p class="counts">{tally}</p>{"".join(rows)}</div>')


def render_results(res: dict) -> str:
    out = [f'<p class="note">Snapshot {E(res["snapshot"])} · as of {E(res["as_of"])} · '
           f'source: {E(res["provider"])} · pulled when: {E(res["market_note"])}</p>',
           render_positions(res.get("positions") or []), render_portfolio(res.get("portfolio")),
           render_summary(res), '<h3>Details</h3>']
    for t in res["tickers"]:
        if not t["ok"]:
            out.append(f'<div class="card"><h2>{E(t["symbol"])}</h2><p class="err">{E(t["error"])}</p></div>')
            continue
        bench = f' vs {E(t["benchmark"])}' if t.get("benchmark") else ""
        s = [f'<details class="card"><summary><b>{E(t["symbol"])}</b> <span class="note">@ {t["spot"]:.2f}{bench} · '
             f'premium regime: {E(t["regime"] or "unknown")} · ideas, candidates, reasons and sources</span></summary>']
        for i in t["ideas"]:
            name = "Wait — no setup" if i["strategy"] == "none" else i["strategy"].replace("_", " ")
            s.append(f'<div class="idea"><b>{E(name)}</b> — {E(i["rationale"])} {chips(i["principles"])}</div>')
        s.append(f'<details><summary>Technical reading</summary><pre>{E(chr(10).join(t["technical"]))}</pre></details>')
        if not t["candidates"]:
            s.append('<p class="note">No concrete trades to rank for these ideas.</p>')
        for n, c in enumerate(t["candidates"], 1):
            legs = "".join(
                f'<tr><td>{E(l["action"])} {l["qty"]}</td><td>{l["strike"]:g} {E(l["type"])}</td>'
                f'<td>{E(l["expiration"])} ({l["dte"]}d)</td><td>{l["price"]:.2f}</td>'
                f'<td>{fmt(l["fair"], ".2f")}</td><td>{fmt(l["iv"], ".0%")}</td>'
                f'<td>{fmt(l["delta"], "+.2f")}</td></tr>' for l in c["legs"])
            stock = "".join(f'<tr><td>{E(l["action"])} {l["qty"]}</td><td>shares</td><td></td>'
                            f'<td>{l["price"]:.2f}</td><td></td><td></td><td></td></tr>' for l in c["stock_legs"])
            pp = "" if c["prob_profit"] is None else f' · P(profit) {c["prob_profit"]:.0%}'
            be = ", ".join(f"{b:g}" for b in c["breakevens"])
            finds = "".join(f'<div class="f {E(f["severity"])}">{E(f["severity"])} — {E(f["message"])} '
                            f'{chips(f["principles"])}</div>' for f in c["findings"])
            mgmt = "".join(f'<div class="f">→ {E(m["message"])} {chips(m["principles"])}</div>'
                           for m in c["management"])
            s.append(
                f'<details><summary><span class="badge b-{BADGE[_label(c)]}" style="min-width:0">{E(_label(c))}</span> '
                f'#{n} {E(c["strategy"].replace("_", " "))} · score {c["score"]} · max profit {money(c["max_profit"])} '
                f'· max loss {money(c["max_loss"])}{pp}</summary>'
                f'<div class="tablewrap"><table><tr><th>Leg</th><th>Strike</th><th>Expiration</th><th>Price</th>'
                f'<th>Fair</th><th>IV</th><th>Δ</th></tr>{stock}{legs}</table></div>'
                f'<p class="note">Break-even(s): {E(be or "n/a")} · vol input: {E(c["volatility_source"] or "n/a")}</p>'
                f'{finds}<p><b>Management plan</b></p>{mgmt}</details>')
        s.append("</details>")
        out.append("".join(s))
    return "".join(out)


class Handler(BaseHTTPRequestHandler):
    app: App = None  # set by serve()

    def log_message(self, *a):
        pass

    def _send(self, body: str, code: int = 200, ctype: str = "text/html; charset=utf-8"):
        data = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _redirect(self, to: str):
        self.send_response(303)
        self.send_header("Location", to)
        self.end_headers()

    def _form(self) -> dict:
        n = int(self.headers.get("Content-Length", 0))
        return {k: v[0] for k, v in urllib.parse.parse_qs(self.rfile.read(n).decode(), keep_blank_values=True).items()}

    def _form_multi(self) -> dict:
        n = int(self.headers.get("Content-Length", 0))
        return urllib.parse.parse_qs(self.rfile.read(n).decode(), keep_blank_values=True)

    # ------------------------------------------------------------ GET
    def do_GET(self):
        path = urllib.parse.urlparse(self.path).path
        try:
            if path == "/":
                return self._send(self.home())
            if path == "/settings":
                return self._send(self.settings_page())
            if path == "/history":
                return self._send(self.history_page())
            if path.startswith("/snapshot/"):
                return self._send(self.snapshot_page(path.split("/", 2)[2]))
            if path == "/principles":
                q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query).get("q", [""])[0]
                return self._send(self.principles_page(q))
            if path.startswith("/principle/"):
                return self._send(self.principle_page(urllib.parse.unquote(path.split("/", 2)[2])))
            if path == "/schwab":
                return self._send(self.schwab_page())
            if path == "/screener":
                return self._send(self.screener_page())
            if path == "/trades":
                return self._send(self.trades_page())
            if path == "/portfolio":
                return self._send(self.portfolio_page())
            self._send(page("Not found", "<p>Not found.</p>"), 404)
        except Exception:
            self._send(page("Error", f'<pre class="err">{E(traceback.format_exc())}</pre>'), 500)

    # ------------------------------------------------------------ POST
    def do_POST(self):
        path = urllib.parse.urlparse(self.path).path
        app = self.app
        try:
            if path == "/pull":
                app.start(True)
                return self._redirect("/")
            if path == "/analyze":
                app.start(False)
                return self._redirect("/")
            if path == "/settings":
                return self.save_settings(self._form())
            if path == "/screener":
                return self.run_screener(self._form())
            if path == "/claude/prepare":
                claude_pull.build_request(self.app.cfg)
                return self._redirect("/#claude")
            if path == "/claude/cancel":
                claude_pull.cancel(self.app.cfg)
                return self._redirect("/")
            if path == "/portfolio/import":
                return self.import_portfolio(self._form())
            if path == "/trades/add":
                return self.add_trade(self._form())
            if path == "/trades/manual":
                return self.add_manual_trade(self._form())
            if path == "/trades/close":
                return self.close_trade(self._form())
            if path == "/screener/add":
                return self.add_to_watchlist(self._form_multi())
            if path == "/schwab/finish":
                auth = app.schwab_auth()
                if auth is None:
                    raise ProviderError("enter your Schwab app key and secret in Settings first")
                auth.finish_login(self._form().get("url", ""))
                return self._redirect("/schwab")
            self._send(page("Not found", "<p>Not found.</p>"), 404)
        except ProviderError as e:
            self._send(page("Error", f'<p class="err">{E(str(e))}</p><p><a href="/schwab">Back</a></p>'), 400)
        except Exception:
            self._send(page("Error", f'<pre class="err">{E(traceback.format_exc())}</pre>'), 500)

    # ------------------------------------------------------------ pages
    def home(self) -> str:
        app, cfg = self.app, self.app.cfg
        job = app.job
        open_, note = pipeline.market_status()
        src = cfg["provider"]
        if src == "schwab":
            auth = app.schwab_auth()
            st = auth.status() if auth else {"logged_in": False, "message": "app key/secret not set"}
            src_note = (f'Schwab: {E(st["message"])}' if st["logged_in"]
                        else f'<span class="warn">Schwab: {E(st["message"])} — <a href="/schwab">connect</a></span>')
        else:
            src_note = f'CSV folder: {E(cfg["csv_folder"])}'
        body = [
            '<div class="bar"><form method="post" action="/pull"><button>Pull &amp; analyze</button></form>'
            '<form method="post" action="/analyze"><button class="sec">Re-analyze last pull</button></form>'
            f'<span class="note">{src_note} · watchlist: {E(", ".join(cfg["watchlist"]))}</span></div>',
            f'<p class="note {"" if open_ else "warn"}">Right now: {E(note)}</p>']
        if cfg.get("aggressive_mode"):
            body.append('<p class="note warn">Aggressive mode is on: some book rules are relaxed; those trades are '
                        'labeled "Buy – Aggressive (off-book)".</p>')
        if cfg.get("small_account_mode"):
            body.append('<p class="note warn">Small-account mode is on: the books\' position-size limits are '
                        f'shown as warnings, and your ${cfg.get("max_trade_risk") or 0:,.0f} per-trade cap is enforced.</p>')
        waiting = False
        if src == "csv":
            panel, waiting = self.claude_panel(cfg, job)
            body.append(panel)
        if job["running"] and job.get("kind") == "pull":
            body.append(f'<div class="card"><b>Working…</b> {E(job["step"])}</div>')
            return page("Working…", "".join(body), refresh=True)
        if job["error"] and job.get("kind") == "pull":
            body.append(f'<div class="card"><b>Last run failed</b><p class="err">{E(job["error"])}</p></div>')
        snap = pipeline.latest_snapshot(cfg)
        if snap and (snap / "results.json").exists():
            body.append(render_results(json.loads((snap / "results.json").read_text(encoding="utf-8"))))
        else:
            body.append('<div class="card">No results yet. Press <b>Pull &amp; analyze</b>.</div>')
        body.append('<p class="note">Decision aid built from the loaded books\' principles — not financial advice. '
                    'Every finding links to the page it comes from.</p>')
        return page("Option ideas", "".join(body), refresh=10 if waiting else False)

    def claude_panel(self, cfg: dict, job: dict) -> tuple[str, bool]:
        """Pull through Claude in Chrome: request, copyable instruction, live file checklist, auto-analyze."""
        req = claude_pull.load_request(cfg)
        if not req or req.get("analyzed"):
            last = (f' <span class="note">Last Claude pull: {E(req["created_at"].replace("T", " "))}, analyzed.</span>'
                    if req else "")
            return (f'<div class="card" id="claude"><b>Get today\'s data with Claude</b> '
                    f'<span class="note">(Schwab website, until the API is approved)</span>'
                    f'<form method="post" action="/claude/prepare" class="bar" style="margin-top:8px">'
                    f'<button>Prepare a Claude pull</button>{last}</form></div>'), False
        st = claude_pull.status(cfg, req)
        if st["complete"] and not job["running"]:
            claude_pull.mark_analyzed(cfg)
            self.app.start(True)
            return ('<div class="card" id="claude"><b>Claude\'s files are in — analyzing now…</b></div>', True)
        checks = "".join(
            f'<span class="chip" style="font-size:13px">{"✔" if r["chain"] and r["spot"] else "…"} {E(r["symbol"])}</span>'
            for r in st["rows"])
        text = E(claude_pull.prompt(req))
        copy_js = ("navigator.clipboard.writeText(document.getElementById('claudeprompt').value);"
                   "this.textContent='Copied ✔'")
        return ('<div class="card" id="claude"><h2>Pull with Claude</h2>'
                '<ol><li>Click <b>Copy instruction</b> and paste it into Claude in Chrome (logged in to Schwab).</li>'
                '<li>Leave this page open. It checks the inbox every 10 seconds and analyzes automatically when '
                'Claude is done.</li></ol>'
                f'<p><button type="button" onclick="{E(copy_js)}">Copy instruction</button></p>'
                f'<textarea id="claudeprompt" rows="10" readonly>{text}</textarea>'
                f'<p class="note">Files received: {st["have"]}/{st["need"]} {checks}</p>'
                '<form method="post" action="/claude/cancel" class="bar"><button class="sec">Cancel</button>'
                '<span class="note">Already have the files? Press Pull &amp; analyze above.</span></form></div>'), True

    def history_page(self) -> str:
        root = Path(self.app.cfg["data_dir"]).expanduser() / "snapshots"
        snaps = sorted((p for p in root.glob("*") if (p / "results.json").exists()), reverse=True) if root.exists() else []
        rows = "".join(f'<li><a href="/snapshot/{E(p.name)}">{E(p.name)}</a></li>' for p in snaps)
        return page("History", f'<h2>Past pulls</h2><ul>{rows or "<li>none yet</li>"}</ul>')

    def snapshot_page(self, name: str) -> str:
        root = (Path(self.app.cfg["data_dir"]).expanduser() / "snapshots").resolve()
        p = (root / name).resolve()
        if p.parent != root or not (p / "results.json").exists():
            return page("Not found", "<p>Not found.</p>")
        return page(name, render_results(json.loads((p / "results.json").read_text(encoding="utf-8"))))

    def principles_page(self, q: str) -> str:
        found = self.app.library.search(q) if q else []
        rows = "".join(f'<div class="f"><a class="chip" href="/principle/{E(p.id)}">{E(p.id)}</a> '
                       f'p.{p.page} — {E(p.principle)}</div>' for p in found[:200])
        books = "".join(f'<li>{E(b["title"])} — {E(b.get("author", ""))}</li>' for b in self.app.library.books.values())
        return page("Principles", f'<h2>Loaded books</h2><ul>{books}</ul><form><input type="text" name="q" '
                    f'value="{E(q)}" placeholder="search, e.g. stop loss"></form>{rows}')

    def principle_page(self, pid: str) -> str:
        lib = self.app.library
        if pid not in lib:
            return page("Unknown", f"<p>{E(pid)} is not in the library.</p>")
        p = lib.get(pid)
        return page(pid, f'<div class="card"><h2>{E(p.id)}</h2><p>{E(p.citation(lib))}</p>'
                    f'<p>{E(p.principle)}</p><blockquote>“{E(p.quote)}”</blockquote>'
                    f'<pre>{E(json.dumps(p.rule, indent=2))}</pre></div>')

    def settings_page(self) -> str:
        c = self.app.cfg
        s = c["schwab"]
        v = lambda x: "" if x is None else E(str(x))
        sel = lambda val: " selected" if c["provider"] == val else ""
        return page("Settings", f'''<h2>Settings</h2><form method="post" action="/settings" class="card">
<label>Data source</label><select name="provider"><option value="schwab"{sel("schwab")}>Schwab Trader API</option>
<option value="csv"{sel("csv")}>CSV folder</option></select>
<label>Watchlist (comma-separated)</label><input type="text" name="watchlist" value="{E(", ".join(c["watchlist"]))}">
<label>Benchmarks (SYMBOL=BENCH, comma-separated)</label><input type="text" name="benchmarks"
 value="{E(", ".join(f"{k}={b}" for k, b in c["benchmarks"].items()))}">
<label>Tickers you own (covered-call / hedge ideas)</label><input type="text" name="holdings" value="{E(", ".join(c["holdings"]))}">
<label>Account value ($)</label><input type="number" name="account_value" value="{v(c["account_value"])}">
<label>Option-buying budget ($, for the 5%-per-position rule)</label><input type="number" name="option_budget" value="{v(c["option_budget"])}">
<label><input type="checkbox" name="small_account_mode" value="1"{" checked" if c.get("small_account_mode") else ""}>
 Small-account mode</label>
<p class="note">For accounts too small for the books' sizing rules. Their 5%-per-position and 5–10%-of-account limits
become warnings instead of rejections, candidates are 1 contract, and your per-trade cap below is enforced instead.
This departs from the books; every other rule still applies.</p>
<label>Max loss per trade ($, your cap)</label><input type="number" name="max_trade_risk" value="{v(c.get("max_trade_risk"))}">
<label><input type="checkbox" name="aggressive_mode" value="1"{" checked" if c.get("aggressive_mode") else ""}>
 Aggressive mode (off-book)</label>
<p class="note">Turns these book rules from Reject into labeled warnings: {E("; ".join(RELAXED))}.
Your per-trade cap, the account limit, cash-for-assignment, unlimited-risk and stop rules stay hard. Trades it lets
through are labeled <b>Buy – Aggressive (off-book)</b> and list the rules they break.</p>
<label>Max option price ($/share, e.g. 0.50 = $50 a contract; blank = no limit)</label>
<input type="number" step="0.01" name="max_option_price" value="{v(c.get("max_option_price"))}">
<label><input type="checkbox" name="include_weeklies" value="1"{" checked" if c.get("include_weeklies") else ""}>
 Include weekly / short-dated expirations</label>
<p class="note">Also lower the "days out (min)" below to e.g. 3. Short-dated options lose value fastest (OTS-024-a);
expect them to show as aggressive unless the books' exceptions apply.</p>
<label>Upcoming events (one per line: <code>TICKER=YYYY-MM-DD</code>, e.g. earnings) — adds strangle ideas</label>
<textarea name="events" rows="3">{E(chr(10).join(f"{k}={d}" for k, d in (c.get("events") or {}).items()))}</textarea>
<label>Expirations: days out (min / max) and how many monthlies</label>
<input type="number" name="min_dte" value="{c["min_dte"]}"> <input type="number" name="max_dte" value="{c["max_dte"]}">
<input type="number" name="expiration_count" value="{c["expiration_count"]}">
<label>CSV folder (CSV source)</label><input type="text" name="csv_folder" value="{E(c["csv_folder"])}">
<p class="note">Per symbol: <code>SYM_chain.csv</code> (required for watchlist tickers), <code>SYM_spot.txt</code>
(live price when the chain was copied), <code>SYM.csv</code> (price history — downloaded from Yahoo if absent).</p>
<h3>Schwab Trader API</h3>
<p class="note">From your app at developer.schwab.com. Stored in the local config file on this computer only.</p>
<label>App key</label><input type="text" name="app_key" value="{E(s["app_key"])}">
<label>App secret</label><input type="text" name="app_secret" value="{E(s["app_secret"])}">
<label>Callback URL (must match the app exactly)</label><input type="text" name="callback_url" value="{E(s["callback_url"])}">
<p><button>Save</button> <a href="/schwab">Connect Schwab →</a></p></form>''')

    def save_settings(self, f: dict):
        c = self.app.cfg
        split = lambda x: [t.strip().upper() for t in x.split(",") if t.strip()]
        num = lambda x: float(x) if x.strip() else None
        c["provider"] = f.get("provider", c["provider"])
        c["watchlist"] = split(f.get("watchlist", ""))
        c["benchmarks"] = {a.strip().upper(): b.strip().upper() for a, _, b in
                           (p.partition("=") for p in f.get("benchmarks", "").split(",")) if a.strip() and b.strip()}
        c["holdings"] = split(f.get("holdings", ""))
        c["account_value"] = num(f.get("account_value", ""))
        c["option_budget"] = num(f.get("option_budget", ""))
        c["small_account_mode"] = f.get("small_account_mode") == "1"
        c["aggressive_mode"] = f.get("aggressive_mode") == "1"
        c["include_weeklies"] = f.get("include_weeklies") == "1"
        c["max_option_price"] = num(f.get("max_option_price", ""))
        ev = {}
        for line in f.get("events", "").replace(",", "\n").splitlines():
            k, _, d = line.partition("=")
            if k.strip() and d.strip():
                try:
                    ev[k.strip().upper()] = date.fromisoformat(d.strip()).isoformat()
                except ValueError:
                    pass
        c["events"] = ev
        c["max_trade_risk"] = num(f.get("max_trade_risk", ""))
        for k in ("min_dte", "max_dte", "expiration_count"):
            c[k] = int(f.get(k) or c[k])
        c["csv_folder"] = f.get("csv_folder", c["csv_folder"]).strip()
        for k in ("app_key", "app_secret", "callback_url"):
            c["schwab"][k] = f.get(k, "").strip()
        pipeline.save_config(self.app.config_path, c)
        self._redirect("/settings")

    def screener_page(self) -> str:
        cfg, job = self.app.cfg, self.app.job
        sc = cfg.get("screener", {})
        o = {**screener.DEFAULTS, **sc.get("opts", {})}
        cap = cfg.get("max_trade_risk")
        sel = lambda v: " selected" if o["direction"] == v else ""
        form = f'''<h2>Screener</h2><form method="post" action="/screener" class="card">
<label>Tickers (spaces or commas; optional sector benchmark as <code>TICKER=ETF</code>, e.g. <code>ABC=XLF</code>)</label>
<textarea name="tickers" rows="4">{E(sc.get("tickers", ""))}</textarea>
<label>Price range ($): min / max</label>
<input type="number" step="any" name="min_price" value="{o["min_price"]:g}">
<input type="number" step="any" name="max_price" value="{o["max_price"]:g}">
<label>Minimum average daily volume (shares)</label><input type="number" name="min_avg_volume" value="{o["min_avg_volume"]:.0f}">
<label>Default benchmark</label><input type="text" name="benchmark" value="{E(o["benchmark"])}">
<label>Direction</label><select name="direction"><option value="both"{sel("both")}>Both</option>
<option value="bull"{sel("bull")}>Bullish only (calls)</option><option value="bear"{sel("bear")}>Bearish only (puts)</option></select>
<p><button>Run screener</button></p>
<p class="note">Uses price history only (downloaded automatically). Ranks trend, momentum, strength vs benchmark,
overbought/oversold, breakouts and volatility, each citing its book principle. Then pull option chains for the top few.</p></form>'''
        if job.get("running") and job.get("kind") == "screen":
            return page("Screening…", form + f'<div class="card"><b>Working…</b> {E(job["step"])}</div>', refresh=True)
        err = f'<div class="card"><p class="err">{E(job["error"])}</p></div>' if job.get("error") and job.get("kind") == "screen" else ""
        path = Path(cfg["data_dir"]).expanduser() / "screens" / "latest.json"
        if not path.exists():
            return page("Screener", form + err)
        data = json.loads(path.read_text(encoding="utf-8"))
        rows = []
        for n, r in enumerate(data["rows"], 1):
            if not r["ok"]:
                rows.append(f'<tr><td></td><td>{n}</td><td><b>{E(r["symbol"])}</b></td>'
                            f'<td colspan="7" class="err">{E(r["error"][:160])}</td></tr>')
                continue
            cost = r["est_atm_cost"]
            fits = cost is not None and cap and cost <= cap
            cost_s = "" if cost is None else f'<span class="{"PASS" if fits else ""}">${cost:,.0f}</span>'
            bias = {"bull": "Bullish", "bear": "Bearish", "wait": "Wait"}[r["bias"]]
            dim = "" if r["passed_filters"] else ' style="opacity:.55"'
            why = "".join(f'<div class="f">{"+" if x["points"] > 0 else ""}{x["points"]:g} {E(x["text"])} '
                          f'{chips(x["principles"])}</div>' for x in r["reasons"])
            rp = "" if r["rel_perf"] is None else f'{r["rel_perf"]:+.1%}'
            hv = "" if r["hv"] is None else f'{r["hv"]:.0%}'
            rows.append(
                f'<tr{dim}><td><input type="checkbox" name="sym" value="{E(r["symbol"])}|{E(r["benchmark"])}"'
                f'{" checked" if n <= 5 and r["passed_filters"] and r["bias"] != "wait" else ""}></td>'
                f'<td>{n}</td><td><b>{E(r["symbol"])}</b></td><td>{bias}</td><td>{r["score"]:g}</td>'
                f'<td>{r["price"]:.2f}</td><td>{cost_s}</td><td>{hv}</td><td>{E(r["trend"])} / {E(r["strength"])}</td>'
                f'<td>{rp} vs {E(r["benchmark"])}</td></tr>'
                f'<tr{dim}><td></td><td colspan="9"><details><summary class="note">why</summary>{why}</details></td></tr>')
        cap_note = (f"Green = estimated at-the-money option cost fits your ${cap:,.0f} per-trade cap. "
                    if cap else "")
        table = (f'<div class="card"><p class="note">Ran {E(data["ran_at"])}. {cap_note}Option cost is an estimate from '
                 'realised volatility, about 60 days out; out-of-the-money strikes are cheaper. Faded rows fail the price or '
                 'volume filter.</p><form method="post" action="/screener/add"><div class="tablewrap"><table>'
                 '<tr><th></th><th>#</th><th>Ticker</th><th>Bias</th><th>Score</th><th>Price</th><th>Est. ATM option</th>'
                 '<th>HV</th><th>Trend</th><th>Rel. perf.</th></tr>' + "".join(rows) +
                 '</table></div><p><button>Add checked to watchlist</button> '
                 '<span class="note">Then get their option chains into the inbox and press Pull &amp; analyze.</span></p>'
                 '</form></div>')
        return page("Screener", form + err + table)

    def trades_page(self) -> str:
        cfg = self.app.cfg
        book = positions.load(cfg)
        snap = pipeline.latest_snapshot(cfg)
        latest = {}
        if snap and (snap / "results.json").exists():
            for r in json.loads((snap / "results.json").read_text(encoding="utf-8")).get("positions") or []:
                latest[r["id"]] = r
        open_ = [p for p in book if p.get("status") == "open"]
        rows = []
        for p in open_:
            r = latest.get(p["id"])
            if r:
                status = render_positions([r], compact=False).replace('<div class="card"><h2>My trades</h2>', "")[:-6]
            else:
                status = (f'<div class="sum"><span class="badge a-HOLD">NEW</span><span class="what"><b>'
                          f'{E(positions.describe(p))}</b><br>Press Pull &amp; analyze to check it.</span></div>')
            plan = " · ".join(x for x in (
                f"entry {abs(p['entry']):.2f} {'debit' if p['entry'] >= 0 else 'credit'}",
                f"stop {p['stop']:.2f}" if p.get("stop") is not None else "",
                f"target {p['target']:.2f}" if p.get("target") is not None else "",
                f"close by {p['close_by']}" if p.get("close_by") else "", f"opened {p['opened']}") if x)
            rows.append(f'{status}<p class="note">{E(plan)}</p>'
                        f'<form method="post" action="/trades/close" class="bar"><input type="hidden" name="id" '
                        f'value="{E(p["id"])}"><label class="inline note">Closed at ($/share)</label>'
                        f'<input class="inline" type="number" step="0.01" name="exit" required>'
                        f'<button class="sec">Mark closed</button></form>')
        closed = [p for p in book if p.get("status") == "closed"]
        total = sum(p.get("pnl", 0) for p in closed)
        hist = "".join(f'<div class="f">{E(p["closed_on"])} · {E(positions.describe(p))} · '
                       f'{"+" if p["pnl"] >= 0 else "-"}${abs(p["pnl"]):,.0f}</div>' for p in reversed(closed))
        manual = '''<form method="post" action="/trades/manual" class="card"><h3>Add a trade by hand</h3>
<p class="note">For a single option you placed yourself. Multi-leg trades: use the button on the order ticket.</p>
<label>Ticker</label><input type="text" name="symbol" required>
<label>Bought or sold</label><select name="action"><option value="buy">Bought (long)</option><option value="sell">Sold (short)</option></select>
<label>Call or put</label><select name="type"><option>call</option><option>put</option></select>
<label>Strike</label><input type="number" step="0.01" name="strike" required>
<label>Expiration</label><input type="text" name="expiration" placeholder="2026-12-18" required>
<label>Contracts</label><input type="number" name="qty" value="1" required>
<label>Fill price ($/share)</label><input type="number" step="0.01" name="fill" required>
<label>Stop on the stock price (optional)</label><input type="number" step="0.01" name="stop">
<label>Stock target (optional)</label><input type="number" step="0.01" name="target">
<label>Close by (optional)</label><input type="text" name="close_by" placeholder="2026-11-18">
<p><button>Add trade</button></p></form>'''
        return page("My trades", f'<h2>My trades</h2><div class="card"><h3>Open</h3>{"".join(rows) or "<p>No open trades.</p>"}'
                    f'<p class="note">Each Pull &amp; analyze re-checks these against the books\' exit rules.</p></div>'
                    f'<div class="card"><h3>Closed · total {"+" if total >= 0 else "-"}${abs(total):,.0f}</h3>'
                    f'{hist or "<p class=note>None yet.</p>"}</div>{manual}')

    def add_trade(self, f: dict):
        t = json.loads(f["ticket"])
        fill = abs(float(f.get("fill") or 0))
        net = fill if t.get("debit", True) else -fill
        cfg = self.app.cfg
        book = positions.load(cfg)
        book.append(positions.new_position(t["symbol"], t["contracts"], net, datetime.now().date().isoformat(),
                                           t.get("stop"), t.get("target"), t.get("close_by"), t.get("strategy", ""),
                                           t.get("spot")))
        positions.save(cfg, book)
        self._redirect("/trades")

    def add_manual_trade(self, f: dict):
        from .advisor import _iso
        num = lambda k: float(f[k]) if f.get(k, "").strip() else None
        contract = {"action": f["action"], "type": f["type"], "strike": float(f["strike"]),
                    "expiration": _iso(f["expiration"]), "qty": int(f.get("qty") or 1), "price": float(f["fill"])}
        net = contract["price"] if contract["action"] == "buy" else -contract["price"]
        cfg = self.app.cfg
        spot = None
        snap = pipeline.latest_snapshot(cfg)
        sym = f["symbol"].strip().upper()
        if snap and (snap / f"{sym}_spot.txt").exists():
            spot = float((snap / f"{sym}_spot.txt").read_text(encoding="utf-8"))
        pos = positions.new_position(sym, [contract], net, datetime.now().date().isoformat(), num("stop"),
                                     num("target"), (_iso(f["close_by"]) if f.get("close_by", "").strip() else None),
                                     f"{'long' if f['action'] == 'buy' else 'short'}_{f['type']}", spot)
        if not spot:
            pos["bias"] = (1 if f["type"] == "call" else -1) * (1 if f["action"] == "buy" else -1)
        book = positions.load(cfg)
        book.append(pos)
        positions.save(cfg, book)
        self._redirect("/trades")

    def close_trade(self, f: dict):
        cfg = self.app.cfg
        book = positions.load(cfg)
        for p in book:
            if p["id"] == f.get("id") and p.get("status") == "open":
                exit_px = abs(float(f["exit"]))
                qty = max(c["qty"] for c in p["contracts"])
                if p["entry"] >= 0:
                    pnl = (exit_px - p["entry"]) * 100 * qty
                else:
                    pnl = (-p["entry"] - exit_px) * 100 * qty
                p.update(status="closed", closed_on=datetime.now().date().isoformat(), exit=exit_px,
                         pnl=round(pnl, 2))
        positions.save(cfg, book)
        self._redirect("/trades")

    def portfolio_page(self, note: str = "") -> str:
        cfg = self.app.cfg
        pf = portfolio.load(cfg)
        inbox_file = claude_pull.inbox(cfg) / portfolio.FILE
        copy_js = ("navigator.clipboard.writeText(document.getElementById('pfprompt').value);"
                   "this.textContent='Copied ✔'")
        imp = (f'<div class="card"><h2>Load my Robinhood portfolio</h2>'
               '<ol><li><b>Claude in Chrome:</b> copy this instruction into Claude while logged in to Robinhood. '
               f'It saves <code>{E(portfolio.FILE)}</code> into your inbox (read-only).</li>'
               '<li>Then click <b>Import from inbox</b>. Or paste the CSV yourself in the box below.</li></ol>'
               f'<p><button type="button" onclick="{E(copy_js)}">Copy instruction</button></p>'
               f'<textarea id="pfprompt" rows="6" readonly>{E(portfolio.claude_prompt(cfg))}</textarea>'
               '<form method="post" action="/portfolio/import" class="bar" style="margin-top:10px">'
               f'<button>Import from inbox</button><span class="note">'
               f'{"found " + E(str(inbox_file)) if inbox_file.exists() else "waiting for " + E(str(inbox_file))}'
               '</span></form>'
               '<form method="post" action="/portfolio/import"><label>…or paste CSV '
               f'(header: <code>{E(portfolio.COLUMNS)}</code>)</label>'
               '<textarea name="csv" rows="5" placeholder="symbol,kind,quantity,avg_price,option_type,strike,expiration,side&#10;'
               'HPQ,stock,10,28.50,,,,&#10;CLF,option,2,0.45,call,15,2026-12-18,long&#10;CASH,cash,61.20,,,,,"></textarea>'
               '<p><button class="sec">Import pasted CSV</button></p></form></div>')
        body = [f'<h2>My portfolio</h2>{note}']
        if not pf:
            return page("Portfolio", "".join(body) + imp)
        snap = pipeline.latest_snapshot(cfg)
        res = None
        if snap and (snap / "results.json").exists():
            res = json.loads((snap / "results.json").read_text(encoding="utf-8")).get("portfolio")
        if not res or res.get("imported") != pf.get("imported"):
            body.append(f'<div class="card">Imported {len(pf["stocks"])} stock(s) and {len(pf["options"])} option '
                        'position(s). Press <b>Pull &amp; analyze</b> on the Ideas page to get recommendations '
                        '(it fetches prices for your holdings).</div>')
        else:
            body.append(render_portfolio(res, full=True))
        body.append(imp)
        return page("Portfolio", "".join(body))

    def import_portfolio(self, f: dict):
        cfg = self.app.cfg
        text = f.get("csv", "").strip()
        src = "pasted CSV"
        if not text:
            p = claude_pull.inbox(cfg) / portfolio.FILE
            if not p.exists():
                return self._send(self.portfolio_page('<p class="err">No ' + E(portfolio.FILE) +
                                                      ' in the inbox yet.</p>'))
            text, src = p.read_text(encoding="utf-8-sig"), str(p)
        try:
            pf = portfolio.parse(text)
        except Exception as e:
            return self._send(self.portfolio_page(f'<p class="err">Could not read the portfolio: {E(str(e))}</p>'))
        res = portfolio.save_import(cfg, pf)
        for sym in res["covered_call_eligible"]:
            if sym not in cfg["holdings"]:
                cfg["holdings"].append(sym)
        pipeline.save_config(self.app.config_path, cfg)
        note = (f'<div class="card"><b>Imported from {E(src)}:</b> {len(pf["stocks"])} stock(s), '
                f'{len(pf["options"])} option position(s) ({res["options_added"]} new in My trades)'
                + (f', cash ${pf["cash"]:,.2f}' if pf.get("cash") is not None else "")
                + '. Now press <b>Pull &amp; analyze</b> on the Ideas page for recommendations.</div>')
        return self._send(self.portfolio_page(note))

    def run_screener(self, f: dict):
        num = lambda k: float(f.get(k) or screener.DEFAULTS[k])
        opts = {"min_price": num("min_price"), "max_price": num("max_price"),
                "min_avg_volume": num("min_avg_volume"),
                "benchmark": (f.get("benchmark") or "SPY").strip().upper(),
                "direction": f.get("direction", "both")}
        c = self.app.cfg
        c["screener"] = {"tickers": f.get("tickers", ""), "opts": opts}
        pipeline.save_config(self.app.config_path, c)
        self.app.start_screen(f.get("tickers", ""), opts)
        self._redirect("/screener")

    def add_to_watchlist(self, f: dict):
        c = self.app.cfg
        for item in f.get("sym", []):
            sym, _, bench = item.partition("|")
            if sym and sym not in c["watchlist"]:
                c["watchlist"].append(sym)
            if sym and bench and bench != sym:
                c["benchmarks"][sym] = bench
        pipeline.save_config(self.app.config_path, c)
        self._redirect("/settings")

    def schwab_page(self) -> str:
        auth = self.app.schwab_auth()
        if auth is None:
            return page("Schwab", '<div class="card">Enter your Schwab app key, secret and callback URL in '
                        '<a href="/settings">Settings</a> first.</div>')
        st = auth.status()
        return page("Schwab", f'''<div class="card"><h2>Connect Schwab</h2>
<p>Status: <b>{E(st["message"])}</b></p>
<ol><li><a href="{E(auth.authorize_url())}" target="_blank" rel="noopener">Log in at Schwab</a> and approve access.</li>
<li>Schwab sends your browser to <code>{E(auth.callback_url)}</code>, which won't load — that's expected.
Copy the full address from the address bar.</li>
<li>Paste it here within 30 seconds (the code expires quickly):</li></ol>
<form method="post" action="/schwab/finish"><input type="text" name="url" placeholder="https://127.0.0.1/?code=... (or just the code)">
<p><button>Finish connecting</button></p></form>
<p class="note">Schwab requires repeating this every 7 days.</p></div>''')


def serve(config_path: str = "config.json", port: int = 8765) -> None:
    Handler.app = App(config_path)
    try:
        httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    except OSError:
        print(f"Port {port} is already in use — an older copy of the app is probably still running.\n"
              "Close its window (or press Ctrl+C in it), then start this one again.")
        return
    print(f"Option ideas app v{__version__} on http://127.0.0.1:{port}  (Ctrl+C to stop)")
    print(f"Running from: {Path(__file__).resolve().parent.parent}")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
