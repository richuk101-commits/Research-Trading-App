from fastapi import FastAPI, Request, Form
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from app.analyzer import analyze_stock

app = FastAPI(title="Trading Research App")

# Set up templates
templates = Jinja2Templates(directory="app/templates")

# Trending stocks to display on the homepage
TRENDING_STOCKS = [
    {"ticker": "NVDA", "name": "NVIDIA Corp"},
    {"ticker": "TSLA", "name": "Tesla Inc"},
    {"ticker": "AAPL", "name": "Apple Inc"},
    {"ticker": "MSFT", "name": "Microsoft Corp"},
]

@app.get("/", response_class=HTMLResponse)
async def read_home(request: Request):
    return templates.TemplateResponse(request=request, name="index.html", context={
        "request": request,
        "trending": TRENDING_STOCKS
    })

@app.post("/analyze", response_class=HTMLResponse)
async def post_analyze(request: Request, ticker: str = Form(...)):
    # Clean up user input
    ticker = ticker.strip().upper()
    
    # Run the analysis
    analysis_result = analyze_stock(ticker)
    
    return templates.TemplateResponse(request=request, name="report.html", context={
        "request": request,
        "ticker": ticker,
        "result": analysis_result
    })

import requests

@app.get("/api/search")
async def api_search(q: str):
    # Use Yahoo Finance search API for autocomplete
    url = f"https://query2.finance.yahoo.com/v1/finance/search?q={q}"
    headers = {'User-Agent': 'Mozilla/5.0'}
    try:
        response = requests.get(url, headers=headers)
        data = response.json()
        quotes = data.get("quotes", [])
        # Filter for equities and ETFs to keep it relevant
        results = [
            {"symbol": q["symbol"], "name": q.get("shortname", q["symbol"]), "exchange": q.get("exchDisp", "")}
            for q in quotes if q.get("quoteType") in ["EQUITY", "ETF"]
        ]
        return {"results": results[:10]} # Return top 10
    except Exception as e:
        return {"results": [], "error": str(e)}

import yfinance as yf

@app.get("/api/chart/{ticker}")
async def api_chart(ticker: str):
    ticker = ticker.upper()
    try:
        # Cache this if possible in a real app, but fetching 1y is relatively fast
        stock = yf.Ticker(ticker)
        hist = stock.history(period="1y")
        if hist.empty:
            return {"dates": [], "prices": []}
        
        # Reset index to easily extract dates as strings
        hist = hist.reset_index()
        dates = hist['Date'].dt.strftime('%Y-%m-%d').tolist()
        prices = hist['Close'].round(2).tolist()
        
        return {"dates": dates, "prices": prices}
    except Exception as e:
        return {"error": str(e)}

@app.get("/analyze/{ticker}", response_class=HTMLResponse)
async def get_analyze(request: Request, ticker: str):
    # This enables clicking on links to trigger analysis (e.g. from trending section)
    ticker = ticker.strip().upper()
    
    # Run the analysis
    analysis_result = analyze_stock(ticker)
    
    return templates.TemplateResponse(request=request, name="report.html", context={
        "request": request,
        "ticker": ticker,
        "result": analysis_result
    })
