"""
Three-Model Stock Evaluator — engine + report generator.

Run:  python3 evaluator_engine.py <ticker.json>
where <ticker.json> contains the input metrics for a single stock.

Outputs a polished single-file HTML report next to the script.

The scoring rules, thresholds, and valuation methods are all defined below
and can be tuned to your firm's actual methodology.
"""
import json
import sys
import os
import math
from datetime import datetime

# ---------- THRESHOLDS (V1 DEFAULTS — TUNE TO FIRM RULES) ----------
THRESHOLDS = {
    # higher-is-better
    "revGrowth": {"good": 15, "ok": 10, "dir": "higher", "label": "Revenue growth (%)"},
    "epsGrowth": {"good": 15, "ok": 10, "dir": "higher", "label": "EPS growth (%)"},
    "roe":       {"good": 15, "ok": 10, "dir": "higher", "label": "Return on Equity (%)"},
    "divYield":  {"good": 3,  "ok": 2,  "dir": "higher", "label": "Dividend yield (%)"},
    "divCagr":   {"good": 5,  "ok": 2,  "dir": "higher", "label": "5Y dividend CAGR (%)"},
    "yearsInc":  {"good": 10, "ok": 5,  "dir": "higher", "label": "Years of consecutive increases"},
    "fcfCov":    {"good": 1.5,"ok": 1.0,"dir": "higher", "label": "FCF / dividend coverage (x)"},
    "ltGrowth":  {"good": 10, "ok": 7,  "dir": "higher", "label": "Long-term growth rate (%)"},
    "fcfYield":  {"good": 5,  "ok": 3,  "dir": "higher", "label": "Free cash flow yield (%)"},
    # lower-is-better
    "fwdPE":     {"good": 30, "ok": 40, "dir": "lower",  "label": "Forward P/E"},
    "peg":       {"good": 1.0,"ok": 1.5,"dir": "lower",  "label": "PEG ratio"},
    "pegVal":    {"good": 1.0,"ok": 1.5,"dir": "lower",  "label": "PEG ratio"},
    "payout":    {"good": 60, "ok": 80, "dir": "lower",  "label": "Payout ratio (%)"},
    "pe":        {"good": 15, "ok": 20, "dir": "lower",  "label": "P/E ratio (trailing)"},
    "debtEq":    {"good": 1.0,"ok": 2.0,"dir": "lower",  "label": "Debt / Equity"},
}

MODELS = {
    "growth": {
        "name": "Growth",
        "tagline": "Compounding revenue and earnings above the market",
        "metrics": ["revGrowth", "epsGrowth", "fwdPE", "roe", "peg"],
    },
    "dividend": {
        "name": "Dividend",
        "tagline": "Sustainable income with strong cash-flow coverage",
        "metrics": ["divYield", "payout", "divCagr", "yearsInc", "fcfCov"],
    },
    "valGrowth": {
        "name": "Value + Growth",
        "tagline": "P/E for value · LT growth · FCF yield (firm combined lens)",
        "metrics": ["pe", "ltGrowth", "fcfYield", "pegVal", "debtEq"],
    },
}

# ---------- SCORING ----------
def score_metric(metric, value):
    if value is None:
        return None
    t = THRESHOLDS.get(metric)
    if not t:
        return None
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    if t["dir"] == "higher":
        if v >= t["good"]:
            return 2
        if v >= t["ok"]:
            return 1
        return 0
    else:
        if v <= t["good"]:
            return 2
        if v <= t["ok"]:
            return 1
        return 0


def score_model(model_key, inputs):
    cfg = MODELS[model_key]
    breakdown = []
    total = 0
    filled = 0
    for m in cfg["metrics"]:
        pts = score_metric(m, inputs.get(m))
        val = inputs.get(m)
        if pts is not None:
            filled += 1
            total += pts
        t = THRESHOLDS[m]
        breakdown.append(
            {
                "metric": m,
                "label": t["label"],
                "value": val,
                "points": pts if pts is not None else 0,
                "scored": pts is not None,
                "good": t["good"],
                "ok": t["ok"],
                "dir": t["dir"],
            }
        )
    return {"total": total, "filled": filled, "breakdown": breakdown}


def tier(total, max_pts=10):
    pct = total / max_pts if max_pts else 0
    if pct >= 0.8:
        return ("good", "Strong fit", "BUY consideration")
    if pct >= 0.5:
        return ("ok", "Moderate fit", "HOLD / watch")
    return ("bad", "Weak fit", "Pass for this model")


# ---------- EXTRA VALUATION METHODS ----------
def graham_number(eps, bvps):
    """Benjamin Graham's intrinsic value formula for defensive investor."""
    if eps is None or bvps is None or eps <= 0 or bvps <= 0:
        return None
    return round(math.sqrt(22.5 * eps * bvps), 2)


def peg_fair_value(eps, growth_pct):
    """Fair price assuming PEG = 1 (Peter Lynch-style)."""
    if eps is None or growth_pct is None or eps <= 0 or growth_pct <= 0:
        return None
    return round(eps * growth_pct, 2)


