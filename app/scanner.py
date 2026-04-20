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

    # ── TECHNOLOGY ────────────────────────────────────────────────────────
    # Mega-cap platforms
    "AAPL", "MSFT", "NVDA", "GOOGL", "META", "AMZN", "TSLA", "AMD",
    # Enterprise software / ERP
    "ADBE", "CRM", "ORCL", "NOW", "INTU", "WDAY", "ADSK", "ANSS",
    "SNPS", "CDNS", "SSNC", "JKHY", "ACIW", "PEGA", "MANH", "PCOR",
    "VRNT", "DSGX", "TNET", "EVBG", "PRFT", "PLXS",
    # IT services / consulting
    "ACN", "IBM", "CTSH", "EPAM", "GLOB", "WIT", "EXLS", "CACI",
    "SAIC", "LDOS", "BAH", "TTEC",
    # Hardware / storage
    "DELL", "HPE", "HPQ", "NTAP", "STX", "WDC", "SMCI", "ARM", "LOGI",
    # Semiconductors — large
    "INTC", "QCOM", "AVGO", "TXN", "MU", "AMAT", "LRCX", "KLAC",
    "MRVL", "ON", "TER", "ENTG", "MCHP", "SWKS", "QRVO", "MPWR",
    # Semiconductors — specialty
    "CRUS", "SLAB", "WOLF", "AMBA", "ONTO", "FORM", "KLIC", "UCTT",
    "ACMR", "MKSI", "OLED", "LSCC", "MTSI", "COHU",
    # Cloud / AI / data
    "PLTR", "SNOW", "MDB", "DDOG", "ESTC", "CFLT", "GTLB", "NTNX",
    "PSTG", "DOCN",
    # Cybersecurity
    "CRWD", "PANW", "FTNT", "NET", "ZS", "OKTA", "CYBR", "TENB",
    "QLYS", "S", "RPD",
    # SaaS / HCM / finance software
    "HUBS", "PAYC", "PCTY", "VEEV", "NCNO", "BILL", "FOUR", "ALRM",
    "TWLO", "ZI", "RNG", "FIVN", "NICE", "EGHT",
    # Networking / comms infra
    "CSCO", "JNPR", "ANET",
    # Consumer internet / marketplaces
    "PINS", "SNAP", "EBAY", "ETSY", "GDDY", "TRIP", "YELP", "IAC",
    "WIX", "BIGC", "SQUSP",
    # Payments / fintech
    "PYPL", "FISV", "FI", "GPN", "WEX", "ADP", "PAYX",
    # Digital advertising / ad-tech
    "TTD", "DV", "IAS", "MGNI", "PUBM", "CRTO", "APPS",
    # New-era / AI / quantum
    "IONQ", "MSTR", "RIOT", "MARA",

    # ── FINANCIALS ────────────────────────────────────────────────────────
    # Banks — money-center
    "JPM", "BAC", "WFC", "C", "GS", "MS",
    # Banks — super-regional
    "USB", "PNC", "TFC", "MTB", "RF", "HBAN", "KEY", "CFG",
    "ZION", "CMA", "WAL", "BOKF", "COLB", "GBCI", "TCBI",
    "FFIN", "CADE", "SFNC", "CBU", "UMBF", "WSFS", "BANR",
    "HOPE", "CVBF", "HAFC",
    # Capital markets
    "AXP", "BLK", "SCHW", "SPGI", "MCO", "ICE", "CME",
    "BX", "KKR", "APO", "ARES", "CG",
    "TROW", "BEN", "IVZ", "NTRS", "STT", "BK", "AMP",
    "LAZ", "EVR", "PJT", "LPLA", "RJF", "STEP", "HLNE",
    "MKTX", "VIRTU",
    # Financial data / analytics
    "V", "MA", "MSCI", "FDS", "SPGI", "EFX", "TRU",
    # Insurance
    "CB", "PGR", "MET", "AIG", "AFL", "ALL", "HIG", "TRV",
    "PRU", "EQH", "MKL", "WRB", "L", "RNR", "ERIE", "GL",
    "AFG", "VOYA", "SFG", "PFG", "LNC", "UNM", "FNF", "FAF",
    # Consumer finance
    "COF", "SYF", "DFS", "SQ", "ALLY", "SLM", "OMF", "ENVA",
    "CACC", "NAVI",

    # ── HEALTHCARE ────────────────────────────────────────────────────────
    # Large-cap pharma
    "LLY", "JNJ", "ABBV", "MRK", "PFE", "BMY", "AMGN", "GILD",
    "BIIB", "VTRS", "PRGO",
    # Large-cap biotech
    "REGN", "VRTX", "MRNA", "ALNY", "NBIX", "INCY", "JAZZ",
    "ILMN", "BMRN", "SRPT", "RCKT", "NKTR", "ACAD", "HALO",
    "VKTX", "CRSP", "NTLA", "EDIT", "BEAM", "PACB",
    # Pharma ADRs
    "SNY", "AZN", "GSK", "NVO",
    # Managed care / health services
    "UNH", "CVS", "CI", "ELV", "HCA", "HUM", "MOH", "CNC",
    "PINC", "AMED", "ENSG", "ADUS", "OPCH",
    # Diagnostics / life sciences
    "TMO", "DHR", "A", "IQV", "WAT", "MTD", "IDXX",
    "EXAS", "NTRA", "SDGR", "RXRX", "TWST",
    # Medical devices
    "ABT", "MDT", "BSX", "SYK", "ZBH", "BDX", "EW", "ISRG",
    "ALGN", "HOLX", "PODD", "RMD", "IART", "NVCR", "NUVA",
    "MMSI", "ITGR", "ICUI", "OMCL", "STE", "XRAY",
    # Animal health / specialty
    "ZTS", "PAHC",
    # Health IT
    "TDOC", "HIMS", "RXRX",
    # Royalty
    "RPRX",

    # ── CONSUMER STAPLES ──────────────────────────────────────────────────
    "WMT", "COST", "PG", "KO", "PEP", "MDLZ", "CL", "MKC", "GIS",
    "CHD", "STZ", "KHC", "CAG", "SJM", "HSY", "HRL", "CPB",
    "EL", "CLX", "MO", "PM", "ADM", "BG", "INGR", "POST",
    "SFM", "GO", "BJ", "PFGC", "CHEF", "LANC", "FRPT",
    "NWL", "ENR", "SPB", "KDP",

    # ── CONSUMER DISCRETIONARY ────────────────────────────────────────────
    # Restaurants
    "MCD", "SBUX", "CMG", "YUM", "DRI", "TXRH", "EAT", "CAKE",
    "JACK", "SHAK", "FAT",
    # Hotels / gaming
    "HLT", "MAR", "H", "IHG", "CHH", "WH",
    "WYNN", "MGM", "LVS", "CZR", "PENN", "DKNG",
    # Travel
    "ABNB", "BKNG", "EXPE", "TCOM",
    "RCL", "CCL", "NCLH",
    "DAL", "UAL", "LUV", "AAL", "ALK", "JBLU",
    # Home improvement / specialty retail
    "HD", "LOW", "TGT", "TSCO", "POOL", "TREX",
    "BBY", "FIVE", "OLLI", "DG", "DLTR",
    "AZO", "ORLY", "GPC", "AAP",
    "FND", "RH", "WSM", "W", "ARHS",
    # Fashion / apparel
    "NKE", "LULU", "ULTA",
    "CPRI", "TPR", "PVH", "RL", "HBI", "GIII", "UAA", "VFC",
    "KSS", "M", "JWN", "BURL", "TJX", "ROST",
    # Auto
    "F", "GM", "APTV", "LEA", "GNTX",
    "LAD", "SAH", "AN", "KMX", "PAG", "CVNA",
    "RIVN", "LCID",
    # Fitness / wellness
    "PTON", "PLNT",
    # E-commerce / pets
    "CHWY", "WOOF", "CPNG",
    # Homebuilders
    "DHI", "LEN", "PHM", "TOL", "NVR", "KBH", "MDC", "MHO", "TPH", "TMHC",

    # ── ENERGY ────────────────────────────────────────────────────────────
    # Integrated / majors
    "XOM", "CVX", "COP", "HES", "OVV",
    "BP", "SHEL", "TTE",
    # E&P
    "EOG", "OXY", "DVN", "APA", "MRO", "FANG", "PR", "SM",
    "MTDR", "CIVI", "CHRD", "EQT", "AR", "RRC", "CNX", "SWN",
    # Refining / downstream
    "MPC", "VLO", "PSX",
    # Oil services
    "SLB", "HAL", "BKR", "NOV", "HP", "NBR",
    # Midstream / pipelines
    "KMI", "WMB", "OKE", "TRGP", "ET", "EPD", "AM", "ENLC",
    # Clean / renewable energy
    "ENPH", "SEDG", "RUN", "NOVA", "ARRY", "MAXN",
    "PLUG", "FCEL", "BE", "CWEN",

    # ── INDUSTRIALS ───────────────────────────────────────────────────────
    # Defence / aerospace
    "LMT", "RTX", "NOC", "GD", "BA", "TDG", "HEICO",
    "HII", "CW", "KTOS", "MOOG", "AVAV", "TDY", "DRS", "AXON",
    # Machinery / diversified
    "HON", "GE", "CAT", "DE", "ETN", "EMR", "PH", "MMM", "ITW",
    "ROK", "DOV", "CARR", "OTIS", "TT", "IR",
    "PNR", "GNRC", "FELE", "MWA",
    "HUBB", "ALLE", "MIDD", "LECO", "RRX", "NDSN", "AME", "LFUS",
    "ROPER", "IDEX", "SPX", "CFX",
    # Freight / logistics
    "UNP", "NSC", "CSX", "UPS", "FDX", "PCAR", "CPRT",
    "ODFL", "SAIA", "KNX", "LSTR", "CHRW", "EXPD", "XPO", "GXO",
    "ZTO", "JBHT",
    # Airlines
    "DAL", "UAL", "LUV", "AAL", "ALK",
    # Construction / building
    "PWR", "SITE", "MTZ", "URI",
    "BLDR", "BECN", "IBP",
    # Waste / services
    "RSG", "WM", "CTAS", "VRSK", "FAST",
    # Staffing / facilities
    "MAN", "KFRC",
    # Rental / equipment
    "HEES", "GATX",

    # ── COMMUNICATION & MEDIA ─────────────────────────────────────────────
    "NFLX", "DIS", "T", "VZ", "TMUS", "CHTR", "CMCSA", "WBD",
    "PARA", "FOXA", "NWSA", "NYT",
    "OMC", "IPG", "PUB",
    "SNAP", "PINS", "MTCH", "SPOT", "LYV",
    "TTWO", "EA", "RBLX", "U",
    "SIRI", "LBRDK", "ATUS",

    # ── MATERIALS ─────────────────────────────────────────────────────────
    # Chemicals
    "LIN", "APD", "ECL", "SHW", "PPG", "RPM", "IFF",
    "DOW", "DD", "LYB", "OLN", "HUN", "WLK", "TROX", "EMN",
    "CF", "MOS", "FMC", "NTR",
    # Mining / precious metals
    "FCX", "NEM", "GOLD", "KGC", "AEM", "WPM", "AG", "CDE", "PAAS",
    "ALB",
    # Steel / metals
    "NUE", "STLD", "RS", "CMC", "X", "CLF", "MT",
    # Packaging / paper
    "MLM", "VMC", "BALL", "PKG", "IP", "SEE", "SON", "BERY", "SILGAN",

    # ── REITs ─────────────────────────────────────────────────────────────
    # Industrial / logistics
    "PLD", "EGP", "FR", "STAG", "TRNO", "REXR",
    # Data centres / towers
    "AMT", "EQIX", "CCI", "SBAC", "IRM",
    # Retail / commercial
    "SPG", "O", "NNN", "REG", "KIM", "FRT", "BXP", "VICI",
    "EPRT", "NTST", "ADC", "BNL", "WPC",
    # Residential
    "AVB", "EQR", "ESS", "MAA", "UDR", "CPT",
    # Healthcare
    "WELL", "VTR", "PEAK", "HR", "DOC", "OHI", "MPW", "LTC", "NHI", "SBRA",
    # Storage / specialty
    "PSA", "EXR", "CUBE", "NSA",
    "GLPI", "COLD", "IIPR",

    # ── UTILITIES ─────────────────────────────────────────────────────────
    "NEE", "DUK", "SO", "D", "AEP", "EXC", "XEL", "PCG", "AWK", "ES",
    "WEC", "CMS", "LNT", "NI", "ATO", "SRE", "ETR", "PPL",
    "OGE", "PNW", "ALE", "MGE", "AVA", "IDA", "POR",
    "WTRG", "SWX", "PEG", "FE", "CNP", "AEE",
    "EVRG", "NWE", "OTTR",

    # ── INTERNATIONAL ADRs ────────────────────────────────────────────────
    # Asia-Pacific
    "TSM", "ASML", "SAP", "NVO", "INFY", "WIT",
    "SONY", "TM", "HMC", "TDK",
    "BABA", "JD", "PDD", "BIDU", "TCOM", "BEKE",
    "NIO", "XPEV", "LI",
    "GRAB", "SE", "CPNG",
    # Europe
    "RACE", "LVMUY", "HESAY", "AZN", "GSK", "SNY",
    "BP", "SHEL", "TTE",
    "SIEGY", "IDEXY", "BAYRY",
    # Latin America
    "MELI", "NU", "ITUB", "BBD", "VALE", "PBR",
    "PAGS", "STNE", "VTEX", "GLOB",
    # India
    "HDB", "IBN", "RDY",

    # ── GROWTH / THEMATIC ─────────────────────────────────────────────────
    # Fintech / neobanks
    "SOFI", "HOOD", "AFRM", "SQ", "COIN",
    # Mobility
    "UBER", "LYFT", "DASH",
    # EV / clean-tech
    "RIVN", "LCID",
    # Crypto infrastructure
    "MSTR", "RIOT", "MARA", "BTBT", "HUT", "CLSK",
    # Space / eVTOL
    "RKLB", "JOBY", "ACHR",
    # AI / quantum
    "IONQ", "SMCI", "ARM", "PLTR",
    # Entertainment / streaming / gaming
    "NFLX", "SPOT", "DKNG", "RBLX", "U",
    # Real estate services
    "CBRE", "JLL",

    # ── ADDITIONAL TECHNOLOGY ─────────────────────────────────────────────
    "AKAM", "VRSN", "CHKP", "AMKR", "IPGP",
    "FRSH", "DUOL", "MNDY", "BRZE", "INTA",
    "APPN", "QTWO", "BLKB", "EXPO",
    "KD", "MMS",
    "ANET", "JNPR",
    "CRTO", "PUBM",
    "ALGT", "NCNO",

    # ── ADDITIONAL FINANCIALS ─────────────────────────────────────────────
    "CBOE", "NDAQ",
    "AON", "MMC", "WTW", "AJG", "BRO", "RYAN",
    "ACGL", "CINF", "ORI", "CNA", "HLI", "RLI",
    "SEIC", "VRTS", "APAM",
    "PFSI", "UWMC",
    "WD", "NMIH", "ESNT", "MTG", "RDN",
    "VIRT",

    # ── ADDITIONAL HEALTHCARE ─────────────────────────────────────────────
    "MEDP", "TNDM", "AXSM", "EXEL",
    "AVTR", "CERT", "VCYT", "GH",
    "SAGE", "ARVN", "FOLD",
    "PRTA", "MCRB",
    "AGIO", "ONCE", "IONS",
    "INVA", "ONEM",
    "HRMY", "SUPN", "PHAT",
    "ADMA", "HROW", "OSUR",
    "TELA", "ATEC", "NVCR",

    # ── ADDITIONAL CONSUMER STAPLES ───────────────────────────────────────
    "BTI", "DEO", "UL",
    "CASY", "MUSA",
    "USFD", "SYY", "PFGC",
    "COTY", "ELF",

    # ── ADDITIONAL CONSUMER DISCRETIONARY ────────────────────────────────
    "CROX", "DECK", "ONON", "BIRK",
    "MTN", "SCI", "PLAY", "WEN",
    "SFIX", "REAL",
    "MHK", "WHR", "FBHS", "FBIN",
    "LKQ", "MNRO", "MTOR",
    "APTV", "BWA", "LEA",
    "TPVG",
    "EVGO", "CHPT",

    # ── ADDITIONAL ENERGY ─────────────────────────────────────────────────
    "AES", "CHK", "STR", "VTLE",
    "OVV",
    "CWEN", "NEP",
    "DT", "SOC",

    # ── ADDITIONAL INDUSTRIALS ────────────────────────────────────────────
    "WSO", "ROLL", "LCII", "PATK",
    "BCC", "UFP", "UFPI",
    "MHK", "PGNY",
    "SRCL", "CIVI",
    "ACCO", "NN",
    "STRA", "KFRC",
    "UHAL", "AMERCO",
    "GMS", "BECN",
    "FTAI",

    # ── ADDITIONAL COMMUNICATION ──────────────────────────────────────────
    "NWSA", "NYT",
    "IACI", "SIRI",
    "WMG", "PARAA",
    "SEAT", "IHRT",

    # ── ADDITIONAL MATERIALS ──────────────────────────────────────────────
    "OLN", "RPM",
    "CSTM", "CENX", "AA",
    "BTG", "OR", "KGC",
    "FNV", "RGLD", "MAG",
    "HL", "FSM",

    # ── ADDITIONAL REITs ──────────────────────────────────────────────────
    "AMH", "INVH", "AIRC", "NHC",
    "DEA", "JBGS", "HIW", "PDM",
    "NXRT", "GOOD", "LAND",
    "SVC", "APLE", "RHP",
    "ROIC", "UE", "RPAI",

    # ── ADDITIONAL UTILITIES ──────────────────────────────────────────────
    "AES", "BKH", "CLNE",
    "SJW", "ARTNA", "CWCO",
    "LABL", "UGI",
    "NFG", "RGCO",
    "MGEE", "YORW",

    # ── ADDITIONAL INTERNATIONAL ──────────────────────────────────────────
    "UL", "DEO", "BTI",
    "AZN", "GSK",
    "KEP", "SKM", "KT",
    "NSRGY", "RHHBY",
    "IQ", "VNET", "KC",
    "BRFS", "CIG", "CBD",
    "HTHT", "TAL",
    "GRAB", "GOTO",
    "TME", "HUYA", "DOYU",

    # ── ADDITIONAL GROWTH / THEMATIC ─────────────────────────────────────
    "OSCR", "ACIC",
    "OPEN", "OPFI",
    "JOBY", "ACHR", "EVTL",
    "STEM", "SPWR", "CSIQ",
    "DKNG", "PENN", "GENI",
    "SMAR", "PCOR",
    "DOCN",
    "GDRX", "HIMS",
    "RELY", "NRDS",
    "FLNC", "AAON", "ALTR", "MASI",
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
