import math
import re
import unittest
from datetime import date
from pathlib import Path

from optiontool import context, engine, indicators, pricing, rules, settings
from optiontool.data import load_csv
from optiontool.knowledge import Library
from optiontool.trade import Leg, Trade, classify

ROOT = Path(__file__).resolve().parent.parent
LIB = Library()
CFG = settings.load()
AS_OF = date(2026, 10, 21)


def evaluate(legs, spot=100.0, hv=0.30, plan=None, **kw):
    t = Trade("XYZ", spot, legs, AS_OF, historical_vol=hv, **kw)
    return engine.evaluate(context.build(t, CFG, plan=plan or {}), LIB)


def ids(decision, severity):
    return {pid for f in decision.findings if f.severity == severity for pid in f.principles}


class Pricing(unittest.TestCase):
    def test_black_scholes_reference(self):
        self.assertAlmostEqual(pricing.black_scholes("call", 100, 100, 1, 0.2, 0.05).price, 10.4506, places=3)

    def test_put_call_parity(self):
        c = pricing.black_scholes("call", 100, 95, 0.5, 0.3, 0.03).price
        p = pricing.black_scholes("put", 100, 95, 0.5, 0.3, 0.03).price
        self.assertAlmostEqual(c - p, 100 - 95 * math.exp(-0.03 * 0.5), places=6)

    def test_implied_vol_round_trip(self):
        price = pricing.black_scholes("put", 50, 55, 0.25, 0.42).price
        self.assertAlmostEqual(pricing.implied_volatility("put", price, 50, 55, 0.25), 0.42, places=4)

    def test_probabilities(self):
        self.assertAlmostEqual(pricing.prob_above(100, 100, 1, 0.2) + pricing.prob_below(100, 100, 1, 0.2), 1)
        self.assertGreater(pricing.prob_touch(100, 110, 0.25, 0.3), pricing.prob_above(100, 110, 0.25, 0.3))


class Indicators(unittest.TestCase):
    def test_sma(self):
        self.assertEqual(indicators.sma([1, 2, 3, 4], 2), [None, 1.5, 2.5, 3.5])

    def test_rsi_bounds(self):
        up = [float(i) for i in range(1, 40)]
        self.assertEqual(indicators.rsi(up)[-1], 100.0)
        r = [x for x in indicators.rsi([100 + math.sin(i) * 5 for i in range(60)]) if x is not None]
        self.assertTrue(all(0 <= x <= 100 for x in r))

    def test_adx_trending(self):
        n = 120
        h = [100 + i + 1 for i in range(n)]
        l = [100 + i - 1 for i in range(n)]
        c = [100 + i for i in range(n)]
        self.assertGreater(indicators.adx(h, l, c)[0][-1], 22)


class Classification(unittest.TestCase):
    def test_names(self):
        c = lambda *legs: classify(list(legs))
        call = lambda a, k, e="2027-01-15", q=1: Leg(a, "call", q, k, e, 1.0)
        put = lambda a, k, e="2027-01-15": Leg(a, "put", 1, k, e, 1.0)
        stock = Leg("buy", "stock", 100, price=100)
        self.assertEqual(c(call("buy", 100)), "long_call")
        self.assertEqual(c(put("sell", 90)), "short_put")
        self.assertEqual(c(call("buy", 100), call("sell", 110)), "vertical_spread")
        self.assertEqual(c(call("buy", 100), put("buy", 100)), "straddle")
        self.assertEqual(c(call("buy", 100, "2027-03-19"), call("sell", 100)), "calendar_spread")
        self.assertEqual(c(call("buy", 100), call("sell", 110, q=2)), "ratio_spread")
        self.assertEqual(c(stock, call("sell", 110)), "covered_call")
        self.assertEqual(c(stock, put("buy", 95), call("sell", 110)), "collar")


