"""Generate SYNTHETIC sample data for trying the tool offline.

These prices are random (seeded) — not real market data. Replace them with
real CSVs from your broker or data vendor for actual decisions.
"""

import csv
import math
import random
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from optiontool.pricing import black_scholes  # noqa: E402

OUT = Path(__file__).resolve().parent
END = date(2026, 10, 21)


def trading_days(n):
    d, out = END, []
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d -= timedelta(days=1)
    return out[::-1]


def series(seed, start, drift, vol, n=400):
    rnd = random.Random(seed)
    days = trading_days(n)
    px, rows = start, []
    for d in days:
        r = rnd.gauss(drift / 252, vol / math.sqrt(252))
        o = px
        px *= math.exp(r)
        hi = max(o, px) * (1 + abs(rnd.gauss(0, vol / 40)))
        lo = min(o, px) * (1 - abs(rnd.gauss(0, vol / 40)))
        rows.append([d.isoformat(), round(o, 2), round(hi, 2), round(lo, 2), round(px, 2),
                     int(rnd.uniform(0.8, 1.3) * 2_000_000)])
    return rows


def write(name, rows):
    with (OUT / name).open("w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["Date", "Open", "High", "Low", "Close", "Volume"])
        w.writerows(rows)


def chain(spot, iv, name):
    rnd = random.Random(7)
    exps = ["2026-11-20", "2026-12-18", "2027-01-15", "2027-03-19"]
    with (OUT / name).open("w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["expiration", "strike", "type", "bid", "ask", "iv", "open_interest", "volume"])
        base = round(spot / 5) * 5
        for e in exps:
            t = (date.fromisoformat(e) - END).days / 365
            for k in range(int(base - 25), int(base + 30), 5):
                for typ in ("call", "put"):
                    skew = iv * (1 + 0.15 * (spot - k) / spot) * rnd.uniform(0.92, 1.08)
                    p = black_scholes(typ, spot, k, t, skew, 0.04).price
                    if p < 0.05:
                        continue
                    half = max(0.03, p * 0.03)
                    w.writerow([e, k, typ, round(p - half, 2), round(p + half, 2), round(skew, 4),
                                rnd.randint(150, 5000), rnd.randint(10, 800)])


if __name__ == "__main__":
    xyz = series(11, 80, 0.30, 0.28)
    write("XYZ.csv", xyz)
    write("BENCH.csv", series(5, 400, 0.10, 0.16))
    chain(xyz[-1][4], 0.30, "XYZ_chain.csv")
    print("spot", xyz[-1][4])
