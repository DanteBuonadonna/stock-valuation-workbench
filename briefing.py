"""
Advisor Co-Pilot — Morning Briefing engine.

Step 1: scan the firm's real data (CRM, holdings, market prices, news, watchlist
        alerts, tagged evaluations, dividend dates) for actionable opportunities.
Step 2: classify candidates into TAX / PORTFOLIO / RELATIONSHIP buckets and
        prioritize URGENT / IMPORTANT / FYI.
Step 3: for each candidate, draft a personalized client communication
        (subject, email body, talking points) using either an AI model
        (Anthropic Claude Haiku) when an API key is configured, OR a
        deterministic template fallback so it still works without AI.

All numerical data is real — pulled live. The AI step is just the writing.
"""
from __future__ import annotations
import json
import os
import sqlite3
from datetime import datetime, timedelta
from typing import Optional

HERE = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(HERE, "evaluations.db")
AI_CONFIG_PATH = os.path.join(HERE, "ai_config.json")
BRANDING_PATH = os.path.join(HERE, "firm_branding.json")


def _conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _load_ai_config() -> dict:
    # Prefer environment variable for the API key (safer for cloud deployments)
    env_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not os.path.exists(AI_CONFIG_PATH):
        return {"anthropic_api_key": env_key, "model": "claude-haiku-4-5", "max_tokens": 800}
    with open(AI_CONFIG_PATH) as f:
        cfg = json.load(f)
    # Env var overrides the file-stored key when present
    if env_key:
        cfg["anthropic_api_key"] = env_key
    return cfg


def _load_branding() -> dict:
    if not os.path.exists(BRANDING_PATH):
        return {"firm_name": "[Firm]", "advisor_name": "[Advisor]"}
    with open(BRANDING_PATH) as f:
        return json.load(f)


def _safe_float(v):
    try:
        if v is None or v == "":
            return None
        return float(v)
    except (TypeError, ValueError):
        return None


def _current_price(ticker: str) -> Optional[float]:
    try:
        from data_fetcher import fetch_price_history
        hist = fetch_price_history(ticker, period="5d")
        if hist is not None and len(hist) > 0:
            return float(hist.iloc[-1])
    except Exception:
        pass
    return None


# ============================================================
#   STEP 1 — Find candidates from real data
# ============================================================

def find_tax_opportunities() -> list:
    """Per client: holdings with material unrealized losses in taxable accounts."""
    conn = _conn()
    candidates = []
    clients = conn.execute(
        "SELECT * FROM clients WHERE LOWER(COALESCE(account_type,'')) IN ('taxable','mixed')"
    ).fetchall()
    for c in clients:
        holdings = conn.execute(
            "SELECT * FROM client_holdings WHERE client_id = ?", (c["id"],)
        ).fetchall()
        losers = []
        total_loss = 0.0
        for h in holdings:
            cost_basis = _safe_float(h["cost_basis"])
            shares = _safe_float(h["shares"]) or 0
            if not cost_basis or shares <= 0:
                continue
            cp = _current_price(h["ticker"])
            if cp is None:
                continue
            unrealized = (cp - cost_basis) * shares
            # Material loss threshold: $2,500 absolute OR 7% drawdown of position
            if unrealized < -2500 or (cost_basis > 0 and (cp / cost_basis - 1) <= -0.07):
                losers.append({
                    "ticker": h["ticker"],
                    "shares": shares,
                    "cost_basis": cost_basis,
                    "current_price": round(cp, 2),
                    "unrealized": round(unrealized, 2),
                    "pct_change": round((cp / cost_basis - 1) * 100, 1),
                })
                total_loss += unrealized
        if losers and total_loss < -2500:
            candidates.append({
                "category": "tax_opportunity",
                "priority": "important" if total_loss > -10000 else "urgent",
                "client_id": c["id"],
                "client_name": c["name"],
                "client_household": c["household"],
                "client_tax_bracket": c["tax_bracket"],
                "client_meta": dict(c),
                "headline": f"Tax-loss harvest opportunity: ${abs(total_loss):,.0f} in unrealized losses across {len(losers)} positions",
                "total_unrealized_loss": round(total_loss, 2),
                "losers": losers,
                "estimated_tax_savings": _estimate_tax_savings(total_loss, c["tax_bracket"]),
            })
    conn.close()
    return candidates


