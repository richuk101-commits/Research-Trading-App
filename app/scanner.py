"""
Stock universe and batch scan logic for the Top Picks scanner.
Each batch_scan() call handles a slice of UNIVERSE — tickers within each
batch are fetched in parallel to keep wall-clock time within Vercel's timeout.

Daily scan: get_daily_tickers() picks DAILY_SCAN_SIZE tickers seeded by
today's date so every visitor sees the same 10 stocks on a given day.
scan_tickers() runs them in parallel and returns scored results.
"""

import random
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date as _date

from app.analyzer import (
    get_safe, _get_stock_info_fast, _make_session, _get_crumb,
    _calc_buffett, _calc_lynch, _calc_graham, _calc_marks,
    _calc_dalio, _calc_burry, _calc_fisher, _calc_wood,
    _calc_greenblatt, _calc_druckenmiller,
    MANAGER_WEIGHTS, _compute_signal,
)

DAILY_SCAN_SIZE = 10

UNIVERSE = [
    # === TECHNOLOGY (72) ===
    # Mega-cap platforms
    "AAPL", "MSFT", "NVDA", "GOOGL", "META", "AMZN", "TSLA", "AMD",
    # Enterprise software / SaaS
    "ADBE", "CRM", "ORCL", "NOW", "INTU", "SNPS", "CDNS", "ANSS",
    "CTSH", "ACN", "IBM", "DELL", "HPE", "HPQ",
    "VEEV", "HUBS", "PAYC", "PCTY", "TWLO", "ZI", "NTNX", "MDB",
    "CFLT", "GTLB", "BILL", "ESTC", "DOMO",
    # Semiconductors
    "INTC", "QCOM", "AVGO", "TXN", "MU", "AMAT", "LRCX", "KLAC",
    "MRVL", "ON", "TER", "ENTG", "MCHP", "SWKS", "QRVO", "MPWR",
    # Cloud / Cybersecurity / AI
    "PLTR", "SNOW", "CRWD", "PANW", "FTNT", "NET", "DDOG", "ZS",
    "OKTA", "CSCO",
    # Consumer Internet
    "PINS", "SNAP", "EBAY", "ETSY", "GDDY", "TRIP", "YELP",
    # Fintech / Payments
    "PYPL", "FISV", "FI", "GPN", "WEX", "EPAM",
    # === FINANCIALS (52) ===
    # Banks — large
    "JPM", "BAC", "WFC", "C", "USB", "PNC", "TFC", "MTB",
    "RF", "HBAN", "KEY", "CFG", "ZION", "CMA", "WAL",
    # Capital markets
    "GS", "MS", "SCHW", "AXP", "BLK", "V", "MA",
    "SPGI", "MCO", "ICE", "CME", "BX", "KKR", "APO", "ARES",
    "TROW", "BEN", "IVZ", "NTRS", "STT", "BK", "AMP",
    # Insurance
    "CB", "PGR", "MET", "AIG", "AFL", "ALL", "HIG", "TRV",
    "PRU", "EQH", "MKL", "WRB", "L",
    # Fintech
    "SYF", "DFS", "COF", "PYPL", "SQ",
    # === HEALTHCARE (45) ===
    # Large pharma / biotech
    "LLY", "UNH", "JNJ", "ABBV", "MRK", "PFE", "TMO", "ABT", "AMGN",
    "GILD", "REGN", "VRTX", "BMY", "BIIB", "MRNA", "ALNY", "NBIX",
    "EXAS", "INCY", "JAZZ", "VTRS",
    # Med devices
    "ISRG", "MDT", "DHR", "BSX", "SYK", "ZBH", "BDX", "EW",
    "ALGN", "HOLX", "PODD", "RMD", "IDXX", "MTD", "WAT",
    # Health services / PBMs
    "CVS", "CI", "ELV", "HCA", "HUM", "MOH", "CNC",
    "A", "IQV", "NTRA", "TDOC",
    # === CONSUMER STAPLES (22) ===
    "WMT", "COST", "PG", "KO", "PEP", "MDLZ", "CL", "MKC", "GIS",
    "CHD", "STZ", "KHC", "CAG", "SJM", "HSY", "HRL", "CPB",
    "EL", "CLX", "MO", "PM", "ADM",
    # === CONSUMER DISCRETIONARY (35) ===
    # Restaurants / hospitality
    "MCD", "SBUX", "CMG", "YUM", "DRI", "HLT", "MAR", "WYNN", "MGM", "LVS",
    # Retail / e-commerce
    "HD", "LOW", "TGT", "TJX", "ROST", "TSCO", "ULTA", "BBY",
    "AZO", "ORLY", "GPC",
    # Autos
    "NKE", "F", "GM", "APTV", "LEA",
    # Travel / leisure
    "ABNB", "BKNG", "EXPE", "POOL", "RCL", "CCL", "NCLH",
    # Apparel / luxury
    "LULU", "RH", "WSM", "W",
    # === ENERGY (25) ===
    "XOM", "CVX", "COP", "EOG", "SLB", "MPC", "VLO", "OXY", "PSX",
    "DVN", "HAL", "BKR", "FANG", "APA", "MRO",
    "KMI", "WMB", "OKE", "TRGP", "HES",
    "PR", "SM", "MTDR", "HP", "NOV",
    # === INDUSTRIALS (38) ===
    # Defence / aerospace
    "LMT", "RTX", "NOC", "GD", "BA", "TDG", "HEICO",
    # Diversified / machinery
    "HON", "GE", "CAT", "DE", "ETN", "EMR", "PH", "MMM", "ITW",
    "ROK", "DOV", "CARR", "OTIS", "TT", "IR", "FAST",
    # Transport / logistics
    "UNP", "NSC", "CSX", "UPS", "FDX", "PCAR", "CPRT",
    "DAL", "UAL", "LUV", "AAL",
    # Waste / services
    "RSG", "WM", "CTAS", "VRSK", "BAH",
    # === COMMUNICATION & MEDIA (18) ===
    "NFLX", "DIS", "T", "VZ", "TMUS", "CHTR", "CMCSA", "WBD",
    "PARA", "FOXA", "OMC", "IPG",
    "SNAP", "MTCH", "LYV", "TTWO", "EA",
    "SPOT",
    # === MATERIALS (18) ===
    "LIN", "APD", "SHW", "ECL", "FCX", "NEM", "ALB", "CF", "MOS", "FMC",
    "PPG", "RPM", "IFF", "MLM", "VMC",
    "BALL", "PKG", "IP",
    # === REITs (20) ===
    "PLD", "AMT", "EQIX", "CCI", "SPG", "O", "WELL", "PSA", "AVB", "EQR",
    "VICI", "WPC", "ARE", "BXP", "KIM", "REG", "NNN",
    "EXR", "CUBE", "REXR",
    # === UTILITIES (18) ===
    "NEE", "DUK", "SO", "D", "AEP", "EXC", "XEL", "PCG", "AWK", "ES",
    "WEC", "CMS", "LNT", "NI", "ATO", "SRE", "ETR", "PPL",
    # === GROWTH / FINTECH / INTERNATIONAL (42) ===
    # US growth / fintech
    "UBER", "COIN", "SOFI", "AFRM", "HOOD", "DASH", "LYFT",
    "DKNG", "RBLX", "U", "PTON", "CHWY",
    # Global e-commerce / platforms
    "SHOP", "MELI", "SE", "NU",
    # International megacaps (ADRs)
    "TSM", "ASML", "SAP", "NVO", "INFY",
    "BABA", "JD", "PDD", "BIDU",
    "NIO", "XPEV", "LI",
    "SONY", "TM", "HMC",
    "RACE", "LVMUY", "HESAY",
    # Misc / thematic
    "SQ", "HOOD", "AFRM",
    "SMCI", "ARM", "IONQ",
    "MSTR", "RIOT", "MARA",
]

