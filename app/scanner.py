"""
Stock universe and batch scan logic for the Top Picks scanner.
Each batch_scan() call handles a slice of UNIVERSE — tickers within each
batch are fetched in parallel to keep wall-clock time within Vercel's timeout.
"""

from concurrent.futures import ThreadPoolExecutor, as_completed

from app.analyzer import (
    get_safe, _get_stock_info_fast, _make_session, _get_crumb,
    _calc_buffett, _calc_lynch, _calc_graham, _calc_marks,
    _calc_dalio, _calc_burry, _calc_fisher, _calc_wood,
    _calc_greenblatt, _calc_druckenmiller,
    MANAGER_WEIGHTS, _compute_signal,
)

UNIVERSE = [
    # === TECHNOLOGY (52) ===
    # Mega-cap
    "AAPL", "MSFT", "NVDA", "GOOGL", "META", "AMZN", "TSLA", "AMD",
    # Software / SaaS
    "ADBE", "CRM", "ORCL", "NOW", "INTU", "SNPS", "CDNS", "ANSS",
    "CTSH", "ACN", "IBM", "DELL",
    # Semiconductors
    "INTC", "QCOM", "AVGO", "TXN", "MU", "AMAT", "LRCX", "KLAC",
    "MRVL", "ON", "TER", "ENTG",
    # Cloud / Cybersecurity / AI
    "PLTR", "SNOW", "CRWD", "PANW", "FTNT", "NET", "DDOG", "ZS",
    "OKTA", "HUBS", "PAYC", "VEEV",
    # Fintech payments
    "PYPL", "FISV", "FI", "GPN",
    # === FINANCIALS (33) ===
    "JPM", "V", "MA", "BAC", "GS", "MS", "WFC", "AXP", "BLK", "SCHW",
    "C", "COF", "USB", "PNC", "TFC", "SPGI", "MCO", "ICE", "CME",
    "BX", "KKR", "APO", "ARES",
    "MTB", "RF", "HBAN", "KEY", "CFG", "SYF", "DFS",
    "CB", "PGR", "MET",
    # === HEALTHCARE (30) ===
    "LLY", "UNH", "JNJ", "ABBV", "MRK", "PFE", "TMO", "ABT", "AMGN",
    "GILD", "REGN", "VRTX", "BMY", "CVS", "CI", "ELV", "ISRG", "MDT",
    "DHR", "BSX", "SYK", "ZBH", "BDX", "HCA", "HUM", "MOH",
    "IDXX", "A", "IQV", "RMD",
    # === CONSUMER STAPLES (15) ===
    "WMT", "COST", "PG", "KO", "PEP", "MDLZ", "CL", "MKC", "GIS",
    "CHD", "STZ", "KHC", "CAG", "SJM", "HSY",
    # === CONSUMER DISCRETIONARY (22) ===
    "MCD", "HD", "NKE", "SBUX", "TGT", "LOW", "TJX", "ROST", "ABNB",
    "BKNG", "CMG", "HLT", "MAR", "YUM", "DRI", "POOL", "ULTA",
    "F", "GM", "TSCO", "EXPE", "LVS",
    # === ENERGY (15) ===
    "XOM", "CVX", "COP", "EOG", "SLB", "MPC", "VLO", "OXY", "PSX",
    "DVN", "HAL", "BKR", "FANG", "APA", "MRO",
    # === INDUSTRIALS (28) ===
    "CAT", "HON", "RTX", "DE", "BA", "GE", "LMT", "NOC", "GD", "UNP",
    "UPS", "FDX", "CTAS", "ETN", "EMR", "PH", "MMM", "ITW", "ROK",
    "DOV", "XYL", "OTIS", "CARR", "TT", "IR", "FAST", "RSG", "WM",
    # === COMMUNICATION & MEDIA (12) ===
    "NFLX", "DIS", "T", "VZ", "TMUS", "CHTR", "CMCSA",
    "PARA", "OMC", "IPG", "FOXA", "WBD",
    # === MATERIALS (10) ===
    "LIN", "APD", "SHW", "ECL", "FCX", "NEM", "ALB", "CF", "MOS", "FMC",
    # === REITs (12) ===
    "PLD", "AMT", "EQIX", "CCI", "SPG", "O", "WELL", "PSA", "AVB", "EQR",
    "VICI", "WPC",
    # === UTILITIES (10) ===
    "NEE", "DUK", "SO", "D", "AEP", "EXC", "XEL", "PCG", "AWK", "ES",
    # === GROWTH / FINTECH / INTERNATIONAL (18) ===
    "UBER", "COIN", "SOFI", "SQ", "AFRM",
    "SHOP", "MELI", "SE", "NU", "DKNG",
    "HOOD", "DASH", "LYFT", "RBLX", "U",
    "TSM", "ASML", "SAP",
]  # 257 total

BATCH_SIZE = 5
TOTAL_BATCHES = (len(UNIVERSE) + BATCH_SIZE - 1) // BATCH_SIZE


def _scan_one(sym: str, session=None, crumb=None) -> dict | None:
    """Fetch and score a single ticker. Returns None on any failure."""
    try:
        info = _get_stock_info_fast(sym, session=session, crumb=crumb)
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
    One session and crumb are fetched once and shared across all tickers
    in the batch — saves 2 round-trips per ticker.
    Skips tickers that fail or time out — never raises.
    """
    start = batch_index * BATCH_SIZE
    tickers = UNIVERSE[start: start + BATCH_SIZE]

    # One session + crumb for the whole batch
    session = _make_session()
    crumb = _get_crumb(session)

    results = []
    with ThreadPoolExecutor(max_workers=BATCH_SIZE) as executor:
        futures = {
            executor.submit(_scan_one, sym, session, crumb): sym
            for sym in tickers
        }
        for future in as_completed(futures):
            result = future.result()
            if result is not None:
                results.append(result)

    # Sort to preserve consistent ordering (parallel completion is non-deterministic)
    order = {sym: i for i, sym in enumerate(tickers)}
    results.sort(key=lambda r: order.get(r["ticker"], 999))
    return results