class Library_(unittest.TestCase):
    def test_books_loaded(self):
        self.assertIn("OTS", LIB.books)
        self.assertIn("IAO", LIB.books)
        self.assertGreater(len(LIB), 400)

    def test_every_cited_principle_exists(self):
        """No decision may cite a principle that isn't in a loaded book."""
        src = "".join(p.read_text(encoding="utf-8") for p in (ROOT / "optiontool").glob("*.py"))
        cited = set(re.findall(r'"((?:OTS|IAO)-[0-9A-Z]+(?:-[0-9a-z]+)?)"', src))
        self.assertTrue(cited)
        missing = sorted(c for c in cited if c not in LIB)
        self.assertEqual(missing, [])

    def test_search(self):
        self.assertTrue(any(p.id == "OTS-024-a" for p in LIB.search("last month")))


class Rules(unittest.TestCase):
    PLAN = {"stop": 95, "target": 115}

    def test_overvalued_long_call_rejected(self):
        d = evaluate([Leg("buy", "call", 3, 105, "2027-01-15", 6.0)], hv=0.20, plan=self.PLAN)
        self.assertEqual(d.verdict, "REJECT")
        self.assertIn("OTS-019-a", ids(d, "BLOCK"))

    def test_undervalued_long_call_passes_value_test(self):
        fair = pricing.black_scholes("call", 100, 105, 86 / 365, 0.40, 0.04).price
        d = evaluate([Leg("buy", "call", 3, 105, "2027-01-15", round(fair * 0.8, 2))], hv=0.40,
                     plan=self.PLAN, account_value=100000)
        self.assertNotIn("OTS-019-a", ids(d, "BLOCK"))
        self.assertIn("OTS-019-a", ids(d, "PASS"))

    def test_last_month_blocked(self):
        d = evaluate([Leg("buy", "call", 3, 100, "2026-11-06", 2.6)], plan=self.PLAN)
        self.assertIn("OTS-024-a", ids(d, "BLOCK"))

    def test_last_month_exception_for_extremely_cheap(self):
        d = evaluate([Leg("buy", "call", 3, 100, "2026-11-06", 1.0)], plan=self.PLAN)
        self.assertNotIn("OTS-024-a", ids(d, "BLOCK"))
        self.assertIn("OTS-024-b", ids(d, "WARN"))

    def test_naked_put_needs_90pct(self):
        d = evaluate([Leg("sell", "put", 1, 98, "2026-11-13", 3.0)], plan=self.PLAN, account_value=50000)
        self.assertIn("OTS-044-a", ids(d, "BLOCK"))

    def test_writer_without_stop_blocked(self):
        d = evaluate([Leg("sell", "put", 1, 80, "2026-11-13", 0.5)], account_value=50000)
        self.assertIn("OTS-087-a", ids(d, "BLOCK"))

    def test_long_dated_write_blocked(self):
        d = evaluate([Leg("sell", "put", 1, 80, "2027-03-19", 2.0)], plan=self.PLAN, account_value=50000)
        self.assertIn("OTS-045-b", ids(d, "BLOCK"))

    def test_three_leg_structure_blocked(self):
        e = "2027-01-15"
        d = evaluate([Leg("buy", "call", 1, 95, e, 7.0), Leg("sell", "call", 2, 100, e, 4.0),
                      Leg("buy", "call", 1, 105, e, 2.0)], plan=self.PLAN)
        self.assertIn("OTS-069", ids(d, "BLOCK"))

    def test_position_size_limit(self):
        d = evaluate([Leg("buy", "call", 10, 105, "2027-01-15", 3.0)], plan={**self.PLAN, "option_buying_portfolio": 20000},
                     account_value=200000)
        self.assertIn("OTS-025-a", ids(d, "BLOCK"))

    def test_small_account_mode(self):
        legs = [Leg("buy", "call", 1, 105, "2027-01-15", 0.80)]
        strict = evaluate(legs, plan={**self.PLAN, "option_buying_portfolio": 20}, account_value=200)
        self.assertIn("OTS-025-a", ids(strict, "BLOCK"))
        small = evaluate(legs, plan={**self.PLAN, "option_buying_portfolio": 200, "small_account_mode": True,
                                     "max_trade_risk": 100}, account_value=200)
        self.assertNotIn("OTS-025-a", ids(small, "BLOCK"))
        self.assertNotIn("OTS-094-a", ids(small, "BLOCK"))
        self.assertIn("OTS-025-a", ids(small, "WARN"))
        over = evaluate([Leg("buy", "call", 1, 100, "2027-01-15", 3.50)],
                        plan={**self.PLAN, "small_account_mode": True, "max_trade_risk": 100}, account_value=200)
        self.assertIn("OTS-004-b", ids(over, "BLOCK"))

    def test_debit_spread_return(self):
        e = "2027-01-15"
        d = evaluate([Leg("buy", "call", 1, 100, e, 5.0), Leg("sell", "call", 1, 105, e, 2.5)], plan=self.PLAN)
        self.assertIn("OTS-073-a", ids(d, "BLOCK"))   # 100% max return < 200%

    def test_index_credit_spread_rules(self):
        e = "2026-11-06"
        t = [Leg("sell", "call", 1, 6200, e, 0.30), Leg("buy", "call", 1, 6207, e, 0.10)]
        tr = Trade("SPX", 6000, t, AS_OF, historical_vol=0.15)
        d = engine.evaluate(context.build(tr, CFG, plan={"stop": 6150, "target": 6000}), LIB)
        blocked = ids(d, "BLOCK")
        self.assertIn("OTS-098-a", blocked)   # width 7
        self.assertIn("OTS-098-b", blocked)   # credit .20 < .45

    def test_every_finding_is_cited(self):
        d = evaluate([Leg("buy", "put", 1, 95, "2026-12-18", 2.0)])
        for f in d.findings + d.management:
            self.assertTrue(f.principles, f.message)