def ddm_fair_value(annual_div, growth_pct, required_return_pct=9.0):
    """Gordon Growth dividend discount model."""
    if annual_div is None or growth_pct is None:
        return None
    g = growth_pct / 100.0
    r = required_return_pct / 100.0
    if r <= g:
        return None
    return round(annual_div * (1 + g) / (r - g), 2)


def upside_pct(fair_value, price):
    if fair_value is None or price is None or price <= 0:
        return None
    return round((fair_value / price - 1) * 100, 1)


# ---------- HTML REPORT GENERATION ----------
def fmt_num(v, unit="", places=2):
    if v is None:
        return "<span class='na'>n/a</span>"
    try:
        f = float(v)
        if places == 0 or f == int(f):
            return f"{int(f):,}{unit}"
        return f"{f:,.{places}f}{unit}"
    except Exception:
        return str(v)


def fmt_money(v):
    if v is None:
        return "<span class='na'>n/a</span>"
    try:
        f = float(v)
        if f >= 1e12:
            return f"${f/1e12:.2f}T"
        if f >= 1e9:
            return f"${f/1e9:.2f}B"
        if f >= 1e6:
            return f"${f/1e6:.2f}M"
        return f"${f:,.2f}"
    except Exception:
        return str(v)


def tier_css(t):
    return {"good": "good", "ok": "ok", "bad": "bad"}.get(t, "")


def render_breakdown_row(row):
    if not row["scored"]:
        return (
            f"<tr class='unscored'><td>{row['label']}</td><td class='val'>—</td>"
            f"<td class='thresh'>{thresh_label(row)}</td><td class='pts'>—</td></tr>"
        )
    pts = row["points"]
    cls = "good" if pts == 2 else "ok" if pts == 1 else "bad"
    return (
        f"<tr><td>{row['label']}</td>"
        f"<td class='val'>{fmt_num(row['value'])}</td>"
        f"<td class='thresh'>{thresh_label(row)}</td>"
        f"<td class='pts {cls}'>{pts} pt{'s' if pts != 1 else ''}</td></tr>"
    )


def thresh_label(row):
    if row["dir"] == "higher":
        return f"≥{row['good']} · ≥{row['ok']} · &lt;{row['ok']}"
    return f"≤{row['good']} · ≤{row['ok']} · &gt;{row['ok']}"


def render_model_card(model_key, result):
    cfg = MODELS[model_key]
    t_cls, t_label, t_action = tier(result["total"])
    rows = "\n".join(render_breakdown_row(r) for r in result["breakdown"])
    return f"""
    <section class="model-card {t_cls}">
      <header>
        <h3>{cfg['name']} Model</h3>
        <p class="tagline">{cfg['tagline']}</p>
      </header>
      <div class="score-block">
        <div class="score-num">{result['total']}<span class="of">/10</span></div>
        <div class="score-meta">
          <div class="verdict {t_cls}">{t_label}</div>
          <div class="action">{t_action}</div>
        </div>
      </div>
      <table class="breakdown">
        <thead><tr><th>Metric</th><th>Value</th><th>Tiers</th><th>Score</th></tr></thead>
        <tbody>{rows}</tbody>
      </table>
    </section>
    """


def render_extra_valuations(data):
    rows = []
    price = data.get("price")

    # Graham number
    gn = graham_number(data.get("eps"), data.get("bvps"))
    rows.append(("Graham Number",
                 "√(22.5 × EPS × Book Value/Share) — defensive value benchmark",
                 fmt_money(gn), upside_pct(gn, price)))

    # PEG fair value
    pfv = peg_fair_value(data.get("eps"), data.get("ltGrowth"))
    rows.append(("Lynch PEG Fair Value",
                 "EPS × growth rate — assumes PEG = 1 is fair",
                 fmt_money(pfv), upside_pct(pfv, price)))

    # DDM (only if dividend payer)
    div = data.get("annualDiv")
    if div and div > 0:
        ddm = ddm_fair_value(div, data.get("divCagr") or data.get("ltGrowth"), 9.0)
        rows.append(("Dividend Discount Model",
                     "Gordon Growth: D × (1+g) / (r−g), required return 9%",
                     fmt_money(ddm), upside_pct(ddm, price)))

    # FCF yield
    fy = data.get("fcfYield")
    rows.append(("FCF Yield",
                 "Free cash flow ÷ market cap — owner-earnings yield",
                 f"{fy:.2f}%" if fy is not None else "<span class='na'>n/a</span>", None))

    # P/E vs sector context — qualitative only without sector data
    pe = data.get("pe")
    rows.append(("Trailing P/E",
                 "Versus 25–30 long-term US large-cap average",
                 fmt_num(pe) if pe else "<span class='na'>n/a</span>", None))

    out = ""
    for name, desc, val, up in rows:
        up_html = ""
        if up is not None:
            cls = "good" if up > 10 else "ok" if up > -10 else "bad"
            sign = "+" if up >= 0 else ""
            up_html = f"<div class='upside {cls}'>{sign}{up:.1f}% vs price</div>"
        out += f"""
        <div class="valuation-item">
          <div class="vi-header"><span class="vi-name">{name}</span><span class="vi-val">{val}</span></div>
          <div class="vi-desc">{desc}</div>
          {up_html}
        </div>
        """
    return out