def _estimate_tax_savings(loss_dollars: float, bracket_str: Optional[str]) -> Optional[float]:
    """Rough estimate of federal tax savings from harvesting. Real number depends on
    short-vs-long term and state tax — for the briefing this is a directional figure."""
    if not bracket_str:
        return None
    try:
        bracket = float(str(bracket_str).rstrip("%")) / 100.0
        return round(abs(loss_dollars) * bracket, 0)
    except Exception:
        return None


def find_portfolio_actions() -> list:
    """Per tagged evaluation: re-score and surface meaningful drift."""
    conn = _conn()
    candidates = []
    # Pull recent active client_evaluations (last 12 months, status considering/recommended/executed)
    cutoff = (datetime.now() - timedelta(days=365)).isoformat()
    rows = conn.execute(
        """SELECT ce.*, c.name AS client_name, c.household
             FROM client_evaluations ce
             JOIN clients c ON c.id = ce.client_id
             WHERE ce.created_at >= ?
               AND ce.status IN ('considering','recommended','executed')""",
        (cutoff,),
    ).fetchall()
    # Group by ticker to avoid re-fetching the same ticker many times
    by_ticker = {}
    for r in rows:
        by_ticker.setdefault(r["ticker"], []).append(dict(r))

    for ticker, tags in by_ticker.items():
        try:
            from data_fetcher import fetch_ticker_data
            from evaluator_engine import MODELS, score_model
            data = fetch_ticker_data(ticker)
            scores = {k: score_model(k, data) for k in MODELS}
            current_agg = sum(s["total"] for s in scores.values())
        except Exception:
            continue

        for tag in tags:
            # Get the baseline aggregate from when the tag was created (look up the evaluation if linked)
            baseline_agg = None
            if tag["evaluation_id"]:
                ev = conn.execute(
                    "SELECT growth_score, dividend_score, valgrowth_score FROM evaluations WHERE id = ?",
                    (tag["evaluation_id"],),
                ).fetchone()
                if ev:
                    baseline_agg = (ev["growth_score"] or 0) + (ev["dividend_score"] or 0) + (ev["valgrowth_score"] or 0)
            if baseline_agg is None:
                continue
            delta = current_agg - baseline_agg
            # Surface material score moves (4+ points either direction)
            if abs(delta) >= 4:
                priority = "urgent" if delta <= -6 else ("important" if delta < 0 else "fyi")
                direction = "improved" if delta > 0 else "deteriorated"
                action = "Consider trimming or reviewing the thesis" if delta < 0 else "Position has improved — consider adding"
                candidates.append({
                    "category": "portfolio_action",
                    "priority": priority,
                    "client_id": tag["client_id"],
                    "client_name": tag["client_name"],
                    "client_household": tag["household"],
                    "ticker": ticker,
                    "headline": f"{ticker} aggregate score {direction} from {baseline_agg} → {current_agg} since you tagged it for {tag['client_name']}",
                    "baseline_aggregate": baseline_agg,
                    "current_aggregate": current_agg,
                    "delta": delta,
                    "current_price": data.get("price"),
                    "suggested_action": action,
                    "scores_now": {k: scores[k]["total"] for k in scores},
                })
    conn.close()
    return candidates


def find_relationship_touchpoints() -> list:
    """Clients with overdue touchpoints + clients with upcoming dividend/earnings on their holdings."""
    conn = _conn()
    candidates = []
    clients = conn.execute("SELECT * FROM clients").fetchall()
    now = datetime.now()

    for c in clients:
        # Overdue touchpoint
        last = c["last_touchpoint_at"]
        days_overdue = None
        if last:
            try:
                last_dt = datetime.fromisoformat(last)
                days_overdue = (now - last_dt).days
            except Exception:
                pass
        if days_overdue is None or days_overdue >= 60:
            # Look for natural conversation hooks: dividend payment or earnings coming up on their holdings
            holdings = conn.execute(
                "SELECT ticker FROM client_holdings WHERE client_id = ?", (c["id"],)
            ).fetchall()
            hook = None
            try:
                from events import get_events
                for h in holdings[:5]:  # cap at 5 to keep this fast
                    evt = get_events(h["ticker"])
                    if (evt.get("earnings_days_away") is not None and 0 <= evt["earnings_days_away"] <= 14) \
                            or (evt.get("ex_dividend_days_away") is not None and 0 <= evt["ex_dividend_days_away"] <= 14):
                        hook = {
                            "ticker": h["ticker"],
                            "event": "Earnings" if evt.get("earnings_days_away") is not None and 0 <= evt["earnings_days_away"] <= 14 else "Ex-dividend",
                            "date": evt.get("next_earnings") or evt.get("ex_dividend_date"),
                        }
                        break
            except Exception:
                pass
            priority = "urgent" if (days_overdue or 0) > 120 else ("important" if (days_overdue or 0) > 90 else "fyi")
            candidates.append({
                "category": "relationship_touchpoint",
                "priority": priority,
                "client_id": c["id"],
                "client_name": c["name"],
                "client_household": c["household"],
                "client_meta": dict(c),
                "days_since_last_touchpoint": days_overdue,
                "natural_hook": hook,
                "headline": (
                    f"Haven't reached out to {c['name']} in {days_overdue} days"
                    if days_overdue is not None else
                    f"No recorded touchpoint with {c['name']} yet"
                ),
            })

    conn.close()
    return candidates


