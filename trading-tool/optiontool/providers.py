"""Market-data providers: where a "pull" gets prices and option chains.

* SchwabProvider — the official Schwab Trader API (developer.schwab.com).
  Free for Schwab account holders; needs an app key/secret and a login about
  once a week (refresh tokens last 7 days).
* CsvFolderProvider — reads files you drop in a folder (<SYM>.csv for
  history, <SYM>_chain.csv for the chain). Works with any broker export.

Everything here uses only the standard library.
"""

from __future__ import annotations

import base64
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from . import advisor
from .data import PriceHistory, load_csv
from .trade import Leg


@dataclass
class ChainSnapshot:
    symbol: str
    spot: float
    legs: list[Leg]


class ProviderError(RuntimeError):
    pass


def monthly_expirations(expirations: list[str], as_of: date, min_dte: int = 20,
                        max_dte: int = 160, count: int = 4) -> list[str]:
    """Pick standard monthly expirations (third Friday, or the Thursday before
    a holiday) between ``min_dte`` and ``max_dte`` days out."""
    by_month: dict[tuple[int, int], str] = {}
    for e in sorted(set(expirations)):
        d = date.fromisoformat(e)
        dte = (d - as_of).days
        if not (min_dte <= dte <= max_dte):
            continue
        third_friday = (d.weekday() == 4 and 15 <= d.day <= 21) or (d.weekday() == 3 and 14 <= d.day <= 20)
        if third_friday:
            by_month[(d.year, d.month)] = e
    return [by_month[k] for k in sorted(by_month)][:count]


# ---------------------------------------------------------------- CSV folder
class CsvFolderProvider:
    """Files in a folder, one set per symbol:

    * ``<SYM>.csv``        daily price history (optional when ``fetch_history`` is on)
    * ``<SYM>_chain.csv``  option chain
    * ``<SYM>_spot.txt``   live underlying price when the chain was copied
      (optional; otherwise a ``spot``/``underlying`` column in the chain, else the
      last close — which is stale intraday, so the app warns)
    """
    name = "csv"

    def __init__(self, folder: str | Path, fetch_history: bool = False):
        self.folder = Path(folder).expanduser()
        self.fetch_history = fetch_history

    def history(self, symbol: str) -> PriceHistory:
        path = self.folder / f"{symbol}.csv"
        if path.exists():
            return load_csv(path, symbol)
        if self.fetch_history:
            return yahoo_history(symbol)
        raise ProviderError(f"missing {path}")

    def chain(self, symbol: str, as_of: date, settings: dict) -> ChainSnapshot:
        path = self.folder / f"{symbol}_chain.csv"
        if not path.exists():
            raise ProviderError(f"missing {path}")
        legs = advisor.load_chain(path)
        keep = set(monthly_expirations([l.expiration for l in legs], as_of, **_exp_window(settings))) \
            if not settings.get("include_weeklies", False) else None
        legs = [l for l in legs if keep is None or l.expiration in keep]
        return ChainSnapshot(symbol, self._spot(symbol, path), legs)

    def _spot(self, symbol: str, chain_path: Path) -> float:
        f = self.folder / f"{symbol}_spot.txt"
        if f.exists():
            return float(f.read_text(encoding="utf-8").strip().replace("$", "").replace(",", ""))
        import csv as _csv
        with chain_path.open(newline="", encoding="utf-8-sig") as fh:
            for r in _csv.DictReader(fh):
                for k, v in r.items():
                    if (k or "").strip().lower() in ("spot", "underlying", "underlying_price", "last") and v:
                        return float(str(v).replace("$", "").replace(",", ""))
                break
        self.stale_spot = True
        return self.history(symbol).last


def yahoo_history(symbol: str, years: int = 2) -> PriceHistory:
    """Daily bars from Yahoo Finance's public chart endpoint (no key needed)."""
    url = (f"https://query1.finance.yahoo.com/v8/finance/chart/{urllib.parse.quote(symbol)}"
           f"?range={years}y&interval=1d")
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    return parse_yahoo_chart(symbol, _http_json(req))


def parse_yahoo_chart(symbol: str, data: dict) -> PriceHistory:
    try:
        r = data["chart"]["result"][0]
        q = r["indicators"]["quote"][0]
        ts = r["timestamp"]
        offset = r.get("meta", {}).get("gmtoffset", -4 * 3600)
    except (KeyError, IndexError, TypeError):
        raise ProviderError(f"Yahoo returned no history for {symbol}") from None
    h = PriceHistory(symbol)
    for i, t in enumerate(ts):
        vals = [q["open"][i], q["high"][i], q["low"][i], q["close"][i]]
        if any(v is None for v in vals):
            continue
        h.dates.append(datetime.fromtimestamp(t + offset, tz=timezone.utc).date().isoformat())
        h.opens.append(float(vals[0]))
        h.highs.append(float(vals[1]))
        h.lows.append(float(vals[2]))
        h.closes.append(float(vals[3]))
        h.volumes.append(float(q["volume"][i] or 0))
    return h