def derive_strengths_and_flags(results, data):
    """Surface the top wins and red flags across all three models + extras."""
    strengths = []
    flags = []
    all_rows = []
    for r in results.values():
        all_rows.extend(r["breakdown"])

    for row in all_rows:
        if not row["scored"]:
            continue
        if row["points"] == 2:
            strengths.append(f"<strong>{row['label']}</strong> at {fmt_num(row['value'])}")
        if row["points"] == 0:
            flags.append(f"<strong>{row['label']}</strong> at {fmt_num(row['value'])}")

    # Hard red flags
    if (data.get("payout") or 0) > 100:
        flags.append("<strong>Payout ratio &gt; 100%</strong> — dividend not covered by earnings")
    if (data.get("fcfCov") or 99) < 1 and (data.get("annualDiv") or 0) > 0:
        flags.append("<strong>FCF does not cover dividend</strong> — sustainability risk")
    if (data.get("debtEq") or 0) > 3:
        flags.append("<strong>Very high debt load</strong> (D/E &gt; 3)")
    if (data.get("eps") or 1) < 0:
        flags.append("<strong>Negative earnings</strong> — value screens unreliable")

    return strengths[:5], flags[:5]


def best_fit_model(results):
    best_key = max(results, key=lambda k: results[k]["total"])
    return best_key, results[best_key]


def render_report(data, track_record=None):
    ticker = data.get("ticker", "—").upper()
    name = data.get("name") or ticker
    sector = data.get("sector") or "—"
    industry = data.get("industry") or ""

    results = {k: score_model(k, data) for k in MODELS}
    best_key, best = best_fit_model(results)
    best_tier, best_label, best_action = tier(best["total"])
    strengths, flags = derive_strengths_and_flags(results, data)

    overall_score = sum(r["total"] for r in results.values())
    overall_max = 30

    # Predictor section (calibration-based)
    try:
        from predictor import predict as predict_returns
        scores_simple = {k: results[k]["total"] for k in results}
        prediction = predict_returns(scores_simple, best_key, overall_score)
    except Exception:
        prediction = None
    predictor_html = render_predictor(prediction, best_key)

    # Track record section (user's own past evaluations)
    track_record_html = render_track_record(track_record or [])

    pct_avg = sum(r["total"] / 10 for r in results.values()) / 3
    if pct_avg >= 0.75:
        overall_rec = ("BUY", "good", "Multiple firm lenses give this name a strong score.")
    elif pct_avg >= 0.50:
        overall_rec = ("HOLD", "ok", "Mixed signal — best as a fit for one specific model rather than a generalist holding.")
    else:
        overall_rec = ("PASS", "bad", "Doesn't clear the bar on the firm's primary evaluation lenses.")

    cards = "\n".join(render_model_card(k, results[k]) for k in MODELS)
    extra_vals = render_extra_valuations(data)

    strengths_html = "".join(f"<li>{s}</li>" for s in strengths) or "<li class='muted'>Not enough scored inputs yet.</li>"
    flags_html = "".join(f"<li>{f}</li>" for f in flags) or "<li class='muted'>No red flags detected.</li>"

    notes_html = ""
    if data.get("notes"):
        notes_html = f"<div class='notes'><strong>Analyst notes:</strong> {data['notes']}</div>"

    sources_html = ""
    if data.get("sources"):
        items = "".join(f"<li><a href='{u}' target='_blank' rel='noopener'>{u}</a></li>" for u in data["sources"])
        sources_html = f"<details class='sources'><summary>Data sources</summary><ul>{items}</ul></details>"

    asof = data.get("asOf") or datetime.now().strftime("%Y-%m-%d %H:%M")

    return REPORT_TEMPLATE.format(
        ticker=ticker,
        name=name,
        sector=sector,
        industry=industry,
        asof=asof,
        price=fmt_money(data.get("price")),
        mcap=fmt_money(data.get("marketCap")),
        eps=fmt_num(data.get("eps")),
        div_annual=fmt_money(data.get("annualDiv")) if data.get("annualDiv") else "—",
        beta=fmt_num(data.get("beta")),
        overall_score=overall_score,
        overall_max=overall_max,
        overall_pct=int(pct_avg * 100),
        overall_rec=overall_rec[0],
        overall_class=overall_rec[1],
        overall_msg=overall_rec[2],
        best_name=MODELS[best_key]["name"],
        best_score=best["total"],
        best_label=best_label,
        best_action=best_action,
        best_class=best_tier,
        cards=cards,
        extras=extra_vals,
        predictor=predictor_html,
        track_record=track_record_html,
        strengths=strengths_html,
        flags=flags_html,
        notes=notes_html,
        sources=sources_html,
    )


