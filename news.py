"""
News feed per ticker.

Pulls recent headlines from Yahoo Finance via yfinance's built-in `.news`
attribute. Free, no extra API key required. Returns the most recent items
with title, source, link, and publish timestamp.
"""
from __future__ import annotations
from datetime import datetime
from typing import Optional


def _ts_to_iso(ts) -> Optional[str]:
    try:
        if ts is None:
            return None
        return datetime.fromtimestamp(int(ts)).isoformat()
    except Exception:
        return None


def get_news(ticker: str, limit: int = 20) -> dict:
    out = {
        "ticker": (ticker or "").upper().strip(),
        "fetched_at": datetime.now().isoformat(),
        "items": [],
        "source": "Yahoo Finance via yfinance",
        "error": None,
    }
    try:
        import yfinance as yf
        t = yf.Ticker(out["ticker"])
        raw = t.news or []
        for item in raw[:limit]:
            # yfinance returns two shapes depending on version — handle both
            content = item.get("content") if isinstance(item, dict) else None
            if content and isinstance(content, dict):
                # Newer yfinance schema: nested under "content"
                title = content.get("title")
                publisher = (content.get("provider") or {}).get("displayName")
                pub_date = content.get("pubDate") or content.get("displayTime")
                link = ((content.get("canonicalUrl") or {}).get("url")
                        or (content.get("clickThroughUrl") or {}).get("url"))
                summary = content.get("summary") or content.get("description")
                thumb = None
                tn = content.get("thumbnail") or {}
                if isinstance(tn, dict):
                    resolutions = tn.get("resolutions") or []
                    if resolutions:
                        thumb = resolutions[0].get("url")
            else:
                # Older yfinance schema: flat fields
                title = item.get("title")
                publisher = item.get("publisher")
                pub_date = _ts_to_iso(item.get("providerPublishTime"))
                link = item.get("link")
                summary = item.get("summary") or None
                thumb = None
                tn = item.get("thumbnail") or {}
                if isinstance(tn, dict):
                    resolutions = tn.get("resolutions") or []
                    if resolutions:
                        thumb = resolutions[0].get("url")
            if not title:
                continue
            out["items"].append({
                "title": title,
                "publisher": publisher or "Unknown",
                "published_at": pub_date,
                "link": link,
                "summary": summary,
                "thumbnail": thumb,
            })
    except Exception as e:
        out["error"] = str(e)
    return out