class ChainParsing(unittest.TestCase):
    def test_broker_formats(self):
        import tempfile
        from optiontool import advisor
        text = ("Expiration,Strike,Type,Bid,Ask,IV,Open Interest,Volume\n"
                '11/20/2026,"1,005.00",C,$2.10,2.25,28.5%,"1,234",--\n'
                "2026-12-18,250,put,0.95,1.05,0.31,,12\n")
        with tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False, encoding="utf-8") as fh:
            fh.write(text)
        a, b = advisor.load_chain(fh.name)
        self.assertEqual((a.expiration, a.strike, a.type, a.bid, a.iv, a.open_interest, a.volume),
                         ("2026-11-20", 1005.0, "call", 2.10, 0.285, 1234, None))
        self.assertEqual((b.type, b.iv, b.open_interest, b.volume), ("put", 0.31, None, 12))

    def test_dense_chain_is_pruned(self):
        from optiontool import advisor
        chain = [Leg("buy", t, 1, float(k), e, bid=1.0, ask=1.1, iv=0.2)
                 for e in ("2026-11-20", "2026-12-18") for k in range(85, 116) for t in ("call", "put")]
        legs = advisor.candidates("vertical_spread:bull_call", chain, 100)
        self.assertTrue(legs)
        self.assertTrue(all(abs(l[0].strike - 100) <= 3 for l in legs))   # bought leg near the money


class EndToEnd(unittest.TestCase):
    def test_example_with_history(self):
        import json
        spec = json.loads((ROOT / "examples" / "long_call.json").read_text(encoding="utf-8"))
        hist = load_csv(ROOT / "examples" / "XYZ.csv", "XYZ")
        ctx = context.build(Trade.from_dict(spec), CFG, hist, spec["plan"])
        d = engine.evaluate(ctx, LIB)
        self.assertIn(d.verdict, ("REJECT", "CAUTION", "ACCEPT"))
        self.assertIsNotNone(ctx.technical)
        self.assertIn(ctx.technical.primary_trend, ("up", "down", "sideways"))


if __name__ == "__main__":
    unittest.main()
