"""
Earnings calendar and dividend events.

Uses yfinance only. Returns None for fields that aren't available rather than
fabricating a date.
"""
from __future__ import annotations
from datetime import datetime
from typing import Optional


def _to_iso(v) -> Optional[str]:
    if v is None:
        return None
    try:
        # yfinance returns various date-like objects depending on version
        if hasattr(v, "strftime"):
            return v.strftime("%Y-%m-%d")
        if isinstance(v, (int, float)):
            # Unix seconds
            return datetime.fromtimestamp(int(v)).strftime("%Y-%m-%d")
        s = str(v)
        return s[:10] if len(s) >= 10 else s
    except Exception:
        return None


def _days_until(iso_date: Optional[str]) -> Optional[int]:
    if not iso_date:
        return None
    try:
        d = datetime.strptime(iso_date, "%Y-%m-%d")
        return (d.date() - datetime.now().date()).days
    except Exception:
        return None


def get_events(ticker: str) -> dict:
    """Return upcoming earnings, ex-dividend, and dividend pay dates."""
    out = {
        "ticker": ticker.upper(),
        "next_earnings": None,
        "earnings_days_away": None,
        "ex_dividend_date": None,
        "ex_dividend_days_away": None,
        "dividend_payment_date": None,
        "dividend_payment_days_away": None,
        "last_earnings": None,
        "fetched_at": datetime.now().isoformat(),
        "source": "Yahoo Finance via yfinance",
    }
    try:
        import yfinance as yf
        t = yf.Ticker(ticker.upper())

        # `calendar` returns a dict in newer versions, DataFrame in older
        cal = None
        try:
            cal = t.calendar
        except Exception:
            cal = None

        if isinstance(cal, dict):
            ed = cal.get("Earnings Date") or cal.get("earnings_date")
            if isinstance(ed, list) and ed:
                out["next_earnings"] = _to_iso(ed[0])
            elif ed:
                out["next_earnings"] = _to_iso(ed)
            out["ex_dividend_date"] = _to_iso(cal.get("Ex-Dividend Date") or cal.get("exDividendDate"))
            out["dividend_payment_date"] = _to_iso(cal.get("Dividend Date") or cal.get("dividendDate"))
        elif cal is not None and hasattr(cal, "T"):
            # Older DataFrame format
            try:
                tcal = cal.T
                if "Earnings Date" in tcal.columns:
                    out["next_earnings"] = _to_iso(tcal["Earnings Date"].iloc[0])
            except Exception:
                pass

        # Fall back to info dict for ex-div / payment dates
        try:
            info = t.info or {}
            if not out["ex_dividend_date"]:
                out["ex_dividend_date"] = _to_iso(info.get("exDividendDate"))
            if not out["dividend_payment_date"]:
                out["dividend_payment_date"] = _to_iso(info.get("dividendDate"))
        except Exception:
            pass

        # Last earnings (from earnings_history if available)
        try:
            eh = t.earnings_dates
            if eh is not None and not eh.empty:
                past = eh[eh.index < datetime.now()]
                if not past.empty:
                    out["last_earnings"] = past.index[-1].strftime("%Y-%m-%d")
        except Exception:
            pass

        out["earnings_days_away"] = _days_until(out["next_earnings"])
        out["ex_dividend_days_away"] = _days_until(out["ex_dividend_date"])
        out["dividend_payment_days_away"] = _days_until(out["dividend_payment_date"])
    except Exception as exc:
        out["error"] = str(exc)
    return out
