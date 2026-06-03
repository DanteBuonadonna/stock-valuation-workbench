"""
Financial Modeling Prep (FMP) data fetcher.

Used when the FMP_API_KEY environment variable is set (typically on
cloud deployments where Yahoo Finance is rate-limited). Falls back
to yfinance via data_fetcher.py when FMP key is absent.

Endpoints used (all free tier):
  /quote/{ticker}              — price, market cap, P/E, EPS, shares outstanding
  /profile/{ticker}            — name, sector, industry, beta, annual dividend
  /ratios-ttm/{ticker}         — ROE, debt/equity, payout, dividend yield, PEG
  /key-metrics-ttm/{ticker}    — FCF yield, book value per share, FCF/share
  /financial-growth/{ticker}   — revenue/EPS growth (last 5 years)
  /historical-price-full/stock_dividend/{ticker} — dividend history for CAGR + years of increases
  /analyst-estimates/{ticker}  — forward EPS estimates (for forward P/E & LT growth)
"""
from __future__ import annotations
import json
import math
import os
import urllib.request
import urllib.parse
from datetime import datetime
from typing import Any, Optional

FMP_BASE = "https://financialmodelingprep.com/stable"


def _has_fmp_key() -> bool:
    return bool(os.environ.get("FMP_API_KEY", "").strip())


def _fmp_get(endpoint: str, params: Optional[dict] = None) -> Any:
    """
    Call FMP's new `/stable/` API. The endpoint argument is just the path
    segment, e.g. 'quote' or 'ratios-ttm'. The ticker should be passed in
    `params` as `symbol`. We DO NOT silently swallow errors anymore —
    the caller needs to see what FMP says.
    """
    key = os.environ.get("FMP_API_KEY", "").strip()
    if not key:
        return None
    q = dict(params or {})
    q["apikey"] = key
    url = f"{FMP_BASE}/{endpoint}?{urllib.parse.urlencode(q)}"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "stock-evaluator/1.0"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            raw = resp.read().decode("utf-8")
        try:
            data = json.loads(raw)
        except Exception:
            return None
        if isinstance(data, dict) and "Error Message" in data:
            # Surface the FMP error so the caller (and the user) can see it
            raise RuntimeError(f"FMP {endpoint}: {data['Error Message']}")
        return data
    except RuntimeError:
        raise  # bubble up FMP error messages
    except Exception as exc:
        # Network / parsing failure — log and return None
        print(f"[fmp_fetcher] {endpoint} request failed: {exc}")
        return None


def _first(arr):
    if isinstance(arr, list) and arr:
        return arr[0]
    return {}


def _safe(v):
    if v is None:
        return None
    try:
        f = float(v)
        if math.isnan(f) or math.isinf(f):
            return None
        return f
    except (TypeError, ValueError):
        return v


def _pct(v):
    """FMP returns decimals (0.025) for ratios; convert to percent for our scoring engine."""
    if v is None:
        return None
    try:
        return float(v) * 100.0
    except (TypeError, ValueError):
        return None


def _compute_div_history(ticker: str) -> tuple:
    """Returns (5y CAGR %, years of consecutive increases)."""
    raw = _fmp_get("dividends", {"symbol": ticker})
    if not raw:
        return None, None
    # The new endpoint returns a list directly, not a dict with "historical"
    if isinstance(raw, list):
        hist = raw
    elif isinstance(raw, dict):
        hist = raw.get("historical") or []
    else:
        return None, None
    if not hist:
        return None, None
    # Bucket by year, summing all dividend payments in each year
    by_year = {}
    for entry in hist:
        date = entry.get("date") or ""
        if len(date) < 4:
            continue
        try:
            year = int(date[:4])
        except ValueError:
            continue
        amt = _safe(entry.get("dividend") or entry.get("adjDividend"))
        if amt is None:
            continue
        by_year[year] = by_year.get(year, 0.0) + amt

    now_year = datetime.now().year
    full_years = sorted([y for y in by_year if y < now_year])
    if len(full_years) < 2:
        return None, None

    # 5Y CAGR
    cagr = None
    if len(full_years) >= 6:
        start = by_year[full_years[-6]]
        end = by_year[full_years[-1]]
        if start > 0:
            cagr = ((end / start) ** (1 / 5) - 1) * 100.0

    # Consecutive years of increase, counting back from most recent full year
    years_inc = 0
    for i in range(len(full_years) - 1, 0, -1):
        if by_year[full_years[i]] > by_year[full_years[i - 1]]:
            years_inc += 1
        else:
            break

    return cagr, years_inc


