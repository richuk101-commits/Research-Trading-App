import time
import requests
import yfinance as yf
from fastapi import FastAPI, Request, Form
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from app.analyzer import analyze_stock, _make_session

app = FastAPI(title="Trading Research App")
templates = Jinja2Templates(directory="app/templates")

_BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    )
}

# ---------------------------------------------------------------------------
# News cache — refreshed at most once every 10 minutes
# ---------------------------------------------------------------------------
_NEWS_TTL = 600  # seconds
_news_cache: dict = {"items": [], "fetched_at": 0.0}

_YF_SEARCH_URL = "https://query2.finance.yahoo.com/v1/finance/search"
_NEWS_QUERIES = ["stock market", "earnings", "Federal Reserve"]


def _fetch_news_direct() -> list:
    """Fetch market news directly from Yahoo Finance search API.
    Uses multiple query terms to get ~10-12 diverse, recent items."""
    session = _make_session()
    now = time.time()
    seen: set = set()
    raw_items: list = []

    for query in _NEWS_QUERIES:
        try:
            resp = session.get(
                _YF_SEARCH_URL,
                params={
                    "q": query,
                    "newsCount": 6,
                    "quotesCount": 0,
                    "region": "US",
                    "lang": "en-US",
                },
                timeout=6,
            )
            resp.raise_for_status()
            for n in resp.json().get("news", []):
                title = (n.get("title") or "").strip()
                if not title or title in seen:
                    continue
                seen.add(title)
                ts = n.get("providerPublishTime") or 0
                diff = int(now - ts) if ts else None
                if diff is not None:
                    if diff < 3600:
                        age = f"{max(diff // 60, 1)}m ago"
                    elif diff < 86400:
                        age = f"{diff // 3600}h ago"
                    else:
                        age = f"{diff // 86400}d ago"
                else:
                    age = ""
                raw_items.append({
                    "title": title,
                    "link": n.get("link") or "#",
                    "source": n.get("publisher") or "Yahoo Finance",
                    "age": age,
                    "_ts": ts,
                })
        except Exception:
            pass

    raw_items.sort(key=lambda x: x["_ts"], reverse=True)
    for item in raw_items:
        item.pop("_ts")
    return raw_items[:12]


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.get("/", response_class=HTMLResponse)
async def read_home(request: Request):
    return templates.TemplateResponse(request=request, name="index.html", context={
        "request": request,
    })


@app.post("/analyze", response_class=HTMLResponse)
async def post_analyze(request: Request, ticker: str = Form(...)):
    ticker = ticker.strip().upper()
    analysis_result = analyze_stock(ticker)
    return templates.TemplateResponse(request=request, name="report.html", context={
        "request": request,
        "ticker": ticker,
        "result": analysis_result,
    })


@app.get("/analyze/{ticker}", response_class=HTMLResponse)
async def get_analyze(request: Request, ticker: str):
    ticker = ticker.strip().upper()
    analysis_result = analyze_stock(ticker)
    return templates.TemplateResponse(request=request, name="report.html", context={
        "request": request,
        "ticker": ticker,
        "result": analysis_result,
    })


@app.get("/api/news")
async def api_news():
    now = time.time()
    age_secs = int(now - _news_cache["fetched_at"])
    if age_secs < _NEWS_TTL and _news_cache["items"]:
        return {"items": _news_cache["items"], "age_secs": age_secs}

    items = _fetch_news_direct()
    _news_cache["items"] = items
    _news_cache["fetched_at"] = now
    return {"items": items, "age_secs": 0}


@app.get("/api/search")
async def api_search(q: str):
    url = f"https://query2.finance.yahoo.com/v1/finance/search?q={q}"
    try:
        response = requests.get(url, headers=_BROWSER_HEADERS, timeout=5)
        data = response.json()
        quotes = data.get("quotes", [])
        results = [
            {"symbol": q["symbol"], "name": q.get("shortname", q["symbol"]), "exchange": q.get("exchDisp", "")}
            for q in quotes if q.get("quoteType") in ["EQUITY", "ETF"]
        ]
        return {"results": results[:10]}
    except Exception as e:
        return {"results": [], "error": str(e)}


@app.get("/api/chart/{ticker}")
async def api_chart(ticker: str):
    ticker = ticker.upper()
    try:
        stock = yf.Ticker(ticker, session=_make_session())
        hist = stock.history(period="1y")
        if hist.empty:
            return {"dates": [], "prices": []}
        hist = hist.reset_index()
        dates = hist["Date"].dt.strftime("%Y-%m-%d").tolist()
        prices = hist["Close"].round(2).tolist()
        return {"dates": dates, "prices": prices}
    except Exception as e:
        return {"error": str(e)}