def _exp_window(settings: dict) -> dict:
    return {"min_dte": settings.get("min_dte", 20), "max_dte": settings.get("max_dte", 160),
            "count": settings.get("expiration_count", 4)}


# ---------------------------------------------------------------- Schwab
SCHWAB_BASE = "https://api.schwabapi.com"


def extract_code(pasted: str) -> str:
    """Pull the authorization code out of whatever was pasted: the full redirect
    URL (with or without https://), just its ?query, or the bare code."""
    import re
    text = pasted.strip().strip('"').strip("'")
    if not text:
        raise ProviderError("nothing was pasted — copy the whole address from the browser's address bar")
    m = re.search(r"[?&#]code=([^&#\s]+)", text) or re.match(r"code=([^&#\s]+)", text)
    if m:
        return urllib.parse.unquote(m.group(1))
    if re.fullmatch(r"[A-Za-z0-9._~%@+/=-]{20,}", text):
        return urllib.parse.unquote(text)      # the code on its own
    parsed = urllib.parse.urlparse(text if "://" in text else "https://" + text)
    params = sorted(urllib.parse.parse_qs(parsed.query))
    seen = f"an address on {parsed.netloc or '?'}" + (f" with {', '.join(params)}" if params else " with no ?code=")
    hint = (" That's a Schwab page, so Schwab didn't redirect: check the app is 'Ready for use' and its callback "
            "URL is exactly https://127.0.0.1, then log in again."
            if "schwab" in (parsed.netloc or "") else
            " Copy the address only after the browser lands on https://127.0.0.1/?code=… (the page itself won't load).")
    raise ProviderError(f"No login code found — got {seen}.{hint}")


class SchwabAuth:
    """OAuth tokens for the Schwab Trader API, kept in a local JSON file."""

    def __init__(self, app_key: str, app_secret: str, callback_url: str, token_path: str | Path):
        self.app_key, self.app_secret, self.callback_url = app_key, app_secret, callback_url
        self.token_path = Path(token_path).expanduser()

    # -- login -------------------------------------------------------------
    def authorize_url(self) -> str:
        q = urllib.parse.urlencode({"client_id": self.app_key, "redirect_uri": self.callback_url})
        return f"{SCHWAB_BASE}/v1/oauth/authorize?{q}"

    def finish_login(self, redirected_url: str) -> None:
        """Exchange the ``code`` in the URL Schwab redirected the browser to."""
        self._token_request({"grant_type": "authorization_code", "code": extract_code(redirected_url),
                             "redirect_uri": self.callback_url})

    # -- tokens --------------------------------------------------------------
    def _load(self) -> dict:
        if not self.token_path.exists():
            return {}
        return json.loads(self.token_path.read_text(encoding="utf-8"))

    def _save(self, tok: dict) -> None:
        self.token_path.parent.mkdir(parents=True, exist_ok=True)
        self.token_path.write_text(json.dumps(tok, indent=2), encoding="utf-8")
        try:
            os.chmod(self.token_path, 0o600)
        except OSError:
            pass

    def _token_request(self, form: dict) -> dict:
        basic = base64.b64encode(f"{self.app_key}:{self.app_secret}".encode()).decode()
        req = urllib.request.Request(
            f"{SCHWAB_BASE}/v1/oauth/token", data=urllib.parse.urlencode(form).encode(),
            headers={"Authorization": f"Basic {basic}",
                     "Content-Type": "application/x-www-form-urlencoded"})
        tok = _http_json(req)
        now = time.time()
        old = self._load()
        tok["access_expires_at"] = now + float(tok.get("expires_in", 1800)) - 60
        if form["grant_type"] == "authorization_code":
            tok["refresh_expires_at"] = now + 7 * 86400 - 3600
        else:
            tok.setdefault("refresh_token", old.get("refresh_token"))
            tok["refresh_expires_at"] = old.get("refresh_expires_at", 0)
        self._save(tok)
        return tok

    def status(self) -> dict:
        tok = self._load()
        if not tok:
            return {"logged_in": False, "message": "not connected"}
        left = tok.get("refresh_expires_at", 0) - time.time()
        if left <= 0:
            return {"logged_in": False, "message": "login expired — reconnect (Schwab requires this weekly)"}
        return {"logged_in": True, "message": f"connected; weekly re-login due in {left / 86400:.1f} days"}

    def access_token(self) -> str:
        tok = self._load()
        if not tok:
            raise ProviderError("Schwab is not connected — use 'Connect Schwab' first")
        if time.time() >= tok.get("refresh_expires_at", 0):
            raise ProviderError("Schwab login expired (7-day limit) — reconnect")
        if time.time() >= tok.get("access_expires_at", 0):
            tok = self._token_request({"grant_type": "refresh_token", "refresh_token": tok["refresh_token"]})
        return tok["access_token"]


