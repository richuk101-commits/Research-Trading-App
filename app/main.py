import time
import requests
import yfinance as yf
from fastapi import FastAPI, Request, Form
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates

from app.analyzer import analyze_stock, _make_session, SEED_SNAPSHOT_RESULTS
from app.scanner import BATCH_SIZE, TOTAL_BATCHES, UNIVERSE, batch_scan
from app.db import get_latest_scan, save_scan, get_scan_history, get_scan_by_id

app = FastAPI(title="Trading Research App")
templates = Jinja2Templates(directory="app/templates")

COVERED_TICKERS = list(UNIVERSE)

_BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    )
}

_NEWS_TTL = 600
_ANALYZE_TTL = 20 * 60
_CHART_TTL = 30 * 60
_SEARCH_TTL = 10 * 60

_news_cache: dict = {"items": [], "fetched_at": 0.0}
_analyze_request_cache: dict = {}
_chart_cache: dict = {}
_search_cache: dict = {}

_YF_SEARCH_URL = "https://query2.finance.yahoo.com/v1/finance/search"
_NEWS_QUERIES = ["stock market", "earnings", "Federal Reserve"]


def _build_seed_results() -> list:
    seeded_results = []
    for ticker in UNIVERSE:
        item = SEED_SNAPSHOT_RESULTS.get(ticker)
        if item:
            seeded_results.append(dict(item))
    return seeded_results


def _cache_get(store: dict, key: str, ttl: int):
    entry = store.get(key)
    if not entry:
        return None
    if time.time() - entry["stored_at"] > ttl:
        store.pop(key, None)
        return None
    return entry["value"]


def _cache_set(store: dict, key: str, value):
    store[key] = {"stored_at": time.time(), "value": value}


def _fetch_news_direct() -> list:
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


def _analyze_with_cache(ticker: str):
    ticker = ticker.upper()
    cached = _cache_get(_analyze_request_cache, ticker, _ANALYZE_TTL)
    if cached:
        cached_copy = dict(cached)
        cached_copy["from_request_cache"] = True
        return cached_copy

    result = analyze_stock(ticker)
    if result and not result.get("error"):
        _cache_set(_analyze_request_cache, ticker, result)
    return result


def _fetch_chart_direct(ticker: str):
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}"
    response = requests.get(
        url,
        headers=_BROWSER_HEADERS,
        params={"range": "1y", "interval": "1d", "includePrePost": "false"},
        timeout=6,
    )
    data = response.json()
    result = (((data or {}).get("chart") or {}).get("result") or [None])[0]
    if not result:
        return {"dates": [], "prices": []}

    timestamps = result.get("timestamp") or []
    quote = (((result.get("indicators") or {}).get("quote") or [None])[0] or {})
    closes = quote.get("close") or []

    dates, prices = [], []
    for ts, close in zip(timestamps, closes):
        if ts is None or close is None:
            continue
        dates.append(time.strftime("%Y-%m-%d", time.gmtime(ts)))
        prices.append(round(float(close), 2))
    return {"dates": dates, "prices": prices}


def _seed_scan_payload() -> dict:
    seeded_results = _build_seed_results()
    passed_count = sum(
        1 for r in seeded_results
        if r.get("consensus", 0) >= 75 and r.get("bullish_count", 0) >= 7
    )
    return {
        "scanned_at": None,
        "results": seeded_results,
        "passed_count": passed_count,
        "is_seeded": True,
    }


@app.get("/", response_class=HTMLResponse)
async def read_home(request: Request):
    scan = get_latest_scan() or _seed_scan_payload()
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={"request": request, "scan": scan},
    )


@app.post("/analyze", response_class=HTMLResponse)
async def post_analyze(request: Request, ticker: str = Form(...)):
    ticker = ticker.strip().upper()
    analysis_result = _analyze_with_cache(ticker)
    return templates.TemplateResponse(
        request=request,
        name="report.html",
        context={"request": request, "ticker": ticker, "result": analysis_result},
    )


