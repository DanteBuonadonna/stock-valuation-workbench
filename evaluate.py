"""
Main entry point — evaluate any ticker.

Usage:
    python3 evaluate.py NVDA
    python3 evaluate.py KO --no-open
    python3 evaluate.py AAPL --json data.json   (override with manual JSON data)
"""
from __future__ import annotations
import argparse
import os
import sys
import json
import sqlite3
import subprocess
import webbrowser
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from evaluator_engine import render_report, score_model, MODELS, tier  # noqa: E402

DB_PATH = os.path.join(HERE, "evaluations.db")


# ---------- SQLite log ----------
def init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS evaluations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ticker TEXT NOT NULL,
            eval_date TEXT NOT NULL,
            price REAL,
            growth_score INTEGER,
            dividend_score INTEGER,
            valgrowth_score INTEGER,
            overall_rec TEXT,
            best_model TEXT,
            data_json TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS price_followups (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            evaluation_id INTEGER NOT NULL,
            checked_at TEXT NOT NULL,
            days_after INTEGER,
            price REAL,
            return_pct REAL,
            FOREIGN KEY (evaluation_id) REFERENCES evaluations(id)
        )
    """)
    conn.commit()
    return conn


def log_evaluation(data: dict, scores: dict, overall_rec: str, best_model: str) -> int:
    conn = init_db()
    cur = conn.execute(
        """
        INSERT INTO evaluations (ticker, eval_date, price, growth_score, dividend_score,
                                  valgrowth_score, overall_rec, best_model, data_json)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            data.get("ticker"),
            datetime.now().isoformat(),
            data.get("price"),
            scores["growth"]["total"],
            scores["dividend"]["total"],
            scores["valGrowth"]["total"],
            overall_rec,
            best_model,
            json.dumps(data, default=str),
        ),
    )
    conn.commit()
    eval_id = cur.lastrowid
    conn.close()
    return eval_id


def refresh_followups():
    """For every past evaluation in the DB, fetch current price and compute realized return."""
    try:
        from data_fetcher import fetch_price_history
    except ImportError:
        return
    conn = init_db()
    rows = conn.execute(
        "SELECT id, ticker, eval_date, price FROM evaluations WHERE price IS NOT NULL"
    ).fetchall()
    for eval_id, ticker, eval_date, price in rows:
        if not price or price <= 0:
            continue
        try:
            hist = fetch_price_history(ticker, period="2y")
            if hist is None or len(hist) == 0:
                continue
            current_price = float(hist.iloc[-1])
            eval_dt = datetime.fromisoformat(eval_date)
            days = (datetime.now() - eval_dt).days
            ret = (current_price / price - 1) * 100
            conn.execute(
                """
                INSERT INTO price_followups (evaluation_id, checked_at, days_after, price, return_pct)
                VALUES (?, ?, ?, ?, ?)
                """,
                (eval_id, datetime.now().isoformat(), days, current_price, ret),
            )
        except Exception:
            continue
    conn.commit()
    conn.close()


def get_track_record(conn=None) -> dict:
    """Pull the user's own track record from the DB to surface in the report."""
    own_conn = False
    if conn is None:
        conn = init_db()
        own_conn = True
    rows = conn.execute(
        """
        SELECT e.ticker, e.eval_date, e.overall_rec, e.best_model,
               e.growth_score, e.dividend_score, e.valgrowth_score, e.price,
               f.return_pct, f.days_after
        FROM evaluations e
        LEFT JOIN (
            SELECT evaluation_id, return_pct, days_after,
                   ROW_NUMBER() OVER (PARTITION BY evaluation_id ORDER BY checked_at DESC) rn
            FROM price_followups
        ) f ON f.evaluation_id = e.id AND f.rn = 1
        ORDER BY e.eval_date DESC LIMIT 50
        """
    ).fetchall()
    if own_conn:
        conn.close()
    return [
        {
            "ticker": r[0],
            "date": r[1][:10] if r[1] else "—",
            "rec": r[2],
            "best_model": r[3],
            "scores": {"growth": r[4], "dividend": r[5], "valGrowth": r[6]},
            "price_at_eval": r[7],
            "return_pct": r[8],
            "days_after": r[9],
        }
        for r in rows
    ]


# ---------- MAIN ----------
def main():
    parser = argparse.ArgumentParser(description="Three-Model Stock Evaluator")
    parser.add_argument("ticker", help="Stock ticker (e.g. NVDA, AAPL, KO)")
    parser.add_argument("--json", help="Path to manual JSON data file (skips yfinance fetch)")
    parser.add_argument("--no-open", action="store_true", help="Don't auto-open the report")
    parser.add_argument("--out-dir", default=HERE, help="Where to write the report")
    args = parser.parse_args()

    ticker = args.ticker.upper().strip()
    print(f"\n▸ Evaluating {ticker}…\n")

    # Load or fetch data
    if args.json:
        with open(args.json) as f:
            data = json.load(f)
        data.setdefault("ticker", ticker)
    else:
        try:
            from data_fetcher import fetch_ticker_data
        except ImportError:
            print("ERROR: yfinance not installed. Install with:")
            print("    python3 -m pip install --user yfinance pandas")
            sys.exit(2)
        try:
            data = fetch_ticker_data(ticker)
        except Exception as e:
            print(f"ERROR fetching {ticker}: {e}")
            sys.exit(3)

    # Score
    scores = {k: score_model(k, data) for k in MODELS}
    overall_score = sum(r["total"] for r in scores.values())
    pct_avg = overall_score / 30
    if pct_avg >= 0.75:
        overall_rec = "BUY"
    elif pct_avg >= 0.50:
        overall_rec = "HOLD"
    else:
        overall_rec = "PASS"
    best_model = max(scores, key=lambda k: scores[k]["total"])

    # Log
    eval_id = log_evaluation(data, scores, overall_rec, best_model)
    print(f"  Growth: {scores['growth']['total']}/10")
    print(f"  Dividend: {scores['dividend']['total']}/10")
    print(f"  Value+Growth: {scores['valGrowth']['total']}/10")
    print(f"  → {overall_rec} (logged as evaluation #{eval_id})\n")

    # Refresh price follow-ups so the track record stays current, then pull it
    try:
        refresh_followups()
    except Exception:
        pass
    track_record = get_track_record()

    # Generate report
    html = render_report(data, track_record=track_record)
    out_path = os.path.join(
        args.out_dir,
        f"evaluation_{ticker}_{datetime.now().strftime('%Y-%m-%d')}.html",
    )
    with open(out_path, "w") as f:
        f.write(html)
    print(f"Report: {out_path}\n")

    if not args.no_open:
        try:
            if sys.platform == "darwin":
                subprocess.run(["open", out_path], check=False)
            else:
                webbrowser.open("file://" + out_path)
        except Exception:
            pass


if __name__ == "__main__":
    main()