def _compute_growth(ticker: str) -> tuple:
    """Returns (revenue growth TTM %, EPS growth TTM %)."""
    raw = _fmp_get("financial-growth", {"symbol": ticker, "limit": 5, "period": "annual"})
    if not raw or not isinstance(raw, list) or not raw:
        return None, None
    latest = raw[0]
    rev = _safe(latest.get("revenueGrowth"))
    eps = _safe(latest.get("epsgrowth")) or _safe(latest.get("epsGrowth"))
    return (rev * 100.0 if rev is not None else None,
            eps * 100.0 if eps is not None else None)


def _compute_forward_pe_and_lt(ticker: str, price: Optional[float]) -> tuple:
    """Returns (forward P/E, LT growth %)."""
    raw = _fmp_get("analyst-estimates", {"symbol": ticker, "limit": 4, "period": "annual"})
    if not raw or not isinstance(raw, list) or not raw or not price:
        return None, None
    # Use the next fiscal year's average EPS estimate
    nxt = sorted(raw, key=lambda x: x.get("date", ""))[0]
    fwd_eps = _safe(nxt.get("estimatedEpsAvg"))
    fwd_pe = (price / fwd_eps) if (fwd_eps and fwd_eps > 0) else None
    # LT growth: rough estimate from year-over-year EPS estimate growth
    by_date = sorted(raw, key=lambda x: x.get("date", ""))
    lt_growth = None
    if len(by_date) >= 2:
        first_eps = _safe(by_date[0].get("estimatedEpsAvg"))
        last_eps = _safe(by_date[-1].get("estimatedEpsAvg"))
        if first_eps and last_eps and first_eps > 0:
            years = len(by_date) - 1
            lt_growth = (((last_eps / first_eps) ** (1 / years)) - 1) * 100.0 if years > 0 else None
    return fwd_pe, lt_growth


