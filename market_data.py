"""
market_data.py — Multi-source real market data engine.
Priority: Binance (crypto) → yfinance → yahooquery
Handles Indian .NS stocks, Crypto, Forex, US stocks.
"""
import time, logging, threading, requests
import pandas as pd
import numpy as np

logger = logging.getLogger(__name__)

try:
    import yfinance as yf
    YFINANCE_OK = True
except ImportError:
    YFINANCE_OK = False

try:
    from yahooquery import Ticker as YQTicker
    YAHOOQUERY_OK = True
except ImportError:
    YAHOOQUERY_OK = False

_price_cache: dict = {}
_hist_cache:  dict = {}
_lock = threading.Lock()
PRICE_TTL   = 5
HISTORY_TTL = 120

STOCK_UNIVERSE = [
    {'symbol':'AAPL',  'name':'Apple Inc.',               'type':'US'},
    {'symbol':'TSLA',  'name':'Tesla Inc.',               'type':'US'},
    {'symbol':'MSFT',  'name':'Microsoft Corp.',          'type':'US'},
    {'symbol':'NVDA',  'name':'NVIDIA Corp.',             'type':'US'},
    {'symbol':'AMZN',  'name':'Amazon.com',               'type':'US'},
    {'symbol':'GOOGL', 'name':'Alphabet Inc.',            'type':'US'},
    {'symbol':'META',  'name':'Meta Platforms',           'type':'US'},
    {'symbol':'AMD',   'name':'Advanced Micro Devices',   'type':'US'},
    {'symbol':'NFLX',  'name':'Netflix Inc.',             'type':'US'},
    {'symbol':'JPM',   'name':'JPMorgan Chase',           'type':'US'},
    {'symbol':'V',     'name':'Visa Inc.',                'type':'US'},
    {'symbol':'UBER',  'name':'Uber Technologies',        'type':'US'},
    {'symbol':'COIN',  'name':'Coinbase Global',          'type':'US'},
    {'symbol':'PLTR',  'name':'Palantir Technologies',    'type':'US'},
    {'symbol':'BAC',   'name':'Bank of America',          'type':'US'},
    {'symbol':'BTC-USD',  'name':'Bitcoin',    'type':'Crypto'},
    {'symbol':'ETH-USD',  'name':'Ethereum',   'type':'Crypto'},
    {'symbol':'SOL-USD',  'name':'Solana',     'type':'Crypto'},
    {'symbol':'BNB-USD',  'name':'Binance Coin','type':'Crypto'},
    {'symbol':'DOGE-USD', 'name':'Dogecoin',   'type':'Crypto'},
    {'symbol':'XRP-USD',  'name':'XRP Ripple', 'type':'Crypto'},
    {'symbol':'ADA-USD',  'name':'Cardano',    'type':'Crypto'},
    {'symbol':'AVAX-USD', 'name':'Avalanche',  'type':'Crypto'},
    {'symbol':'RELIANCE.NS',   'name':'Reliance Industries',       'type':'India'},
    {'symbol':'TCS.NS',        'name':'Tata Consultancy Services', 'type':'India'},
    {'symbol':'INFY.NS',       'name':'Infosys Ltd',               'type':'India'},
    {'symbol':'HDFCBANK.NS',   'name':'HDFC Bank',                 'type':'India'},
    {'symbol':'ICICIBANK.NS',  'name':'ICICI Bank',                'type':'India'},
    {'symbol':'SBIN.NS',       'name':'State Bank of India',       'type':'India'},
    {'symbol':'ITC.NS',        'name':'ITC Limited',               'type':'India'},
    {'symbol':'WIPRO.NS',      'name':'Wipro Ltd',                 'type':'India'},
    {'symbol':'LT.NS',         'name':'Larsen & Toubro',           'type':'India'},
    {'symbol':'BAJFINANCE.NS', 'name':'Bajaj Finance',             'type':'India'},
    {'symbol':'HCLTECH.NS',    'name':'HCL Technologies',          'type':'India'},
    {'symbol':'AXISBANK.NS',   'name':'Axis Bank',                 'type':'India'},
    {'symbol':'KOTAKBANK.NS',  'name':'Kotak Mahindra Bank',       'type':'India'},
    {'symbol':'MARUTI.NS',     'name':'Maruti Suzuki',             'type':'India'},
    {'symbol':'TITAN.NS',      'name':'Titan Company',             'type':'India'},
    {'symbol':'ADANIENT.NS',   'name':'Adani Enterprises',         'type':'India'},
    {'symbol':'SUNPHARMA.NS',  'name':'Sun Pharmaceutical',        'type':'India'},
    {'symbol':'BHARTIARTL.NS', 'name':'Bharti Airtel',             'type':'India'},
    {'symbol':'TATASTEEL.NS',  'name':'Tata Steel',                'type':'India'},
    {'symbol':'^NSEI',  'name':'NIFTY 50',   'type':'Index'},
    {'symbol':'^GSPC',  'name':'S&P 500',     'type':'Index'},
    {'symbol':'^DJI',   'name':'Dow Jones',   'type':'Index'},
    {'symbol':'^IXIC',  'name':'NASDAQ',      'type':'Index'},
    {'symbol':'^BSESN', 'name':'BSE Sensex',  'type':'Index'},
    {'symbol':'USDINR=X','name':'USD/INR',    'type':'Forex'},
    {'symbol':'EURUSD=X','name':'EUR/USD',    'type':'Forex'},
    {'symbol':'GBPUSD=X','name':'GBP/USD',    'type':'Forex'},
    {'symbol':'USDJPY=X','name':'USD/JPY',    'type':'Forex'},
]

