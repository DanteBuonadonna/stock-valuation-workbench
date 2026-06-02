"""
Lightweight CRM — clients, holdings, and tagging of evaluations to clients.

Tables (all in evaluations.db so backups/exports are one file):
  clients              — client roster
  client_holdings      — what each client currently owns
  client_evaluations   — link table: which ticker recommendations apply to which client
"""
from __future__ import annotations
import json
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


def init_crm():
    conn = _conn()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS clients (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            household TEXT,
            risk_tolerance TEXT,            -- conservative / moderate / aggressive
            aum REAL,                        -- current AUM in dollars
            tax_bracket TEXT,                -- e.g. 24%, 32%, 37%
            account_type TEXT,               -- taxable / IRA / Roth / Trust / 401k / mixed
            contact_email TEXT,
            contact_phone TEXT,
            notes TEXT,
            goals TEXT,                       -- retirement target, college, big purchase, etc.
            life_events TEXT,                 -- recent or upcoming (kid college, retirement, sale of business)
            meeting_summary TEXT,             -- last meeting summary used as context for AI briefings
            last_touchpoint_at TEXT,          -- ISO date of last contact
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS client_holdings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            client_id INTEGER NOT NULL,
            ticker TEXT NOT NULL,
            shares REAL NOT NULL,
            cost_basis REAL,
            purchase_date TEXT,
            notes TEXT,
            updated_at TEXT NOT NULL,
            FOREIGN KEY (client_id) REFERENCES clients(id)
        );
        CREATE TABLE IF NOT EXISTS client_evaluations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            client_id INTEGER NOT NULL,
            ticker TEXT NOT NULL,
            evaluation_id INTEGER,           -- link to evaluations table (optional)
            recommendation TEXT,              -- BUY / HOLD / PASS at time of tag
            recommended_size_pct REAL,        -- suggested position size
            status TEXT NOT NULL DEFAULT 'considering',  -- considering / recommended / executed / rejected
            advisor_notes TEXT,
            created_at TEXT NOT NULL,
            FOREIGN KEY (client_id) REFERENCES clients(id),
            FOREIGN KEY (evaluation_id) REFERENCES evaluations(id)
        );
        CREATE INDEX IF NOT EXISTS idx_holdings_client ON client_holdings(client_id);
        CREATE INDEX IF NOT EXISTS idx_holdings_ticker ON client_holdings(ticker);
        CREATE INDEX IF NOT EXISTS idx_clieval_client ON client_evaluations(client_id);
        CREATE INDEX IF NOT EXISTS idx_clieval_ticker ON client_evaluations(ticker);
        CREATE TABLE IF NOT EXISTS briefings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            generated_at TEXT NOT NULL,
            ai_used INTEGER DEFAULT 0,
            model TEXT,
            payload_json TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS briefing_items_state (
            briefing_item_key TEXT PRIMARY KEY,
            status TEXT NOT NULL,             -- new / done / dismissed / sent
            updated_at TEXT NOT NULL
        );
    """)
    # Backfill new columns for upgraded DBs
    for col, ddl in [
        ("goals", "TEXT"),
        ("life_events", "TEXT"),
        ("meeting_summary", "TEXT"),
        ("last_touchpoint_at", "TEXT"),
    ]:
        try:
            conn.execute(f"ALTER TABLE clients ADD COLUMN {col} {ddl}")
        except sqlite3.OperationalError:
            pass  # column already exists
    conn.commit()
    conn.close()


# ---------- Clients CRUD ----------
def list_clients() -> list:
    init_crm()
    conn = _conn()
    rows = conn.execute("SELECT * FROM clients ORDER BY name ASC").fetchall()
    out = []
    for r in rows:
        item = dict(r)
        # Add holding count
        cnt = conn.execute(
            "SELECT COUNT(*) c FROM client_holdings WHERE client_id = ?", (r["id"],)
        ).fetchone()["c"]
        item["holding_count"] = cnt
        out.append(item)
    conn.close()
    return out


def get_client(client_id: int) -> Optional[dict]:
    init_crm()
    conn = _conn()
    row = conn.execute("SELECT * FROM clients WHERE id = ?", (client_id,)).fetchone()
    if not row:
        conn.close()
        return None
    client = dict(row)
    client["holdings"] = [dict(r) for r in conn.execute(
        "SELECT * FROM client_holdings WHERE client_id = ? ORDER BY ticker",
        (client_id,)
    ).fetchall()]
    client["evaluations"] = [dict(r) for r in conn.execute(
        """SELECT * FROM client_evaluations WHERE client_id = ?
           ORDER BY created_at DESC LIMIT 50""",
        (client_id,)
    ).fetchall()]
    conn.close()
    return client


def upsert_client(payload: dict) -> dict:
    init_crm()
    name = (payload.get("name") or "").strip()
    if not name:
        raise ValueError("Client name is required.")
    now = datetime.now().isoformat()
    fields = {
        "household": payload.get("household"),
        "risk_tolerance": payload.get("risk_tolerance"),
        "aum": _to_float(payload.get("aum")),
        "tax_bracket": payload.get("tax_bracket"),
        "account_type": payload.get("account_type"),
        "contact_email": payload.get("contact_email"),
        "contact_phone": payload.get("contact_phone"),
        "notes": payload.get("notes"),
        "goals": payload.get("goals"),
        "life_events": payload.get("life_events"),
        "meeting_summary": payload.get("meeting_summary"),
        "last_touchpoint_at": payload.get("last_touchpoint_at"),
    }
    conn = _conn()
    existing = conn.execute("SELECT id FROM clients WHERE name = ?", (name,)).fetchone()
    if existing:
        client_id = existing["id"]
        conn.execute(
            """UPDATE clients SET household=?, risk_tolerance=?, aum=?, tax_bracket=?,
                                   account_type=?, contact_email=?, contact_phone=?,
                                   notes=?, goals=?, life_events=?, meeting_summary=?,
                                   last_touchpoint_at=?, updated_at=?
                 WHERE id=?""",
            (*fields.values(), now, client_id),
        )
    else:
        cur = conn.execute(
            """INSERT INTO clients (name, household, risk_tolerance, aum, tax_bracket,
                                     account_type, contact_email, contact_phone, notes,
                                     goals, life_events, meeting_summary, last_touchpoint_at,
                                     created_at, updated_at)
                 VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (name, *fields.values(), now, now),
        )
        client_id = cur.lastrowid
    conn.commit()
    conn.close()
    return get_client(client_id)


