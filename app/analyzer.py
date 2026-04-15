import yfinance as yf
import math
from typing import Dict, Any, Tuple, List

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

def analyze_stock(ticker_symbol: str) -> Dict[str, Any]:
    ticker = yf.Ticker(ticker_symbol)
    try:
        info = ticker.info
    except Exception as e:
        return {"error": f"Could not fetch data for {ticker_symbol}: {e}"}

    if not info or "shortName" not in info:
        return {"error": f"Ticker {ticker_symbol} not found or no data available."}

    # --- fetch metrics ---
    roe               = get_safe(info, ["returnOnEquity"])
    roa               = get_safe(info, ["returnOnAssets"])
    debt_to_equity    = get_safe(info, ["debtToEquity"])     # as %, e.g. 150 = 1.5x ratio
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
    short_ratio       = get_safe(info, ["shortRatio"])
    market_cap        = get_safe(info, ["marketCap"])
    current_price     = get_safe(info, ["currentPrice", "regularMarketPrice", "previousClose"])
    high_52w          = get_safe(info, ["fiftyTwoWeekHigh"])
    low_52w           = get_safe(info, ["fiftyTwoWeekLow"])
    total_revenue     = get_safe(info, ["totalRevenue"])
    rd_expense        = get_safe(info, ["researchDevelopment"])

    # Data completeness
    expected = [roe, debt_to_equity, fcf, revenue_growth, pe_ratio, pb_ratio,
                beta, current_ratio, ev_ebitda, peg_ratio]
    confidence_score = int(sum(1 for x in expected if x is not None) / len(expected) * 100)

    # --- score each manager ---
    scores_raw = {
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

    # Weighted consensus (exact formula from skill)
    consensus_score = round(sum(scores_raw[m] * MANAGER_WEIGHTS[m] for m in MANAGER_WEIGHTS))
    scores_list = list(scores_raw.values())
    signal_label, signal_color, signal_note = _compute_signal(consensus_score, scores_list)

    # Per-manager data for the template
    managers = [
        {
            "name":       m,
            "philosophy": MANAGER_PHILOSOPHY[m],
            "weight":     int(MANAGER_WEIGHTS[m] * 100),
            "score":      scores_raw[m],
            "verdict":    _verdict(scores_raw[m]),
        }
        for m in MANAGER_WEIGHTS
    ]

    # Convergence analysis
    convergence = {
        "bullish": [m for m, s in scores_raw.items() if s > 65],
        "neutral":  [m for m, s in scores_raw.items() if 40 <= s <= 65],
        "bearish":  [m for m, s in scores_raw.items() if s < 40],
    }

    fcf_yield = (
        f"{fcf / market_cap * 100:.1f}%"
        if (fcf and market_cap and market_cap > 0)
        else "N/A"
    )

    return {
        "ticker":          ticker_symbol.upper(),
        "name":            info.get("shortName", ticker_symbol.upper()),
        "sector":          info.get("sector", "Unknown"),
        "industry":        info.get("industry", "Unknown"),
        "price":           current_price or "N/A",
        "market_cap":      f"${market_cap / 1e9:.2f}B" if market_cap else "N/A",
        "currency":        info.get("financialCurrency", "USD"),
        "consensus_score": consensus_score,
        "signal": {
            "label": signal_label,
            "color": signal_color,
            "note":  signal_note,
        },
        "confidence_score": confidence_score,
        "managers":         managers,
        "convergence":      convergence,
        "raw_metrics": {
            "P/E":         f"{pe_ratio:.1f}"             if pe_ratio      else "N/A",
            "PEG":         f"{peg_ratio:.2f}"            if peg_ratio     else "N/A",
            "P/B":         f"{pb_ratio:.1f}"             if pb_ratio      else "N/A",
            "EV/EBITDA":   f"{ev_ebitda:.1f}x"           if ev_ebitda     else "N/A",
            "ROE":         f"{roe * 100:.1f}%"           if roe           else "N/A",
            "Debt/Equity": f"{debt_to_equity / 100:.2f}x" if debt_to_equity else "N/A",
            "Rev Growth":  f"{revenue_growth * 100:.1f}%" if revenue_growth else "N/A",
            "FCF Yield":   fcf_yield,
        },
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
