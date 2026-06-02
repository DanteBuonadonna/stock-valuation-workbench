"""
Portfolio fit check.

Given a candidate ticker (the one currently being evaluated) and a CSV of
existing client holdings, this module reports:

  * Correlation of the candidate to each holding (1Y daily log returns)
  * The "you basically already own this" flag if max correlation > 0.75
  * Current sector concentration of the portfolio
  * Projected sector concentration if the candidate is added at the suggested size
  * Suggested starter position size based on simple, transparent rules

CSV format expected (header row required):
    ticker,shares        # or
    ticker,weight        # weights either fractions (0.05) or percents (5)

Real data only — uses yfinance for price history and sector lookups.
"""
from __future__ import annotations
import io
import csv
import math
from typing import Optional


def _safe(v):
    try:
        v = float(v)
        if v != v or v in (float('inf'), float('-inf')):
            return None
        return v
    except Exception:
        return None


def parse_holdings_csv(text: str) -> list:
    """Return list of {ticker, value_input, value_type ('shares'|'weight')}."""
    text = (text or "").strip()
    if not text:
        return []
    reader = csv.DictReader(io.StringIO(text))
    out = []
    for row in reader:
        if not row:
            continue
        # Normalize keys to lowercase
        norm = {(k or "").strip().lower(): (v or "").strip() for k, v in row.items()}
        ticker = norm.get("ticker") or norm.get("symbol") or ""
        ticker = ticker.upper()
        if not ticker:
            continue
        shares = _safe(norm.get("shares"))
        weight = _safe(norm.get("weight")) or _safe(norm.get("pct")) or _safe(norm.get("percent"))
        value = _safe(norm.get("value")) or _safe(norm.get("market_value"))
        if shares is not None:
            out.append({"ticker": ticker, "shares": shares})
        elif weight is not None:
            # Accept either 5 or 0.05
            if weight > 1.5:
                weight = weight / 100.0
            out.append({"ticker": ticker, "weight": weight})
        elif value is not None:
            out.append({"ticker": ticker, "value": value})
        else:
            out.append({"ticker": ticker})
    return out


def _fetch_history(tickers: list, period: str = "1y"):
    """Return dict of ticker -> close-price pandas Series; sector dict; current price dict."""
    import yfinance as yf
    closes = {}
    sectors = {}
    prices = {}
    for t in tickers:
        try:
            tk = yf.Ticker(t)
            hist = tk.history(period=period, auto_adjust=True)
            if hist is not None and not hist.empty:
                closes[t] = hist["Close"]
                prices[t] = float(hist["Close"].iloc[-1])
            try:
                info = tk.info or {}
                sectors[t] = info.get("sector") or "—"
            except Exception:
                sectors[t] = "—"
        except Exception:
            closes[t] = None
            sectors[t] = "—"
    return closes, sectors, prices


def _correlations(candidate_close, holding_closes: dict) -> dict:
    """Pearson correlation of daily log returns. Returns ticker -> correlation."""
    import numpy as np
    if candidate_close is None or len(candidate_close) < 30:
        return {}
    out = {}
    cand_log = candidate_close.pct_change().dropna()
    for tkr, ser in holding_closes.items():
        if ser is None or len(ser) < 30:
            out[tkr] = None
            continue
        # Align on common dates
        joined = cand_log.to_frame("c").join(ser.pct_change().dropna().to_frame("h"), how="inner").dropna()
        if len(joined) < 20:
            out[tkr] = None
            continue
        c = joined["c"].values
        h = joined["h"].values
        if c.std() == 0 or h.std() == 0:
            out[tkr] = None
            continue
        out[tkr] = float(np.corrcoef(c, h)[0, 1])
    return out


def _compute_dollar_values(holdings: list, prices: dict, default_portfolio_value: float) -> tuple:
    """Compute per-holding dollar value, total portfolio value, and weight."""
    # If shares are given, value = shares * price
    # If weight is given, value = weight * default_portfolio_value
    # If value is given, use directly
    total = 0.0
    raw_values = []
    for h in holdings:
        if "shares" in h and prices.get(h["ticker"]):
            v = h["shares"] * prices[h["ticker"]]
        elif "weight" in h:
            v = h["weight"] * default_portfolio_value
        elif "value" in h:
            v = h["value"]
        else:
            v = 0.0
        raw_values.append(v)
        total += v
    weights = []
    for v in raw_values:
        weights.append((v / total) if total > 0 else 0.0)
    return raw_values, weights, total


