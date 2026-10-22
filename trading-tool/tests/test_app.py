import json
import shutil
import tempfile
import threading
import unittest
import urllib.request
from datetime import date, datetime, timezone, timedelta
from pathlib import Path

from optiontool import pipeline, providers, webapp
from optiontool.data import load_csv

ROOT = Path(__file__).resolve().parent.parent
EX = ROOT / "examples"


def schwab_history_json(symbol):
    h = load_csv(EX / "XYZ.csv", symbol)
    candles = []
    for d, o, hi, lo, c, v in zip(h.dates, h.opens, h.highs, h.lows, h.closes, h.volumes):
        # Schwab stamps daily candles at midnight US/Central (05:00/06:00 UTC)
        ts = datetime.fromisoformat(d).replace(tzinfo=timezone.utc) + timedelta(hours=5)
        candles.append({"open": o, "high": hi, "low": lo, "close": c, "volume": v,
                        "datetime": int(ts.timestamp() * 1000)})
    return {"symbol": symbol, "empty": False, "candles": candles}


def schwab_chain_json(symbol, spot=180.58):
    import csv
    calls, puts = {}, {}
    for r in csv.DictReader((EX / "XYZ_chain.csv").open()):
        exp = r["expiration"]
        dte = (date.fromisoformat(exp) - date(2026, 10, 22)).days
        target = calls if r["type"] == "call" else puts
        target.setdefault(f"{exp}:{dte}", {})[f"{float(r['strike']):.1f}"] = [{
            "putCall": r["type"].upper(), "bid": float(r["bid"]), "ask": float(r["ask"]),
            "volatility": float(r["iv"]) * 100, "openInterest": int(r["open_interest"]),
            "totalVolume": int(r["volume"]), "strikePrice": float(r["strike"]),
            "expirationDate": exp + "T21:00:00.000+00:00"}]
    # a weekly that should be filtered out, and a NaN IV
    calls["2026-11-27:36"] = {"180.0": [{"bid": 1, "ask": 1.1, "volatility": -999.0, "openInterest": 5,
                                         "totalVolume": 1, "strikePrice": 180.0}]}
    return {"symbol": symbol, "status": "SUCCESS", "underlyingPrice": spot,
            "callExpDateMap": calls, "putExpDateMap": puts}


class FakeAuth:
    def access_token(self):
        return "token"


class SchwabParsing(unittest.TestCase):
    def test_history_dates_are_session_dates(self):
        h = providers.parse_schwab_history("XYZ", schwab_history_json("XYZ"))
        ref = load_csv(EX / "XYZ.csv")
        self.assertEqual(h.dates, ref.dates)
        self.assertEqual(h.closes, ref.closes)

    def test_chain_monthlies_and_band(self):
        def opener(req):
            return schwab_chain_json("XYZ") if "/chains" in req.full_url else schwab_history_json("XYZ")
        p = providers.SchwabProvider(FakeAuth(), opener)
        snap = p.chain("XYZ", date(2026, 10, 22), pipeline.DEFAULT_CONFIG)
        exps = sorted({l.expiration for l in snap.legs})
        self.assertEqual(exps, ["2026-11-20", "2026-12-18", "2027-01-15", "2027-03-19"])
        self.assertTrue(all(abs(l.strike - 180.58) / 180.58 <= 0.15 for l in snap.legs))
        self.assertTrue(all(l.iv is None or 0 < l.iv < 3 for l in snap.legs))

    def test_monthly_expirations_handles_holiday_thursday(self):
        # Good Friday 2027-03-26 is not third Friday; 2027-04-15 is a Thursday (example holiday shift)
        got = providers.monthly_expirations(["2026-11-20", "2026-11-27", "2027-04-15"], date(2026, 10, 22),
                                            max_dte=200)
        self.assertEqual(got, ["2026-11-20", "2027-04-15"])

    def test_authorize_url_and_code_exchange_parsing(self):
        tmp = Path(tempfile.mkdtemp())
        auth = providers.SchwabAuth("KEY", "SECRET", "https://127.0.0.1", tmp / "tok.json")
        self.assertIn("client_id=KEY", auth.authorize_url())
        with self.assertRaises(providers.ProviderError):
            auth.finish_login("https://127.0.0.1/?session=abc")
        self.assertFalse(auth.status()["logged_in"])


class Pipeline(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        inbox = self.tmp / "inbox"
        inbox.mkdir()
        shutil.copy(EX / "XYZ.csv", inbox / "XYZ.csv")
        shutil.copy(EX / "XYZ_chain.csv", inbox / "XYZ_chain.csv")
        shutil.copy(EX / "BENCH.csv", inbox / "BENCH.csv")
        self.cfg_path = self.tmp / "config.json"
        cfg = pipeline.load_config(self.cfg_path)
        cfg.update({"provider": "csv", "csv_folder": str(inbox), "data_dir": str(self.tmp / "data"),
                    "watchlist": ["XYZ", "MISSING"], "benchmarks": {"XYZ": "BENCH"}, "holdings": ["XYZ"],
                    "account_value": 100000})
        pipeline.save_config(self.cfg_path, cfg)
        self.cfg = cfg

    def test_pull_and_analyze(self):
        now = datetime(2026, 10, 22, 10, 0, tzinfo=pipeline.NY)
        snap = pipeline.pull(self.cfg, now)
        meta = json.loads((snap / "meta.json").read_text())
        self.assertIn("MISSING", meta["errors"])
        self.assertTrue((Path(self.cfg["data_dir"]) / "iv_log.csv").exists())
        res = pipeline.analyze(self.cfg, snap)
        by = {t.symbol: t for t in res.tickers}
        self.assertTrue(by["XYZ"].ok, by["XYZ"].error)
        self.assertFalse(by["MISSING"].ok)
        self.assertTrue(by["XYZ"].ideas)
        self.assertTrue((snap / "results.json").exists())

    def test_market_status(self):
        self.assertFalse(pipeline.market_status(datetime(2026, 10, 22, 8, 0, tzinfo=pipeline.NY))[0])
        self.assertTrue(pipeline.market_status(datetime(2026, 10, 22, 11, 0, tzinfo=pipeline.NY))[0])
        self.assertFalse(pipeline.market_status(datetime(2026, 10, 24, 11, 0, tzinfo=pipeline.NY))[0])

    def test_webapp_pages(self):
        snap = pipeline.pull(self.cfg, datetime(2026, 10, 22, 10, 0, tzinfo=pipeline.NY))
        pipeline.analyze(self.cfg, snap)
        webapp.Handler.app = webapp.App(str(self.cfg_path))
        srv = webapp.ThreadingHTTPServer(("127.0.0.1", 0), webapp.Handler)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        base = f"http://127.0.0.1:{srv.server_address[1]}"
        try:
            home = urllib.request.urlopen(base + "/").read().decode()
            self.assertIn("XYZ", home)
            self.assertIn("Pull &amp; analyze", home)
            self.assertIn("/principle/", home)
            for path in ("/settings", "/history", "/principles?q=stop", "/principle/OTS-024-a", "/schwab",
                         f"/snapshot/{snap.name}"):
                self.assertEqual(urllib.request.urlopen(base + path).status, 200, path)
            # path traversal is refused
            self.assertIn("Not found", urllib.request.urlopen(base + "/snapshot/..%2F..").read().decode())
        finally:
            srv.shutdown()


if __name__ == "__main__":
    unittest.main()
