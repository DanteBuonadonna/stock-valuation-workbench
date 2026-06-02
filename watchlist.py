"""
Watchlist + alerts.

Real data only. Stores user-selected tickers and per-ticker alert rules
in SQLite. When `check_all()` runs, it fetches live data via yfinance and
generates an alert row whenever a rule trips. Alerts persist so the UI
can mark them acknowledged.

Supported alert types (per ticker, all optional):
- dailyMovePct: trigger if abs(daily % change) >= threshold
- weeklyMovePct: trigger if abs(5-day % change) >= threshold
- priceAbove: trigger when price >= target
- priceBelow: trigger when price <= target
- touch52High: trigger when price >= 52-week high
- touch52Low: trigger when price <= 52-week low
- scoreChange: trigger if aggregate score moves by >= N points vs baseline
"""
from __future__ import annotations
import json
import os
import sqlite3
from datetime import datetime, timedelta
from typing import Optional

HERE = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(HERE, "evaluations.db")


def _conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_watchlist():
    conn = _conn()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS watchlist (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ticker TEXT NOT NULL UNIQUE,
            added_at TEXT NOT NULL,
            baseline_price REAL,
            baseline_aggregate INTEGER,
            note TEXT,
            rules_json TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS watchlist_alerts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            watch_id INTEGER NOT NULL,
            ticker TEXT NOT NULL,
            triggered_at TEXT NOT NULL,
            alert_type TEXT NOT NULL,
            value REAL,
            threshold REAL,
            message TEXT NOT NULL,
            acknowledged INTEGER DEFAULT 0,
            FOREIGN KEY (watch_id) REFERENCES watchlist(id)
        );
    """)
    conn.commit()
    conn.close()


def _safe_float(v):
    try:
        if v is None:
            return None
        v = float(v)
        if v != v:  # NaN
            return None
        return v
    except Exception:
        return None


def list_watchlist():
    init_watchlist()
    conn = _conn()
    rows = conn.execute("SELECT * FROM watchlist ORDER BY ticker ASC").fetchall()
    conn.close()
    return [
        {
            "id": r["id"],
            "ticker": r["ticker"],
            "added_at": r["added_at"],
            "baseline_price": r["baseline_price"],
            "baseline_aggregate": r["baseline_aggregate"],
            "note": r["note"],
            "rules": json.loads(r["rules_json"] or "{}"),
        }
        for r in rows
    ]


def add_to_watchlist(ticker: str, rules: dict, note: Optional[str] = None) -> dict:
    """
    Add a ticker. We fetch live data once to capture a baseline price + score
    so future change-since-added comparisons are honest.
    """
    init_watchlist()
    ticker = (ticker or "").strip().upper()
    if not ticker:
        raise ValueError("Ticker required.")

    baseline_price = None
    baseline_aggregate = None
    error = None
    try:
        from data_fetcher import fetch_ticker_data
        from evaluator_engine import MODELS, score_model
        data = fetch_ticker_data(ticker)
        baseline_price = _safe_float(data.get("price"))
        scores = {k: score_model(k, data) for k in MODELS}
        baseline_aggregate = sum(s["total"] for s in scores.values())
    except Exception as exc:
        # We still allow adding; alerts that need baseline will note the gap.
        error = str(exc)

    conn = _conn()
    try:
        conn.execute(
            """
            INSERT INTO watchlist (ticker, added_at, baseline_price, baseline_aggregate, note, rules_json)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(ticker) DO UPDATE SET
                rules_json = excluded.rules_json,
                note = COALESCE(excluded.note, watchlist.note)
            """,
            (ticker, datetime.now().isoformat(), baseline_price, baseline_aggregate, note, json.dumps(rules)),
        )
        conn.commit()
    finally:
        conn.close()
    return {"ok": True, "ticker": ticker, "baseline_price": baseline_price,
            "baseline_aggregate": baseline_aggregate, "fetch_error": error}


def remove_from_watchlist(watch_id: int) -> dict:
    init_watchlist()
    conn = _conn()
    conn.execute("DELETE FROM watchlist_alerts WHERE watch_id = ?", (watch_id,))
    conn.execute("DELETE FROM watchlist WHERE id = ?", (watch_id,))
    conn.commit()
    conn.close()
    return {"ok": True}


def update_rules(watch_id: int, rules: dict, note: Optional[str] = None) -> dict:
    init_watchlist()
    conn = _conn()
    if note is not None:
        conn.execute(
            "UPDATE watchlist SET rules_json = ?, note = ? WHERE id = ?",
            (json.dumps(rules), note, watch_id),
        )
    else:
        conn.execute("UPDATE watchlist SET rules_json = ? WHERE id = ?", (json.dumps(rules), watch_id))
    conn.commit()
    conn.close()
    return {"ok": True}


def _fetch_live(ticker: str) -> dict:
    """Pull current price, 5d ago price, 52w hi/lo, and current aggregate score."""
    out = {"ticker": ticker, "price": None, "price5d_ago": None,
           "hi52": None, "lo52": None, "aggregate": None, "error": None}
    try:
        import yfinance as yf
        from data_fetcher import fetch_ticker_data
        from evaluator_engine import MODELS, score_model

        # Live price + 52w range
        t = yf.Ticker(ticker)
        hist = t.history(period="6mo", auto_adjust=True)
        if hist is not None and not hist.empty:
            out["price"] = float(hist["Close"].iloc[-1])
            out["hi52"] = float(hist["High"].max())
            out["lo52"] = float(hist["Low"].min())
            if len(hist) >= 6:
                out["price5d_ago"] = float(hist["Close"].iloc[-6])

        # Score
        data = fetch_ticker_data(ticker)
        scores = {k: score_model(k, data) for k in MODELS}
        out["aggregate"] = sum(s["total"] for s in scores.values())
    except Exception as exc:
        out["error"] = str(exc)
    return out


def _evaluate_rules(item: dict, live: dict, last_close: Optional[float]) -> list:
    """Compare current live data to rules; return any triggered alerts as dicts."""
    triggered = []
    rules = item.get("rules") or {}
    ticker = item["ticker"]
    price = live.get("price")
    if price is None:
        return triggered

    # Daily move
    th = _safe_float(rules.get("dailyMovePct"))
    if th and last_close and last_close > 0:
        move = (price / last_close - 1) * 100
        if abs(move) >= th:
            triggered.append({
                "alert_type": "dailyMovePct", "value": round(move, 2), "threshold": th,
                "message": f"{ticker} moved {move:+.2f}% in the last session (threshold ±{th}%).",
            })

    # 5-day move
    th = _safe_float(rules.get("weeklyMovePct"))
    if th and live.get("price5d_ago"):
        move = (price / live["price5d_ago"] - 1) * 100
        if abs(move) >= th:
            triggered.append({
                "alert_type": "weeklyMovePct", "value": round(move, 2), "threshold": th,
                "message": f"{ticker} moved {move:+.2f}% over the last 5 sessions (threshold ±{th}%).",
            })

    # Price above
    th = _safe_float(rules.get("priceAbove"))
    if th and price >= th:
        triggered.append({
            "alert_type": "priceAbove", "value": round(price, 2), "threshold": th,
            "message": f"{ticker} crossed above ${th:.2f} (now ${price:.2f}).",
        })

    # Price below
    th = _safe_float(rules.get("priceBelow"))
    if th and price <= th:
        triggered.append({
            "alert_type": "priceBelow", "value": round(price, 2), "threshold": th,
            "message": f"{ticker} fell below ${th:.2f} (now ${price:.2f}).",
        })

    # 52w high/low touch (allow tiny rounding buffer)
    if rules.get("touch52High") and live.get("hi52") and price >= live["hi52"] * 0.999:
        triggered.append({
            "alert_type": "touch52High", "value": round(price, 2), "threshold": round(live["hi52"], 2),
            "message": f"{ticker} touched its 6-month high zone at ${price:.2f}.",
        })
    if rules.get("touch52Low") and live.get("lo52") and price <= live["lo52"] * 1.001:
        triggered.append({
            "alert_type": "touch52Low", "value": round(price, 2), "threshold": round(live["lo52"], 2),
            "message": f"{ticker} touched its 6-month low zone at ${price:.2f}.",
        })

    # Score change vs baseline
    th = _safe_float(rules.get("scoreChange"))
    if th and item.get("baseline_aggregate") is not None and live.get("aggregate") is not None:
        delta = live["aggregate"] - item["baseline_aggregate"]
        if abs(delta) >= th:
            direction = "improved" if delta > 0 else "fell"
            triggered.append({
                "alert_type": "scoreChange", "value": delta, "threshold": th,
                "message": f"{ticker} aggregate score {direction} by {abs(delta)} points "
                           f"(baseline {item['baseline_aggregate']} → now {live['aggregate']}).",
            })

    return triggered


def _last_close(ticker: str) -> Optional[float]:
    """Closing price from the second-to-last bar (i.e., previous session close)."""
    try:
        import yfinance as yf
        hist = yf.Ticker(ticker).history(period="5d", auto_adjust=True)
        if hist is not None and len(hist) >= 2:
            return float(hist["Close"].iloc[-2])
    except Exception:
        pass
    return None


def check_all(dedupe_hours: int = 12) -> dict:
    """
    Scan all watchlist tickers, generate any new alerts.
    Dedupes by (ticker, alert_type) over the last `dedupe_hours` so the same
    alert doesn't spam the user every page load.
    """
    init_watchlist()
    items = list_watchlist()
    if not items:
        return {"ok": True, "scanned": 0, "triggered": [], "errors": []}

    new_alerts = []
    errors = []
    conn = _conn()
    cutoff = (datetime.now() - timedelta(hours=dedupe_hours)).isoformat()

    for it in items:
        ticker = it["ticker"]
        try:
            live = _fetch_live(ticker)
            if live.get("error"):
                errors.append({"ticker": ticker, "error": live["error"]})
                continue
            last_close = _last_close(ticker)
            triggered = _evaluate_rules(it, live, last_close)
            for alert in triggered:
                # Dedupe
                exists = conn.execute(
                    """SELECT id FROM watchlist_alerts
                       WHERE ticker = ? AND alert_type = ? AND triggered_at >= ?""",
                    (ticker, alert["alert_type"], cutoff),
                ).fetchone()
                if exists:
                    continue
                cur = conn.execute(
                    """INSERT INTO watchlist_alerts
                       (watch_id, ticker, triggered_at, alert_type, value, threshold, message)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (it["id"], ticker, datetime.now().isoformat(),
                     alert["alert_type"], alert.get("value"), alert.get("threshold"),
                     alert["message"]),
                )
                new_alerts.append({
                    "id": cur.lastrowid,
                    "ticker": ticker,
                    "alert_type": alert["alert_type"],
                    "value": alert.get("value"),
                    "threshold": alert.get("threshold"),
                    "message": alert["message"],
                    "triggered_at": datetime.now().isoformat(),
                })
        except Exception as exc:
            errors.append({"ticker": ticker, "error": str(exc)})
    conn.commit()
    conn.close()
    return {"ok": True, "scanned": len(items), "triggered": new_alerts, "errors": errors}


def list_alerts(unacknowledged_only: bool = False, limit: int = 100) -> list:
    init_watchlist()
    conn = _conn()
    q = "SELECT * FROM watchlist_alerts"
    if unacknowledged_only:
        q += " WHERE acknowledged = 0"
    q += " ORDER BY triggered_at DESC LIMIT ?"
    rows = conn.execute(q, (limit,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def acknowledge_alert(alert_id: int) -> dict:
    init_watchlist()
    conn = _conn()
    conn.execute("UPDATE watchlist_alerts SET acknowledged = 1 WHERE id = ?", (alert_id,))
    conn.commit()
    conn.close()
    return {"ok": True}


def acknowledge_all() -> dict:
    init_watchlist()
    conn = _conn()
    conn.execute("UPDATE watchlist_alerts SET acknowledged = 1 WHERE acknowledged = 0")
    conn.commit()
    conn.close()
    return {"ok": True}