# ============================================================
#   STEP 2 — Generate communications (AI or template)
# ============================================================

def _call_anthropic(prompt: str, api_key: str, model: str, max_tokens: int = 800) -> Optional[str]:
    try:
        import anthropic
    except ImportError:
        return None
    try:
        client = anthropic.Anthropic(api_key=api_key)
        resp = client.messages.create(
            model=model,
            max_tokens=max_tokens,
            messages=[{"role": "user", "content": prompt}],
        )
        # Concatenate text blocks
        parts = []
        for block in resp.content:
            if hasattr(block, "text"):
                parts.append(block.text)
            elif isinstance(block, dict) and "text" in block:
                parts.append(block["text"])
        return "\n".join(parts).strip()
    except Exception:
        return None


def _draft_for_candidate(candidate: dict, ai_cfg: dict, branding: dict) -> dict:
    """Add subject / email_body / talking_points to a candidate."""
    api_key = (ai_cfg.get("anthropic_api_key") or "").strip()
    model = ai_cfg.get("model") or "claude-haiku-4-5"
    advisor = branding.get("advisor_name") or "[Advisor]"
    firm = branding.get("firm_name") or "[Firm]"

    if api_key:
        prompt = _build_prompt(candidate, advisor, firm)
        raw = _call_anthropic(prompt, api_key, model, ai_cfg.get("max_tokens", 800))
        if raw:
            parsed = _parse_ai_output(raw)
            if parsed.get("email_body"):
                return {**candidate, "draft": parsed, "ai_used": True, "model": model}

    # Fallback: deterministic template
    return {**candidate, "draft": _template_draft(candidate, advisor, firm), "ai_used": False, "model": None}


def _build_prompt(c: dict, advisor: str, firm: str) -> str:
    cat = c["category"]
    cname = c["client_name"]
    risk = c.get("client_meta", {}).get("risk_tolerance") or "unspecified"
    bracket = c.get("client_meta", {}).get("tax_bracket") or "unspecified"
    goals = c.get("client_meta", {}).get("goals") or ""
    meeting = c.get("client_meta", {}).get("meeting_summary") or ""
    base = f"""You are drafting a short, professional, client-ready communication for a wealth-management advisor named {advisor} at {firm}.

Client: {cname}
Risk tolerance: {risk}
Tax bracket: {bracket}
Goals on file: {goals or '(none recorded)'}
Most recent meeting summary: {meeting or '(none recorded)'}

The situation:
"""
    if cat == "tax_opportunity":
        losers_lines = "\n".join(
            f"  - {l['ticker']}: {l['shares']} shares, cost ${l['cost_basis']}, now ${l['current_price']}, unrealized {l['pct_change']}% (${l['unrealized']})"
            for l in c.get("losers", [])
        )
        savings = c.get("estimated_tax_savings")
        situation = (
            f"This client has ${abs(c['total_unrealized_loss']):,.0f} in unrealized losses across the following taxable positions:\n"
            f"{losers_lines}\n\n"
            + (f"Estimated federal tax savings from harvesting these losses: roughly ${savings:,.0f} (using their {bracket} bracket as a rough proxy)." if savings else "")
            + "\nThe advisor wants to recommend harvesting these losses now and rotating into similar but not substantially identical positions to avoid wash sales."
        )
    elif cat == "portfolio_action":
        situation = (
            f"This client owns or was recommended {c['ticker']}. Since the original tag, the aggregate score across the firm's three models has moved from {c['baseline_aggregate']}/30 to {c['current_aggregate']}/30 (delta: {c['delta']:+d}). "
            f"Current model scores: Growth {c['scores_now']['growth']}/10, Dividend {c['scores_now']['dividend']}/10, Value+Growth {c['scores_now']['valGrowth']}/10. Current price ${c.get('current_price')}.\n\n"
            f"Suggested action: {c['suggested_action']}."
        )
    else:  # relationship_touchpoint
        days = c.get("days_since_last_touchpoint")
        hook = c.get("natural_hook")
        situation = (
            f"It has been {days} days since the last recorded touchpoint with this client. "
            + (f"A natural conversation hook is available: {hook['ticker']} has a {hook['event']} on {hook['date']}." if hook else "There is no specific market hook — this is a relationship check-in.")
            + " The advisor wants to reach out proactively."
        )

    return base + situation + """

Output exactly in this format, no markdown, no preamble:

SUBJECT: <a short, specific email subject line>

EMAIL:
<a 4-6 sentence email from the advisor to the client. Professional, warm, plain English. NO investment-advice disclaimers, the advisor adds those separately. If suggesting an action, be specific but not pushy.>

TALKING POINTS:
- <bullet 1, one sentence>
- <bullet 2, one sentence>
- <bullet 3, one sentence>
"""