def delete_client(client_id: int) -> dict:
    init_crm()
    conn = _conn()
    conn.execute("DELETE FROM client_holdings WHERE client_id = ?", (client_id,))
    conn.execute("DELETE FROM client_evaluations WHERE client_id = ?", (client_id,))
    conn.execute("DELETE FROM clients WHERE id = ?", (client_id,))
    conn.commit()
    conn.close()
    return {"ok": True}


# ---------- Holdings ----------
def add_holding(client_id: int, ticker: str, shares: float,
                cost_basis: Optional[float] = None,
                purchase_date: Optional[str] = None,
                notes: Optional[str] = None) -> dict:
    init_crm()
    conn = _conn()
    conn.execute(
        """INSERT INTO client_holdings (client_id, ticker, shares, cost_basis,
                                          purchase_date, notes, updated_at)
             VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (client_id, (ticker or "").upper().strip(), float(shares),
         _to_float(cost_basis), purchase_date, notes, datetime.now().isoformat()),
    )
    conn.commit()
    conn.close()
    return get_client(client_id)


def remove_holding(holding_id: int) -> dict:
    init_crm()
    conn = _conn()
    row = conn.execute("SELECT client_id FROM client_holdings WHERE id = ?", (holding_id,)).fetchone()
    client_id = row["client_id"] if row else None
    conn.execute("DELETE FROM client_holdings WHERE id = ?", (holding_id,))
    conn.commit()
    conn.close()
    return get_client(client_id) if client_id else {"ok": True}


def clients_holding(ticker: str) -> list:
    """Return clients who currently hold a given ticker — answers 'who holds AAPL?'."""
    init_crm()
    ticker = (ticker or "").upper().strip()
    conn = _conn()
    rows = conn.execute(
        """SELECT c.id, c.name, c.household, h.shares, h.cost_basis, h.purchase_date
             FROM client_holdings h
             JOIN clients c ON c.id = h.client_id
             WHERE h.ticker = ?
             ORDER BY c.name""",
        (ticker,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ---------- Tagging evaluations to clients ----------
def tag_evaluation(client_id: int, ticker: str, evaluation_id: Optional[int],
                    recommendation: Optional[str], recommended_size_pct: Optional[float],
                    status: str = "considering",
                    advisor_notes: Optional[str] = None) -> dict:
    init_crm()
    conn = _conn()
    conn.execute(
        """INSERT INTO client_evaluations
             (client_id, ticker, evaluation_id, recommendation, recommended_size_pct,
              status, advisor_notes, created_at)
             VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (client_id, (ticker or "").upper().strip(), evaluation_id,
         recommendation, _to_float(recommended_size_pct), status, advisor_notes,
         datetime.now().isoformat()),
    )
    conn.commit()
    conn.close()
    return get_client(client_id)


def evaluations_for_ticker(ticker: str) -> list:
    """Across all clients, return tagged evaluations for a ticker."""
    init_crm()
    ticker = (ticker or "").upper().strip()
    conn = _conn()
    rows = conn.execute(
        """SELECT ce.*, c.name AS client_name, c.household
             FROM client_evaluations ce
             JOIN clients c ON c.id = ce.client_id
             WHERE ce.ticker = ?
             ORDER BY ce.created_at DESC LIMIT 100""",
        (ticker,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def _to_float(v):
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None
