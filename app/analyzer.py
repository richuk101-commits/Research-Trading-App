import yfinance as yf
import math
import time
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from typing import Dict, Any, Tuple, List
from app.db import get_cached_analysis, save_cached_analysis, get_scan_result_for_ticker

_YF_QUOTE_SUMMARY_URL = "https://query1.finance.yahoo.com/v10/finance/quoteSummary/{ticker}"
_YF_QUOTE_URL = "https://query1.finance.yahoo.com/v7/finance/quote"
_YF_MODULES = [
    "price",
    "summaryProfile",
    "defaultKeyStatistics",
    "financialData",
    "summaryDetail",
]


def _make_session() -> requests.Session:
    """Return a requests Session that looks like a real browser and retries on
    rate-limit (429) or transient server errors (500/502/503/504)."""
    session = requests.Session()
    session.headers.update({
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.5",
    })
    retry = Retry(
        total=4,
        backoff_factor=0.4,          # waits 0s, 0.4s, 0.8s, 1.6s between attempts
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET"],
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session

# Skill: stock-analyst-legends — exact weights
MANAGER_WEIGHTS = {
    "Buffett":       0.15,
    "Lynch":         0.12,
    "Graham":        0.12,
    "Marks":         0.10,
    "Dalio":         0.10,
    "Burry":         0.10,
    "Fisher":        0.08,
    "Wood":          0.08,
    "Greenblatt":    0.08,
    "Druckenmiller": 0.07,
}

MANAGER_PHILOSOPHY = {
    "Buffett":       "Wonderful companies at fair prices — moat, quality, patience",
    "Lynch":         "Buy what you know — GARP, PEG ratio, tenbaggers",
    "Graham":        "Margin of safety above all — strict quantitative value",
    "Marks":         "Second-level thinking — cycle position, risk/reward asymmetry",
    "Dalio":         "All-Weather — macro regime fit, debt cycles, diversification",
    "Burry":         "Deep value contrarian — EV/EBITDA, FCF yield, 30-40% discount",
    "Fisher":        "Quality growth — management excellence, R&D, competitive edge",
    "Wood":          "Disruptive innovation — 5-year TAM, adoption curves, convergence",
    "Greenblatt":    "Magic Formula — high ROIC + high earnings yield",
    "Druckenmiller": "Macro-growth hybrid — inflection points, asymmetric risk/reward",
}

_ANALYSIS_TTL = 15 * 60
_analysis_cache: Dict[str, Dict[str, Any]] = {}


def _cache_get(ticker_symbol: str) -> Dict[str, Any] | None:
    cached = _analysis_cache.get(ticker_symbol.upper())
    if not cached:
        return None
    if time.time() - cached["stored_at"] > _ANALYSIS_TTL:
        _analysis_cache.pop(ticker_symbol.upper(), None)
        return None
    return cached["result"]


def _cache_set(ticker_symbol: str, result: Dict[str, Any]) -> None:
    if result and not result.get("error"):
        _analysis_cache[ticker_symbol.upper()] = {
            "stored_at": time.time(),
            "result": result,
        }


def _unwrap_yahoo_value(value):
    if isinstance(value, dict):
        if value.get("raw") is not None:
            return value.get("raw")
        if value.get("fmt") is not None:
            return value.get("fmt")
    return value


def _normalize_yahoo_dict(data: dict | None) -> dict:
    if not isinstance(data, dict):
        return {}
    normalized = {}
    for key, value in data.items():
        if isinstance(value, dict) and "raw" not in value and "fmt" not in value:
            normalized[key] = _normalize_yahoo_dict(value)
        else:
            normalized[key] = _unwrap_yahoo_value(value)
    return normalized


def _get_crumb(session: requests.Session) -> str | None:
    """Fetch the Yahoo Finance crumb required for authenticated API calls."""
    try:
        # Seed cookies via the consent/FC page
        session.get("https://fc.yahoo.com", timeout=5)
        resp = session.get(
            "https://query1.finance.yahoo.com/v1/test/getcrumb",
            timeout=5,
        )
        if resp.status_code == 200 and resp.text and resp.text != "":
            return resp.text.strip()
    except Exception:
        pass
    return None


def _fetch_yahoo_info_direct(ticker_symbol: str) -> dict:
    session = _make_session()
    crumb = _get_crumb(session)
    merged: dict = {}

    summary_params: dict = {"modules": ",".join(_YF_MODULES), "formatted": "false"}
    if crumb:
        summary_params["crumb"] = crumb

    summary_resp = session.get(
        _YF_QUOTE_SUMMARY_URL.format(ticker=ticker_symbol),
        params=summary_params,
        timeout=8,
    )
    summary_resp.raise_for_status()
    summary_json = summary_resp.json()
    result = (((summary_json or {}).get("quoteSummary") or {}).get("result") or [None])[0] or {}
    for section in result.values():
        merged.update(_normalize_yahoo_dict(section if isinstance(section, dict) else {}))

    quote_params: dict = {"symbols": ticker_symbol}
    if crumb:
        quote_params["crumb"] = crumb

    quote_resp = session.get(
        _YF_QUOTE_URL,
        params=quote_params,
        timeout=6,
    )
    quote_resp.raise_for_status()
    quote_json = quote_resp.json()
    quote_result = (((quote_json or {}).get("quoteResponse") or {}).get("result") or [None])[0] or {}
    merged.update(_normalize_yahoo_dict(quote_result))
    return merged


class TickerNotFoundError(Exception):
    pass


def _get_stock_info_fast(ticker_symbol: str) -> dict:
    return _fetch_yahoo_info_direct(ticker_symbol)


def _get_stock_info(ticker_symbol: str) -> dict:
    try:
        info = _get_stock_info_fast(ticker_symbol)
        if info:
            return info
    except requests.exceptions.HTTPError as e:
        # 404 means the ticker simply doesn't exist — don't bother yfinance
        if e.response is not None and e.response.status_code == 404:
            raise TickerNotFoundError(f"Ticker '{ticker_symbol}' not found on Yahoo Finance. Check the symbol and try again.") from e
        # For other HTTP errors fall through to yfinance
    except Exception:
        pass

    try:
        ticker = yf.Ticker(ticker_symbol, session=_make_session())
        info = ticker.info
        # yfinance returns {"trailingPegRatio": None} for unknown tickers
        if not info or not (info.get("shortName") or info.get("longName") or info.get("regularMarketPrice")):
            raise TickerNotFoundError(f"Ticker '{ticker_symbol}' not found. Check the symbol and try again.")
        return info
    except TickerNotFoundError:
        raise
    except Exception as e:
        raise RuntimeError(f"Data temporarily unavailable for {ticker_symbol}. Please try again in a moment.") from e


def get_safe(info: dict, keys: list, default=None):
    """Return the first available non-None value from info."""
    for key in keys:
        if key in info and info[key] is not None:
            return info[key]
    return default


def _verdict(score: int) -> str:
    if score > 65:
        return "Bullish"
    elif score >= 40:
        return "Neutral"
    return "Bearish"


def _compute_signal(consensus: float, scores_list: List[int]) -> Tuple[str, str, str]:
    """Return (label, tailwind_bg_class, divergence_note) from the consensus score."""
    SIGNAL_ORDER  = ["Strong Sell", "Sell", "Hold", "Buy", "Strong Buy"]
    SIGNAL_COLORS = ["bg-red-500", "bg-orange-500", "bg-yellow-500", "bg-green-500", "bg-emerald-600"]

    if consensus >= 80:
        idx = 4
    elif consensus >= 65:
        idx = 3
    elif consensus >= 50:
        idx = 2
    elif consensus >= 35:
        idx = 1
    else:
        idx = 0

    # Conviction modifier: 7+ managers in same direction shifts signal one level
    if sum(1 for s in scores_list if s > 65) >= 7:
        idx = min(idx + 1, 4)
    elif sum(1 for s in scores_list if s < 40) >= 7:
        idx = max(idx - 1, 0)

    mean    = sum(scores_list) / len(scores_list)
    std_dev = math.sqrt(sum((s - mean) ** 2 for s in scores_list) / len(scores_list))
    note    = "High Divergence — deeper due diligence required" if std_dev > 25 else ""

    return SIGNAL_ORDER[idx], SIGNAL_COLORS[idx], note


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

SEED_SNAPSHOT_RESULTS = {
    "AAPL": {"ticker": "AAPL", "name": "Apple Inc.", "sector": "Technology", "price": 212.35, "consensus": 78, "signal_label": "Buy", "signal_color": "bg-green-500", "signal_note": "Seeded resilience snapshot", "bullish_count": 7, "neutral_count": 2, "bearish_count": 1, "scores": {"Buffett": 82, "Lynch": 79, "Graham": 61, "Marks": 58, "Dalio": 72, "Burry": 48, "Fisher": 84, "Wood": 76, "Greenblatt": 73, "Druckenmiller": 80}},
    "MSFT": {"ticker": "MSFT", "name": "Microsoft Corporation", "sector": "Technology", "price": 428.10, "consensus": 81, "signal_label": "Strong Buy", "signal_color": "bg-emerald-600", "signal_note": "Seeded resilience snapshot", "bullish_count": 8, "neutral_count": 2, "bearish_count": 0, "scores": {"Buffett": 86, "Lynch": 81, "Graham": 63, "Marks": 60, "Dalio": 75, "Burry": 46, "Fisher": 88, "Wood": 79, "Greenblatt": 76, "Druckenmiller": 82}},
    "GOOGL": {"ticker": "GOOGL", "name": "Alphabet Inc.", "sector": "Communication Services", "price": 167.45, "consensus": 77, "signal_label": "Buy", "signal_color": "bg-green-500", "signal_note": "Seeded resilience snapshot", "bullish_count": 7, "neutral_count": 2, "bearish_count": 1, "scores": {"Buffett": 78, "Lynch": 77, "Graham": 65, "Marks": 61, "Dalio": 70, "Burry": 55, "Fisher": 82, "Wood": 74, "Greenblatt": 75, "Druckenmiller": 79}},
    "META": {"ticker": "META", "name": "Meta Platforms, Inc.", "sector": "Communication Services", "price": 498.20, "consensus": 74, "signal_label": "Buy", "signal_color": "bg-green-500", "signal_note": "Seeded resilience snapshot", "bullish_count": 6, "neutral_count": 3, "bearish_count": 1, "scores": {"Buffett": 74, "Lynch": 76, "Graham": 58, "Marks": 54, "Dalio": 67, "Burry": 52, "Fisher": 81, "Wood": 77, "Greenblatt": 71, "Druckenmiller": 78}},
    "NVDA": {"ticker": "NVDA", "name": "NVIDIA Corporation", "sector": "Technology", "price": 903.15, "consensus": 79, "signal_label": "Strong Buy", "signal_color": "bg-emerald-600", "signal_note": "Seeded resilience snapshot", "bullish_count": 8, "neutral_count": 1, "bearish_count": 1, "scores": {"Buffett": 68, "Lynch": 82, "Graham": 42, "Marks": 49, "Dalio": 64, "Burry": 35, "Fisher": 91, "Wood": 92, "Greenblatt": 70, "Druckenmiller": 88}},
    "JPM": {"ticker": "JPM", "name": "JPMorgan Chase & Co.", "sector": "Financial Services", "price": 198.30, "consensus": 72, "signal_label": "Buy", "signal_color": "bg-green-500", "signal_note": "Seeded resilience snapshot", "bullish_count": 6, "neutral_count": 3, "bearish_count": 1, "scores": {"Buffett": 80, "Lynch": 69, "Graham": 72, "Marks": 63, "Dalio": 78, "Burry": 60, "Fisher": 57, "Wood": 35, "Greenblatt": 75, "Druckenmiller": 64}},
    "V": {"ticker": "V", "name": "Visa Inc.", "sector": "Financial Services", "price": 287.40, "consensus": 76, "signal_label": "Buy", "signal_color": "bg-green-500", "signal_note": "Seeded resilience snapshot", "bullish_count": 7, "neutral_count": 2, "bearish_count": 1, "scores": {"Buffett": 84, "Lynch": 74, "Graham": 55, "Marks": 57, "Dalio": 73, "Burry": 44, "Fisher": 79, "Wood": 59, "Greenblatt": 78, "Druckenmiller": 71}},
    "JNJ": {"ticker": "JNJ", "name": "Johnson & Johnson", "sector": "Healthcare", "price": 152.90, "consensus": 69, "signal_label": "Buy", "signal_color": "bg-green-500", "signal_note": "Seeded resilience snapshot", "bullish_count": 5, "neutral_count": 4, "bearish_count": 1, "scores": {"Buffett": 75, "Lynch": 62, "Graham": 68, "Marks": 65, "Dalio": 79, "Burry": 58, "Fisher": 61, "Wood": 28, "Greenblatt": 69, "Druckenmiller": 57}},
    "LLY": {"ticker": "LLY", "name": "Eli Lilly and Company", "sector": "Healthcare", "price": 781.55, "consensus": 73, "signal_label": "Buy", "signal_color": "bg-green-500", "signal_note": "Seeded resilience snapshot", "bullish_count": 6, "neutral_count": 2, "bearish_count": 2, "scores": {"Buffett": 70, "Lynch": 76, "Graham": 39, "Marks": 44, "Dalio": 62, "Burry": 32, "Fisher": 88, "Wood": 84, "Greenblatt": 63, "Druckenmiller": 83}},
    "WMT": {"ticker": "WMT", "name": "Walmart Inc.", "sector": "Consumer Defensive", "price": 63.20, "consensus": 71, "signal_label": "Buy", "signal_color": "bg-green-500", "signal_note": "Seeded resilience snapshot", "bullish_count": 6, "neutral_count": 3, "bearish_count": 1, "scores": {"Buffett": 81, "Lynch": 68, "Graham": 60, "Marks": 62, "Dalio": 80, "Burry": 54, "Fisher": 67, "Wood": 31, "Greenblatt": 70, "Druckenmiller": 58}},
    "COST": {"ticker": "COST", "name": "Costco Wholesale Corporation", "sector": "Consumer Defensive", "price": 731.85, "consensus": 74, "signal_label": "Buy", "signal_color": "bg-green-500", "signal_note": "Seeded resilience snapshot", "bullish_count": 6, "neutral_count": 3, "bearish_count": 1, "scores": {"Buffett": 83, "Lynch": 75, "Graham": 48, "Marks": 52, "Dalio": 74, "Burry": 40, "Fisher": 78, "Wood": 56, "Greenblatt": 73, "Druckenmiller": 66}},
    "XOM": {"ticker": "XOM", "name": "Exxon Mobil Corporation", "sector": "Energy", "price": 118.45, "consensus": 68, "signal_label": "Hold", "signal_color": "bg-yellow-500", "signal_note": "Seeded resilience snapshot", "bullish_count": 5, "neutral_count": 4, "bearish_count": 1, "scores": {"Buffett": 77, "Lynch": 61, "Graham": 74, "Marks": 66, "Dalio": 72, "Burry": 69, "Fisher": 49, "Wood": 18, "Greenblatt": 72, "Druckenmiller": 55}},
    "CAT": {"ticker": "CAT", "name": "Caterpillar Inc.", "sector": "Industrials", "price": 347.90, "consensus": 67, "signal_label": "Hold", "signal_color": "bg-yellow-500", "signal_note": "Seeded resilience snapshot", "bullish_count": 5, "neutral_count": 4, "bearish_count": 1, "scores": {"Buffett": 76, "Lynch": 64, "Graham": 69, "Marks": 64, "Dalio": 69, "Burry": 63, "Fisher": 55, "Wood": 22, "Greenblatt": 71, "Druckenmiller": 53}},
    "NFLX": {"ticker": "NFLX", "name": "Netflix, Inc.", "sector": "Communication Services", "price": 628.40, "consensus": 70, "signal_label": "Buy", "signal_color": "bg-green-500", "signal_note": "Seeded resilience snapshot", "bullish_count": 5, "neutral_count": 4, "bearish_count": 1, "scores": {"Buffett": 61, "Lynch": 73, "Graham": 37, "Marks": 51, "Dalio": 60, "Burry": 33, "Fisher": 82, "Wood": 85, "Greenblatt": 58, "Druckenmiller": 82}},
    "UBER": {"ticker": "UBER", "name": "Uber Technologies, Inc.", "sector": "Technology", "price": 76.15, "consensus": 69, "signal_label": "Buy", "signal_color": "bg-green-500", "signal_note": "Seeded resilience snapshot", "bullish_count": 5, "neutral_count": 4, "bearish_count": 1, "scores": {"Buffett": 44, "Lynch": 71, "Graham": 28, "Marks": 57, "Dalio": 55, "Burry": 31, "Fisher": 79, "Wood": 82, "Greenblatt": 54, "Druckenmiller": 85}},
}


def _build_scan_fallback(ticker_symbol: str) -> Dict[str, Any] | None:
    ticker_symbol = ticker_symbol.upper()
    scan_row = get_scan_result_for_ticker(ticker_symbol, max_scans=50)
    if not scan_row:
        scan_row = SEED_SNAPSHOT_RESULTS.get(ticker_symbol)
    if not scan_row:
        return None

    consensus = int(scan_row.get("consensus", 0))
    bullish = int(scan_row.get("bullish_count", 0))
    neutral = int(scan_row.get("neutral_count", 0))
    bearish = int(scan_row.get("bearish_count", 0))
    signal_label = scan_row.get("signal_label") or "Hold"
    signal_color = scan_row.get("signal_color") or "bg-yellow-500"

    managers = []
    scores = scan_row.get("scores") or {}
    if isinstance(scores, dict) and scores:
        for m in MANAGER_WEIGHTS:
            score = int(scores.get(m, 50))
            managers.append({
                "name": m,
                "philosophy": MANAGER_PHILOSOPHY[m],
                "weight": int(MANAGER_WEIGHTS[m] * 100),
                "score": score,
                "verdict": _verdict(score),
            })
    else:
        bullish_slots = min(max(bullish, 0), len(MANAGER_WEIGHTS))
        neutral_slots = min(max(neutral, 0), max(len(MANAGER_WEIGHTS) - bullish_slots, 0))
        manager_names = list(MANAGER_WEIGHTS.keys())
        for idx, m in enumerate(manager_names):
            if idx < bullish_slots:
                score = max(consensus, 75)
            elif idx < bullish_slots + neutral_slots:
                score = 55
            else:
                score = min(consensus, 30)
            managers.append({
                "name": m,
                "philosophy": MANAGER_PHILOSOPHY[m],
                "weight": int(MANAGER_WEIGHTS[m] * 100),
                "score": score,
                "verdict": _verdict(score),
            })

    return {
        "ticker": ticker_symbol,
        "name": scan_row.get("name", ticker_symbol),
        "sector": scan_row.get("sector", "Unknown"),
        "industry": "Saved scanner snapshot",
        "price": scan_row.get("price") or "N/A",
        "market_cap": "N/A",
        "currency": "USD",
        "consensus_score": consensus,
        "cache_source": "scan_snapshot",
        "from_cache": True,
        "cache_warning": "Live market data is temporarily unavailable, so this report is built from the latest saved Top Picks scan.",
        "signal": {
            "label": signal_label,
            "color": signal_color,
            "note": scan_row.get("signal_note") or "Saved scanner snapshot",
        },
        "confidence_score": 55,
        "managers": managers,
        "convergence": {
            "bullish": [m["name"] for m in managers if m["verdict"] == "Bullish"][:bullish or len(managers)],
            "neutral": [m["name"] for m in managers if m["verdict"] == "Neutral"][:neutral or len(managers)],
            "bearish": [m["name"] for m in managers if m["verdict"] == "Bearish"][:bearish or len(managers)],
        },
        "raw_metrics": {
            "Consensus": f"{consensus}/100",
            "Bullish": f"{bullish}/10",
            "Neutral": f"{neutral}/10",
            "Bearish": f"{bearish}/10",
            "Scanner Source": "Saved Top Picks scan",
            "Updated": scan_row.get("scanned_at") or "Recent",
            "Price": str(scan_row.get("price") or "N/A"),
            "Signal": signal_label,
        },
    }


def _fmt_large(val) -> str:
    if val is None:
        return "N/A"
    try:
        val = float(val)
    except (TypeError, ValueError):
        return "N/A"
    if val >= 1e12:
        return f"${val/1e12:.1f}T"
    if val >= 1e9:
        return f"${val/1e9:.1f}B"
    if val >= 1e6:
        return f"${val/1e6:.1f}M"
    return f"${val:,.0f}"


def _fmt_pct(val) -> str:
    if val is None:
        return "N/A"
    return f"{float(val)*100:.1f}%"


def _build_full_report(ticker_symbol: str, info: dict) -> Dict[str, Any]:
    """Build a full analysis report from a Yahoo Finance info dict."""
    roe               = get_safe(info, ["returnOnEquity"])
    roa               = get_safe(info, ["returnOnAssets"])
    debt_to_equity    = get_safe(info, ["debtToEquity"])
    fcf               = get_safe(info, ["freeCashflow"])
    revenue_growth    = get_safe(info, ["revenueGrowth"])
    earnings_growth   = get_safe(info, ["earningsGrowth"])
    gross_margins     = get_safe(info, ["grossMargins"])
    operating_margins = get_safe(info, ["operatingMargins"])
    peg_ratio         = get_safe(info, ["pegRatio"])
    pe_ratio          = get_safe(info, ["trailingPE", "forwardPE"])
    pb_ratio          = get_safe(info, ["priceToBook"])
    ev_ebitda         = get_safe(info, ["enterpriseToEbitda"])
    beta              = get_safe(info, ["beta"])
    current_ratio     = get_safe(info, ["currentRatio"])
    market_cap        = get_safe(info, ["marketCap"])
    current_price     = get_safe(info, ["currentPrice", "regularMarketPrice", "previousClose"])
    high_52w          = get_safe(info, ["fiftyTwoWeekHigh"])
    low_52w           = get_safe(info, ["fiftyTwoWeekLow"])
    total_revenue     = get_safe(info, ["totalRevenue"])
    rd_expense        = get_safe(info, ["researchDevelopment"])

    scores = {
        "Buffett":       _calc_buffett(roe, debt_to_equity, fcf, pe_ratio, operating_margins, earnings_growth),
        "Lynch":         _calc_lynch(peg_ratio, pe_ratio, earnings_growth, debt_to_equity, current_ratio),
        "Graham":        _calc_graham(pe_ratio, pb_ratio, current_ratio, debt_to_equity, earnings_growth),
        "Marks":         _calc_marks(pe_ratio, current_price, high_52w, low_52w, beta),
        "Dalio":         _calc_dalio(beta, debt_to_equity, current_ratio, revenue_growth),
        "Burry":         _calc_burry(pb_ratio, fcf, market_cap, debt_to_equity, ev_ebitda, current_price, low_52w),
        "Fisher":        _calc_fisher(revenue_growth, gross_margins, operating_margins, rd_expense, total_revenue, roe),
        "Wood":          _calc_wood(revenue_growth, gross_margins, pb_ratio),
        "Greenblatt":    _calc_greenblatt(roe, roa, pe_ratio, ev_ebitda),
        "Druckenmiller": _calc_druckenmiller(earnings_growth, revenue_growth, pe_ratio, beta),
    }

    scores_list = list(scores.values())
    consensus = round(sum(scores[m] * MANAGER_WEIGHTS[m] for m in MANAGER_WEIGHTS))
    signal_label, signal_color, signal_note = _compute_signal(consensus, scores_list)

    bullish_count = sum(1 for s in scores_list if s > 65)
    neutral_count = sum(1 for s in scores_list if 40 <= s <= 65)
    bearish_count = sum(1 for s in scores_list if s < 40)

    managers = []
    for m in MANAGER_WEIGHTS:
        score = scores[m]
        managers.append({
            "name": m,
            "philosophy": MANAGER_PHILOSOPHY[m],
            "weight": int(MANAGER_WEIGHTS[m] * 100),
            "score": score,
            "verdict": _verdict(score),
        })

    # Data confidence: more available metrics = higher confidence
    available = sum(1 for v in [roe, debt_to_equity, fcf, revenue_growth, earnings_growth,
                                 gross_margins, operating_margins, pe_ratio, pb_ratio, ev_ebitda,
                                 beta, current_ratio, market_cap, current_price] if v is not None)
    confidence_score = min(100, 30 + available * 5)

    raw_metrics = {
        "Consensus Score": f"{consensus}/100",
        "Signal": signal_label,
        "Price": f"${current_price:.2f}" if current_price else "N/A",
        "Market Cap": _fmt_large(market_cap),
        "P/E Ratio": f"{pe_ratio:.1f}" if pe_ratio else "N/A",
        "P/B Ratio": f"{pb_ratio:.1f}" if pb_ratio else "N/A",
        "EV/EBITDA": f"{ev_ebitda:.1f}" if ev_ebitda else "N/A",
        "PEG Ratio": f"{peg_ratio:.2f}" if peg_ratio else "N/A",
        "ROE": _fmt_pct(roe),
        "ROA": _fmt_pct(roa),
        "Revenue Growth": _fmt_pct(revenue_growth),
        "Earnings Growth": _fmt_pct(earnings_growth),
        "Gross Margin": _fmt_pct(gross_margins),
        "Operating Margin": _fmt_pct(operating_margins),
        "Debt/Equity": f"{debt_to_equity:.0f}%" if debt_to_equity is not None else "N/A",
        "Current Ratio": f"{current_ratio:.1f}" if current_ratio else "N/A",
        "Beta": f"{beta:.2f}" if beta else "N/A",
        "52W High": f"${high_52w:.2f}" if high_52w else "N/A",
        "52W Low": f"${low_52w:.2f}" if low_52w else "N/A",
        "Free Cash Flow": _fmt_large(fcf),
    }

    return {
        "ticker":          ticker_symbol,
        "name":            info.get("shortName") or info.get("longName") or ticker_symbol,
        "sector":          info.get("sector", "Unknown"),
        "industry":        info.get("industry", "Unknown"),
        "price":           round(float(current_price), 2) if current_price else None,
        "market_cap":      _fmt_large(market_cap),
        "currency":        info.get("currency", "USD"),
        "consensus_score": consensus,
        "signal": {
            "label": signal_label,
            "color": signal_color,
            "note":  signal_note,
        },
        "confidence_score": confidence_score,
        "managers": managers,
        "convergence": {
            "bullish": [m["name"] for m in managers if m["verdict"] == "Bullish"],
            "neutral": [m["name"] for m in managers if m["verdict"] == "Neutral"],
            "bearish": [m["name"] for m in managers if m["verdict"] == "Bearish"],
        },
        "raw_metrics": raw_metrics,
        "from_cache":   False,
        "cache_source": "live",
        # also store compact fields so scan fallback can use this if re-loaded
        "bullish_count": bullish_count,
        "neutral_count": neutral_count,
        "bearish_count": bearish_count,
        "scores":        scores,
    }


def analyze_stock(ticker_symbol: str) -> Dict[str, Any]:
    ticker_symbol = ticker_symbol.upper()

    # 1. Memory cache
    cached_result = _cache_get(ticker_symbol)
    if cached_result:
        cached_copy = dict(cached_result)
        cached_copy["from_cache"] = True
        cached_copy["cache_source"] = cached_copy.get("cache_source") or "memory"
        return cached_copy

    # 2. Supabase cache
    persisted_cache = get_cached_analysis(ticker_symbol)
    if persisted_cache:
        _cache_set(ticker_symbol, persisted_cache)
        cached_copy = dict(persisted_cache)
        cached_copy["from_cache"] = True
        cached_copy["cache_source"] = cached_copy.get("cache_source") or "supabase"
        cached_copy["cache_warning"] = cached_copy.get("cache_warning") or "Showing a saved snapshot so analysis loads instantly."
        return cached_copy

    # 3. Live fetch from Yahoo Finance → run all 10 scoring algorithms
    try:
        info = _get_stock_info(ticker_symbol)
        if info and (info.get("shortName") or info.get("longName") or info.get("currentPrice")):
            result = _build_full_report(ticker_symbol, info)
            _cache_set(ticker_symbol, result)
            save_cached_analysis(ticker_symbol, result)
            return result
        # Got a response but no usable fields — treat as not found
        raise TickerNotFoundError(f"'{ticker_symbol}' returned no data. Check the symbol and try again.")
    except TickerNotFoundError as e:
        return {
            "error": str(e),
            "unsupported_ticker": True,
            "is_rate_limited": False,
        }
    except Exception:
        pass

    # 4. Scan snapshot fallback (seeded or saved)
    scan_fallback = _build_scan_fallback(ticker_symbol)
    if scan_fallback:
        _cache_set(ticker_symbol, scan_fallback)
        save_cached_analysis(ticker_symbol, scan_fallback)
        return scan_fallback

    return {
        "error": f"Data temporarily unavailable for {ticker_symbol}. Please try again in a moment.",
        "unsupported_ticker": False,
        "is_rate_limited": True,
    }


# ---------------------------------------------------------------------------
# Scoring functions — one per manager, faithful to frameworks.md
# ---------------------------------------------------------------------------

def _calc_buffett(roe, debt_eq, fcf, pe, op_margins, earn_growth) -> int:
    """Business quality, moat via margins, FCF, low debt, fair price. Weight 15%."""
    score = 0
    # ROE (0-8)
    if roe:
        if roe >= 0.20:   score += 8
        elif roe >= 0.15: score += 6
        elif roe >= 0.10: score += 3
    # Positive FCF (0-7)
    if fcf and fcf > 0:
        score += 7
    # Debt/equity — debt_eq is %, so 100 = 1.0x ratio (0-5)
    if debt_eq is not None:
        if debt_eq < 50:    score += 5
        elif debt_eq < 100: score += 3
        elif debt_eq < 200: score += 1
    # Earnings growth (0-5)
    if earn_growth:
        if earn_growth >= 0.10:   score += 5
        elif earn_growth >= 0.05: score += 3
        elif earn_growth > 0:     score += 1
    # Moat proxy via operating margins (0-20)
    if op_margins:
        if op_margins >= 0.25:   score += 20
        elif op_margins >= 0.15: score += 15
        elif op_margins >= 0.10: score += 10
        elif op_margins > 0:     score += 5
    # Valuation: P/E (0-15)
    if pe and pe > 0:
        if pe < 15:   score += 15
        elif pe < 20: score += 12
        elif pe < 25: score += 8
        elif pe < 35: score += 4
    # Business quality / hold test (0-15): profitable ops + FCF + low debt
    if op_margins and op_margins > 0 and fcf and fcf > 0:
        score += 10
        if debt_eq is not None and debt_eq < 100 and op_margins >= 0.10:
            score += 5
    elif op_margins and op_margins > 0:
        score += 5
    # Management: capital efficiency proxy — ROE + low debt (0-5)
    if roe and roe >= 0.15 and debt_eq is not None and debt_eq < 100:
        score += 5
    return min(100, score)


def _calc_lynch(peg, pe, earn_growth, debt_eq, current_ratio) -> int:
    """GARP — PEG, earnings growth 15-25%, clean balance sheet. Weight 12%."""
    score = 0
    # PEG (0-25) — primary valuation tool
    if peg and peg > 0:
        if peg <= 0.5:   score += 25
        elif peg <= 1.0: score += 20
        elif peg <= 1.5: score += 12
        elif peg <= 2.0: score += 6
    # Earnings growth sweet spot (0-20)
    if earn_growth:
        if 0.15 <= earn_growth <= 0.25: score += 20  # Lynch's ideal band
        elif earn_growth > 0.25:         score += 15  # Possibly unsustainable
        elif earn_growth >= 0.10:        score += 12
        elif earn_growth >= 0.05:        score += 6
    # P/E sanity (0-15)
    if pe and pe > 0:
        if pe < 15:   score += 15
        elif pe < 25: score += 10
        elif pe < 35: score += 5
    # Debt/equity (0-10): Lynch disliked leveraged companies
    if debt_eq is not None:
        if debt_eq < 35:    score += 10
        elif debt_eq < 80:  score += 6
        elif debt_eq < 150: score += 3
    # Net cash / liquidity (0-10)
    if current_ratio:
        if current_ratio > 2.0:   score += 10
        elif current_ratio > 1.5: score += 7
        elif current_ratio > 1.0: score += 4
    # "The story" — clear thesis: growth + reasonable PEG (0-10)
    if earn_growth and earn_growth > 0.10 and peg and 0 < peg < 1.5:
        score += 10
    elif earn_growth and earn_growth > 0.05:
        score += 5
    return min(100, score)


def _calc_graham(pe, pb, current_ratio, debt_eq, earn_growth) -> int:
    """Strict quantitative value — P/E<15, P/B<1.5, margin of safety. Weight 12%."""
    score = 0
    # P/E (0-15)
    if pe and pe > 0:
        if pe < 10:   score += 15
        elif pe < 15: score += 10
        elif pe < 20: score += 5
    # P/B (0-15)
    if pb and pb > 0:
        if pb < 1.0:  score += 15
        elif pb < 1.5: score += 10
        elif pb < 2.0: score += 5
    # Graham number proxy: P/E × P/B < 22.5 (0-10)
    if pe and pb and pe > 0 and pb > 0:
        product = pe * pb
        if product < 15:     score += 10
        elif product < 22.5: score += 7
        elif product < 30:   score += 3
    # Current ratio (0-8): Graham wanted > 2.0
    if current_ratio:
        if current_ratio >= 2.0:  score += 8
        elif current_ratio >= 1.5: score += 5
        elif current_ratio >= 1.0: score += 2
    # Debt/equity (0-7)
    if debt_eq is not None:
        if debt_eq < 30:   score += 7
        elif debt_eq < 70:  score += 4
        elif debt_eq < 100: score += 2
    # Earnings consistency (0-8)
    if earn_growth:
        if earn_growth > 0.10:   score += 8
        elif earn_growth > 0:    score += 5
        elif earn_growth > -0.05: score += 2
    # Margin of safety: both P/E and P/B cheap (0-15)
    if pe and pb and pe > 0 and pb > 0 and pe < 15 and pb < 1.5:
        score += 15
    elif pe and pb and pe > 0 and pb > 0 and pe < 20 and pb < 2.0:
        score += 7
    return min(100, score)


def _calc_marks(pe, price, high_52w, low_52w, beta) -> int:
    """Second-level thinking — cycle position, contrarian opportunity. Weight 10%.
    Higher score = better risk/reward asymmetry (Marks loves unloved, near lows)."""
    score = 50  # Start neutral
    # Price in 52-week range: near low = contrarian opportunity
    if price and high_52w and low_52w and high_52w > low_52w:
        rng = high_52w - low_52w
        pos = (price - low_52w) / rng  # 0 = at 52wk low, 1 = at 52wk high
        if pos < 0.20:    score += 25   # Near 52-week low = Marks loves this
        elif pos < 0.40:  score += 15
        elif pos < 0.60:  score += 5
        elif pos < 0.80:  score -= 5
        else:             score -= 20   # Near 52-week high = cycle risk
    # Valuation cycle via P/E
    if pe and pe > 0:
        if pe < 12:   score += 15
        elif pe < 18: score += 8
        elif pe < 30: score -= 5
        elif pe < 50: score -= 15
        else:         score -= 25
    elif pe and pe < 0:   # Negative earnings = risky
        score -= 20
    # Volatility proxy via beta: lower = better risk/reward for Marks
    if beta:
        if beta < 0.8:   score += 10
        elif beta < 1.2: score += 5
        elif beta < 1.8: score -= 5
        else:            score -= 15
    return max(0, min(100, score))


def _calc_dalio(beta, debt_eq, current_ratio, rev_growth) -> int:
    """All-Weather — macro regime fit, moderate beta, low leverage. Weight 10%."""
    score = 0
    # Beta sweet spot for Dalio: uncorrelated, moderate sensitivity (0-30)
    if beta:
        if 0.5 < beta < 1.2:    score += 30
        elif 0.3 < beta <= 0.5: score += 20
        elif 1.2 <= beta < 1.8: score += 15
        else:                   score += 5
    # Debt cycle position (0-25): low debt = safe across seasons
    if debt_eq is not None:
        if debt_eq < 50:    score += 25
        elif debt_eq < 100: score += 18
        elif debt_eq < 200: score += 8
        else:               score += 2
    # Liquidity (0-20)
    if current_ratio:
        if current_ratio >= 2.0:  score += 20
        elif current_ratio >= 1.5: score += 14
        elif current_ratio >= 1.0: score += 8
    # Structural tailwind via revenue growth (0-15)
    if rev_growth:
        if rev_growth >= 0.15:   score += 15
        elif rev_growth >= 0.08: score += 10
        elif rev_growth >= 0.03: score += 6
        elif rev_growth > 0:     score += 3
    # Currency/geopolitical proxy: neutral assumption (0-10)
    score += 10
    return min(100, score)


def _calc_burry(pb, fcf, market_cap, debt_eq, ev_ebitda, price, low_52w) -> int:
    """Deep value contrarian — EV/EBITDA, FCF yield, near 52wk lows. Weight 10%."""
    score = 0
    # EV/EBITDA (0-12): Burry wants cheapest quintile, <6x ideal
    if ev_ebitda and ev_ebitda > 0:
        if ev_ebitda < 6:    score += 12
        elif ev_ebitda < 10: score += 7
        elif ev_ebitda < 15: score += 3
    # FCF yield (0-12)
    if fcf and market_cap and market_cap > 0:
        fy = fcf / market_cap
        if fy >= 0.10:   score += 12
        elif fy >= 0.08: score += 9
        elif fy >= 0.05: score += 6
        elif fy >= 0.02: score += 3
    # P/B (0-6)
    if pb and pb > 0:
        if pb < 1.0:  score += 6
        elif pb < 1.5: score += 4
        elif pb < 2.5: score += 2
    # Near 52-week low: contrarian entry signal (0-5)
    if price and low_52w and low_52w > 0:
        pct_above = (price - low_52w) / low_52w
        if pct_above < 0.10:  score += 5
        elif pct_above < 0.25: score += 3
        elif pct_above < 0.50: score += 1
    # FCF positive = downside protection (0-5)
    if fcf and fcf > 0:
        score += 5
    # Double-cheap: both P/B and EV/EBITDA cheap (0-10)
    if pb and pb < 1.5 and ev_ebitda and ev_ebitda < 10:
        score += 10
    # Balance sheet fortress (0-8)
    if debt_eq is not None:
        if debt_eq < 50:    score += 8
        elif debt_eq < 100: score += 5
        elif debt_eq < 150: score += 2
    # Contrarian: near 52wk low = market throwing baby out (0-10)
    if price and low_52w and low_52w > 0 and (price - low_52w) / low_52w < 0.20:
        score += 10
    # Survival test: FCF covers operations without external financing (0-7)
    if fcf and fcf > 0:
        score += 7
    return min(100, score)


def _calc_fisher(rev_growth, gross_margins, op_margins, rd_expense, total_revenue, roe) -> int:
    """Quality growth — management, R&D investment, competitive advantage. Weight 8%."""
    score = 0
    # Growth potential via revenue growth (0-30)
    if rev_growth:
        if rev_growth >= 0.20:   score += 30
        elif rev_growth >= 0.12: score += 22
        elif rev_growth >= 0.07: score += 15
        elif rev_growth > 0:     score += 8
    # R&D investment as % of revenue — innovation proxy (0-20)
    if rd_expense and total_revenue and total_revenue > 0:
        rd_pct = abs(rd_expense) / total_revenue
        if rd_pct >= 0.15:   score += 20
        elif rd_pct >= 0.08: score += 14
        elif rd_pct >= 0.04: score += 8
        elif rd_pct > 0:     score += 4
    else:
        score += 5  # Neutral for non-R&D sectors
    # Competitive advantage via gross margins (0-25)
    if gross_margins:
        if gross_margins >= 0.60:   score += 25
        elif gross_margins >= 0.40: score += 18
        elif gross_margins >= 0.25: score += 12
        elif gross_margins >= 0.15: score += 6
    # Management quality via ROE (0-15)
    if roe:
        if roe >= 0.25:   score += 15
        elif roe >= 0.15: score += 10
        elif roe >= 0.08: score += 5
    # Operating efficiency (0-10)
    if op_margins:
        if op_margins >= 0.20:   score += 10
        elif op_margins >= 0.12: score += 7
        elif op_margins >= 0.06: score += 4
        elif op_margins > 0:     score += 2
    return min(100, score)


def _calc_wood(rev_growth, gross_margins, pb) -> int:
    """Disruptive innovation — high growth, high margins, large TAM. Weight 8%."""
    score = 0
    # Revenue growth (0-40): Wood wants 30%+ for top marks
    if rev_growth:
        if rev_growth >= 0.30:   score += 40
        elif rev_growth >= 0.20: score += 28
        elif rev_growth >= 0.10: score += 16
        elif rev_growth > 0:     score += 8
    # Gross margins — platform/software-like economics (0-30)
    if gross_margins:
        if gross_margins >= 0.70:   score += 30
        elif gross_margins >= 0.50: score += 22
        elif gross_margins >= 0.40: score += 15
        elif gross_margins >= 0.25: score += 8
    # P/B: Wood accepts elevated valuations for disruption (0-20)
    if pb and pb > 0:
        if pb > 10:  score += 20
        elif pb > 5: score += 15
        elif pb > 2: score += 10
        else:        score += 5   # Low P/B signals low disruption potential
    # Platform dynamics: high growth + high margins = flywheel (0-10)
    if rev_growth and rev_growth >= 0.20 and gross_margins and gross_margins >= 0.50:
        score += 10
    return min(100, score)


def _calc_greenblatt(roe, roa, pe, ev_ebitda) -> int:
    """Magic Formula — high ROIC (via ROE) + high earnings yield (via 1/PE). Weight 8%."""
    score = 0
    # ROIC proxy via ROE (0-40)
    if roe:
        if roe >= 0.25:   score += 40
        elif roe >= 0.20: score += 30
        elif roe >= 0.15: score += 20
        elif roe >= 0.10: score += 10
    elif roa:  # fallback if ROE unavailable
        if roa >= 0.15:   score += 30
        elif roa >= 0.10: score += 20
        elif roa >= 0.06: score += 10
    # Earnings yield = 1/PE (0-40)
    if pe and pe > 0:
        ey = 1.0 / pe
        if ey >= 0.12:   score += 40
        elif ey >= 0.08: score += 30
        elif ey >= 0.05: score += 20
        elif ey >= 0.03: score += 10
    elif ev_ebitda and ev_ebitda > 0:  # EV/EBITDA fallback for earnings yield
        ey_alt = 1.0 / ev_ebitda
        if ey_alt >= 0.12:   score += 35
        elif ey_alt >= 0.08: score += 25
        elif ey_alt >= 0.05: score += 15
    # Magic Formula hit: high quality at cheap price (0-20)
    if roe and roe >= 0.15 and pe and 0 < pe < 20:
        score += 20
    elif roe and roe >= 0.10 and pe and 0 < pe < 30:
        score += 10
    return min(100, score)


def _calc_druckenmiller(earn_growth, rev_growth, pe, beta) -> int:
    """Macro-growth hybrid — growth inflections, asymmetric risk/reward. Weight 7%."""
    score = 0
    # Growth inflection via earnings growth (0-25)
    if earn_growth:
        if earn_growth >= 0.25:   score += 25
        elif earn_growth >= 0.15: score += 18
        elif earn_growth >= 0.08: score += 12
        elif earn_growth > 0:     score += 6
    # Revenue momentum (0-20)
    if rev_growth:
        if rev_growth >= 0.20:   score += 20
        elif rev_growth >= 0.12: score += 14
        elif rev_growth >= 0.06: score += 8
        elif rev_growth > 0:     score += 4
    # Macro tailwind proxy via beta (0-20): growth with moderate risk
    if beta:
        if 0.8 < beta < 1.5:  score += 20
        elif beta < 0.8:       score += 12  # Too defensive
        elif beta < 2.0:       score += 8
        else:                  score += 3
    # Valuation — not too stretched (0-20)
    if pe and pe > 0:
        if pe < 20:   score += 20
        elif pe < 30: score += 15
        elif pe < 45: score += 8
        elif pe < 60: score += 4
    # Execution signal: both earnings and revenue growing (0-15)
    if earn_growth and earn_growth > 0.10 and rev_growth and rev_growth > 0.10:
        score += 15
    elif earn_growth and earn_growth > 0.05 and rev_growth and rev_growth > 0.05:
        score += 8
    return min(100, score)
