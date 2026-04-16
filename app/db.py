"""
Supabase REST API wrapper for scan result persistence and cross-instance analysis caching.
Uses the service-role key (server-side only — never exposed to the browser).
"""
import os
from datetime import datetime, timedelta, timezone
from typing import Optional

import requests as _requests

_SUPABASE_URL = os.environ.get("investorlens_SUPABASE_URL", "").rstrip("/")
_SUPABASE_KEY = (
    os.environ.get("investorlens_SUPABASE_SERVICE_ROLE_KEY")
    or os.environ.get("investorlens_SUPABASE_SECRET_KEY")
    or ""
)

_SCAN_TABLE = "scan_results"
_ANALYSIS_TABLE = "analysis_cache"


def _headers(prefer: str = "") -> dict:
    h = {
        "apikey": _SUPABASE_KEY,
        "Authorization": f"Bearer {_SUPABASE_KEY}",
        "Content-Type": "application/json",
    }
    if prefer:
        h["Prefer"] = prefer
    return h


def is_configured() -> bool:
    return bool(_SUPABASE_URL and _SUPABASE_KEY)


def _parse_dt(value: str | None) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except Exception:
        return None


def get_latest_scan() -> Optional[dict]:
    """Return the most recent scan row, or None if unavailable."""
    if not is_configured():
        return None
    try:
        resp = _requests.get(
            f"{_SUPABASE_URL}/rest/v1/{_SCAN_TABLE}",
            headers=_headers(),
            params={"order": "scanned_at.desc", "limit": "1"},
            timeout=5,
        )
        data = resp.json()
        return data[0] if isinstance(data, list) and data else None
    except Exception:
        return None


def save_scan(results: list, passed_count: int, total_scanned: int | None = None) -> bool:
    """Insert a new scan row. Returns True on success."""
    if not is_configured():
        return False
    try:
        payload: dict = {
            "results": results,
            "passed_count": passed_count,
            "total_scanned": total_scanned if total_scanned is not None else len(results),
        }
        resp = _requests.post(
            f"{_SUPABASE_URL}/rest/v1/{_SCAN_TABLE}",
            headers=_headers(prefer="return=minimal"),
            json=payload,
            timeout=5,
        )
        return resp.status_code in (200, 201)
    except Exception:
        return False


def get_scan_history(limit: int = 20) -> list:
    """
    Return the last `limit` scan rows, most recent first.
    Each row contains: id, scanned_at, passed_count, total_scanned,
    and a compact 'top_passed' list (ticker + consensus for the passed stocks).
    Full results are omitted to keep the payload small.
    """
    if not is_configured():
        return []
    try:
        resp = _requests.get(
            f"{_SUPABASE_URL}/rest/v1/{_SCAN_TABLE}",
            headers=_headers(),
            params={"order": "scanned_at.desc", "limit": str(limit)},
            timeout=8,
        )
        rows = resp.json()
        if not isinstance(rows, list):
            return []
        history = []
        for row in rows:
            raw_results = row.get("results") or []
            top_passed = sorted(
                [r for r in raw_results if r.get("consensus", 0) >= 75 and r.get("bullish_count", 0) >= 7],
                key=lambda r: r.get("consensus", 0),
                reverse=True,
            )[:10]
            history.append({
                "id":            row.get("id"),
                "scanned_at":    row.get("scanned_at"),
                "passed_count":  row.get("passed_count", 0),
                "total_scanned": row.get("total_scanned", len(raw_results)),
                "top_passed":    [{"ticker": r["ticker"], "consensus": r["consensus"]} for r in top_passed],
            })
        return history
    except Exception:
        return []


def get_scan_by_id(scan_id: int) -> Optional[dict]:
    """Return the full results for a specific scan row by primary key."""
    if not is_configured():
        return None
    try:
        resp = _requests.get(
            f"{_SUPABASE_URL}/rest/v1/{_SCAN_TABLE}",
            headers=_headers(),
            params={"id": f"eq.{scan_id}", "limit": "1"},
            timeout=8,
        )
        rows = resp.json()
        return rows[0] if isinstance(rows, list) and rows else None
    except Exception:
        return None


def get_cached_analysis(ticker: str, max_age_minutes: int = 240) -> Optional[dict]:
    """Return the latest cached analysis for a ticker if recent enough."""
    if not is_configured():
        return None
    try:
        resp = _requests.get(
            f"{_SUPABASE_URL}/rest/v1/{_ANALYSIS_TABLE}",
            headers=_headers(),
            params={
                "ticker": f"eq.{ticker.upper()}",
                "order": "cached_at.desc",
                "limit": "1",
            },
            timeout=5,
        )
        rows = resp.json()
        if not isinstance(rows, list) or not rows:
            return None
        row = rows[0]
        cached_at = _parse_dt(row.get("cached_at"))
        if cached_at:
            age_limit = datetime.now(timezone.utc) - timedelta(minutes=max_age_minutes)
            if cached_at < age_limit:
                return None
        result = row.get("result")
        return result if isinstance(result, dict) else None
    except Exception:
        return None


def save_cached_analysis(ticker: str, result: dict) -> bool:
    """Persist a completed analysis snapshot. Gracefully fails if table is absent."""
    if not is_configured() or not result or result.get("error"):
        return False
    try:
        resp = _requests.post(
            f"{_SUPABASE_URL}/rest/v1/{_ANALYSIS_TABLE}",
            headers=_headers(prefer="return=minimal"),
            json={
                "ticker": ticker.upper(),
                "cached_at": datetime.now(timezone.utc).isoformat(),
                "result": result,
            },
            timeout=5,
        )
        return resp.status_code in (200, 201)
    except Exception:
        return False


def get_scan_result_for_ticker(ticker: str, max_scans: int = 10) -> Optional[dict]:
    """Return the most recent scan result entry for a ticker from saved scanner runs."""
    if not is_configured():
        return None
    try:
        resp = _requests.get(
            f"{_SUPABASE_URL}/rest/v1/{_SCAN_TABLE}",
            headers=_headers(),
            params={"order": "scanned_at.desc", "limit": str(max_scans)},
            timeout=5,
        )
        rows = resp.json()
        if not isinstance(rows, list):
            return None
        ticker = ticker.upper()
        for row in rows:
            for result in row.get("results", []) or []:
                if isinstance(result, dict) and (result.get("ticker") or "").upper() == ticker:
                    enriched = dict(result)
                    enriched["scanned_at"] = row.get("scanned_at")
                    return enriched
        return None
    except Exception:
        return None
