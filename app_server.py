from __future__ import annotations

import json
import math
import os
import sqlite3
import sys
from datetime import datetime
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from data_fetcher import fetch_price_history, fetch_ticker_data  # noqa: E402
from evaluator_engine import MODELS, THRESHOLDS, ddm_fair_value, graham_number, peg_fair_value, score_model, upside_pct  # noqa: E402
from evaluate import DB_PATH, get_track_record, init_db, log_evaluation, refresh_followups  # noqa: E402
from predictor import predict  # noqa: E402


PEER_GROUPS = {
    "semiconductors": ["NVDA", "AMD", "AVGO", "QCOM", "INTC", "MU", "TXN", "ADI"],
    "consumer electronics": ["AAPL", "MSFT", "GOOGL", "META", "AMZN"],
    "software": ["MSFT", "ORCL", "ADBE", "CRM", "NOW", "INTU"],
    "internet content": ["GOOGL", "META", "NFLX", "SPOT", "PINS", "SNAP"],
    "banks": ["JPM", "BAC", "WFC", "C", "GS", "MS"],
    "credit services": ["V", "MA", "AXP", "DFS", "COF"],
    "oil": ["XOM", "CVX", "COP", "EOG", "SLB", "MPC"],
    "pharmaceutical": ["LLY", "JNJ", "PFE", "MRK", "ABBV", "BMY"],
    "biotechnology": ["AMGN", "GILD", "REGN", "VRTX", "BIIB"],
    "retail": ["WMT", "COST", "TGT", "HD", "LOW", "TJX"],
    "beverages": ["KO", "PEP", "MNST", "KDP"],
    "utilities": ["NEE", "DUK", "SO", "D", "AEP", "EXC"],
    "reit": ["PLD", "AMT", "EQIX", "O", "SPG", "PSA"],
    "automobiles": ["TSLA", "GM", "F", "TM", "RACE"],
}

SECTOR_ETFS = {
    "Technology": "XLK",
    "Communication Services": "XLC",
    "Consumer Cyclical": "XLY",
    "Consumer Defensive": "XLP",
    "Financial Services": "XLF",
    "Healthcare": "XLV",
    "Industrials": "XLI",
    "Energy": "XLE",
    "Utilities": "XLU",
    "Real Estate": "XLRE",
    "Basic Materials": "XLB",
}


def clean_float(value):
    if value is None:
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(out) or math.isinf(out):
        return None
    return out


def model_summary(scores):
    total = sum(item["total"] for item in scores.values())
    if total >= 23:
        rec = "BUY"
        tone = "Strong across the firm lens. Still verify thesis, risk, and client fit."
    elif total >= 16:
        rec = "HOLD"
        tone = "Usable fit in one or more models, but not a clean all-weather buy."
    else:
        rec = "PASS"
        tone = "Does not clear enough of the current firm thresholds."
    best_key = max(scores, key=lambda k: scores[k]["total"])
    return {
        "recommendation": rec,
        "aggregateScore": total,
        "maxScore": 30,
        "bestModel": best_key,
        "bestModelName": MODELS[best_key]["name"],
        "message": tone,
    }


def valuation_methods(data):
    price = clean_float(data.get("price"))
    eps = clean_float(data.get("eps"))
    bvps = clean_float(data.get("bvps"))
    annual_div = clean_float(data.get("annualDiv"))
    growth = clean_float(data.get("ltGrowth") or data.get("epsGrowth"))
    pe = clean_float(data.get("pe"))
    fcf_yield = clean_float(data.get("fcfYield"))

    graham = graham_number(eps, bvps)
    peg_fair = peg_fair_value(eps, growth)
    ddm = ddm_fair_value(annual_div, min(growth or 0, 6.0) if growth else None)
    earnings_yield = (100 / pe) if pe and pe > 0 else None

    rows = [
        {
            "name": "Graham Number",
            "value": graham,
            "upside": upside_pct(graham, price),
            "method": "Defensive value check using EPS and book value.",
        },
        {
            "name": "PEG Fair Value",
            "value": peg_fair,
            "upside": upside_pct(peg_fair, price),
            "method": "Price if PEG = 1 using EPS and growth rate.",
        },
        {
            "name": "Dividend Discount Model",
            "value": ddm,
            "upside": upside_pct(ddm, price),
            "method": "Gordon model with 9% required return and capped growth.",
        },
        {
            "name": "Earnings Yield",
            "value": earnings_yield,
            "upside": None,
            "method": "E/P, useful for comparing stocks to bond-like alternatives.",
            "unit": "%",
        },
        {
            "name": "Free Cash Flow Yield",
            "value": fcf_yield,
            "upside": None,
            "method": "FCF divided by market cap. Higher is cheaper if cash flow is durable.",
            "unit": "%",
        },
    ]
    return rows


def infer_peers(data):
    ticker = data.get("ticker", "").upper()
    industry = (data.get("industry") or "").lower()
    sector = data.get("sector") or ""

    peers = []
    for key, group in PEER_GROUPS.items():
        if key in industry:
            peers = group[:]
            break
    if not peers:
        for key, group in PEER_GROUPS.items():
            if key in sector.lower():
                peers = group[:]
                break
    if ticker and ticker not in peers:
        peers.insert(0, ticker)
    peers = [p for p in peers if p != ticker][:5]
    sector_etf = SECTOR_ETFS.get(sector)
    if sector_etf and sector_etf not in peers:
        peers.append(sector_etf)
    if "SPY" not in peers:
        peers.append("SPY")
    return peers[:7]