def fetch_ticker_data_fmp(ticker: str) -> dict:
    """Pull a normalized dict using the Financial Modeling Prep API."""
    ticker = (ticker or "").upper().strip()
    if not ticker:
        raise RuntimeError("Empty ticker.")

    quote_raw = _fmp_get("quote", {"symbol": ticker})
    profile_raw = _fmp_get("profile", {"symbol": ticker})
    if not quote_raw or not profile_raw:
        raise RuntimeError(
            f"FMP returned no data for {ticker}. "
            f"Verify the ticker is a US-listed equity (FMP free tier is US-only) "
            f"and your FMP_API_KEY is valid."
        )

    q = _first(quote_raw)
    p = _first(profile_raw)
    ratios = _first(_fmp_get("ratios-ttm", {"symbol": ticker}) or [])
    metrics = _first(_fmp_get("key-metrics-ttm", {"symbol": ticker}) or [])

    price = _safe(q.get("price"))
    market_cap = _safe(q.get("marketCap"))
    shares_out = _safe(q.get("sharesOutstanding"))
    eps = _safe(q.get("eps"))

    annual_div = _safe(p.get("lastDiv")) or 0
    beta = _safe(p.get("beta"))

    # Dividend yield: try multiple FMP fields, all decimals
    div_yield = (_pct(ratios.get("dividendYielTTM"))
                  or _pct(ratios.get("dividendYieldTTM"))
                  or _pct(metrics.get("dividendYieldTTM")))
    # Fallback: compute from price + lastDiv
    if div_yield is None and annual_div and price and price > 0:
        div_yield = (annual_div / price) * 100.0

    payout = _pct(ratios.get("payoutRatioTTM")) or _pct(metrics.get("payoutRatioTTM"))

    pe = _safe(q.get("pe")) or _safe(ratios.get("priceEarningsRatioTTM"))
    peg = _safe(ratios.get("priceEarningsToGrowthRatioTTM"))
    roe = _pct(ratios.get("returnOnEquityTTM"))
    debt_eq = _safe(ratios.get("debtEquityRatioTTM")) or _safe(metrics.get("debtToEquityTTM"))
    fcf_yield = _pct(metrics.get("freeCashFlowYieldTTM"))

    bvps = _safe(metrics.get("bookValuePerShareTTM"))
    fcf_per_share = _safe(metrics.get("freeCashFlowPerShareTTM"))
    free_cf = (fcf_per_share * shares_out) if (fcf_per_share and shares_out) else None

    # Dividend coverage
    fcf_cov = None
    if free_cf and annual_div and shares_out:
        total_div_paid = annual_div * shares_out
        if total_div_paid > 0:
            fcf_cov = free_cf / total_div_paid

    # Growth (needs separate endpoint call)
    rev_growth, eps_growth = _compute_growth(ticker)

    # Forward P/E and LT growth from analyst estimates
    fwd_pe, lt_growth = _compute_forward_pe_and_lt(ticker, price)

    # Dividend history
    div_cagr, years_inc = _compute_div_history(ticker)

    data = {
        "ticker": q.get("symbol", ticker),
        "name": p.get("companyName") or q.get("name") or ticker,
        "sector": p.get("sector") or "—",
        "industry": p.get("industry") or "",
        "asOf": datetime.now().strftime("%Y-%m-%d %H:%M"),

        "price": price,
        "marketCap": market_cap,
        "freeCashflow": free_cf,
        "sharesOutstanding": shares_out,
        "totalDebt": None,  # available via /balance-sheet-statement (extra call, skipped for free-tier budget)
        "totalCash": None,
        "eps": eps,
        "bvps": bvps,
        "annualDiv": annual_div,
        "beta": beta,

        # GROWTH MODEL
        "revGrowth": rev_growth,
        "epsGrowth": eps_growth,
        "fwdPE": fwd_pe,
        "roe": roe,
        "peg": peg,

        # DIVIDEND MODEL
        "divYield": div_yield,
        "payout": payout,
        "divCagr": div_cagr,
        "yearsInc": years_inc,
        "fcfCov": fcf_cov,

        # VALUE+GROWTH MODEL
        "pe": pe,
        "ltGrowth": lt_growth,
        "fcfYield": fcf_yield,
        "pegVal": peg,
        "debtEq": debt_eq,

        "notes": None,
        "sources": [
            f"https://financialmodelingprep.com/financial-statements/{ticker}",
            f"https://site.financialmodelingprep.com/company/{ticker}",
        ],
        "dataQuality": {
            "source": "Financial Modeling Prep API",
            "realFetchedData": True,
            "missingOrUnavailable": [],
            "notes": [
                "Data sourced from FMP. Growth fields are TTM where available.",
                "Forward P/E and long-term growth derived from FMP analyst estimates.",
            ],
        },
    }
    return data


def fetch_price_history_fmp(ticker: str, period: str = "5y"):
    """Return a pandas Series of daily closing prices, indexed by date."""
    try:
        import pandas as pd
        # New stable endpoint for end-of-day prices
        raw = _fmp_get("historical-price-eod/full", {"symbol": ticker.upper()})
        if not raw:
            return None
        # New API returns a list directly
        if isinstance(raw, list):
            hist = raw
        elif isinstance(raw, dict):
            hist = raw.get("historical") or []
        else:
            return None
        if not hist:
            return None
        # Sort ascending by date
        hist = sorted(hist, key=lambda x: x.get("date", ""))
        # Trim to requested period
        dates = [h["date"] for h in hist if "date" in h and "close" in h]
        closes = [h["close"] for h in hist if "date" in h and "close" in h]
        if not dates:
            return None
        series = pd.Series(closes, index=pd.to_datetime(dates), name="Close")
        # Trim by period suffix
        n_days = {"3mo": 90, "6mo": 180, "1y": 365, "2y": 730, "5y": 1825}.get(period, 1825)
        cutoff = (datetime.now() - pd.Timedelta(days=n_days))
        return series[series.index >= cutoff]
    except Exception:
        return None
