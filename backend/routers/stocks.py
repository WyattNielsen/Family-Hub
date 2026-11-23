from fastapi import APIRouter
import asyncio
import json
import time
import sys, os; sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import outbound
import jobs
from database import get_db, get_setting_value_async

router = APIRouter()

# Quotes move, but not every few seconds. A stored copy is served while a refresh runs.
_QUOTE_TTL = 120

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

async def configured_symbols() -> str:
    val = await get_setting_value_async("stock_symbols", "")
    return val if val else "BTC,TSLA,SPY"


def _load_quotes(cache_key: str):
    conn = get_db()
    try:
        row = conn.execute(
            "SELECT payload, fetched_at FROM stock_quotes WHERE cache_key = ?",
            (cache_key,),
        ).fetchone()
    finally:
        conn.close()
    if not row:
        return None
    try:
        payload = json.loads(row["payload"])
    except Exception:
        return None
    if not isinstance(payload, list):
        return None
    return {"payload": payload, "fetched_at": row["fetched_at"]}


def _save_quotes(cache_key: str, payload: list):
    conn = get_db()
    try:
        conn.execute(
            """
            INSERT INTO stock_quotes (cache_key, payload, fetched_at)
            VALUES (?, ?, ?)
            ON CONFLICT(cache_key) DO UPDATE SET
                payload = excluded.payload,
                fetched_at = excluded.fetched_at
            """,
            (cache_key, json.dumps(payload), time.time()),
        )
        conn.commit()
    finally:
        conn.close()


async def _refresh_quotes(cache_key: str, symbol_list: list[str]):
    payload = await _fetch_quotes(symbol_list)
    if payload:
        await asyncio.to_thread(_save_quotes, cache_key, payload)


@router.get("/")
async def get_stocks(symbols: str | None = None):
    """Quotes for the saved ticker, or for `symbols` when a caller passes them."""
    if symbols is None or not symbols.strip():
        symbols = await configured_symbols()
    symbol_list = [s.strip().upper() for s in symbols.split(",") if s.strip()]
    if not symbol_list:
        return []
    cache_key = ",".join(symbol_list)
    stored = await asyncio.to_thread(_load_quotes, cache_key)
    if stored and stored["payload"]:
        age = time.time() - float(stored["fetched_at"] or 0)
        if age >= _QUOTE_TTL:
            fresh = list(symbol_list)
            await jobs.run_background(
                "stock-quotes",
                lambda cache_key=cache_key, fresh=fresh: _refresh_quotes(cache_key, fresh),
            )
        return stored["payload"]
    # The ticker hides an empty list and will not ask again for several minutes.
    payload = await _fetch_quotes(symbol_list)
    if payload:
        await asyncio.to_thread(_save_quotes, cache_key, payload)
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

    async with outbound.client(timeout=5.0) as client:
        tasks = []
        if crypto_syms:
            tasks.append(fetch_crypto(client))
        tasks.extend(fetch_stock(client, sym) for sym in stock_syms)
        if tasks:
            await asyncio.gather(*tasks)

    return [results[s] for s in symbol_list if s in results]

@router.get("/settings")
async def get_stock_settings():
    return {"symbols": await configured_symbols()}
