from fastapi import APIRouter
import asyncio
import time
import sys, os; sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import outbound
from database import get_db

router = APIRouter()

# Quotes move, but not every few seconds. Share one fetch across screens.
_QUOTE_TTL = 120
_quote_cache: dict[str, tuple[float, list]] = {}
_quote_lock = asyncio.Lock()

# CoinGecko IDs for crypto symbols
CRYPTO_IDS = {
    "BTC": "bitcoin", "ETH": "ethereum", "SOL": "solana",
    "DOGE": "dogecoin", "XRP": "ripple", "ADA": "cardano",
    "AVAX": "avalanche-2", "MATIC": "matic-network", "LINK": "chainlink",
    "LTC": "litecoin", "DOT": "polkadot", "SHIB": "shiba-inu",
}

def format_price(price: float) -> str:
    if price >= 1000:
        return f"{price:,.0f}"
    elif price >= 1:
        return f"{price:,.2f}"
    else:
        return f"{price:.4f}"

def configured_symbols() -> str:
    conn = get_db()
    row = conn.execute("SELECT value FROM settings WHERE key='stock_symbols'").fetchone()
    conn.close()
    return row["value"] if row and row["value"] else "BTC,TSLA,SPY"


@router.get("/")
async def get_stocks(symbols: str | None = None):
    """Quotes for the saved ticker, or for `symbols` when a caller passes them."""
    if symbols is None or not symbols.strip():
        symbols = configured_symbols()
    symbol_list = [s.strip().upper() for s in symbols.split(",") if s.strip()]
    if not symbol_list:
        return []
    cache_key = ",".join(symbol_list)
    now = time.monotonic()
    hit = _quote_cache.get(cache_key)
    if hit and now - hit[0] < _QUOTE_TTL:
        return hit[1]

    async with _quote_lock:
        hit = _quote_cache.get(cache_key)
        if hit and time.monotonic() - hit[0] < _QUOTE_TTL:
            return hit[1]
        payload = await _fetch_quotes(symbol_list)
        if payload:
            _quote_cache[cache_key] = (time.monotonic(), payload)
        return payload


async def _fetch_quotes(symbol_list: list[str]) -> list:
    crypto_syms = [s for s in symbol_list if s in CRYPTO_IDS]
    stock_syms = [s for s in symbol_list if s not in CRYPTO_IDS]
    results = {}

    async def fetch_crypto(client):
        try:
            ids = ",".join(CRYPTO_IDS[s] for s in crypto_syms)
            resp = await client.get(
                "https://api.coingecko.com/api/v3/simple/price",
                params={"ids": ids, "vs_currencies": "usd", "include_24hr_change": "true"},
                headers={"Accept": "application/json"},
            )
            data = resp.json()
            for sym in crypto_syms:
                coin_id = CRYPTO_IDS[sym]
                if coin_id in data:
                    price = data[coin_id]["usd"]
                    change = data[coin_id].get("usd_24h_change") or 0.0
                    results[sym] = {
                        "symbol": sym,
                        "price": price,
                        "price_fmt": format_price(price),
                        "change_pct": round(change, 2),
                    }
        except Exception as e:
            print(f"CoinGecko error: {e}")

    async def fetch_stock(client, sym):
        yahoo_sym = "^GSPC" if sym in ("SPX", "SP500") else sym
        try:
            resp = await client.get(
                f"https://query1.finance.yahoo.com/v8/finance/chart/{yahoo_sym}",
                params={"interval": "1d", "range": "1d"},
                headers={"User-Agent": "Mozilla/5.0"},
            )
            data = resp.json()
            meta = data["chart"]["result"][0]["meta"]
            price = meta.get("regularMarketPrice") or meta.get("chartPreviousClose", 0)
            prev = meta.get("chartPreviousClose") or meta.get("previousClose") or price
            change_pct = ((price - prev) / prev * 100) if prev else 0.0
            results[sym] = {
                "symbol": sym,
                "price": price,
                "price_fmt": format_price(price),
                "change_pct": round(change_pct, 2),
            }
        except Exception as e:
            print(f"Yahoo Finance error for {sym}: {e}")

    async with outbound.client(timeout=10.0) as client:
        tasks = []
        if crypto_syms:
            tasks.append(fetch_crypto(client))
        tasks.extend(fetch_stock(client, sym) for sym in stock_syms)
        if tasks:
            await asyncio.gather(*tasks)

    return [results[s] for s in symbol_list if s in results]

@router.get("/settings")
def get_stock_settings():
    return {"symbols": configured_symbols()}
