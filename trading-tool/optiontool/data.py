"""Price-history loading. Market data is supplied as CSV so the tool works
offline and with any data vendor (IAO ch. 17 surveys many sources)."""

from __future__ import annotations

import csv
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class PriceHistory:
    symbol: str
    dates: list[str] = field(default_factory=list)
    opens: list[float] = field(default_factory=list)
    highs: list[float] = field(default_factory=list)
    lows: list[float] = field(default_factory=list)
    closes: list[float] = field(default_factory=list)
    volumes: list[float] = field(default_factory=list)

    def __len__(self) -> int:
        return len(self.closes)

    @property
    def last(self) -> float:
        return self.closes[-1]

    def weekly(self) -> "PriceHistory":
        """Resample to 5-bar weeks, for multi-timeframe confirmation (IAO ch. 4)."""
        w = PriceHistory(self.symbol + ":W")
        start = len(self) % 5
        for i in range(start, len(self), 5):
            j = i + 5
            w.dates.append(self.dates[j - 1])
            w.opens.append(self.opens[i])
            w.highs.append(max(self.highs[i:j]))
            w.lows.append(min(self.lows[i:j]))
            w.closes.append(self.closes[j - 1])
            w.volumes.append(sum(self.volumes[i:j]))
        return w


def _pick(row: dict, *names: str) -> str | None:
    lowered = {k.strip().lower(): v for k, v in row.items() if k}
    for n in names:
        if n in lowered and lowered[n] not in ("", None):
            return lowered[n]
    return None


def load_csv(path: str | Path, symbol: str | None = None) -> PriceHistory:
    """Read a Date,Open,High,Low,Close[,Adj Close],Volume CSV (Yahoo-style).

    Rows are sorted by date ascending. Missing open/high/low fall back to close.
    """
    path = Path(path)
    hist = PriceHistory(symbol or path.stem.upper())
    rows = []
    with path.open(newline="", encoding="utf-8-sig") as fh:
        for row in csv.DictReader(fh):
            close = _pick(row, "close", "adj close", "adj_close")
            date = _pick(row, "date", "timestamp", "time")
            if close is None or date is None:
                continue
            c = float(close)
            rows.append((
                date,
                float(_pick(row, "open") or c),
                float(_pick(row, "high") or c),
                float(_pick(row, "low") or c),
                c,
                float(_pick(row, "volume") or 0),
            ))
    rows.sort(key=lambda r: r[0])
    for d, o, h, l, c, v in rows:
        hist.dates.append(d)
        hist.opens.append(o)
        hist.highs.append(h)
        hist.lows.append(l)
        hist.closes.append(c)
        hist.volumes.append(v)
    return hist
