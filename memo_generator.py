"""
Client memo PDF generator.

Produces a clean, 2-page client-ready PDF using ReportLab. Firm branding,
advisor name, and disclaimer come from `firm_branding.json` — edit that file
to put real firm details on the cover.

Real data only — numbers come straight from the evaluator payload.
"""
from __future__ import annotations
import io
import json
import os
from datetime import datetime
from typing import Optional


HERE = os.path.dirname(os.path.abspath(__file__))
BRANDING_PATH = os.path.join(HERE, "firm_branding.json")


def load_branding() -> dict:
    if not os.path.exists(BRANDING_PATH):
        return {"firm_name": "[Your Firm Name]", "advisor_name": "[Advisor Name]",
                "firm_tagline": "", "disclaimer": ""}
    with open(BRANDING_PATH) as f:
        return json.load(f)


def _fmt_money(v):
    if v is None:
        return "n/a"
    try:
        v = float(v)
        if abs(v) >= 1e12: return f"${v/1e12:.2f}T"
        if abs(v) >= 1e9:  return f"${v/1e9:.2f}B"
        if abs(v) >= 1e6:  return f"${v/1e6:.2f}M"
        return f"${v:,.2f}"
    except Exception:
        return "n/a"


def _fmt_pct(v, digits=2):
    if v is None:
        return "n/a"
    try:
        return f"{float(v):.{digits}f}%"
    except Exception:
        return "n/a"


def _verdict_explanation(rec: str, best_model_name: str, agg: int) -> str:
    if rec == "BUY":
        return (
            f"This stock clears most of our internal evaluation lenses (aggregate score "
            f"{agg}/30). Its strongest fit is the {best_model_name} model. We would "
            f"consider this name for client portfolios at an appropriate position size."
        )
    if rec == "HOLD":
        return (
            f"This stock fits one lens but not all (aggregate score {agg}/30). The "
            f"{best_model_name} model gives it the strongest score. Suitable for clients "
            f"whose mandate matches that style; less compelling as an all-weather holding."
        )
    return (
        f"This stock does not clear enough of our evaluation thresholds (aggregate "
        f"{agg}/30). We would not initiate a position at this time. Re-evaluate if "
        f"price, growth, or yield change materially."
    )


