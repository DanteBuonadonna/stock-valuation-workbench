"""
Data fetcher — pulls fundamentals for any ticker.

Primary source: Yahoo Finance via the `yfinance` Python library.
This is the same data feed wrapping the official Yahoo Finance API, much more
reliable and consistent than scraping search results.

If yfinance is unavailable, this module can also be fed a pre-built dict.
"""
from __future__ import annotations
import math
import os
import time
import random
from datetime import datetime, timedelta
from typing import Optional


# Use a realistic browser User-Agent. Yahoo is aggressive about blocking
# the default Python/requests UA and cloud datacenter IPs.
_BROWSER_UAS = [
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Safari/605.1.15",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36",
]


def _get_session():
    """Build a requests session with realistic browser headers."""
    try:
        import requests
        s = requests.Session()
        s.headers.update({
            "User-Agent": random.choice(_BROWSER_UAS),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
            "Accept-Encoding": "gzip, deflate, br",
            "Connection": "keep-alive",
            "Upgrade-Insecure-Requests": "1",
        })
        return s
    except ImportError:
        return None


def safe_get(d, key, default=None):
    v = d.get(key) if d else None
    if v is None:
        return default
    try:
        if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
            return default
    except Exception:
        pass
    return v


def pct(v: Optional[float]) -> Optional[float]:
    """Yahoo returns fractions for ratios (e.g. 0.025 for 2.5%). Convert to percent."""
    if v is None:
        return None
    try:
        return float(v) * 100.0
    except (TypeError, ValueError):
        return None


