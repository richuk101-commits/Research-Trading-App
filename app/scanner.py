"""
Stock universe and batch scan logic for the Top Picks scanner.
Each batch_scan() call handles a slice of UNIVERSE — designed to complete
well within Vercel Hobby's 10-second function timeout.
"""

from app.analyzer import (
    get_safe, _make_session,
    _calc_buffett, _calc_lynch, _calc_graham, _calc_marks,
    _calc_dalio, _calc_burry, _calc_fisher, _calc_wood,
    _calc_greenblatt, _calc_druckenmiller,
    MANAGER_WEIGHTS, _verdict, _compute_signal,
)
import yfinance as yf

# ---------------------------------------------------------------------------
# 60-stock universe across 7 sectors
# ---------------------------------------------------------------------------
UNIVERSE = [
    # Tech (15)
    "AAPL", "MSFT", "GOOGL", "META", "NVDA",
    "AMZN", "AVGO", "AMD",  "QCOM", "CRM",
    "ADBE", "ORCL", "TSLA", "INTC", "IBM",
    # Finance (10)
    "JPM",  "GS",   "MS",   "BAC",  "V",
    "MA",   "AXP",  "BLK",  "BX",   "SCHW",
    # Healthcare (9)
    "JNJ",  "UNH",  "LLY",  "ABBV", "MRK",
    "PFE",  "AMGN", "GILD", "TMO",
    # Consumer (10)
    "WMT",  "COST", "MCD",  "HD",   "TGT",
    "PG",   "KO",   "PEP",  "NKE",  "SBUX",
    # Energy (5)
    "XOM",  "CVX",  "COP",  "EOG",  "SLB",
    # Industrial (6)
    "CAT",  "DE",   "HON",  "BA",   "RTX",  "GE",
    # Other (5)
    "DIS",  "NFLX", "UBER", "SPOT", "ABNB",
]

BATCH_SIZE = 10
TOTAL_BATCHES = (len(UNIVERSE) + BATCH_SIZE - 1) // BATCH_SIZE  # = 6


def batch_scan(batch_index: int) -> list:
    """
    Scan one batch of BATCH_SIZE tickers.
    Returns a list of result dicts (one per ticker that succeeded).
    Skips tickers that fail or time out — never raises.
    """
    start = batch_index * BATCH_SIZE
    tickers = UNIVERSE[start: start + BATCH_SIZE]
    session = _make_session()
    results = []

    for sym in tickers:
        try:
            info = yf.Ticker(sym, session=session).info
            if not info or "shortName" not in info:
                continue

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

            consensus = round(sum(scores[m] * MANAGER_WEIGHTS[m] for m in MANAGER_WEIGHTS))
            scores_list = list(scores.values())
            signal_label, signal_color, signal_note = _compute_signal(consensus, scores_list)

            bullish_count = sum(1 for s in scores_list if s > 65)
            neutral_count = sum(1 for s in scores_list if 40 <= s <= 65)
            bearish_count = sum(1 for s in scores_list if s < 40)

            results.append({
                "ticker":        sym,
                "name":          info.get("shortName", sym),
                "sector":        info.get("sector", ""),
                "price":         round(current_price, 2) if current_price else None,
                "consensus":     consensus,
                "signal_label":  signal_label,
                "signal_color":  signal_color,
                "signal_note":   signal_note,
                "bullish_count": bullish_count,
                "neutral_count": neutral_count,
                "bearish_count": bearish_count,
                "scores":        scores,
            })
        except Exception:
            # Skip failures silently — one bad ticker shouldn't abort the batch
            continue

    return results