def generate_memo(payload: dict, client_name: Optional[str] = None) -> bytes:
    """Generate the PDF as bytes. Imports ReportLab lazily so the rest of the app
    doesn't depend on it being installed unless the memo is actually requested."""
    try:
        from reportlab.lib.pagesizes import letter
        from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
        from reportlab.lib.units import inch
        from reportlab.lib import colors
        from reportlab.platypus import (SimpleDocTemplate, Paragraph, Spacer, Table,
                                          TableStyle, PageBreak, KeepTogether)
        from reportlab.lib.enums import TA_LEFT, TA_RIGHT
    except ImportError as e:
        raise RuntimeError(
            "ReportLab is not installed. Run: python3 -m pip install --user reportlab"
        ) from e

    branding = load_branding()
    data = payload.get("data", {})
    summary = payload.get("summary", {})
    scores = payload.get("scores", {})
    insights = payload.get("insights", {})
    tax_tags = payload.get("taxTags", {})
    events = payload.get("events", {})

    ticker = data.get("ticker", "—")
    name = data.get("name", "—")
    rec = summary.get("recommendation", "—")
    agg = summary.get("aggregateScore", 0)
    best_model = summary.get("bestModelName", "—")

    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=letter,
                            leftMargin=0.6*inch, rightMargin=0.6*inch,
                            topMargin=0.5*inch, bottomMargin=0.55*inch)
    styles = getSampleStyleSheet()
    H1 = ParagraphStyle("H1", parent=styles["Heading1"], fontSize=20, spaceAfter=4)
    H2 = ParagraphStyle("H2", parent=styles["Heading2"], fontSize=13, textColor=colors.HexColor("#1f3a68"), spaceBefore=10, spaceAfter=4)
    Body = ParagraphStyle("Body", parent=styles["BodyText"], fontSize=10.5, leading=15)
    Small = ParagraphStyle("Small", parent=styles["BodyText"], fontSize=9, textColor=colors.HexColor("#555"))
    RightSmall = ParagraphStyle("RightSmall", parent=Small, alignment=TA_RIGHT)
    Verdict = ParagraphStyle("Verdict", parent=Body, fontSize=22, leading=28, textColor=colors.HexColor("#1f3a68"))

    rec_color = {"BUY": "#2e7d32", "HOLD": "#b8860b", "PASS": "#c62828"}.get(rec, "#555")
    VerdictColored = ParagraphStyle("VerdictColored", parent=Verdict, textColor=colors.HexColor(rec_color))

    today = datetime.now().strftime("%B %d, %Y")
    client_line = f"Prepared for: <b>{client_name}</b>" if client_name else "Prepared for: __________________________"

    story = []

    # ---- Header bar
    header_tbl = Table([[
        Paragraph(f"<b>{branding.get('firm_name', '[Your Firm Name]')}</b><br/><font size=9 color='#666'>{branding.get('firm_tagline','')}</font>", Body),
        Paragraph(f"<para alignment='right'><b>Investment Memo</b><br/><font size=9 color='#666'>{today}</font></para>", Body),
    ]], colWidths=[3.7*inch, 3.7*inch])
    header_tbl.setStyle(TableStyle([
        ("BOTTOMPADDING", (0,0), (-1,-1), 6),
        ("LINEBELOW", (0,0), (-1,0), 1, colors.HexColor("#1f3a68")),
    ]))
    story.append(header_tbl)
    story.append(Spacer(1, 10))

    # ---- Client + advisor line
    story.append(Paragraph(client_line, Body))
    advisor_line = f"Advisor: <b>{branding.get('advisor_name','[Advisor Name]')}</b>"
    if branding.get("advisor_credentials"):
        advisor_line += f", {branding['advisor_credentials']}"
    story.append(Paragraph(advisor_line, Body))
    story.append(Spacer(1, 14))

    # ---- Company block
    story.append(Paragraph(f"<b>{ticker} — {name}</b>", H1))
    sub_bits = []
    if data.get("sector"): sub_bits.append(data["sector"])
    if data.get("industry"): sub_bits.append(data["industry"])
    for tag in (tax_tags or {}).get("tags", []):
        sub_bits.append(tag)
    story.append(Paragraph(" · ".join(sub_bits), Small))
    story.append(Spacer(1, 12))

    # ---- Verdict block
    verdict_tbl = Table([[
        Paragraph(f"<b>Recommendation</b>", Small),
        Paragraph(f"<b>Best-fit Model</b>", Small),
        Paragraph(f"<b>Aggregate Score</b>", Small),
    ], [
        Paragraph(rec, VerdictColored),
        Paragraph(best_model, Body),
        Paragraph(f"<font size=18><b>{agg}</b></font> / 30", Body),
    ]], colWidths=[2.4*inch, 2.4*inch, 2.4*inch])
    verdict_tbl.setStyle(TableStyle([
        ("BOX", (0,0), (-1,-1), 0.5, colors.HexColor("#cfd6e3")),
        ("BACKGROUND", (0,0), (-1,0), colors.HexColor("#f4f6fa")),
        ("LEFTPADDING", (0,0), (-1,-1), 10),
        ("TOPPADDING", (0,0), (-1,-1), 8),
        ("BOTTOMPADDING", (0,0), (-1,-1), 8),
        ("VALIGN", (0,0), (-1,-1), "MIDDLE"),
    ]))
    story.append(verdict_tbl)
    story.append(Spacer(1, 10))

    # ---- Why
    story.append(Paragraph("Why this recommendation", H2))
    story.append(Paragraph(_verdict_explanation(rec, best_model, agg), Body))
    story.append(Spacer(1, 8))

    # ---- Key metrics
    story.append(Paragraph("Key metrics at a glance", H2))
    metrics = [
        ["Current Price", _fmt_money(data.get("price")), "P/E (trailing)", _fmt_pct(data.get("pe"))],
        ["Market Cap", _fmt_money(data.get("marketCap")), "Forward P/E", _fmt_pct(data.get("fwdPE"))],
        ["Dividend Yield", _fmt_pct(data.get("divYield")), "FCF Yield", _fmt_pct(data.get("fcfYield"))],
        ["Revenue Growth", _fmt_pct(data.get("revGrowth")), "Return on Equity", _fmt_pct(data.get("roe"))],
    ]
    mtbl = Table(metrics, colWidths=[1.6*inch, 1.6*inch, 1.6*inch, 1.6*inch])
    mtbl.setStyle(TableStyle([
        ("BOX", (0,0), (-1,-1), 0.5, colors.HexColor("#cfd6e3")),
        ("INNERGRID", (0,0), (-1,-1), 0.25, colors.HexColor("#e4e8f0")),
        ("TEXTCOLOR", (0,0), (0,-1), colors.HexColor("#666")),
        ("TEXTCOLOR", (2,0), (2,-1), colors.HexColor("#666")),
        ("FONTSIZE", (0,0), (-1,-1), 9.5),
        ("LEFTPADDING", (0,0), (-1,-1), 6),
        ("RIGHTPADDING", (0,0), (-1,-1), 6),
        ("TOPPADDING", (0,0), (-1,-1), 6),
        ("BOTTOMPADDING", (0,0), (-1,-1), 6),
    ]))
    story.append(mtbl)
    story.append(Spacer(1, 10))

    # ---- Strengths / risks
    strengths = insights.get("strengths") or []
    risks = insights.get("risks") or []
    sr_data = [
        [Paragraph("<b>What looks good</b>", Body), Paragraph("<b>What needs review</b>", Body)],
        [
            Paragraph("<br/>".join(f"• {s}" for s in strengths) or "<font color='#888'>—</font>", Body),
            Paragraph("<br/>".join(f"• {r}" for r in risks) or "<font color='#888'>—</font>", Body),
        ]
    ]
    sr_tbl = Table(sr_data, colWidths=[3.55*inch, 3.55*inch])
    sr_tbl.setStyle(TableStyle([
        ("BOX", (0,0), (-1,-1), 0.5, colors.HexColor("#cfd6e3")),
        ("INNERGRID", (0,0), (-1,-1), 0.25, colors.HexColor("#e4e8f0")),
        ("BACKGROUND", (0,0), (-1,0), colors.HexColor("#f4f6fa")),
        ("VALIGN", (0,0), (-1,-1), "TOP"),
        ("LEFTPADDING", (0,0), (-1,-1), 8),
        ("TOPPADDING", (0,0), (-1,-1), 6),
        ("BOTTOMPADDING", (0,0), (-1,-1), 6),
    ]))
    story.append(sr_tbl)

    # ---- Page break
    story.append(PageBreak())

    # ---- PAGE 2: Model detail
    story.append(Paragraph(f"{ticker} — Supporting Detail", H1))
    story.append(Spacer(1, 6))
    story.append(Paragraph("Model scorecards", H2))
    model_rows = [["Model", "Score", "Verdict"]]
    for key, model in scores.items():
        models_meta = payload.get("models", {}).get(key, {})
        m_name = models_meta.get("name", key)
        total = model.get("total", 0)
        pct = total / 10
        v = "Strong fit" if pct >= 0.8 else ("Moderate fit" if pct >= 0.5 else "Weak fit")
        model_rows.append([m_name, f"{total} / 10", v])
    mtbl2 = Table(model_rows, colWidths=[2.6*inch, 1.6*inch, 2.4*inch])
    mtbl2.setStyle(TableStyle([
        ("BACKGROUND", (0,0), (-1,0), colors.HexColor("#1f3a68")),
        ("TEXTCOLOR", (0,0), (-1,0), colors.white),
        ("FONTNAME", (0,0), (-1,0), "Helvetica-Bold"),
        ("BOX", (0,0), (-1,-1), 0.5, colors.HexColor("#cfd6e3")),
        ("INNERGRID", (0,0), (-1,-1), 0.25, colors.HexColor("#e4e8f0")),
        ("LEFTPADDING", (0,0), (-1,-1), 6),
        ("TOPPADDING", (0,0), (-1,-1), 6),
        ("BOTTOMPADDING", (0,0), (-1,-1), 6),
    ]))
    story.append(mtbl2)
    story.append(Spacer(1, 10))

    # ---- Events
    if events and not events.get("error"):
        story.append(Paragraph("Upcoming catalysts", H2))
        ev_rows = [
            ["Next earnings", events.get("next_earnings") or "n/a"],
            ["Ex-dividend date", events.get("ex_dividend_date") or "n/a"],
            ["Dividend payment date", events.get("dividend_payment_date") or "n/a"],
        ]
        etbl = Table(ev_rows, colWidths=[2.5*inch, 4.5*inch])
        etbl.setStyle(TableStyle([
            ("BOX", (0,0), (-1,-1), 0.5, colors.HexColor("#cfd6e3")),
            ("INNERGRID", (0,0), (-1,-1), 0.25, colors.HexColor("#e4e8f0")),
            ("TEXTCOLOR", (0,0), (0,-1), colors.HexColor("#555")),
            ("FONTSIZE", (0,0), (-1,-1), 10),
            ("LEFTPADDING", (0,0), (-1,-1), 6),
            ("TOPPADDING", (0,0), (-1,-1), 5),
            ("BOTTOMPADDING", (0,0), (-1,-1), 5),
        ]))
        story.append(etbl)
        story.append(Spacer(1, 10))

    # ---- Tax notes
    if tax_tags and tax_tags.get("notes"):
        story.append(Paragraph("Tax considerations", H2))
        for note in tax_tags["notes"]:
            story.append(Paragraph(f"• {note}", Body))
        story.append(Spacer(1, 8))

    # ---- Suitability + signoff
    story.append(Paragraph("Suitability assessment", H2))
    story.append(Paragraph(
        "Confirm that the recommendation matches the client's stated risk tolerance, "
        "time horizon, income needs, and tax situation. Document any deviation from "
        "the model recommendation and the reasoning.", Body))
    story.append(Spacer(1, 6))
    suit_tbl = Table([
        ["Risk tolerance match", "☐ Yes  ☐ No"],
        ["Time horizon appropriate", "☐ Yes  ☐ No"],
        ["Income / tax fit", "☐ Yes  ☐ No"],
        ["Position size approved", "_______ % of portfolio"],
        ["Advisor signature", "_______________________   Date: ___________"],
    ], colWidths=[2.5*inch, 4.5*inch])
    suit_tbl.setStyle(TableStyle([
        ("BOX", (0,0), (-1,-1), 0.5, colors.HexColor("#cfd6e3")),
        ("INNERGRID", (0,0), (-1,-1), 0.25, colors.HexColor("#e4e8f0")),
        ("FONTSIZE", (0,0), (-1,-1), 10),
        ("LEFTPADDING", (0,0), (-1,-1), 6),
        ("TOPPADDING", (0,0), (-1,-1), 5),
        ("BOTTOMPADDING", (0,0), (-1,-1), 5),
    ]))
    story.append(suit_tbl)
    story.append(Spacer(1, 12))

    # ---- Disclaimer
    story.append(Paragraph("Disclaimer", H2))
    story.append(Paragraph(branding.get("disclaimer", ""), Small))
    sources = (data.get("sources") or [])
    if sources:
        story.append(Spacer(1, 6))
        story.append(Paragraph("<i>Data sources</i>: " + " · ".join(sources), Small))

    doc.build(story)
    return buf.getvalue()