def fetch_ticker_data(ticker: str) -> dict:
    """
    Returns a normalized dict ready for the scoring engine.

    Routing:
      - If FMP_API_KEY env var is set → use Financial Modeling Prep (works from cloud)
      - Otherwise → use yfinance + Yahoo Finance (works locally, often blocked on cloud)
    """
    # Try Financial Modeling Prep first if key is configured (cloud deployment path)
    if os.environ.get("FMP_API_KEY", "").strip():
        try:
            from fmp_fetcher import fetch_ticker_data_fmp
            return fetch_ticker_data_fmp(ticker)
        except Exception as exc:
            # If FMP fails (e.g., quota exhausted), fall through to yfinance
            print(f"FMP fetch failed for {ticker}: {exc} — falling back to yfinance")

    try:
        import yfinance as yf
    except ImportError as e:
        raise RuntimeError(
            "yfinance not installed. Run: python3 -m pip install --user yfinance"
        ) from e

    # Use a browser-like session and retry on transient rate-limits
    session = _get_session()
    t = yf.Ticker(ticker.upper(), session=session) if session else yf.Ticker(ticker.upper())

    # info dict contains most fundamentals
    info = {}
    last_err = None
    for attempt in range(3):
        try:
            info = t.info or {}
            if info and safe_get(info, "symbol"):
                break
        except Exception as e:
            last_err = e
            msg = str(e).lower()
            if "rate limit" in msg or "too many" in msg or "429" in msg:
                # Wait with jittered backoff before retrying
                time.sleep(1.5 + attempt * 1.5 + random.random())
                # Rotate User-Agent on the session
                if session:
                    session.headers["User-Agent"] = random.choice(_BROWSER_UAS)
                continue
            raise RuntimeError(f"Could not fetch data for {ticker}: {e}")
    if not info or not safe_get(info, "symbol"):
        if last_err and ("rate" in str(last_err).lower() or "too many" in str(last_err).lower()):
            raise RuntimeError(
                f"Yahoo Finance is rate-limiting this cloud server's IP. "
                f"This is a known limitation of yfinance from cloud hosts. "
                f"The fix is to use a paid data API designed for server-side calls "
                f"(e.g. Financial Modeling Prep, $0 free tier with 250 calls/day). "
                f"Underlying error: {last_err}"
            )
        raise RuntimeError(f"No data found for ticker '{ticker}' — check the symbol.")

    if not info or not safe_get(info, "symbol"):
        raise RuntimeError(f"No data found for ticker '{ticker}' — check the symbol.")

    # Derived metrics
    market_cap = safe_get(info, "marketCap")
    free_cf = safe_get(info, "freeCashflow")
    fcf_yield = None
    if free_cf and market_cap and market_cap > 0:
        fcf_yield = (free_cf / market_cap) * 100.0

    # Dividend coverage = FCF / total dividends paid
    div_rate = safe_get(info, "dividendRate")  # annual dollar dividend per share
    price = safe_get(info, "currentPrice") or safe_get(info, "regularMarketPrice") or safe_get(info, "previousClose")
    shares_out = safe_get(info, "sharesOutstanding")
    div_yield = None
    if div_rate and price and price > 0:
        div_yield = (div_rate / price) * 100.0
    else:
        raw_div_yield = safe_get(info, "dividendYield")
        if raw_div_yield is not None:
            raw_div_yield = float(raw_div_yield)
            # yfinance/Yahoo can return either 0.0047 or 0.47 depending on payload.
            div_yield = raw_div_yield if raw_div_yield > 0.20 else raw_div_yield * 100.0
    fcf_cov = None
    total_div_paid = None
    if div_rate and shares_out:
        total_div_paid = div_rate * shares_out
        if free_cf and total_div_paid > 0:
            fcf_cov = free_cf / total_div_paid

    # 5Y dividend CAGR — yfinance doesn't ship this directly; try to compute from history
    div_cagr_5y = None
    years_inc = None
    try:
        divs = t.dividends  # pandas Series indexed by date
        if divs is not None and len(divs) > 0:
            # Annualize: sum per calendar year, compute CAGR over last 5 full years
            annual = divs.groupby(divs.index.year).sum()
            now_year = datetime.now().year
            full_years = annual[annual.index < now_year]  # exclude in-progress year
            if len(full_years) >= 6:
                start = full_years.iloc[-6]
                end = full_years.iloc[-1]
                if start > 0:
                    div_cagr_5y = ((end / start) ** (1 / 5) - 1) * 100.0
            # Years of consecutive increases (annual sums)
            yrs = 0
            for i in range(len(full_years) - 1, 0, -1):
                if full_years.iloc[i] > full_years.iloc[i - 1]:
                    yrs += 1
                else:
                    break
            years_inc = yrs
    except Exception:
        pass

    # PEG: yfinance offers trailingPegRatio in some payloads, otherwise compute
    peg = safe_get(info, "trailingPegRatio") or safe_get(info, "pegRatio")
    fwd_pe = safe_get(info, "forwardPE")
    eps_growth = pct(safe_get(info, "earningsQuarterlyGrowth")) or pct(safe_get(info, "earningsGrowth"))
    if not peg and fwd_pe and eps_growth and eps_growth > 0:
        peg = fwd_pe / eps_growth

    # Build the normalized dict the scoring engine expects
    data = {
        "ticker": (safe_get(info, "symbol") or ticker).upper(),
        "name": safe_get(info, "longName") or safe_get(info, "shortName") or ticker.upper(),
        "sector": safe_get(info, "sector") or "—",
        "industry": safe_get(info, "industry") or "",
        "asOf": datetime.now().strftime("%Y-%m-%d %H:%M"),

        "price": price,
        "marketCap": market_cap,
        "freeCashflow": free_cf,
        "sharesOutstanding": shares_out,
        "totalDebt": safe_get(info, "totalDebt"),
        "totalCash": safe_get(info, "totalCash"),
        "eps": safe_get(info, "trailingEps") or safe_get(info, "epsTrailingTwelveMonths"),
        "bvps": safe_get(info, "bookValue"),
        "annualDiv": div_rate or 0,
        "beta": safe_get(info, "beta"),

        # GROWTH MODEL inputs
        "revGrowth": pct(safe_get(info, "revenueGrowth")),  # current revenue growth as %, used as proxy
        "epsGrowth": eps_growth,
        "fwdPE": fwd_pe,
        "roe": pct(safe_get(info, "returnOnEquity")),
        "peg": peg,

        # DIVIDEND MODEL inputs
        "divYield": div_yield,
        "payout": pct(safe_get(info, "payoutRatio")),
        "divCagr": div_cagr_5y,
        "yearsInc": years_inc,
        "fcfCov": fcf_cov,

        # VALUE+GROWTH MODEL inputs
        "pe": safe_get(info, "trailingPE"),
        # Do not fake long-term growth. Yahoo/yfinance often does not expose a
        # clean long-term consensus growth field, so leave it blank unless a
        # vetted data provider is added.
        "ltGrowth": None,
        "fcfYield": fcf_yield,
        "pegVal": peg,
        "debtEq": (safe_get(info, "debtToEquity") or 0) / 100.0 if safe_get(info, "debtToEquity") else None,
        # ^ yfinance reports debt/equity as a percentage (e.g. 103 means 1.03×). Normalize.

        "notes": None,
        "sources": [
            f"https://finance.yahoo.com/quote/{ticker.upper()}/key-statistics/",
            f"https://finance.yahoo.com/quote/{ticker.upper()}/financials/",
        ],
        "dataQuality": {
            "source": "Yahoo Finance via yfinance",
            "realFetchedData": True,
            "missingOrUnavailable": [
                "Long-term consensus growth rate is not available from the current free Yahoo/yfinance feed."
            ],
            "notes": [
                "Revenue growth and EPS growth are Yahoo-reported current growth fields, not a manually modeled five-year CAGR.",
                "Peer groups are selected by local industry mapping; peer metrics themselves are fetched live."
            ],
        },
    }

    return data