def _is_crypto(s): return '-USD' in s or '-USDT' in s
def _is_indian(s): return s.endswith('.NS') or s.endswith('.BO')
def _binance_sym(s): return s.replace('-USD','USDT').upper()


def fetch_live_price(symbol: str):
    with _lock:
        c = _price_cache.get(symbol)
        if c and time.time() - c[0] < PRICE_TTL:
            return c[1]

    price = None

    # Binance — fastest for crypto
    if _is_crypto(symbol):
        try:
            bsym = _binance_sym(symbol)
            r = requests.get(f"https://api.binance.com/api/v3/ticker/price?symbol={bsym}", timeout=3)
            if r.status_code == 200:
                price = float(r.json()['price'])
        except Exception:
            pass

    # yfinance fast_info
    if price is None and YFINANCE_OK:
        try:
            info = yf.Ticker(symbol).fast_info
            v = getattr(info, 'last_price', None) or getattr(info, 'lastPrice', None)
            if v and float(v) > 0:
                price = float(v)
        except Exception:
            pass

    # yahooquery fallback
    if price is None and YAHOOQUERY_OK:
        try:
            t = YQTicker(symbol)
            p = t.price
            if isinstance(p, dict) and symbol in p:
                pd_ = p[symbol]
                if isinstance(pd_, dict):
                    v = pd_.get('regularMarketPrice') or pd_.get('postMarketPrice')
                    if v and float(v) > 0:
                        price = float(v)
        except Exception:
            pass

    if price and price > 0:
        price = round(price, 6)
        with _lock:
            _price_cache[symbol] = (time.time(), price)
    return price


def fetch_history(symbol: str):
    with _lock:
        c = _hist_cache.get(symbol)
        if c and time.time() - c[0] < HISTORY_TTL:
            return c[1]

    df = None

    # Binance klines for crypto
    if _is_crypto(symbol):
        try:
            bsym = _binance_sym(symbol)
            r = requests.get(
                f"https://api.binance.com/api/v3/klines?symbol={bsym}&interval=5m&limit=288",
                timeout=8)
            if r.status_code == 200:
                cols = ['t','o','h','l','c','v','ct','qa','nt','tbv','tqv','i']
                tmp = pd.DataFrame(r.json(), columns=cols)
                tmp['t'] = pd.to_datetime(tmp['t'], unit='ms', utc=True)
                tmp = tmp.set_index('t').rename(columns={'o':'open','h':'high','l':'low','c':'close','v':'volume'})
                for col in ['open','high','low','close','volume']:
                    tmp[col] = tmp[col].astype(float)
                df = tmp[['open','high','low','close','volume']].dropna()
                if df.empty: df = None
        except Exception as e:
            logger.debug(f"Binance {symbol}: {e}")

    # yfinance — handles Indian .NS stocks very well
    if df is None and YFINANCE_OK:
        tk = yf.Ticker(symbol)
        for per, ivl in [('1d','1m'), ('5d','5m'), ('1mo','30m')]:
            try:
                hist = tk.history(period=per, interval=ivl)
                if hist is None or hist.empty or len(hist) < 20:
                    continue
                hist.columns = [c.lower() for c in hist.columns]
                hist.index = pd.to_datetime(hist.index, utc=True)
                needed = ['open','high','low','close','volume']
                if all(c in hist.columns for c in needed):
                    tmp_df = hist[needed].dropna()
                    if len(tmp_df) >= 20:
                        df = tmp_df
                        break
            except Exception as e:
                logger.debug(f"yfinance {symbol} {per}/{ivl}: {e}")

    # yahooquery fallback
    if df is None and YAHOOQUERY_OK:
        try:
            t = YQTicker(symbol)
            for per, ivl in [('1d','1m'), ('5d','5m')]:
                try:
                    hist = t.history(period=per, interval=ivl)
                    if hist is None or isinstance(hist, str) or hist.empty:
                        continue
                    hist = hist.reset_index()
                    for col in ['date','Datetime','timestamp']:
                        if col in hist.columns:
                            hist = hist.rename(columns={col:'time'}); break
                    hist['time'] = pd.to_datetime(hist['time'], utc=True)
                    hist = hist.set_index('time')
                    hist.columns = [c.lower() for c in hist.columns]
                    needed = ['open','high','low','close','volume']
                    if all(c in hist.columns for c in needed):
                        tmp_df = hist[needed].dropna()
                        if len(tmp_df) >= 20:
                            df = tmp_df; break
                except Exception:
                    continue
        except Exception as e:
            logger.warning(f"yahooquery {symbol}: {e}")

    if df is None or df.empty or len(df) < 10:
        logger.warning(f"No history for {symbol}")
        return None

    with _lock:
        _hist_cache[symbol] = (time.time(), df)
    logger.info(f"History: {symbol} — {len(df)} bars")
    return df


def search_symbols(query: str):
    q = query.upper().strip()
    if not q: return []
    
    try:
        res = requests.get(f"https://query2.finance.yahoo.com/v1/finance/search?q={q}&quotesCount=10", timeout=3, headers={'User-Agent': 'Mozilla/5.0'})
        if res.status_code == 200:
            data = res.json().get('quotes', [])
            results = []
            for item in data:
                sym = item.get('symbol')
                name = item.get('shortname') or item.get('longname') or sym
                type_disp = item.get('quoteType', 'Stock')
                if sym and name:
                    results.append({'symbol': sym, 'name': name, 'type': type_disp})
            if results: return results
    except Exception as e:
        logger.debug(f"Search error: {e}")
        
    return [
        {'symbol': s['symbol'], 'name': s['name'], 'type': s.get('type','Stock')}
        for s in STOCK_UNIVERSE
        if q in s['symbol'].upper() or q in s['name'].upper()
    ][:15]
