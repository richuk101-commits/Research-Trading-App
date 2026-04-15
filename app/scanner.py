"""
Stock universe and batch scan logic for the Top Picks scanner.
Each batch_scan() call handles a slice of UNIVERSE — tickers within each
batch are fetched in parallel to keep wall-clock time within Vercel's timeout.
"""

from concurrent.futures import ThreadPoolExecutor, as_completed

from app.analyzer import (
    get_safe, _get_stock_info_fast,
    _calc_buffett, _calc_lynch, _calc_graham, _calc_marks,
    _calc_dalio, _calc_burry, _calc_fisher, _calc_wood,
    _calc_greenblatt, _calc_druckenmiller,
    MANAGER_WEIGHTS, _compute_signal,
)

UNIVERSE = [
    # Mega-cap Tech
    "AAPL", "MSFT", "NVDA", "GOOGL", "META", "AMZN", "TSLA", "AMD",
    "ADBE", "CRM", "ORCL", "INTC", "QCOM", "AVGO", "TXN",
    # Financials
    "JPM", "V", "MA", "BAC", "GS", "MS", "WFC", "AXP", "BLK", "SCHW",
    # Healthcare
    "LLY", "UNH", "JNJ", "ABBV", "MRK", "PFE", "TMO", "ABT", "AMGN",
    # Consumer Staples / Discretionary
    "WMT", "COST", "PG", "KO", "PEP", "MCD", "HD", "NKE", "SBUX", "TGT",
    # Energy
    "XOM", "CVX",
    # Industrials
    "CAT", "HON", "RTX", "DE", "BA",
    # Communication / Other
    "NFLX", "DIS", "UBER", "PYPL", "T",
]

BATCH_SIZE = 5
TOTAL_BATCHES = (len(UNIVERSE) + BATCH_SIZE - 1) // BATCH_SIZE


def _scan_one(sym: str) -> dict | None:
    """Fetch and score a single ticker. Returns None on any failure."""
    try:
        info = _get_stock_info_fast(sym)
        if not info or not (info.get("shortName") or info.get("longName")):
            return None

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

        return {
            "ticker":        sym,
            "name":          info.get("shortName") or info.get("longName") or sym,
            "sector":        info.get("sector", ""),
            "price":         round(float(current_price), 2) if current_price else None,
            "consensus":     consensus,
            "signal_label":  signal_label,
            "signal_color":  signal_color,
            "signal_note":   signal_note,
            "bullish_count": bullish_count,
            "neutral_count": neutral_count,
            "bearish_count": bearish_count,
            "scores":        scores,
        }
    except Exception:
        return None


def batch_scan(batch_index: int) -> list:
    """
    Scan one batch of BATCH_SIZE tickers in parallel.
    Returns a list of result dicts (one per ticker that succeeded).
    Skips tickers that fail or time out — never raises.
    """
    start = batch_index * BATCH_SIZE
    tickers = UNIVERSE[start: start + BATCH_SIZE]

    results = []
    with ThreadPoolExecutor(max_workers=BATCH_SIZE) as executor:
        futures = {executor.submit(_scan_one, sym): sym for sym in tickers}
        for future in as_completed(futures):
            result = future.result()
            if result is not None:
                results.append(result)

    # Sort to preserve consistent ordering (parallel completion is non-deterministic)
    order = {sym: i for i, sym in enumerate(tickers)}
    results.sort(key=lambda r: order.get(r["ticker"], 999))
    return results
