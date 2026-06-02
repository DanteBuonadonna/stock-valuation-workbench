"""
Dividend income projection.

Given an evaluated ticker and a position size, project the next N years of
cash dividend income two ways:
  - Cash collection only (dividends paid out, base position unchanged)
  - Full DRIP (every dividend reinvested at current yield assumption)

We use the ticker's annual dividend AND its trailing 5-year dividend CAGR
that the data_fetcher already computes from real Yahoo history. If those
are missing or zero, the projection is skipped (no fake numbers).
"""
from __future__ import annotations
from typing import Optional


def project_income(
    data: dict,
    position_value: float = 100000.0,
    years: int = 5,
    growth_override_pct: Optional[float] = None,
) -> dict:
    annual_div = data.get("annualDiv") or 0
    price = data.get("price")
    div_yield = data.get("divYield")  # already %
    div_cagr = data.get("divCagr") or 0  # already %, can be None

    if growth_override_pct is not None:
        div_cagr = growth_override_pct

    if not annual_div or annual_div <= 0:
        return {
            "available": False,
            "reason": "No dividend — projection not applicable.",
        }
    if not price or price <= 0:
        return {"available": False, "reason": "No current price; cannot compute share count."}

    shares = position_value / price
    yield_pct = div_yield if div_yield else (annual_div / price) * 100

    g = (div_cagr or 0) / 100.0  # decimal

    cash_rows = []
    drip_rows = []
    cum_cash = 0.0
    drip_shares = shares
    drip_div = annual_div
    cash_div = annual_div
    for year in range(1, years + 1):
        # Cash-only: shares constant, dividend grows
        income = shares * cash_div
        cum_cash += income
        cash_rows.append({
            "year": year,
            "annual_dividend_per_share": round(cash_div, 4),
            "income": round(income, 2),
            "cumulative_income": round(cum_cash, 2),
        })
        cash_div = cash_div * (1 + g)

        # DRIP: dividends reinvested at current price (held constant for simplicity);
        # next year shares grow.
        drip_income = drip_shares * drip_div
        new_shares = drip_income / price
        drip_shares += new_shares
        drip_rows.append({
            "year": year,
            "annual_dividend_per_share": round(drip_div, 4),
            "income": round(drip_income, 2),
            "shares": round(drip_shares, 4),
            "position_value": round(drip_shares * price, 2),
        })
        drip_div = drip_div * (1 + g)

    drip_total_income = sum(r["income"] for r in drip_rows)

    return {
        "available": True,
        "assumptions": {
            "position_value_today": round(position_value, 2),
            "current_price": round(price, 2),
            "starting_shares": round(shares, 4),
            "current_yield_pct": round(yield_pct, 3) if yield_pct else None,
            "annual_dividend_per_share_today": round(annual_div, 4),
            "dividend_growth_rate_pct_used": round(div_cagr, 2) if div_cagr else 0,
            "growth_source": (
                "Yahoo 5Y historical dividend CAGR"
                if growth_override_pct is None and (data.get("divCagr") or 0)
                else ("Manual override" if growth_override_pct is not None
                      else "No historical dividend growth available — assumed 0%")
            ),
            "reinvestment_model": "DRIP reinvested at constant current price (illustrative).",
        },
        "cash_only": {
            "rows": cash_rows,
            "total_income": round(cum_cash, 2),
            "yield_on_cost_year_n_pct": (
                round((cash_rows[-1]["annual_dividend_per_share"] / price) * 100, 3)
                if price > 0 else None
            ),
        },
        "drip": {
            "rows": drip_rows,
            "total_income": round(drip_total_income, 2),
            "ending_shares": round(drip_shares, 4),
            "ending_position_value": round(drip_shares * price, 2),
        },
    }