def safe_fetch_peer(ticker):
    try:
        data = fetch_ticker_data(ticker)
        scores = {k: score_model(k, data) for k in MODELS}
        return {
            "ticker": data.get("ticker", ticker),
            "name": data.get("name", ticker),
            "price": data.get("price"),
            "pe": data.get("pe"),
            "fwdPE": data.get("fwdPE"),
            "peg": data.get("peg") or data.get("pegVal"),
            "roe": data.get("roe"),
            "revGrowth": data.get("revGrowth"),
            "fcfYield": data.get("fcfYield"),
            "divYield": data.get("divYield"),
            "aggregateScore": sum(s["total"] for s in scores.values()),
        }
    except Exception as exc:
        return {"ticker": ticker, "error": str(exc)}


def peer_analysis(data):
    target = {
        "ticker": data.get("ticker"),
        "name": data.get("name"),
        "price": data.get("price"),
        "pe": data.get("pe"),
        "fwdPE": data.get("fwdPE"),
        "peg": data.get("peg") or data.get("pegVal"),
        "roe": data.get("roe"),
        "revGrowth": data.get("revGrowth"),
        "fcfYield": data.get("fcfYield"),
        "divYield": data.get("divYield"),
    }
    tickers = infer_peers(data)
    peers = [safe_fetch_peer(t) for t in tickers]
    rows = [target] + peers

    comparable = [r for r in rows if not r.get("error")]
    averages = {}
    for key in ["pe", "fwdPE", "peg", "roe", "revGrowth", "fcfYield", "divYield"]:
        vals = [clean_float(r.get(key)) for r in comparable]
        vals = [v for v in vals if v is not None]
        averages[key] = sum(vals) / len(vals) if vals else None

    return {
        "peers": rows,
        "averages": averages,
        "peerTickers": tickers,
    }


def price_chart(ticker):
    try:
        hist = fetch_price_history(ticker, period="2y")
        if hist is None or len(hist) == 0:
            return []
        # Resample to about 80 points for the browser.
        step = max(1, len(hist) // 80)
        sampled = hist.iloc[::step]
        if sampled.index[-1] != hist.index[-1]:
            sampled = sampled._append(hist.iloc[-1:])
        return [
            {"date": idx.strftime("%Y-%m-%d"), "close": round(float(val), 2)}
            for idx, val in sampled.items()
            if clean_float(val) is not None
        ]
    except Exception:
        return []


def strengths_and_risks(scores, data, peers):
    strengths = []
    risks = []
    for model_key, result in scores.items():
        for row in result["breakdown"]:
            if not row["scored"]:
                continue
            label = row["label"]
            value = row["value"]
            if row["points"] == 2:
                strengths.append(f"{label}: {value:.2f}" if isinstance(value, float) else f"{label}: {value}")
            elif row["points"] == 0:
                risks.append(f"{label}: {value:.2f}" if isinstance(value, float) else f"{label}: {value}")

    avg_pe = peers.get("averages", {}).get("pe")
    pe = clean_float(data.get("pe"))
    if avg_pe and pe and pe > avg_pe * 1.2:
        risks.append(f"P/E is above peer average ({pe:.1f} vs {avg_pe:.1f}).")
    if avg_pe and pe and pe < avg_pe * 0.8:
        strengths.append(f"P/E is below peer average ({pe:.1f} vs {avg_pe:.1f}).")

    if clean_float(data.get("divYield")) is not None and clean_float(data.get("divYield")) < 1:
        risks.append("Dividend yield is too low for a true income-stock mandate.")
    if clean_float(data.get("fcfYield")) is not None and clean_float(data.get("fcfYield")) >= 5:
        strengths.append("Free cash flow yield clears the 5% value threshold.")

    return {"strengths": strengths[:6], "risks": risks[:6]}


def api_payload(ticker):
    data = fetch_ticker_data(ticker)
    scores = {k: score_model(k, data) for k in MODELS}
    summary = model_summary(scores)
    peer = peer_analysis(data)
    scores_flat = {k: v["total"] for k, v in scores.items()}
    pred = predict(scores_flat, summary["bestModel"], summary["aggregateScore"])

    try:
        eval_id = log_evaluation(data, scores, summary["recommendation"], summary["bestModel"])
        refresh_followups()
    except Exception:
        eval_id = None

    return {
        "ok": True,
        "evaluationId": eval_id,
        "data": data,
        "scores": scores,
        "summary": summary,
        "valuations": valuation_methods(data),
        "peers": peer,
        "chart": price_chart(ticker),
        "insights": strengths_and_risks(scores, data, peer),
        "predictor": pred,
        "trackRecord": get_track_record(),
        "thresholds": THRESHOLDS,
        "models": MODELS,
    }


class Handler(SimpleHTTPRequestHandler):
    def end_headers(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        super().end_headers()

    def do_OPTIONS(self):
        self.send_response(204)
        self.end_headers()

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/api/evaluate":
            params = parse_qs(parsed.query)
            ticker = (params.get("ticker") or [""])[0].strip().upper()
            if not ticker:
                self.write_json({"ok": False, "error": "Enter a ticker."}, status=400)
                return
            try:
                self.write_json(api_payload(ticker))
            except Exception as exc:
                self.write_json({"ok": False, "error": str(exc)}, status=500)
            return
        if parsed.path == "/api/health":
            self.write_json({"ok": True, "time": datetime.now().isoformat()})
            return
        return super().do_GET()

    def write_json(self, payload, status=200):
        body = json.dumps(payload, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def main():
    init_db()
    os.chdir(HERE)
    port = int(os.environ.get("PORT") or os.environ.get("STOCK_APP_PORT", "8765"))
    host = os.environ.get("HOST", "127.0.0.1")
    server = ThreadingHTTPServer((host, port), Handler)
    shown_host = "127.0.0.1" if host in ("0.0.0.0", "") else host
    print(f"Stock evaluator running at http://{shown_host}:{port}/stock_evaluator.html")
    server.serve_forever()


if __name__ == "__main__":
    main()