def fit_check(candidate_data: dict, candidate_aggregate: int, holdings_csv: str,
              portfolio_value: Optional[float] = None) -> dict:
    """
    Run the full fit check.

    candidate_data: the dict returned by data_fetcher for the candidate ticker
    candidate_aggregate: aggregate score (0-30) for the candidate
    holdings_csv: raw CSV text uploaded by the user
    portfolio_value: optional explicit total portfolio value (only used when weights given)
    """
    holdings = parse_holdings_csv(holdings_csv)
    if not holdings:
        return {"ok": False, "error": "No valid rows parsed from CSV."}

    cand_ticker = (candidate_data.get("ticker") or "").upper()
    cand_sector = candidate_data.get("sector") or "—"

    tickers = [h["ticker"] for h in holdings]
    if cand_ticker:
        tickers.append(cand_ticker)

    closes, sectors, prices = _fetch_history(tickers)

    pv = portfolio_value or 100000.0
    dollar_values, weights, total_pv = _compute_dollar_values(holdings, prices, pv)
    if total_pv > 0:
        pv = total_pv

    # Correlations
    cand_close = closes.get(cand_ticker)
    holding_closes = {h["ticker"]: closes.get(h["ticker"]) for h in holdings if h["ticker"] != cand_ticker}
    cors = _correlations(cand_close, holding_closes)

    # Build per-holding report
    holdings_report = []
    for h, dv, w in zip(holdings, dollar_values, weights):
        t = h["ticker"]
        holdings_report.append({
            "ticker": t,
            "sector": sectors.get(t, "—"),
            "value": round(dv, 2),
            "weight": round(w, 4),
            "correlation_to_candidate": cors.get(t),
        })

    # Most-correlated holding
    valid = [(t, c) for t, c in cors.items() if c is not None]
    if valid:
        most_corr = max(valid, key=lambda x: x[1])
        max_corr = most_corr[1]
        max_corr_ticker = most_corr[0]
    else:
        max_corr = None
        max_corr_ticker = None

    # Sector concentration (current)
    sector_totals = {}
    for h, dv in zip(holdings, dollar_values):
        sec = sectors.get(h["ticker"], "—") or "—"
        sector_totals[sec] = sector_totals.get(sec, 0.0) + dv
    sector_weights_current = {s: round(v / pv, 4) for s, v in sector_totals.items()} if pv > 0 else {}

    # Suggested starter size
    suggested = _suggest_size(candidate_aggregate, max_corr)
    target_dollar = pv * (suggested["weight"] / 100.0)

    # Projected sector concentration if added at suggested size
    new_pv = pv + target_dollar
    projected_sector_dollars = dict(sector_totals)
    projected_sector_dollars[cand_sector] = projected_sector_dollars.get(cand_sector, 0.0) + target_dollar
    sector_weights_projected = {s: round(v / new_pv, 4) for s, v in projected_sector_dollars.items()} if new_pv > 0 else {}

    # Cap warnings — common firm thresholds, surfaced as advisory only
    sector_caps = {
        "soft_cap_pct": 25,   # informational
        "hard_cap_pct": 35,
    }
    cap_warning = None
    new_cand_sector_pct = sector_weights_projected.get(cand_sector, 0) * 100
    if new_cand_sector_pct > sector_caps["hard_cap_pct"]:
        cap_warning = f"Adding {cand_ticker} pushes {cand_sector} to {new_cand_sector_pct:.1f}% — above the {sector_caps['hard_cap_pct']}% hard cap."
    elif new_cand_sector_pct > sector_caps["soft_cap_pct"]:
        cap_warning = f"Adding {cand_ticker} pushes {cand_sector} to {new_cand_sector_pct:.1f}% — above the {sector_caps['soft_cap_pct']}% soft cap."

    overlap_warning = None
    if max_corr is not None and max_corr >= 0.75:
        overlap_warning = (
            f"{cand_ticker} is highly correlated ({max_corr:.2f}) with {max_corr_ticker} — "
            f"you may already own most of this exposure."
        )

    return {
        "ok": True,
        "candidate": {
            "ticker": cand_ticker,
            "sector": cand_sector,
            "aggregate_score": candidate_aggregate,
        },
        "portfolio": {
            "total_value": round(pv, 2),
            "holdings": holdings_report,
        },
        "correlations": {
            "max": max_corr,
            "max_ticker": max_corr_ticker,
            "all": cors,
        },
        "sector_concentration": {
            "current_pct": {s: round(w * 100, 1) for s, w in sector_weights_current.items()},
            "projected_pct": {s: round(w * 100, 1) for s, w in sector_weights_projected.items()},
            "caps_used": sector_caps,
        },
        "suggested_position": {
            "weight_pct": suggested["weight"],
            "dollar_amount": round(target_dollar, 2),
            "rationale": suggested["rationale"],
        },
        "warnings": [w for w in [cap_warning, overlap_warning] if w],
    }


def _suggest_size(aggregate_score: int, max_corr: Optional[float]) -> dict:
    """
    Transparent position-size rule. Tune to firm rules later.

    Base: 5% of portfolio.
    Tilt up to 7% if aggregate score >= 22 AND max correlation <= 0.6
    Tilt down to 3% if max correlation >= 0.75 OR aggregate score <= 15
    Cap: 8% in any single name.
    """
    reasons = []
    weight = 5.0
    if aggregate_score >= 22 and (max_corr is None or max_corr <= 0.6):
        weight = 7.0
        reasons.append("Strong aggregate score and low overlap with existing holdings.")
    if max_corr is not None and max_corr >= 0.75:
        weight = min(weight, 3.0)
        reasons.append("High correlation with an existing holding — start smaller to avoid concentrating the same exposure.")
    if aggregate_score <= 15:
        weight = min(weight, 3.0)
        reasons.append("Weak aggregate score — smaller starter position is prudent.")
    weight = min(weight, 8.0)
    if not reasons:
        reasons.append("Default starter size for a typical name in this framework.")
    return {"weight": weight, "rationale": " ".join(reasons)}
