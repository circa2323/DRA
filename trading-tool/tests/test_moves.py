import json
import shutil
import tempfile
import threading
import unittest
import urllib.parse
import urllib.request
from datetime import date, datetime
from pathlib import Path

from optiontool import advisor, context, engine, pipeline, positions, pricing, settings, ticket, webapp
from optiontool.knowledge import Library
from optiontool.trade import Leg, Trade

ROOT = Path(__file__).resolve().parent.parent
EX = ROOT / "examples"
LIB = Library()
CFG = settings.load()
AS_OF = date(2026, 10, 22)


def evaluate(legs, spot=100.0, hv=0.30, plan=None, **kw):
    t = Trade("XYZ", spot, legs, AS_OF, historical_vol=hv, **kw)
    return engine.evaluate(context.build(t, CFG, plan=plan or {}), LIB)


class Aggressive(unittest.TestCase):
    def test_relaxes_book_rules_but_not_account_rules(self):
        # overvalued and inside the last month: two relaxable rules
        legs = [Leg("buy", "call", 1, 100, "2026-11-13", 3.5)]
        plan = {"stop": 95, "target": 110, "max_trade_risk": 100}
        strict = evaluate(legs, plan=plan, account_value=200)
        self.assertEqual(ticket.action_label(strict), ticket.REJECT)
        loose = evaluate(legs, plan={**plan, "aggressive_mode": True}, account_value=200)
        blocks = {p for f in loose.findings if f.severity == "BLOCK" for p in f.principles}
        self.assertNotIn("OTS-019-a", blocks)
        self.assertNotIn("OTS-024-a", blocks)
        self.assertIn("OTS-004-b", blocks)          # $350 > $100 cap stays hard
        cheap = [Leg("buy", "call", 1, 106, "2026-11-13", 0.95, bid=0.9, ask=0.95)]
        d = evaluate(cheap, plan={**plan, "aggressive_mode": True, "small_account_mode": True,
                                  "option_buying_portfolio": 200}, account_value=200)
        self.assertEqual(ticket.action_label(d), ticket.AGGR, [f.message for f in d.findings if f.severity != "PASS"])
        self.assertTrue(any("aggressive mode" in f.message for f in d.findings))


class NewStrategies(unittest.TestCase):
    def chain(self, spot=20.0):
        out = []
        for e in ("2026-10-28", "2026-11-20", "2026-12-18"):
            t = (date.fromisoformat(e) - AS_OF).days / 365
            for k in range(16, 25):
                for ty in ("call", "put"):
                    p = pricing.black_scholes(ty, spot, k, t, 0.5).price
                    out.append(Leg("buy", ty, 1, float(k), e, bid=round(p * 0.97, 2), ask=round(p * 1.03, 2) or 0.01,
                                   iv=0.5))
        return out

    def test_bull_put_strangle_long_shot_and_sizing(self):
        ch = self.chain()
        bp = advisor.candidates("vertical_spread:bull_put", ch, 20.0)
        self.assertTrue(bp)
        self.assertTrue(all(l[0].action == "sell" and l[0].strike > l[1].strike and l[0].strike < 20 for l in bp))
        self.assertEqual(Trade("X", 20, bp[0]).strategy(), "vertical_spread")
        st = advisor.candidates("strangle", ch, 20.0)
        self.assertTrue(st and all(Trade("X", 20, l).strategy() == "strangle" for l in st))
        ls = advisor.candidates("long_shot:call", ch, 20.0, as_of=AS_OF)
        self.assertTrue(ls and all(l[0].expiration == "2026-10-28" and l[0].ask < 1 for l in ls))
        sized = advisor.candidates("long_call", ch, 20.0, 3, cap=100, max_price=0.50)
        self.assertTrue(sized)
        for (l,) in sized:
            self.assertLessEqual(l.ask, 0.50)
            self.assertLessEqual(l.quantity * l.ask * 100, 100 + 1e-9)
            self.assertGreaterEqual(l.quantity, 1)

    def test_ideas_include_new_types(self):
        from optiontool.technical import TechnicalView
        tv = TechnicalView("SPY", 500, primary_trend="up", trend_strength="trending")
        names = [i.strategy for i in advisor.ideas(tv, "high", event_date="2026-11-05", short_dated=True, is_index=True)]
        self.assertIn("vertical_spread:bull_put", names)
        self.assertIn("long_shot:call", names)
        self.assertNotIn("strangle", names)   # premiums already high
        names = [i.strategy for i in advisor.ideas(tv, "low", event_date="2026-11-05")]
        self.assertIn("strangle", names)


