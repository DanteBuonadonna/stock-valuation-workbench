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

# New modules
import watchlist as wl  # noqa: E402
from macro import get_macro_snapshot, macro_context_for_stock  # noqa: E402
from events import get_events  # noqa: E402
from tax_tags import tag_security  # noqa: E402
from income_proj import project_income  # noqa: E402
from notes_store import get_notes, init_notes, save_notes  # noqa: E402
from portfolio_fit import fit_check  # noqa: E402
from memo_generator import generate_memo, load_branding  # noqa: E402
import crm  # noqa: E402
from news import get_news  # noqa: E402
import briefing  # noqa: E402

# Light in-process cache for the most recent payload per ticker — used by /api/memo and
# /api/portfolio so we don't re-fetch on top of the eval the user just ran.
_PAYLOAD_CACHE = {}


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


def dcf_base(data):
    total_debt = clean_float(data.get("totalDebt"))
    total_cash = clean_float(data.get("totalCash"))
    net_debt = None
    if total_debt is not None or total_cash is not None:
        net_debt = (total_debt or 0) - (total_cash or 0)
    return {
        "price": clean_float(data.get("price")),
        "freeCashflow": clean_float(data.get("freeCashflow")),
        "sharesOutstanding": clean_float(data.get("sharesOutstanding")),
        "totalDebt": total_debt,
        "totalCash": total_cash,
        "netDebt": net_debt,
        "source": "Yahoo Finance via yfinance",
        "assumptionsRequired": [
            "5-year FCF growth rate",
            "terminal growth rate",
            "discount rate / required return",
        ],
    }


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
        hist = fetch_price_history(ticker, period="5y")
        if hist is None or len(hist) == 0:
            return []
        # Resample to about 260 points for the browser while keeping the last close.
        step = max(1, len(hist) // 260)
        sampled = hist.iloc[::step]
        if sampled.index[-1] != hist.index[-1]:
            import pandas as pd
            sampled = pd.concat([sampled, hist.iloc[-1:]])
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

    # New enrichments — all real data; degrade gracefully on per-feature error.
    try:
        ticker_news = get_news(ticker, limit=15)
    except Exception as exc:
        ticker_news = {"items": [], "error": str(exc)}
    try:
        clients_who_hold = crm.clients_holding(ticker)
    except Exception:
        clients_who_hold = []
    try:
        client_eval_history = crm.evaluations_for_ticker(ticker)
    except Exception:
        client_eval_history = []
    try:
        all_clients = crm.list_clients()
    except Exception:
        all_clients = []
    try:
        tax_info = tag_security(data)
    except Exception:
        tax_info = {"tags": [], "notes": []}
    try:
        evt = get_events(ticker)
    except Exception as exc:
        evt = {"error": str(exc)}
    try:
        macro_ctx = macro_context_for_stock(data)
    except Exception as exc:
        macro_ctx = {"snapshot": {}, "comparisons": [], "error": str(exc)}
    try:
        ticker_notes = get_notes(ticker)
    except Exception:
        ticker_notes = {"ticker": ticker, "research_notes": "", "meeting_talking_points": "", "updated_at": None}
    try:
        income = project_income(data)
    except Exception as exc:
        income = {"available": False, "reason": str(exc)}
    try:
        in_watchlist = next((w for w in wl.list_watchlist() if w["ticker"] == ticker), None)
    except Exception:
        in_watchlist = None

    return {
        "ok": True,
        "evaluationId": eval_id,
        "data": data,
        "scores": scores,
        "summary": summary,
        "valuations": valuation_methods(data),
        "dcf": dcf_base(data),
        "peers": peer,
        "chart": price_chart(ticker),
        "insights": strengths_and_risks(scores, data, peer),
        "predictor": pred,
        "trackRecord": get_track_record(),
        "thresholds": THRESHOLDS,
        "models": MODELS,
        # New
        "taxTags": tax_info,
        "events": evt,
        "macroContext": macro_ctx,
        "notes": ticker_notes,
        "incomeProjection": income,
        "watchlistEntry": in_watchlist,
        "news": ticker_news,
        "clientsHolding": clients_who_hold,
        "clientEvalHistory": client_eval_history,
        "allClients": all_clients,
    }


class Handler(SimpleHTTPRequestHandler):
    def end_headers(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        super().end_headers()

    def do_OPTIONS(self):
        self.send_response(204)
        self.end_headers()

    def _read_json_body(self):
        length = int(self.headers.get("Content-Length", "0") or 0)
        if length <= 0:
            return {}
        raw = self.rfile.read(length)
        try:
            return json.loads(raw.decode("utf-8"))
        except Exception:
            return {}

    def do_GET(self):
        parsed = urlparse(self.path)
        params = parse_qs(parsed.query)

        if parsed.path in ("", "/"):
            self.send_response(302)
            self.send_header("Location", "/stock_evaluator.html")
            self.end_headers()
            return

        if parsed.path == "/api/evaluate":
            ticker = (params.get("ticker") or [""])[0].strip().upper()
            if not ticker:
                self.write_json({"ok": False, "error": "Enter a ticker."}, status=400)
                return
            try:
                payload = api_payload(ticker)
                _PAYLOAD_CACHE[ticker] = payload
                self.write_json(payload)
            except Exception as exc:
                self.write_json({"ok": False, "error": str(exc)}, status=500)
            return

        if parsed.path == "/api/memo":
            ticker = (params.get("ticker") or [""])[0].strip().upper()
            client_name = (params.get("client") or [""])[0].strip()
            if not ticker:
                self.write_json({"ok": False, "error": "Ticker required."}, status=400)
                return
            try:
                payload = _PAYLOAD_CACHE.get(ticker) or api_payload(ticker)
                pdf_bytes = generate_memo(payload, client_name or None)
                self.send_response(200)
                self.send_header("Content-Type", "application/pdf")
                self.send_header("Content-Disposition",
                                  f'inline; filename="memo_{ticker}_{datetime.now().strftime("%Y%m%d")}.pdf"')
                self.send_header("Content-Length", str(len(pdf_bytes)))
                self.end_headers()
                self.wfile.write(pdf_bytes)
            except Exception as exc:
                self.write_json({"ok": False, "error": str(exc)}, status=500)
            return

        if parsed.path == "/api/branding":
            try:
                self.write_json({"ok": True, "branding": load_branding()})
            except Exception as exc:
                self.write_json({"ok": False, "error": str(exc)}, status=500)
            return

        if parsed.path == "/api/health":
            self.write_json({"ok": True, "time": datetime.now().isoformat()})
            return

        # --- Watchlist read endpoints
        if parsed.path == "/api/watchlist":
            try:
                items = wl.list_watchlist()
                # Enrich with current price for each (best-effort)
                for it in items:
                    try:
                        hist = fetch_price_history(it["ticker"], period="5d")
                        if hist is not None and len(hist) > 0:
                            it["current_price"] = float(hist.iloc[-1])
                            if it.get("baseline_price"):
                                it["change_since_added_pct"] = round(
                                    (it["current_price"] / it["baseline_price"] - 1) * 100, 2
                                )
                    except Exception:
                        it["current_price"] = None
                self.write_json({"ok": True, "items": items})
            except Exception as exc:
                self.write_json({"ok": False, "error": str(exc)}, status=500)
            return

        if parsed.path == "/api/alerts":
            try:
                only_unack = (params.get("unacked_only") or ["0"])[0] in ("1", "true", "yes")
                self.write_json({"ok": True, "alerts": wl.list_alerts(only_unack)})
            except Exception as exc:
                self.write_json({"ok": False, "error": str(exc)}, status=500)
            return

        if parsed.path == "/api/macro":
            try:
                self.write_json({"ok": True, "snapshot": get_macro_snapshot()})
            except Exception as exc:
                self.write_json({"ok": False, "error": str(exc)}, status=500)
            return

        if parsed.path == "/api/events":
            ticker = (params.get("ticker") or [""])[0].strip().upper()
            if not ticker:
                self.write_json({"ok": False, "error": "Ticker required."}, status=400)
                return
            try:
                self.write_json({"ok": True, "events": get_events(ticker)})
            except Exception as exc:
                self.write_json({"ok": False, "error": str(exc)}, status=500)
            return

        if parsed.path == "/api/notes":
            ticker = (params.get("ticker") or [""])[0].strip().upper()
            if not ticker:
                self.write_json({"ok": False, "error": "Ticker required."}, status=400)
                return
            try:
                self.write_json({"ok": True, "notes": get_notes(ticker)})
            except Exception as exc:
                self.write_json({"ok": False, "error": str(exc)}, status=500)
            return

        if parsed.path == "/api/news":
            ticker = (params.get("ticker") or [""])[0].strip().upper()
            if not ticker:
                self.write_json({"ok": False, "error": "Ticker required."}, status=400)
                return
            try:
                self.write_json({"ok": True, **get_news(ticker)})
            except Exception as exc:
                self.write_json({"ok": False, "error": str(exc)}, status=500)
            return

        if parsed.path == "/api/clients":
            try:
                self.write_json({"ok": True, "clients": crm.list_clients()})
            except Exception as exc:
                self.write_json({"ok": False, "error": str(exc)}, status=500)
            return

        if parsed.path == "/api/clients/get":
            try:
                cid = int((params.get("id") or ["0"])[0])
                c = crm.get_client(cid)
                if not c:
                    self.write_json({"ok": False, "error": "Client not found."}, status=404)
                    return
                self.write_json({"ok": True, "client": c})
            except Exception as exc:
                self.write_json({"ok": False, "error": str(exc)}, status=500)
            return

        if parsed.path == "/api/briefing/latest":
            try:
                latest = briefing.get_latest_briefing()
                self.write_json({"ok": True, "briefing": latest})
            except Exception as exc:
                self.write_json({"ok": False, "error": str(exc)}, status=500)
            return

        if parsed.path == "/api/clients/by_ticker":
            try:
                ticker = (params.get("ticker") or [""])[0].strip().upper()
                self.write_json({
                    "ok": True,
                    "holding": crm.clients_holding(ticker),
                    "evaluations": crm.evaluations_for_ticker(ticker),
                })
            except Exception as exc:
                self.write_json({"ok": False, "error": str(exc)}, status=500)
            return

        if parsed.path == "/api/income":
            ticker = (params.get("ticker") or [""])[0].strip().upper()
            if not ticker:
                self.write_json({"ok": False, "error": "Ticker required."}, status=400)
                return
            try:
                position = float((params.get("position") or ["100000"])[0])
            except Exception:
                position = 100000.0
            try:
                growth = (params.get("growth") or [None])[0]
                growth = float(growth) if growth is not None else None
            except Exception:
                growth = None
            try:
                data = fetch_ticker_data(ticker)
                self.write_json({"ok": True, "projection": project_income(data, position, 5, growth)})
            except Exception as exc:
                self.write_json({"ok": False, "error": str(exc)}, status=500)
            return

        return super().do_GET()

    def do_POST(self):
        parsed = urlparse(self.path)
        body = self._read_json_body()

        # --- Watchlist write endpoints
        if parsed.path == "/api/watchlist/add":
            try:
                result = wl.add_to_watchlist(
                    body.get("ticker", ""),
                    body.get("rules") or {},
                    body.get("note"),
                )
                self.write_json(result)
            except Exception as exc:
                self.write_json({"ok": False, "error": str(exc)}, status=500)
            return

        if parsed.path == "/api/watchlist/update":
            try:
                self.write_json(wl.update_rules(
                    int(body.get("id")),
                    body.get("rules") or {},
                    body.get("note"),
                ))
            except Exception as exc:
                self.write_json({"ok": False, "error": str(exc)}, status=500)
            return

        if parsed.path == "/api/watchlist/remove":
            try:
                self.write_json(wl.remove_from_watchlist(int(body.get("id"))))
            except Exception as exc:
                self.write_json({"ok": False, "error": str(exc)}, status=500)
            return

        if parsed.path == "/api/watchlist/check":
            try:
                self.write_json(wl.check_all())
            except Exception as exc:
                self.write_json({"ok": False, "error": str(exc)}, status=500)
            return

        # --- Alerts
        if parsed.path == "/api/alerts/ack":
            try:
                if body.get("all"):
                    self.write_json(wl.acknowledge_all())
                else:
                    self.write_json(wl.acknowledge_alert(int(body.get("id"))))
            except Exception as exc:
                self.write_json({"ok": False, "error": str(exc)}, status=500)
            return

        # --- Briefing
        if parsed.path == "/api/briefing/generate":
            try:
                self.write_json({"ok": True, "briefing": briefing.generate_briefing()})
            except Exception as exc:
                self.write_json({"ok": False, "error": str(exc)}, status=500)
            return
        if parsed.path == "/api/briefing/item_status":
            try:
                self.write_json(briefing.set_item_status(
                    body.get("item_key", ""),
                    body.get("status", "done"),
                ))
            except Exception as exc:
                self.write_json({"ok": False, "error": str(exc)}, status=500)
            return

        # --- CRM endpoints
        if parsed.path == "/api/clients/upsert":
            try:
                self.write_json({"ok": True, "client": crm.upsert_client(body)})
            except Exception as exc:
                self.write_json({"ok": False, "error": str(exc)}, status=500)
            return
        if parsed.path == "/api/clients/delete":
            try:
                self.write_json(crm.delete_client(int(body.get("id"))))
            except Exception as exc:
                self.write_json({"ok": False, "error": str(exc)}, status=500)
            return
        if parsed.path == "/api/clients/holding/add":
            try:
                self.write_json({"ok": True, "client": crm.add_holding(
                    int(body.get("client_id")), body.get("ticker", ""),
                    float(body.get("shares") or 0),
                    body.get("cost_basis"), body.get("purchase_date"), body.get("notes")
                )})
            except Exception as exc:
                self.write_json({"ok": False, "error": str(exc)}, status=500)
            return
        if parsed.path == "/api/clients/holding/remove":
            try:
                self.write_json({"ok": True, "client": crm.remove_holding(int(body.get("id")))})
            except Exception as exc:
                self.write_json({"ok": False, "error": str(exc)}, status=500)
            return
        if parsed.path == "/api/clients/tag_eval":
            try:
                self.write_json({"ok": True, "client": crm.tag_evaluation(
                    int(body.get("client_id")), body.get("ticker", ""),
                    body.get("evaluation_id"),
                    body.get("recommendation"),
                    body.get("recommended_size_pct"),
                    body.get("status") or "considering",
                    body.get("advisor_notes"),
                )})
            except Exception as exc:
                self.write_json({"ok": False, "error": str(exc)}, status=500)
            return

        # --- Portfolio fit check
        if parsed.path == "/api/portfolio/check":
            try:
                ticker = (body.get("ticker") or "").strip().upper()
                csv_text = body.get("csv") or ""
                pv = body.get("portfolio_value")
                if not ticker:
                    self.write_json({"ok": False, "error": "Ticker required."}, status=400)
                    return
                payload = _PAYLOAD_CACHE.get(ticker) or api_payload(ticker)
                aggregate = payload.get("summary", {}).get("aggregateScore", 0)
                self.write_json(fit_check(payload.get("data", {}), aggregate, csv_text, pv))
            except Exception as exc:
                self.write_json({"ok": False, "error": str(exc)}, status=500)
            return

        # --- Notes
        if parsed.path == "/api/notes":
            try:
                self.write_json({"ok": True, "notes": save_notes(
                    body.get("ticker", ""),
                    body.get("research_notes"),
                    body.get("meeting_talking_points"),
                )})
            except Exception as exc:
                self.write_json({"ok": False, "error": str(exc)}, status=500)
            return

        self.write_json({"ok": False, "error": f"Unknown POST endpoint {parsed.path}"}, status=404)

    def write_json(self, payload, status=200):
        body = json.dumps(payload, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def main():
    init_db()
    wl.init_watchlist()
    init_notes()
    crm.init_crm()
    os.chdir(HERE)
    port = int(os.environ.get("PORT") or os.environ.get("STOCK_APP_PORT", "8765"))
    # On cloud hosts (Render, Railway, Fly) the PORT env var is set by the platform
    # and we need to bind to all interfaces (0.0.0.0). Locally we stick to 127.0.0.1.
    default_host = "0.0.0.0" if os.environ.get("PORT") else "127.0.0.1"
    host = os.environ.get("HOST", default_host)
    server = ThreadingHTTPServer((host, port), Handler)
    shown_host = "127.0.0.1" if host in ("0.0.0.0", "") else host
    print(f"Stock evaluator running at http://{shown_host}:{port}/stock_evaluator.html")
    server.serve_forever()


if __name__ == "__main__":
    main()