def _parse_ai_output(raw: str) -> dict:
    subject = ""
    email_body = ""
    talking_points = []
    section = None
    for line in raw.splitlines():
        s = line.strip()
        if s.upper().startswith("SUBJECT:"):
            subject = s.split(":", 1)[1].strip()
            section = "subject"
        elif s.upper().startswith("EMAIL:"):
            section = "email"
        elif s.upper().startswith("TALKING POINTS:"):
            section = "tp"
        elif section == "email":
            if s:
                email_body += (("\n" if email_body else "") + s)
        elif section == "tp":
            if s.startswith("-") or s.startswith("•"):
                talking_points.append(s.lstrip("-•").strip())
    return {"subject": subject, "email_body": email_body, "talking_points": talking_points}


def _template_draft(c: dict, advisor: str, firm: str) -> dict:
    """Deterministic fallback — useful, just not personalized by AI."""
    cname = c["client_name"]
    cat = c["category"]
    if cat == "tax_opportunity":
        loss = abs(c["total_unrealized_loss"])
        savings = c.get("estimated_tax_savings")
        savings_line = f"At your tax bracket the federal tax savings should be roughly ${savings:,.0f}." if savings else "I'll share the estimated tax savings when we talk."
        positions = ", ".join(l["ticker"] for l in c.get("losers", []))
        body = (
            f"Hi,\n\n"
            f"Year-end is approaching and I wanted to flag a tax-loss harvesting opportunity in your account. "
            f"You currently have approximately ${loss:,.0f} of unrealized losses across {positions}. "
            f"By selling these positions and rotating into similar (but not substantially identical) exposures, "
            f"we can capture the losses for tax purposes without materially changing your investment posture. "
            f"{savings_line}\n\n"
            f"Want to chat this week to walk through it?\n\n"
            f"Best,\n{advisor}"
        )
        return {
            "subject": f"Tax-loss harvest opportunity before year-end",
            "email_body": body,
            "talking_points": [
                f"Harvestable loss: ~${loss:,.0f} across {len(c.get('losers', []))} positions",
                "Plan: sell at loss, rotate to similar (non-substantially-identical) exposure, avoid wash sale",
                "Estimated tax savings: " + (f"~${savings:,.0f}" if savings else "tbd"),
            ],
        }
    if cat == "portfolio_action":
        body = (
            f"Hi,\n\n"
            f"Quick note on {c['ticker']}. Since I first flagged this for you, our internal score has moved from "
            f"{c['baseline_aggregate']}/30 to {c['current_aggregate']}/30. {c['suggested_action']}.\n\n"
            f"Let's review on our next call.\n\nBest,\n{advisor}"
        )
        return {
            "subject": f"{c['ticker']} — score change worth a look",
            "email_body": body,
            "talking_points": [
                f"Aggregate score moved {c['delta']:+d} points ({c['baseline_aggregate']} → {c['current_aggregate']})",
                f"Suggested action: {c['suggested_action']}",
                "Current model split (G/D/V+G): " + " / ".join(
                    str(c["scores_now"][k]) for k in ["growth", "dividend", "valGrowth"]
                ),
            ],
        }
    # relationship
    days = c.get("days_since_last_touchpoint")
    hook = c.get("natural_hook")
    hook_line = ""
    if hook:
        hook_line = f"With {hook['ticker']}'s {hook['event'].lower()} on {hook['date']}, this is a good moment to check in. "
    body = (
        f"Hi,\n\n"
        f"It has been a little while since we connected and I wanted to reach out. "
        f"{hook_line}Are you free for a 20-minute call this week or next?\n\n"
        f"Best,\n{advisor}"
    )
    return {
        "subject": f"Quick check-in",
        "email_body": body,
        "talking_points": [
            f"Days since last touchpoint: {days if days is not None else 'unknown'}",
            "Suggested action: schedule a brief catch-up",
            (f"Natural hook: {hook['ticker']} {hook['event'].lower()} on {hook['date']}" if hook else "No specific market trigger — relationship check-in"),
        ],
    }