# Deduplicate while preserving order (some tickers appear twice in source)
_seen: set = set()
_deduped: list = []
for _t in UNIVERSE:
    if _t not in _seen:
        _seen.add(_t)
        _deduped.append(_t)
UNIVERSE = _deduped

BATCH_SIZE = 5
TOTAL_BATCHES = (len(UNIVERSE) + BATCH_SIZE - 1) // BATCH_SIZE


# ── Daily scan helpers ──────────────────────────────────────────────────────

def get_daily_tickers(n: int = DAILY_SCAN_SIZE, for_date: "_date | None" = None) -> list:
    """
    Return n tickers seeded by today's date — same picks for every visitor
    on the same calendar day, fresh selection the next day.
    """
    d = for_date or _date.today()
    seed = int(d.strftime("%Y%m%d"))
    rng = random.Random(seed)
    picks = list(UNIVERSE)
    rng.shuffle(picks)
    return picks[:n]


def scan_tickers(tickers: list) -> list:
    """
    Scan a specific list of tickers in parallel using one shared session + crumb.
    Returns scored results, silently skipping any that fail.
    """
    session = _make_session()
    crumb   = _get_crumb(session)
    results = []
    with ThreadPoolExecutor(max_workers=min(len(tickers), 10)) as executor:
        futures = {executor.submit(_scan_one, sym, session, crumb): sym for sym in tickers}
        for future in as_completed(futures):
            result = future.result()
            if result is not None:
                results.append(result)
    order = {sym: i for i, sym in enumerate(tickers)}
    results.sort(key=lambda r: order.get(r["ticker"], 999))
    return results


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