def fetch_price_history(ticker: str, period: str = "5y"):
    """Return a pandas Series of daily closing prices, indexed by date."""
    # Try FMP first if key configured
    if os.environ.get("FMP_API_KEY", "").strip():
        try:
            from fmp_fetcher import fetch_price_history_fmp
            series = fetch_price_history_fmp(ticker, period)
            if series is not None and len(series) > 0:
                return series
        except Exception:
            pass
    try:
        import yfinance as yf
        t = yf.Ticker(ticker.upper())
        hist = t.history(period=period, auto_adjust=True)
        if hist is not None and not hist.empty:
            return hist["Close"]
    except Exception:
        pass

    # Fallback: Yahoo's chart endpoint does not require the quoteSummary cookie
    # flow that sometimes breaks yfinance.history().
    try:
        import pandas as pd
        import requests
        seconds = {
            "3mo": 90 * 86400,
            "6mo": 180 * 86400,
            "1y": 365 * 86400,
            "2y": 2 * 365 * 86400,
            "5y": 5 * 365 * 86400,
        }.get(period, 5 * 365 * 86400)
        end = int(datetime.now().timestamp())
        start = end - seconds
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker.upper()}"
        params = {
            "period1": start,
            "period2": end,
            "interval": "1d",
            "events": "history",
            "includeAdjustedClose": "true",
        }
        res = requests.get(url, params=params, timeout=12, headers={"User-Agent": "Mozilla/5.0"})
        res.raise_for_status()
        payload = res.json()
        result = (payload.get("chart", {}).get("result") or [None])[0]
        if not result:
            return None
        timestamps = result.get("timestamp") or []
        indicators = result.get("indicators", {})
        adj = (indicators.get("adjclose") or [{}])[0].get("adjclose")
        close = adj or (indicators.get("quote") or [{}])[0].get("close")
        if not timestamps or not close:
            return None
        rows = [
            (datetime.fromtimestamp(ts), val)
            for ts, val in zip(timestamps, close)
            if val is not None
        ]
        if not rows:
            return None
        idx = [r[0] for r in rows]
        vals = [r[1] for r in rows]
        return pd.Series(vals, index=pd.to_datetime(idx), name="Close")
    except Exception:
        return None
