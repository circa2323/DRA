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
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import pipeline
from .knowledge import Library
from .providers import ProviderError, SchwabAuth

E = html.escape


class App:
    def __init__(self, config_path: str):
        self.config_path = config_path
        self.library = Library()
        self.lock = threading.Lock()
        self.job = {"running": False, "step": "", "error": "", "finished": None}

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
                        "error": "", "finished": None}
        threading.Thread(target=self._run, args=(do_pull,), daemon=True).start()

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
"""


def page(title: str, body: str, refresh: bool = False) -> str:
    meta = '<meta http-equiv="refresh" content="3">' if refresh else ""
    return (f'<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" '
            f'content="width=device-width,initial-scale=1">{meta}<title>{E(title)}</title>'
            f'<style>{CSS}</style></head><body><header><h1>Option Ideas from the Books</h1><nav>'
            f'<a href="/">Ideas</a><a href="/history">History</a><a href="/settings">Settings</a>'
            f'<a href="/principles">Principles</a></nav></header><main>{body}</main></body></html>')


def chips(ids) -> str:
    return "".join(f'<a class="chip" href="/principle/{E(i)}">{E(i)}</a>' for i in ids)


def money(x) -> str:
    return "unlimited" if x is None else (f"-${-x:,.0f}" if x < 0 else f"${x:,.0f}")


def fmt(x, spec: str) -> str:
    return "" if x is None else format(x, spec)


def render_results(res: dict) -> str:
    out = [f'<p class="note">Snapshot {E(res["snapshot"])} · as of {E(res["as_of"])} · '
           f'source: {E(res["provider"])} · pulled when: {E(res["market_note"])}</p>']
    for t in res["tickers"]:
        if not t["ok"]:
            out.append(f'<div class="card"><h2>{E(t["symbol"])}</h2><p class="err">{E(t["error"])}</p></div>')
            continue
        bench = f' vs {E(t["benchmark"])}' if t.get("benchmark") else ""
        s = [f'<div class="card"><h2>{E(t["symbol"])} <span class="note">@ {t["spot"]:.2f}{bench} · '
             f'premium regime: {E(t["regime"] or "unknown")}</span></h2>']
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
                f'<details{" open" if n == 1 else ""}><summary><span class="v {E(c["verdict"])}">{E(c["verdict"])}</span> '
                f'#{n} {E(c["strategy"].replace("_", " "))} · score {c["score"]} · max profit {money(c["max_profit"])} '
                f'· max loss {money(c["max_loss"])}{pp}</summary>'
                f'<div class="tablewrap"><table><tr><th>Leg</th><th>Strike</th><th>Expiration</th><th>Price</th>'
                f'<th>Fair</th><th>IV</th><th>Δ</th></tr>{stock}{legs}</table></div>'
                f'<p class="note">Break-even(s): {E(be or "n/a")} · vol input: {E(c["volatility_source"] or "n/a")}</p>'
                f'{finds}<p><b>Management plan</b></p>{mgmt}</details>')
        s.append("</div>")
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
        if job["running"]:
            body.append(f'<div class="card"><b>Working…</b> {E(job["step"])}</div>')
            return page("Working…", "".join(body), refresh=True)
        if job["error"]:
            body.append(f'<div class="card"><b>Last run failed</b><p class="err">{E(job["error"])}</p></div>')
        snap = pipeline.latest_snapshot(cfg)
        if snap and (snap / "results.json").exists():
            body.append(render_results(json.loads((snap / "results.json").read_text())))
        else:
            body.append('<div class="card">No results yet. Press <b>Pull &amp; analyze</b>.</div>')
        body.append('<p class="note">Decision aid built from the loaded books\' principles — not financial advice. '
                    'Every finding links to the page it comes from.</p>')
        return page("Option ideas", "".join(body))

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
        return page(name, render_results(json.loads((p / "results.json").read_text())))

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
<label>Expirations: days out (min / max) and how many monthlies</label>
<input type="number" name="min_dte" value="{c["min_dte"]}"> <input type="number" name="max_dte" value="{c["max_dte"]}">
<input type="number" name="expiration_count" value="{c["expiration_count"]}">
<label>CSV folder (CSV source)</label><input type="text" name="csv_folder" value="{E(c["csv_folder"])}">
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
        for k in ("min_dte", "max_dte", "expiration_count"):
            c[k] = int(f.get(k) or c[k])
        c["csv_folder"] = f.get("csv_folder", c["csv_folder"]).strip()
        for k in ("app_key", "app_secret", "callback_url"):
            c["schwab"][k] = f.get(k, "").strip()
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
<form method="post" action="/schwab/finish"><input type="text" name="url" placeholder="https://127.0.0.1/?code=...">
<p><button>Finish connecting</button></p></form>
<p class="note">Schwab requires repeating this every 7 days.</p></div>''')


def serve(config_path: str = "config.json", port: int = 8765) -> None:
    Handler.app = App(config_path)
    httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"Option ideas app on http://127.0.0.1:{port}  (Ctrl+C to stop)")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