class SchwabProvider:
    name = "schwab"

    def __init__(self, auth: SchwabAuth, opener=None):
        self.auth = auth
        self.opener = opener or _http_json

    def _get(self, path: str, params: dict) -> dict:
        url = f"{SCHWAB_BASE}{path}?{urllib.parse.urlencode(params)}"
        req = urllib.request.Request(url, headers={"Authorization": f"Bearer {self.auth.access_token()}",
                                                   "Accept": "application/json"})
        return self.opener(req)

    def history(self, symbol: str, years: int = 2) -> PriceHistory:
        data = self._get("/marketdata/v1/pricehistory", {
            "symbol": symbol, "periodType": "year", "period": years,
            "frequencyType": "daily", "frequency": 1})
        return parse_schwab_history(symbol, data)

    def chain(self, symbol: str, as_of: date, settings: dict) -> ChainSnapshot:
        w = _exp_window(settings)
        data = self._get("/marketdata/v1/chains", {
            "symbol": symbol, "contractType": "ALL", "includeUnderlyingQuote": "true",
            "fromDate": (as_of + timedelta(days=w["min_dte"])).isoformat(),
            "toDate": (as_of + timedelta(days=w["max_dte"])).isoformat()})
        snap = parse_schwab_chain(symbol, data)
        band = settings.get("strike_band", 0.15)
        exps = monthly_expirations([l.expiration for l in snap.legs], as_of, **w) \
            if not settings.get("include_weeklies", False) else None
        snap.legs = [l for l in snap.legs
                     if abs(l.strike - snap.spot) / snap.spot <= band and (exps is None or l.expiration in exps)]
        return snap


def parse_schwab_history(symbol: str, data: dict) -> PriceHistory:
    if data.get("empty") or not data.get("candles"):
        raise ProviderError(f"Schwab returned no price history for {symbol}")
    h = PriceHistory(symbol)
    for c in sorted(data["candles"], key=lambda c: c["datetime"]):
        # daily candles are stamped at local midnight (US/Central) in epoch ms;
        # shifting 12h keeps the session date whether CST or CDT applies
        d = datetime.fromtimestamp(c["datetime"] / 1000 + 12 * 3600, tz=timezone.utc).date()
        h.dates.append(d.isoformat())
        h.opens.append(float(c["open"]))
        h.highs.append(float(c["high"]))
        h.lows.append(float(c["low"]))
        h.closes.append(float(c["close"]))
        h.volumes.append(float(c.get("volume", 0)))
    return h


def parse_schwab_chain(symbol: str, data: dict) -> ChainSnapshot:
    if data.get("status") == "FAILED":
        raise ProviderError(f"Schwab returned no option chain for {symbol}")
    spot = data.get("underlyingPrice") or (data.get("underlying") or {}).get("last")
    if not spot:
        raise ProviderError(f"no underlying price in Schwab chain for {symbol}")
    legs: list[Leg] = []
    for key, typ in (("callExpDateMap", "call"), ("putExpDateMap", "put")):
        for exp_key, strikes in (data.get(key) or {}).items():
            exp = exp_key.split(":")[0]
            for contracts in strikes.values():
                for c in contracts:
                    vol = c.get("volatility")
                    iv = vol / 100.0 if isinstance(vol, (int, float)) and vol > 0 else None
                    legs.append(Leg("buy", typ, 1, strike=float(c["strikePrice"]), expiration=exp,
                                    bid=_f(c.get("bid")), ask=_f(c.get("ask")), iv=iv,
                                    open_interest=_i(c.get("openInterest")),
                                    volume=_i(c.get("totalVolume"))))
    return ChainSnapshot(symbol, float(spot), legs)


def _f(x):
    return float(x) if isinstance(x, (int, float)) and x >= 0 else None


def _i(x):
    return int(x) if isinstance(x, (int, float)) and x >= 0 else None


def _http_json(req: urllib.request.Request) -> dict:
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace")[:300]
        raise ProviderError(f"HTTP {e.code} from {req.full_url.split('?')[0]}: {body}") from None
    except urllib.error.URLError as e:
        raise ProviderError(f"cannot reach {req.full_url.split('?')[0]}: {e.reason}") from None


def make_provider(config: dict):
    p = config.get("provider", "csv")
    if p == "schwab":
        s = config.get("schwab", {})
        missing = [k for k in ("app_key", "app_secret", "callback_url") if not s.get(k)]
        if missing:
            raise ProviderError(f"schwab settings missing: {', '.join(missing)}")
        auth = SchwabAuth(s["app_key"], s["app_secret"], s["callback_url"],
                          s.get("token_path", "~/.optiontool/schwab_token.json"))
        return SchwabProvider(auth)
    return CsvFolderProvider(config.get("csv_folder", "data/inbox"), config.get("fetch_history", True))