class Tracking(unittest.TestCase):
    def pos(self, **kw):
        c = [{"action": "buy", "type": "call", "strike": 22.0, "expiration": "2026-12-18", "qty": 2, "price": 0.50}]
        p = positions.new_position("ABC", c, 0.50, "2026-10-22", stop=19.0, target=25.0, close_by="2026-11-18",
                                   spot=20.0)
        p.update(kw)
        return p

    def leg(self, bid, ask):
        return [Leg("buy", "call", 1, 22.0, "2026-12-18", bid=bid, ask=ask)]

    def test_hold_double_stop_time(self):
        p = self.pos()
        self.assertEqual(positions.check(p, 20.5, self.leg(0.55, 0.6), date(2026, 10, 23))["action"], positions.HOLD)
        r = positions.check(p, 21.5, self.leg(1.05, 1.1), date(2026, 10, 24))
        self.assertEqual(r["action"], positions.PROFIT)
        self.assertAlmostEqual(r["pnl"], (1.05 - 0.5) * 200)
        self.assertEqual(positions.check(p, 18.9, self.leg(0.2, 0.25), date(2026, 10, 25))["action"], positions.EXIT)
        self.assertEqual(positions.check(self.pos(), 20.4, self.leg(0.5, 0.55), date(2026, 11, 18))["action"],
                         positions.EXIT)

    def test_trailing_stop_and_trend(self):
        p = self.pos()
        positions.check(p, 23.0, self.leg(1.2, 1.3), date(2026, 10, 24))   # new high
        r = positions.check(p, 21.8, self.leg(0.9, 1.0), date(2026, 10, 27))  # >5% off the high, still in profit
        self.assertEqual(r["action"], positions.EXIT)
        self.assertTrue(any("Trailing" in x["text"] for x in r["reasons"]))
        r = positions.check(self.pos(), 20.2, self.leg(0.5, 0.55), date(2026, 10, 24), trend="down")
        self.assertEqual(r["action"], positions.EXIT)

    def test_credit_buyback(self):
        c = [{"action": "sell", "type": "put", "strike": 18.0, "expiration": "2026-11-20", "qty": 1, "price": 0.60},
             {"action": "buy", "type": "put", "strike": 17.0, "expiration": "2026-11-20", "qty": 1, "price": 0.30}]
        p = positions.new_position("ABC", c, -0.30, "2026-10-22", stop=18.5, spot=20.0)
        self.assertEqual(p["bias"], 1)
        chain = [Leg("buy", "put", 1, 18.0, "2026-11-20", bid=0.04, ask=0.06),
                 Leg("buy", "put", 1, 17.0, "2026-11-20", bid=0.01, ask=0.02)]
        r = positions.check(p, 21.0, chain, date(2026, 11, 5))
        self.assertEqual(r["action"], positions.BUYBACK)


class TradesWeb(unittest.TestCase):
    def test_track_ticket_and_close(self):
        tmp = Path(tempfile.mkdtemp())
        inbox = tmp / "inbox"
        inbox.mkdir()
        for f in ("XYZ.csv", "XYZ_chain.csv"):
            shutil.copy(EX / f, inbox / f)
        cfg_path = tmp / "config.json"
        cfg = pipeline.load_config(cfg_path)
        cfg.update({"provider": "csv", "csv_folder": str(inbox), "data_dir": str(tmp / "data"),
                    "fetch_history": False, "watchlist": [], "benchmarks": {}})
        pipeline.save_config(cfg_path, cfg)
        webapp.Handler.app = webapp.App(str(cfg_path))
        srv = webapp.ThreadingHTTPServer(("127.0.0.1", 0), webapp.Handler)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        base = f"http://127.0.0.1:{srv.server_address[1]}"
        try:
            tk = {"symbol": "XYZ", "strategy": "long_call", "stop": 170, "target": 200, "close_by": "2026-11-18",
                  "spot": 180.58, "debit": True,
                  "contracts": [{"action": "buy", "type": "call", "strike": 190.0, "expiration": "2027-01-15",
                                 "qty": 1, "price": 7.64}]}
            urllib.request.urlopen(base + "/trades/add",
                                   data=urllib.parse.urlencode({"ticket": json.dumps(tk), "fill": "7.50"}).encode())
            book = positions.load(cfg)
            self.assertEqual(len(book), 1)
            self.assertEqual(book[0]["entry"], 7.5)
            # a pull includes held symbols and checks the trade
            snap = pipeline.pull(cfg, datetime(2026, 10, 22, 11, 0, tzinfo=pipeline.NY))
            self.assertTrue((snap / "XYZ_poschain.csv").exists())
            res = pipeline.analyze(cfg, snap, LIB)
            self.assertEqual(len(res.positions), 1)
            self.assertIn(res.positions[0]["action"], (positions.HOLD, positions.EXIT, positions.PROFIT))
            self.assertIsNotNone(res.positions[0]["value"])
            page = urllib.request.urlopen(base + "/trades").read().decode()
            self.assertIn("Long 1 × XYZ Jan 15, 2027 $190 call", page)
            self.assertIn("My trades", urllib.request.urlopen(base + "/").read().decode())
            urllib.request.urlopen(base + "/trades/manual", data=urllib.parse.urlencode({
                "symbol": "abc", "action": "buy", "type": "put", "strike": "20", "expiration": "12/18/2026",
                "qty": "2", "fill": "0.40", "stop": "", "target": "", "close_by": ""}).encode())
            self.assertEqual(positions.load(cfg)[1]["bias"], -1)
            pid = positions.load(cfg)[0]["id"]
            urllib.request.urlopen(base + "/trades/close", data=urllib.parse.urlencode({"id": pid, "exit": "9.00"}).encode())
            closed = [p for p in positions.load(cfg) if p["status"] == "closed"][0]
            self.assertAlmostEqual(closed["pnl"], 150.0)
            body = urllib.parse.urlencode({"provider": "csv", "watchlist": "XYZ", "benchmarks": "",
                                           "holdings": "", "account_value": "200", "option_budget": "200",
                                           "min_dte": "3", "max_dte": "160", "expiration_count": "4",
                                           "csv_folder": str(inbox), "aggressive_mode": "1", "include_weeklies": "1",
                                           "max_option_price": "0.5", "events": "XYZ=2026-11-05\nbad=nope",
                                           "app_key": "", "app_secret": "", "callback_url": "https://127.0.0.1"})
            urllib.request.urlopen(base + "/settings", data=body.encode())
            c = pipeline.load_config(cfg_path)
            self.assertTrue(c["aggressive_mode"] and c["include_weeklies"])
            self.assertEqual(c["max_option_price"], 0.5)
            self.assertEqual(c["events"], {"XYZ": "2026-11-05"})
        finally:
            srv.shutdown()
            srv.server_close()


if __name__ == "__main__":
    unittest.main()