def render_predictor(prediction, best_key):
    if not prediction:
        return ""
    is_seed = prediction.get("is_seed")
    band = prediction.get("band", "—")
    bs = prediction.get("band_stats") or {}
    ms = prediction.get("model_stats") or {}
    seed_warning = ""
    if is_seed:
        seed_warning = (
            "<div class='seed-warn'>⚠ Using seed calibration. "
            "Run <code>python3 backtest.py</code> to replace with real backtest results from your basket.</div>"
        )
    band_block = ""
    if bs.get("median") is not None:
        band_block = f"""
        <div class='pred-band'>
          <div class='pred-label'>Aggregate band: <strong>{band}</strong></div>
          <div class='pred-stats'>
            <div><span class='lbl'>Median 1Y</span><span class='val {tone(bs.get("median"))}'>{fmt_signed(bs.get("median"))}%</span></div>
            <div><span class='lbl'>25th–75th pct</span><span class='val'>{fmt_signed(bs.get("p25"))}% to {fmt_signed(bs.get("p75"))}%</span></div>
            <div><span class='lbl'>Range</span><span class='val'>{fmt_signed(bs.get("min"))}% to {fmt_signed(bs.get("max"))}%</span></div>
            <div><span class='lbl'>Samples (n)</span><span class='val'>{bs.get("n", 0)}</span></div>
          </div>
        </div>
        """
    model_block = ""
    if ms.get("median") is not None:
        model_name = MODELS[best_key]["name"]
        model_block = f"""
        <div class='pred-band'>
          <div class='pred-label'>When best-fit model is <strong>{model_name}</strong> at this strength</div>
          <div class='pred-stats'>
            <div><span class='lbl'>Median 1Y</span><span class='val {tone(ms.get("median"))}'>{fmt_signed(ms.get("median"))}%</span></div>
            <div><span class='lbl'>25th–75th pct</span><span class='val'>{fmt_signed(ms.get("p25"))}% to {fmt_signed(ms.get("p75"))}%</span></div>
            <div><span class='lbl'>Range</span><span class='val'>{fmt_signed(ms.get("min"))}% to {fmt_signed(ms.get("max"))}%</span></div>
            <div><span class='lbl'>Samples (n)</span><span class='val'>{ms.get("n", 0)}</span></div>
          </div>
        </div>
        """
    return f"""
    <div class="predictor">
      <h2>Predictor — Historical Context</h2>
      <p class="pred-intro">
        Not a forecast. Calibration data showing how stocks at this score level have historically performed over the next 12 months.
        Used as decision support, not a price target.
      </p>
      {seed_warning}
      <div class="pred-bands">{band_block}{model_block}</div>
    </div>
    """


def render_track_record(items):
    if not items:
        return ""
    rows = ""
    n_calls = len(items)
    matured = [it for it in items if it.get("return_pct") is not None and it.get("days_after", 0) >= 90]
    accuracy_line = ""
    if matured:
        buys_up = sum(1 for it in matured if it["rec"] == "BUY" and it["return_pct"] > 0)
        buys_total = sum(1 for it in matured if it["rec"] == "BUY")
        pass_down = sum(1 for it in matured if it["rec"] == "PASS" and it["return_pct"] < 0)
        pass_total = sum(1 for it in matured if it["rec"] == "PASS")
        parts = []
        if buys_total:
            parts.append(f"BUY calls right (positive return): {buys_up}/{buys_total} ({100*buys_up/buys_total:.0f}%)")
        if pass_total:
            parts.append(f"PASS calls right (negative return): {pass_down}/{pass_total} ({100*pass_down/pass_total:.0f}%)")
        if parts:
            accuracy_line = "<div class='tr-accuracy'>" + " · ".join(parts) + "</div>"

    for it in items[:15]:
        ret = it.get("return_pct")
        ret_html = "<span class='na'>pending</span>" if ret is None else (
            f"<span class='{tone(ret)}'>{fmt_signed(ret)}%</span>"
        )
        rec = it.get("rec") or "—"
        rec_cls = {"BUY": "good", "HOLD": "ok", "PASS": "bad"}.get(rec, "")
        rows += f"""
        <tr>
          <td class='tk'>{it.get('ticker', '—')}</td>
          <td>{it.get('date', '—')}</td>
          <td class='{rec_cls}'><strong>{rec}</strong></td>
          <td>{MODELS.get(it.get('best_model', ''), {}).get('name', '—')}</td>
          <td class='val'>{it['scores'].get('growth', 0)}·{it['scores'].get('dividend', 0)}·{it['scores'].get('valGrowth', 0)}</td>
          <td>{fmt_money(it.get('price_at_eval'))}</td>
          <td>{ret_html}</td>
        </tr>
        """
    return f"""
    <div class="track-record">
      <h2>Your Track Record ({n_calls} past evaluation{'s' if n_calls != 1 else ''})</h2>
      <p class="tr-intro">
        Every evaluation you've run is logged. The system periodically checks the realized 1-year return so calls accumulate as actual evidence the predictor can learn from.
      </p>
      {accuracy_line}
      <table class="tr-table">
        <thead>
          <tr><th>Ticker</th><th>Date</th><th>Call</th><th>Best Fit</th><th>Scores (G·D·V)</th><th>Price at Eval</th><th>Return since</th></tr>
        </thead>
        <tbody>{rows}</tbody>
      </table>
    </div>
    """