@app.get("/analyze/{ticker}", response_class=HTMLResponse)
async def get_analyze(request: Request, ticker: str):
    ticker = ticker.strip().upper()
    analysis_result = _analyze_with_cache(ticker)
    return templates.TemplateResponse(
        request=request,
        name="report.html",
        context={"request": request, "ticker": ticker, "result": analysis_result},
    )


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


@app.get("/scanner", response_class=HTMLResponse)
async def scanner_page(request: Request):
    scan = get_latest_scan() or _seed_scan_payload()
    return templates.TemplateResponse(
        request=request,
        name="scanner.html",
        context={
            "request": request,
            "total_batches": TOTAL_BATCHES,
            "total_stocks": len(UNIVERSE),
            "scan": scan,
        },
    )


@app.get("/api/scan/batch")
async def api_scan_batch(n: int = 0):
    if n < 0 or n >= TOTAL_BATCHES:
        return JSONResponse({"error": "invalid batch index"}, status_code=400)

    results = batch_scan(n)
    return {
        "batch": n,
        "results": results,
        "total_batches": TOTAL_BATCHES,
        "snapshot_only": False,
    }


@app.post("/api/scan/save")
async def api_scan_save(request: Request):
    body = await request.json()
    results = body.get("results", [])
    passed_count = sum(
        1 for r in results
        if r.get("consensus", 0) >= 75 and r.get("bullish_count", 0) >= 7
    )
    ok = save_scan(results, passed_count, total_scanned=len(results))
    return {"ok": ok}


@app.get("/api/scan/history")
async def api_scan_history(limit: int = 20):
    history = get_scan_history(limit=min(limit, 20))
    return {"history": history}


@app.get("/api/scan/history/{scan_id}")
async def api_scan_history_detail(scan_id: int):
    row = get_scan_by_id(scan_id)
    if not row:
        return JSONResponse({"error": "not found"}, status_code=404)
    return {"id": row.get("id"), "scanned_at": row.get("scanned_at"), "results": row.get("results", [])}


@app.get("/api/search")
async def api_search(q: str):
    query = q.strip()
    if len(query) < 2:
        return {"results": []}

    cache_key = query.upper()
    cached = _cache_get(_search_cache, cache_key, _SEARCH_TTL)
    if cached is not None:
        return {"results": cached, "cached": True}

    try:
        response = requests.get(
            _YF_SEARCH_URL,
            headers=_BROWSER_HEADERS,
            params={"q": query, "quotesCount": 10, "newsCount": 0},
            timeout=5,
        )
        data = response.json()
        quotes = data.get("quotes", [])
        results = [
            {
                "symbol": item["symbol"],
                "name": item.get("shortname", item["symbol"]),
                "exchange": item.get("exchDisp", ""),
            }
            for item in quotes if item.get("quoteType") in ["EQUITY", "ETF"]
        ][:10]
        _cache_set(_search_cache, cache_key, results)
        return {"results": results}
    except Exception as e:
        return {"results": [], "error": str(e)}


@app.get("/api/chart/{ticker}")
async def api_chart(ticker: str):
    ticker = ticker.upper()
    cached = _cache_get(_chart_cache, ticker, _CHART_TTL)
    if cached:
        return cached

    try:
        stock = yf.Ticker(ticker, session=_make_session())
        hist = stock.history(period="1y")
        if hist.empty:
            chart = _fetch_chart_direct(ticker)
        else:
            hist = hist.reset_index()
            chart = {
                "dates": hist["Date"].dt.strftime("%Y-%m-%d").tolist(),
                "prices": hist["Close"].round(2).tolist(),
            }
    except Exception:
        try:
            chart = _fetch_chart_direct(ticker)
        except Exception as e:
            return {"error": str(e)}

    _cache_set(_chart_cache, ticker, chart)
    return chart
