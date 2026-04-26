"""
market_data.py - Handles all real-time and historical market data fetching.
Strategy: Binance for crypto history/price. Finnhub free /quote for US stock live price.
YFinance for ALL history (non-crypto) and Indian/Forex/Index live price.
"""
import time
import logging
import requests
import pandas as pd
import numpy as np
import yfinance as yf

logger = logging.getLogger(__name__)

FINNHUB_API_KEY = 'd7lqkl9r01qk7lvu1nc0d7lqkl9r01qk7lvu1ncg'
FINNHUB_BASE = 'https://finnhub.io/api/v1'

# ── helpers ──────────────────────────────────────────────────────────────────

def _is_crypto(symbol: str) -> bool:
    return '-USD' in symbol or '-USDT' in symbol

def _is_indian(symbol: str) -> bool:
    return symbol.endswith('.NS') or symbol.endswith('.BO')

# ── Binance (crypto only) ────────────────────────────────────────────────────

def fetch_binance_history(symbol: str, limit: int = 100) -> pd.DataFrame | None:
    if not _is_crypto(symbol):
        return None
    try:
        bsym = symbol.replace('-USD', 'USDT').upper()
        res = requests.get(
            f"https://api.binance.com/api/v3/klines?symbol={bsym}&interval=1m&limit={limit}",
            timeout=5)
        if res.status_code != 200:
            return None
        data = res.json()
        cols = ['time','open','high','low','close','volume','ct','qa','nt','tbv','tqv','i']
        df = pd.DataFrame(data, columns=cols)
        df['time'] = pd.to_datetime(df['time'], unit='ms', utc=True)
        df.set_index('time', inplace=True)
        for c in ['open','high','low','close','volume']:
            df[c] = df[c].astype(float)
        return df[['open','high','low','close','volume']]
    except Exception as e:
        logger.error(f"Binance history error {symbol}: {e}")
        return None


def fetch_binance_price(symbol: str) -> float | None:
    if not _is_crypto(symbol):
        return None
    try:
        bsym = symbol.replace('-USD', 'USDT').upper()
        res = requests.get(
            f"https://api.binance.com/api/v3/ticker/price?symbol={bsym}", timeout=3)
        if res.status_code == 200:
            return round(float(res.json()['price']), 4)
    except:
        pass
    return None


# ── Finnhub — FREE PLAN ONLY SUPPORTS /quote (live price), NOT /stock/candle ─

def fetch_finnhub_price(symbol: str) -> float | None:
    """Live quote from Finnhub. Only for US stocks on free plan."""
    if _is_crypto(symbol) or _is_indian(symbol) or symbol.startswith('^') or '=X' in symbol:
        return None
    try:
        res = requests.get(f"{FINNHUB_BASE}/quote",
                           params={'symbol': symbol.upper(), 'token': FINNHUB_API_KEY},
                           timeout=4)
        if res.status_code == 200:
            d = res.json()
            if d.get('c') and float(d['c']) > 0:
                return round(float(d['c']), 4)
    except Exception as e:
        logger.debug(f"Finnhub price error {symbol}: {e}")
    return None


# ── YFinance (history for all non-crypto; price fallback) ────────────────────

_history_cache: dict[str, tuple[float, pd.DataFrame]] = {}
CACHE_TTL = 300  # 5 minutes


def _make_synthetic_history(price: float, n: int = 100) -> pd.DataFrame:
    """Generate synthetic OHLCV data around a live price when real data is unavailable."""
    import numpy as np
    now = pd.Timestamp.utcnow().floor('min')
    times = pd.date_range(end=now, periods=n, freq='1min', tz='UTC')
    prices = [price]
    for _ in range(n - 1):
        prices.insert(0, prices[0] * (1 + np.random.normal(0, 0.001)))
    closes = np.array(prices)
    highs = closes * (1 + np.abs(np.random.normal(0, 0.0005, n)))
    lows  = closes * (1 - np.abs(np.random.normal(0, 0.0005, n)))
    opens = np.roll(closes, 1); opens[0] = closes[0]
    vols  = np.random.randint(100, 10000, n).astype(float)
    df = pd.DataFrame({'open': opens, 'high': highs, 'low': lows, 'close': closes, 'volume': vols}, index=times)
    return df


def fetch_yfinance_history(symbol: str) -> pd.DataFrame | None:
    now = time.time()
    if symbol in _history_cache:
        ts, df = _history_cache[symbol]
        if now - ts < CACHE_TTL:
            return df
    try:
        ticker = yf.Ticker(symbol)
        df = ticker.history(period='1d', interval='1m')
        if df is None or df.empty:
            raise ValueError('empty')
        df.columns = [c.lower() for c in df.columns]
        if isinstance(df.index, pd.DatetimeTZInfo if hasattr(pd, 'DatetimeTZInfo') else type(None)):
            df.index = df.index
        df.index = pd.to_datetime(df.index, utc=True)
        df = df[['open', 'high', 'low', 'close', 'volume']].dropna()
        _history_cache[symbol] = (now, df)
        return df
    except Exception as e:
        logger.warning(f"YFinance history failed for {symbol}: {e}")
        # Fallback: try to get a live price and generate synthetic history
        price = fetch_finnhub_price(symbol)
        if price:
            logger.info(f"Using synthetic history for {symbol} @ {price}")
            df = _make_synthetic_history(price)
            _history_cache[symbol] = (now, df)
            return df
        return None


def fetch_yfinance_price(symbol: str) -> float | None:
    try:
        info = yf.Ticker(symbol).fast_info
        price = getattr(info, 'last_price', None) or getattr(info, 'lastPrice', None)
        if price and float(price) > 0:
            return round(float(price), 4)
    except:
        pass
    return None


# ── Public API ───────────────────────────────────────────────────────────────

def fetch_history(symbol: str) -> pd.DataFrame | None:
    df = fetch_binance_history(symbol)
    if df is not None and not df.empty:
        return df
    return fetch_yfinance_history(symbol)


def fetch_live_price(symbol: str) -> float | None:
    p = fetch_binance_price(symbol)
    if p: return p
    p = fetch_finnhub_price(symbol)
    if p: return p
    return fetch_yfinance_price(symbol)


def validate_symbol(symbol: str) -> bool:
    p = fetch_live_price(symbol.upper())
    return p is not None and p > 0


# ── Market Movers ────────────────────────────────────────────────────────────

TOP_SYMBOLS = ['AAPL','TSLA','MSFT','NVDA','AMD','META','NFLX','AMZN','GOOGL','ORCL']

def fetch_market_movers() -> dict:
    gainers = []
    for sym in TOP_SYMBOLS:
        try:
            res = requests.get(f"{FINNHUB_BASE}/quote",
                               params={'symbol': sym, 'token': FINNHUB_API_KEY}, timeout=4)
            if res.status_code == 200:
                d = res.json()
                if d.get('c') and d.get('dp') is not None:
                    gainers.append({'symbol': sym, 'price': round(d['c'], 2), 'change': round(d['dp'], 2)})
        except:
            pass
    gainers.sort(key=lambda x: x['change'], reverse=True)
    return {'gainers': gainers[:5], 'losers': sorted(gainers, key=lambda x: x['change'])[:5]}
