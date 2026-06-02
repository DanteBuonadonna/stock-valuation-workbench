"""
Per-ticker notes — freeform research notes and meeting prep, kept locally.
"""
from __future__ import annotations
import os
import sqlite3
from datetime import datetime
from typing import Optional

HERE = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(HERE, "evaluations.db")


def _conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_notes():
    conn = _conn()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS ticker_notes (
            ticker TEXT PRIMARY KEY,
            research_notes TEXT,
            meeting_talking_points TEXT,
            updated_at TEXT NOT NULL
        );
    """)
    conn.commit()
    conn.close()


def get_notes(ticker: str) -> dict:
    init_notes()
    ticker = (ticker or "").strip().upper()
    if not ticker:
        return {"ticker": "", "research_notes": "", "meeting_talking_points": "",
                "updated_at": None}
    conn = _conn()
    row = conn.execute("SELECT * FROM ticker_notes WHERE ticker = ?", (ticker,)).fetchone()
    conn.close()
    if row:
        return dict(row)
    return {"ticker": ticker, "research_notes": "", "meeting_talking_points": "",
            "updated_at": None}


def save_notes(ticker: str, research_notes: Optional[str] = None,
               meeting_talking_points: Optional[str] = None) -> dict:
    init_notes()
    ticker = (ticker or "").strip().upper()
    if not ticker:
        raise ValueError("Ticker required.")
    current = get_notes(ticker)
    research_notes = research_notes if research_notes is not None else (current.get("research_notes") or "")
    meeting_talking_points = (meeting_talking_points if meeting_talking_points is not None
                              else (current.get("meeting_talking_points") or ""))
    conn = _conn()
    conn.execute(
        """
        INSERT INTO ticker_notes (ticker, research_notes, meeting_talking_points, updated_at)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(ticker) DO UPDATE SET
            research_notes = excluded.research_notes,
            meeting_talking_points = excluded.meeting_talking_points,
            updated_at = excluded.updated_at
        """,
        (ticker, research_notes, meeting_talking_points, datetime.now().isoformat()),
    )
    conn.commit()
    conn.close()
    return get_notes(ticker)