# ============================================================
#   STEP 3 — Generate + persist a briefing
# ============================================================

PRIORITY_ORDER = {"urgent": 0, "important": 1, "fyi": 2}


def generate_briefing() -> dict:
    """Run the whole pipeline and persist the result."""
    ai_cfg = _load_ai_config()
    branding = _load_branding()

    all_candidates = []
    try:
        all_candidates += find_tax_opportunities()
    except Exception as e:
        print(f"tax scan failed: {e}")
    try:
        all_candidates += find_portfolio_actions()
    except Exception as e:
        print(f"portfolio scan failed: {e}")
    try:
        all_candidates += find_relationship_touchpoints()
    except Exception as e:
        print(f"relationship scan failed: {e}")

    # Draft for each
    drafted = []
    for cand in all_candidates:
        drafted.append(_draft_for_candidate(cand, ai_cfg, branding))

    # Sort by priority then category
    drafted.sort(key=lambda x: (PRIORITY_ORDER.get(x["priority"], 9), x.get("category", "")))

    # Tag each with a stable key for tracking "done" status across reloads
    for i, item in enumerate(drafted):
        ticker_part = item.get("ticker") or item.get("losers", [{}])[0].get("ticker") if item.get("losers") else ""
        item["item_key"] = f"{item['category']}-{item.get('client_id','?')}-{ticker_part}-{datetime.now().strftime('%Y-%m-%d')}"

    payload = {
        "generated_at": datetime.now().isoformat(),
        "ai_used": any(d.get("ai_used") for d in drafted),
        "model": next((d["model"] for d in drafted if d.get("model")), None),
        "counts": {
            "urgent": sum(1 for d in drafted if d["priority"] == "urgent"),
            "important": sum(1 for d in drafted if d["priority"] == "important"),
            "fyi": sum(1 for d in drafted if d["priority"] == "fyi"),
            "total": len(drafted),
            "tax_opportunity": sum(1 for d in drafted if d["category"] == "tax_opportunity"),
            "portfolio_action": sum(1 for d in drafted if d["category"] == "portfolio_action"),
            "relationship_touchpoint": sum(1 for d in drafted if d["category"] == "relationship_touchpoint"),
        },
        "items": drafted,
    }

    # Persist
    conn = _conn()
    conn.execute(
        "INSERT INTO briefings (generated_at, ai_used, model, payload_json) VALUES (?, ?, ?, ?)",
        (payload["generated_at"], 1 if payload["ai_used"] else 0, payload["model"], json.dumps(payload, default=str)),
    )
    conn.commit()
    conn.close()
    return payload


def get_latest_briefing(max_age_hours: int = 12) -> Optional[dict]:
    """Return the most recent briefing if it's still fresh; otherwise None."""
    conn = _conn()
    try:
        row = conn.execute(
            "SELECT * FROM briefings ORDER BY id DESC LIMIT 1"
        ).fetchone()
    except sqlite3.OperationalError:
        # Table may not exist yet
        conn.close()
        return None
    if not row:
        conn.close()
        return None
    try:
        gen_at = datetime.fromisoformat(row["generated_at"])
        age = (datetime.now() - gen_at).total_seconds() / 3600
        if age > max_age_hours:
            conn.close()
            return None
        payload = json.loads(row["payload_json"])
        # Merge in done/dismissed state
        items_state = {r["briefing_item_key"]: r["status"]
                       for r in conn.execute("SELECT * FROM briefing_items_state").fetchall()}
        for item in payload.get("items", []):
            k = item.get("item_key")
            if k and k in items_state:
                item["status"] = items_state[k]
        conn.close()
        return payload
    except Exception:
        conn.close()
        return None


def set_item_status(item_key: str, status: str) -> dict:
    """Mark a briefing item as done / dismissed / sent."""
    conn = _conn()
    conn.execute(
        """INSERT INTO briefing_items_state (briefing_item_key, status, updated_at)
             VALUES (?, ?, ?)
             ON CONFLICT(briefing_item_key) DO UPDATE SET status=excluded.status, updated_at=excluded.updated_at""",
        (item_key, status, datetime.now().isoformat()),
    )
    conn.commit()
    conn.close()
    return {"ok": True}
