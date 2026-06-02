"""
Macro context — live rates and benchmarks.

All values are pulled from Yahoo Finance via yfinance using their standard
index/futures tickers. No mock data; if yfinance returns nothing for a symbol,
that field comes back as None and the UI shows "n/a".
"""
from __future__ import annotations
from typing import Optional


MACRO_TICKERS = {
    "us10y":   {"symbol": "^TNX", "label": "US 10-Year Treasury Yield", "unit": "%", "scale": 1.0},
    "us2y":    {"symbol": "^IRX", "label": "13-Week T-Bill (short-rate proxy)", "unit": "%", "scale": 1.0},
    "us30y":   {"symbol": "^TYX", "label": "US 30-Year Treasury Yield", "unit": "%", "scale": 1.0},
    "vix":     {"symbol": "^VIX", "label": "VIX Volatility Index", "unit": "", "scale": 1.0},
    "dxy":     {"symbol": "DX-Y.NYB", "label": "US Dollar Index", "unit": "", "scale": 1.0},
    "gold":    {"symbol": "GC=F", "label": "Gold (front month)", "unit": "$/oz", "scale": 1.0},
    "oil":     {"symbol": "CL=F", "label": "WTI Crude (front month)", "unit": "$/bbl", "scale": 1.0},
    "spy":     {"symbol": "SPY", "label": "S&P 500 ETF", "unit": "", "scale": 1.0},
}


def _fetch_last(symbol: str) -> Optional[float]:
    try:
        import yfinance as yf
        hist = yf.Ticker(symbol).history(period="5d", auto_adjust=True)
        if hist is None or hist.empty:
            return None
        v = float(hist["Close"].iloc[-1])
        if v != v:
            return None
        return v
    except Exception:
        return None


def _fetch_change(symbol: str) -> Optional[float]:
    try:
        import yfinance as yf
        hist = yf.Ticker(symbol).history(period="10d", auto_adjust=True)
        if hist is None or len(hist) < 2:
            return None
        return float(hist["Close"].iloc[-1] - hist["Close"].iloc[-2])
    except Exception:
        return None


def get_macro_snapshot() -> dict:
    """Return current macro indicators."""
    out = {}
    for key, meta in MACRO_TICKERS.items():
        last = _fetch_last(meta["symbol"])
        change = _fetch_change(meta["symbol"])
        out[key] = {
            "label": meta["label"],
            "symbol": meta["symbol"],
            "value": last,
            "unit": meta["unit"],
            "change_vs_prev": change,
        }
    return out


def macro_context_for_stock(data: dict) -> dict:
    """
    Compare a stock's key yields to current macro rates so the value lens
    becomes regime-aware (e.g., FCF yield 5% vs 10Y at 4.5% = 50bps premium).
    """
    snap = get_macro_snapshot()
    us10y = snap.get("us10y", {}).get("value")

    fcf_yield = data.get("fcfYield")
    div_yield = data.get("divYield")
    earnings_yield = None
    pe = data.get("pe")
    if pe and pe > 0:
        earnings_yield = 100.0 / pe

    rows = []
    if fcf_yield is not None and us10y is not None:
        rows.append({
            "name": "FCF Yield Spread",
            "stock_value": fcf_yield,
            "benchmark_value": us10y,
            "spread_bps": int(round((fcf_yield - us10y) * 100)),
            "interpretation": (
                "Owner-earnings yield premium over the risk-free rate. "
                "Negative or low premium means you're paid little to take equity risk."
            ),
        })
    if div_yield is not None and us10y is not None:
        rows.append({
            "name": "Dividend Yield Spread",
            "stock_value": div_yield,
            "benchmark_value": us10y,
            "spread_bps": int(round((div_yield - us10y) * 100)),
            "interpretation": (
                "Income premium over the 10-year. Below zero, the bond market pays "
                "you more for less risk."
            ),
        })
    if earnings_yield is not None and us10y is not None:
        rows.append({
            "name": "Earnings Yield Spread",
            "stock_value": round(earnings_yield, 2),
            "benchmark_value": us10y,
            "spread_bps": int(round((earnings_yield - us10y) * 100)),
            "interpretation": "E/P versus the 10-year — Buffett's classic relative value check.",
        })

    return {"snapshot": snap, "comparisons": rows}
