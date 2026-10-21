"""Thresholds the tool needs where the books give no number.

Everything here is a *user setting*, not a book rule: findings that use one
say so. Book-stated numbers live in the rule checks themselves, next to the
principle id they come from. Override any value with a JSON file passed via
``--settings``.
"""

from __future__ import annotations

import json
from pathlib import Path

DEFAULTS = {
    # OTS-075-a illustrates slippage of ~30% of an option's price but sets no
    # limit. Warn when the bid-ask spread is wider than this fraction of mid.
    "max_bid_ask_pct": 0.10,
    # OTS-TUT-a says liquidity matters without a number.
    "min_open_interest": 100,
    # OTS-012-d: "suddenly very expensive" options. IV/HV ratio treated as a spike.
    "iv_spike_ratio": 1.5,
    # OTS-057-b: "extremely" low/high IV versus its own history (percentile).
    "iv_extreme_low_pct": 20,
    "iv_extreme_high_pct": 80,
    # OTS-046-a "most of the premium": fraction captured that triggers buy-back.
    "writer_buyback_captured": 0.80,
    # OTS-024-b "extremely cheap": market price as fraction of fair value.
    "extremely_cheap_ratio": 0.80,
    # Long-dated options are treated as LEAPS from this many days out.
    "leaps_min_dte": 270,
    # OTS-056-b: switch from 20-30 day to ~100 day volatility for long holds.
    "long_hold_dte": 60,
    # Underlyings treated as broad indexes (OTS-097-a, OTS-098-a, OTS-039-a).
    "index_symbols": ["SPX", "XSP", "OEX", "XEO", "NDX", "RUT", "DJX", "SPY", "QQQ", "IWM", "DIA"],
}


def load(path: str | Path | None = None) -> dict:
    s = dict(DEFAULTS)
    if path:
        s.update(json.loads(Path(path).read_text()))
    return s