def tone(v):
    if v is None:
        return ""
    try:
        f = float(v)
        if f >= 5:
            return "good"
        if f >= -5:
            return "ok"
        return "bad"
    except Exception:
        return ""


def fmt_signed(v):
    if v is None:
        return "—"
    try:
        f = float(v)
        return f"{'+' if f >= 0 else ''}{f:.1f}"
    except Exception:
        return str(v)


REPORT_TEMPLATE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8" />
<meta name="viewport" content="width=device-width, initial-scale=1.0"/>
<title>{ticker} — Stock Evaluation</title>
<style>
  :root {{
    --bg: #0d1117;
    --panel: #161b22;
    --panel-2: #1f2630;
    --border: #2d3743;
    --text: #e6edf3;
    --muted: #8b96a3;
    --accent: #58a6ff;
    --good: #3fb950;
    --ok: #d29922;
    --bad: #f85149;
    --good-bg: rgba(63,185,80,0.10);
    --ok-bg: rgba(210,153,34,0.10);
    --bad-bg: rgba(248,81,73,0.10);
    --shadow: 0 4px 24px rgba(0,0,0,0.4);
  }}
  * {{ box-sizing: border-box; }}
  body {{ margin:0; padding:32px 24px; background: var(--bg); color: var(--text);
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; }}
  .container {{ max-width: 1280px; margin: 0 auto; }}
  .header {{ display:flex; justify-content:space-between; align-items:flex-start;
    padding-bottom: 18px; border-bottom: 1px solid var(--border); margin-bottom: 24px; gap: 20px; flex-wrap: wrap; }}
  .title-block .ticker {{ font-size: 36px; font-weight: 700; letter-spacing: -1px; font-family: "SF Mono", Monaco, monospace; }}
  .title-block .name {{ font-size: 18px; color: var(--text); margin-top: 2px; }}
  .title-block .meta {{ color: var(--muted); font-size: 13px; margin-top: 6px; }}
  .quick-stats {{ display: grid; grid-template-columns: repeat(auto-fit,minmax(110px,1fr)); gap: 14px 24px; }}
  .qs {{ display:flex; flex-direction: column; gap: 2px; }}
  .qs .lbl {{ font-size: 10.5px; color: var(--muted); text-transform: uppercase; letter-spacing: 0.6px; }}
  .qs .val {{ font-size: 17px; font-weight: 600; font-family: "SF Mono", Monaco, monospace; }}

  /* HERO VERDICT */
  .hero {{ background: linear-gradient(135deg, var(--panel) 0%, var(--panel-2) 100%);
    border: 1px solid var(--border); border-radius: 12px; padding: 24px; margin-bottom: 20px;
    box-shadow: var(--shadow); display: grid; grid-template-columns: 1fr 1.4fr 1fr; gap: 24px; }}
  @media (max-width: 900px) {{ .hero {{ grid-template-columns: 1fr; }} }}
  .hero-rec {{ display:flex; flex-direction:column; align-items:flex-start; justify-content:center; }}
  .hero-rec .label {{ color: var(--muted); font-size: 11px; letter-spacing: 1px; text-transform: uppercase; margin-bottom: 6px; }}
  .hero-rec .big {{ font-size: 56px; font-weight: 800; line-height: 1; letter-spacing: -1.5px; font-family: "SF Mono", Monaco, monospace; }}
  .hero-rec.good .big {{ color: var(--good); }}
  .hero-rec.ok .big {{ color: var(--ok); }}
  .hero-rec.bad .big {{ color: var(--bad); }}
  .hero-rec .msg {{ font-size: 14px; color: var(--text); margin-top: 10px; line-height: 1.5; max-width: 360px; }}

  .hero-best {{ background: var(--bg); border: 1px solid var(--border); border-radius: 10px; padding: 18px; display: flex; flex-direction: column; justify-content: center; }}
  .hero-best .label {{ color: var(--muted); font-size: 11px; letter-spacing: 1px; text-transform: uppercase; margin-bottom: 8px; }}
  .hero-best .name {{ font-size: 22px; font-weight: 700; margin-bottom: 8px; }}
  .hero-best .score {{ font-size: 32px; font-weight: 700; font-family: "SF Mono", Monaco, monospace; }}
  .hero-best .score .of {{ color: var(--muted); font-size: 18px; font-weight: 500; }}
  .hero-best .v {{ font-size: 13px; margin-top: 6px; font-weight: 600; }}
  .hero-best.good .v, .hero-best.good .name {{ color: var(--good); }}
  .hero-best.ok .v, .hero-best.ok .name {{ color: var(--ok); }}
  .hero-best.bad .v, .hero-best.bad .name {{ color: var(--bad); }}

  .hero-totals {{ display: flex; flex-direction: column; justify-content: center; gap: 6px; }}
  .hero-totals .label {{ color: var(--muted); font-size: 11px; letter-spacing: 1px; text-transform: uppercase; }}
  .hero-totals .agg {{ font-size: 28px; font-weight: 700; font-family: "SF Mono", Monaco, monospace; }}
  .hero-totals .agg .of {{ color: var(--muted); font-size: 16px; font-weight: 500; }}
  .hero-totals .pctbar {{ width: 100%; height: 8px; background: var(--bg); border: 1px solid var(--border); border-radius: 4px; overflow: hidden; margin-top: 4px; }}
  .hero-totals .pctbar .fill {{ height: 100%; background: linear-gradient(90deg, var(--bad), var(--ok), var(--good)); }}

  /* MODEL CARDS */
  .models {{ display: grid; grid-template-columns: repeat(3, 1fr); gap: 16px; margin-bottom: 24px; }}
  @media (max-width: 1000px) {{ .models {{ grid-template-columns: 1fr; }} }}
  .model-card {{ background: var(--panel); border: 1px solid var(--border); border-radius: 12px; padding: 20px; box-shadow: var(--shadow); border-top: 3px solid var(--border); }}
  .model-card.good {{ border-top-color: var(--good); }}
  .model-card.ok {{ border-top-color: var(--ok); }}
  .model-card.bad {{ border-top-color: var(--bad); }}
  .model-card header h3 {{ margin: 0; font-size: 18px; font-weight: 600; }}
  .model-card .tagline {{ color: var(--muted); font-size: 12px; margin-top: 2px; }}
  .score-block {{ display: flex; align-items: center; gap: 16px; margin: 14px 0 12px; padding: 12px 14px; background: var(--bg); border-radius: 8px; }}
  .score-num {{ font-size: 36px; font-weight: 700; font-family: "SF Mono", Monaco, monospace; line-height: 1; }}
  .score-num .of {{ color: var(--muted); font-size: 16px; font-weight: 500; margin-left: 2px; }}
  .model-card.good .score-num {{ color: var(--good); }}
  .model-card.ok .score-num {{ color: var(--ok); }}
  .model-card.bad .score-num {{ color: var(--bad); }}
  .score-meta {{ flex: 1; }}
  .verdict {{ font-size: 13px; font-weight: 600; text-transform: uppercase; letter-spacing: 0.5px; }}
  .verdict.good {{ color: var(--good); }}
  .verdict.ok {{ color: var(--ok); }}
  .verdict.bad {{ color: var(--bad); }}
  .action {{ font-size: 11.5px; color: var(--muted); margin-top: 2px; }}

  table.breakdown {{ width: 100%; border-collapse: collapse; font-size: 12.5px; margin-top: 8px; }}
  table.breakdown th {{ text-align: left; color: var(--muted); font-weight: 500; padding: 6px 8px; font-size: 10.5px; text-transform: uppercase; letter-spacing: 0.5px; border-bottom: 1px solid var(--border); }}
  table.breakdown td {{ padding: 8px; border-bottom: 1px solid var(--border); }}
  table.breakdown tr:last-child td {{ border-bottom: none; }}
  table.breakdown .val {{ font-family: "SF Mono", Monaco, monospace; }}
  table.breakdown .thresh {{ color: var(--muted); font-size: 11px; font-family: "SF Mono", Monaco, monospace; }}
  table.breakdown .pts {{ font-family: "SF Mono", Monaco, monospace; font-weight: 600; text-align: right; }}
  table.breakdown .pts.good {{ color: var(--good); }}
  table.breakdown .pts.ok {{ color: var(--ok); }}
  table.breakdown .pts.bad {{ color: var(--bad); }}
  table.breakdown tr.unscored td {{ color: var(--muted); font-style: italic; }}
  .na {{ color: var(--muted); font-style: italic; }}

  /* EXTRA VALUATIONS */
  .extras {{ background: var(--panel); border: 1px solid var(--border); border-radius: 12px; padding: 22px; margin-bottom: 24px; box-shadow: var(--shadow); }}
  .extras h2 {{ font-size: 14px; color: var(--muted); text-transform: uppercase; letter-spacing: 0.8px; font-weight: 500; margin: 0 0 14px; }}
  .extras-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(260px, 1fr)); gap: 14px; }}
  .valuation-item {{ background: var(--bg); border: 1px solid var(--border); border-radius: 8px; padding: 14px 16px; }}
  .vi-header {{ display: flex; justify-content: space-between; align-items: baseline; }}
  .vi-name {{ font-size: 13.5px; font-weight: 600; }}
  .vi-val {{ font-size: 16px; font-weight: 700; font-family: "SF Mono", Monaco, monospace; }}
  .vi-desc {{ font-size: 11.5px; color: var(--muted); margin-top: 4px; line-height: 1.45; }}
  .upside {{ font-size: 12px; font-weight: 600; margin-top: 6px; font-family: "SF Mono", Monaco, monospace; }}
  .upside.good {{ color: var(--good); }}
  .upside.ok {{ color: var(--ok); }}
  .upside.bad {{ color: var(--bad); }}

  /* STRENGTHS / FLAGS */
  .panels {{ display: grid; grid-template-columns: 1fr 1fr; gap: 16px; margin-bottom: 24px; }}
  @media (max-width: 800px) {{ .panels {{ grid-template-columns: 1fr; }} }}
  .panel {{ background: var(--panel); border: 1px solid var(--border); border-radius: 12px; padding: 20px; box-shadow: var(--shadow); }}
  .panel.green {{ border-left: 4px solid var(--good); }}
  .panel.red {{ border-left: 4px solid var(--bad); }}
  .panel h2 {{ margin: 0 0 12px; font-size: 14px; text-transform: uppercase; letter-spacing: 0.8px; font-weight: 500; color: var(--muted); }}
  .panel ul {{ margin: 0; padding-left: 18px; }}
  .panel li {{ padding: 4px 0; font-size: 13.5px; line-height: 1.5; }}
  .panel li.muted {{ color: var(--muted); font-style: italic; }}

  .notes {{ background: var(--panel-2); border-left: 4px solid var(--accent); padding: 14px 16px; border-radius: 8px; margin-bottom: 20px; font-size: 13.5px; line-height: 1.55; }}
  .sources {{ margin-top: 24px; background: var(--panel); border: 1px solid var(--border); border-radius: 8px; padding: 14px 18px; }}
  .sources summary {{ cursor: pointer; color: var(--muted); font-size: 12.5px; }}
  .sources ul {{ margin: 10px 0 0; padding-left: 18px; }}
  .sources a {{ color: var(--accent); font-size: 12px; word-break: break-all; }}

  /* PREDICTOR */
  .predictor {{ background: var(--panel); border: 1px solid var(--border); border-radius: 12px; padding: 22px; margin-bottom: 24px; box-shadow: var(--shadow); border-left: 4px solid var(--accent); }}
  .predictor h2 {{ font-size: 14px; color: var(--muted); text-transform: uppercase; letter-spacing: 0.8px; font-weight: 500; margin: 0 0 8px; }}
  .pred-intro {{ font-size: 13px; color: var(--text); margin: 0 0 14px; line-height: 1.5; }}
  .pred-bands {{ display: grid; grid-template-columns: 1fr 1fr; gap: 14px; }}
  @media (max-width: 800px) {{ .pred-bands {{ grid-template-columns: 1fr; }} }}
  .pred-band {{ background: var(--bg); border: 1px solid var(--border); border-radius: 8px; padding: 14px 16px; }}
  .pred-label {{ font-size: 12px; color: var(--muted); margin-bottom: 10px; }}
  .pred-stats {{ display: grid; grid-template-columns: 1fr 1fr; gap: 8px 16px; }}
  .pred-stats > div {{ display: flex; flex-direction: column; gap: 2px; }}
  .pred-stats .lbl {{ font-size: 10.5px; color: var(--muted); text-transform: uppercase; letter-spacing: 0.5px; }}
  .pred-stats .val {{ font-size: 15px; font-weight: 600; font-family: "SF Mono", Monaco, monospace; }}
  .pred-stats .val.good {{ color: var(--good); }}
  .pred-stats .val.ok {{ color: var(--ok); }}
  .pred-stats .val.bad {{ color: var(--bad); }}
  .seed-warn {{ background: rgba(210,153,34,0.10); border: 1px solid var(--ok); color: var(--ok); border-radius: 6px; padding: 8px 12px; margin-bottom: 12px; font-size: 12px; }}
  .seed-warn code {{ background: rgba(0,0,0,0.3); padding: 1px 5px; border-radius: 3px; font-size: 11px; }}

  /* TRACK RECORD */
  .track-record {{ background: var(--panel); border: 1px solid var(--border); border-radius: 12px; padding: 22px; margin-bottom: 24px; box-shadow: var(--shadow); }}
  .track-record h2 {{ font-size: 14px; color: var(--muted); text-transform: uppercase; letter-spacing: 0.8px; font-weight: 500; margin: 0 0 8px; }}
  .tr-intro {{ font-size: 13px; color: var(--muted); margin: 0 0 12px; line-height: 1.5; }}
  .tr-accuracy {{ background: var(--bg); border-left: 3px solid var(--good); padding: 8px 12px; border-radius: 6px; margin-bottom: 12px; font-size: 13px; font-weight: 500; }}
  .tr-table {{ width: 100%; border-collapse: collapse; font-size: 12.5px; }}
  .tr-table th {{ text-align: left; color: var(--muted); font-weight: 500; padding: 8px; font-size: 10.5px; text-transform: uppercase; letter-spacing: 0.5px; border-bottom: 1px solid var(--border); }}
  .tr-table td {{ padding: 9px 8px; border-bottom: 1px solid var(--border); }}
  .tr-table td.tk {{ font-family: "SF Mono", Monaco, monospace; font-weight: 600; }}
  .tr-table td.val {{ font-family: "SF Mono", Monaco, monospace; color: var(--muted); }}
  .tr-table td .good {{ color: var(--good); font-family: "SF Mono", Monaco, monospace; font-weight: 600; }}
  .tr-table td .ok {{ color: var(--ok); font-family: "SF Mono", Monaco, monospace; font-weight: 600; }}
  .tr-table td .bad {{ color: var(--bad); font-family: "SF Mono", Monaco, monospace; font-weight: 600; }}
  .tr-table td.good strong {{ color: var(--good); }}
  .tr-table td.ok strong {{ color: var(--ok); }}
  .tr-table td.bad strong {{ color: var(--bad); }}

  .footer {{ text-align: center; color: var(--muted); font-size: 11.5px; margin-top: 30px; padding-top: 18px; border-top: 1px solid var(--border); }}
  .footer .warn {{ color: var(--ok); margin-top: 4px; }}

  @media print {{
    body {{ background: white; color: black; padding: 12px; }}
    .hero, .model-card, .panel, .extras, .notes, .sources {{ background: white; border-color: #ccc; box-shadow: none; }}
    .score-block, .valuation-item {{ background: #fafafa; border-color: #ddd; }}
  }}
</style>
</head>
<body>
<div class="container">

  <!-- HEADER -->
  <div class="header">
    <div class="title-block">
      <div class="ticker">{ticker}</div>
      <div class="name">{name}</div>
      <div class="meta">{sector} · {industry}</div>
      <div class="meta">As of {asof}</div>
    </div>
    <div class="quick-stats">
      <div class="qs"><div class="lbl">Price</div><div class="val">{price}</div></div>
      <div class="qs"><div class="lbl">Market Cap</div><div class="val">{mcap}</div></div>
      <div class="qs"><div class="lbl">EPS (TTM)</div><div class="val">{eps}</div></div>
      <div class="qs"><div class="lbl">Annual Div</div><div class="val">{div_annual}</div></div>
      <div class="qs"><div class="lbl">Beta</div><div class="val">{beta}</div></div>
    </div>
  </div>

  <!-- HERO VERDICT -->
  <div class="hero">
    <div class="hero-rec {overall_class}">
      <div class="label">Overall Recommendation</div>
      <div class="big">{overall_rec}</div>
      <div class="msg">{overall_msg}</div>
    </div>
    <div class="hero-best {best_class}">
      <div class="label">Best-Fit Model</div>
      <div class="name">{best_name}</div>
      <div class="score">{best_score}<span class="of">/10</span></div>
      <div class="v">{best_label} · {best_action}</div>
    </div>
    <div class="hero-totals">
      <div class="label">Aggregate Score</div>
      <div class="agg">{overall_score}<span class="of">/{overall_max}</span></div>
      <div style="color:var(--muted);font-size:12px;">{overall_pct}% across the three lenses</div>
      <div class="pctbar"><div class="fill" style="width:{overall_pct}%;"></div></div>
    </div>
  </div>

  <!-- MODEL CARDS -->
  <div class="models">
    {cards}
  </div>

  <!-- EXTRA VALUATIONS -->
  <div class="extras">
    <h2>Supplementary Valuation Methods</h2>
    <div class="extras-grid">
      {extras}
    </div>
  </div>

  <!-- PREDICTOR -->
  {predictor}

  <!-- TRACK RECORD -->
  {track_record}

  <!-- STRENGTHS / FLAGS -->
  <div class="panels">
    <div class="panel green">
      <h2>✓ Strengths (top metrics)</h2>
      <ul>{strengths}</ul>
    </div>
    <div class="panel red">
      <h2>⚠ Red flags (weak metrics)</h2>
      <ul>{flags}</ul>
    </div>
  </div>

  {notes}

  {sources}

  <div class="footer">
    Generated by the firm's three-model evaluator · Thresholds are v1 scaffold defaults — tune to firm methodology.
    <div class="warn">This is decision-support output, not investment advice. Always verify data before client use.</div>
  </div>
</div>
</body>
</html>
"""


def main():
    if len(sys.argv) < 2:
        print("Usage: python3 evaluator_engine.py <input.json>")
        sys.exit(1)
    inp = sys.argv[1]
    with open(inp) as f:
        data = json.load(f)
    html = render_report(data)
    ticker = (data.get("ticker") or "ticker").lower()
    out_dir = os.path.dirname(os.path.abspath(inp))
    out_path = os.path.join(out_dir, f"evaluation_{ticker}_{datetime.now().strftime('%Y%m%d')}.html")
    with open(out_path, "w") as f:
        f.write(html)
    print(f"Report written to: {out_path}")


if __name__ == "__main__":
    main()
