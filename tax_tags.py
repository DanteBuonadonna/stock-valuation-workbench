"""
Tax-treatment tags for an evaluated security.

We don't claim tax advice — we surface FACTS from the security's classification
that materially change client tax outcomes:

  * REIT: most dividends are ORDINARY income (no qualified rate), some can be
    return-of-capital. Sector "Real Estate" plus a REIT-style payout is the cue.
  * MLP / partnership: issues K-1 partnership tax forms (not 1099-DIV).
    Distributions are mostly return-of-capital. Material for taxable accounts.
  * Foreign domicile (ADR/non-US): foreign withholding tax on dividends may apply.
  * Ordinary C-corp dividend payer: assume QUALIFIED rate if held >60 days
    around ex-div (standard US rule). We flag the assumption, not certify it.

This module reads `info` plus a small heuristic on the ticker/name.
"""
from __future__ import annotations


def tag_security(data: dict) -> dict:
    sector = (data.get("sector") or "").lower()
    industry = (data.get("industry") or "").lower()
    name = (data.get("name") or "").lower()
    ticker = (data.get("ticker") or "").upper()
    div_yield = data.get("divYield") or 0
    annual_div = data.get("annualDiv") or 0

    tags = []
    notes = []

    is_reit = (
        sector == "real estate"
        or "reit" in industry
        or "real estate investment trust" in name
    )
    is_mlp = (
        "partnership" in name
        or "lp" in (name.split())
        or " mlp" in (" " + name)
        or industry.endswith("midstream") and ticker.endswith("P")
    )
    is_adr = "adr" in name or "ads" in name  # imperfect; foreign domicile heuristic

    if is_reit:
        tags.append("REIT")
        notes.append(
            "Most REIT distributions are taxed as ORDINARY income, not at the qualified "
            "dividend rate. Some portion may be return-of-capital. Best held in tax-advantaged "
            "accounts (IRA/401k) for high-bracket clients."
        )
    if is_mlp:
        tags.append("MLP / Partnership")
        notes.append(
            "MLPs distribute K-1 partnership income, not 1099-DIV. Distributions are largely "
            "return-of-capital. Material complication for client tax filings; UBTI applies "
            "in IRAs."
        )
    if is_adr:
        tags.append("ADR / Foreign")
        notes.append(
            "Foreign withholding tax on dividends may apply (commonly 15% under treaty). "
            "Treaty rates vary by country and account type."
        )

    if not tags and (annual_div or 0) > 0:
        tags.append("Qualified Dividend (assumed)")
        notes.append(
            "Standard US C-corp dividend — qualifies for long-term capital gains tax rates "
            "if shares held >60 days around the ex-dividend date. Confirm against the firm's "
            "tax engine before client work."
        )

    if (annual_div or 0) == 0:
        tags.append("No Dividend")

    return {"tags": tags, "notes": notes}
