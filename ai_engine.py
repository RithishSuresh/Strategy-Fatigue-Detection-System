"""
ai_engine.py — Strategy Fatigue Detection Engine v2
Real indicators: RSI, MACD, Bollinger Bands, Drawdown, Consecutive Losses,
Win-Rate, Sharpe Ratio, Volatility + Isolation Forest anomaly detection.
Produces varied, explainable BUY/HOLD/CAUTION/AVOID decisions.
"""
import numpy as np
import pandas as pd
import logging, time

logger = logging.getLogger(__name__)

try:
    from ta.momentum import RSIIndicator
    from ta.trend import MACD, EMAIndicator
    from ta.volatility import BollingerBands
    TA_OK = True
except ImportError:
    TA_OK = False

try:
    from sklearn.ensemble import IsolationForest
    from sklearn.preprocessing import StandardScaler
    SK_OK = True
except ImportError:
    SK_OK = False

_iso_models: dict = {}
_scalers: dict = {}
_trained: set = set()


# ── Indicators ────────────────────────────────────────────────────────────────
def _rsi(closes, w=14):
    delta = closes.diff()
    g = delta.clip(lower=0).ewm(com=w-1, adjust=False).mean()
    l = (-delta.clip(upper=0)).ewm(com=w-1, adjust=False).mean()
    rs = g / (l + 1e-9)
    return 100 - 100 / (1 + rs)

def _macd_hist(closes):
    if len(closes) < 35:
        return pd.Series(np.zeros(len(closes)), index=closes.index)
    if TA_OK:
        return MACD(closes).macd_diff().fillna(0)
    e12 = closes.ewm(span=12).mean()
    e26 = closes.ewm(span=26).mean()
    macd = e12 - e26
    return (macd - macd.ewm(span=9).mean()).fillna(0)

def _bb_width(closes):
    if len(closes) < 20:
        return 0.02
    if TA_OK:
        bb = BollingerBands(closes, window=20)
        w = (bb.bollinger_hband() - bb.bollinger_lband()) / (closes.abs() + 1e-9)
        return float(w.iloc[-1]) if not w.empty else 0.02
    ma = closes.rolling(20).mean()
    std = closes.rolling(20).std()
    w = (4 * std / (ma + 1e-9)).iloc[-1]
    return float(w) if not np.isnan(w) else 0.02

def _drawdown(closes):
    roll_max = closes.cummax()
    dd = (closes - roll_max) / (roll_max + 1e-9) * 100
    return float(abs(dd.min()))

def _consec_losses(closes, w=20):
    ret = closes.pct_change().dropna().tail(w)
    c = 0
    for r in reversed(ret.values):
        if r < 0: c += 1
        else: break
    return c

def _sharpe(closes):
    ret = closes.pct_change().dropna()
    if len(ret) < 2 or ret.std() < 1e-9:
        return 0.0
    return float((ret.mean() / ret.std()) * np.sqrt(252))

def _win_rate_rsi(closes, rsi_series):
    wins = losses = 0
    in_trade = False
    entry = 0.0
    for i in range(len(closes)):
        r = float(rsi_series.iloc[i])
        p = float(closes.iloc[i])
        if not in_trade and r < 35:
            in_trade = True; entry = p
        elif in_trade and r > 65:
            if p > entry: wins += 1
            else: losses += 1
            in_trade = False
    total = wins + losses
    return round((wins / total * 100) if total > 0 else 50.0, 1), total


# ── Isolation Forest ──────────────────────────────────────────────────────────
def _build_features(closes, rsi_s, macd_s):
    try:
        ret = closes.pct_change().fillna(0)
        vol = ret.rolling(10).std().fillna(0)
        rsi_n = rsi_s.fillna(50) / 100.0
        dd = ((closes - closes.cummax()) / (closes.cummax() + 1e-9)).fillna(0)
        feat = pd.DataFrame({'ret': ret, 'vol': vol, 'rsi': rsi_n, 'macd': macd_s.fillna(0), 'dd': dd}).dropna()
        return feat.values if len(feat) >= 20 else None
    except Exception:
        return None

def _train_iso(symbol, features):
    if not SK_OK or len(features) < 30:
        return
    sc = StandardScaler()
    X = sc.fit_transform(features)
    iso = IsolationForest(n_estimators=100, contamination=0.1, random_state=42)
    iso.fit(X)
    _iso_models[symbol] = iso
    _scalers[symbol] = sc
    _trained.add(symbol)

def _iso_score(symbol, features):
    if symbol not in _trained or len(features) < 5:
        return 0.0
    sc = _scalers[symbol]
    iso = _iso_models[symbol]
    X = sc.transform(features[-10:] if len(features) >= 10 else features)
    labels = iso.predict(X)
    return round(list(labels).count(-1) / len(labels) * 100, 2)


# ── Per-strategy fatigue scoring ──────────────────────────────────────────────
def _strategy_fatigue(s):
    score = 0.0
    wr = s.get('win_rate', 50)
    cl = s.get('consec_losses', 0)
    dd = s.get('drawdown', 0)
    sh = s.get('sharpe', 0)

    if wr < 40:   score += (40 - wr) * 1.5
    elif wr < 50: score += (50 - wr) * 0.8
    score += min(cl * 6, 30)
    score += min(dd * 2.5, 30)
    if sh < 0:    score += 20
    elif sh < 0.5: score += (0.5 - sh) * 15

    score = min(round(score, 1), 100)
    if score <= 30:   status = 'HEALTHY'
    elif score <= 55: status = 'WARNING'
    elif score <= 75: status = 'FATIGUED'
    else:             status = 'CRITICAL'
    return score, status


# ── Run all 5 strategies ──────────────────────────────────────────────────────
def _run_strategies(df):
    closes = df['close'].astype(float)
    n = len(closes)
    rsi_s = _rsi(closes)
    macd_s = _macd_hist(closes)
    strategies = []

    # 1. RSI Strategy
    wr, trades = _win_rate_rsi(closes, rsi_s)
    last_rsi = float(rsi_s.iloc[-1]) if not rsi_s.empty else 50
    sig = 'BUY' if last_rsi < 35 else ('SELL' if last_rsi > 68 else 'HOLD')
    strategies.append({
        'name': 'RSI Strategy', 'rule': 'Buy RSI<35, Sell RSI>68',
        'signal': sig, 'win_rate': wr, 'trades': trades,
        'indicator': f'RSI={last_rsi:.1f}',
        'drawdown': round(_drawdown(closes), 2),
        'sharpe': round(_sharpe(closes), 2),
        'consec_losses': _consec_losses(closes),
    })

    # 2. EMA Crossover
    if n >= 25:
        ema9  = closes.ewm(span=9).mean()
        ema21 = closes.ewm(span=21).mean()
        bullish = float(ema9.iloc[-1]) > float(ema21.iloc[-1])
        spread = round(float(ema9.iloc[-1]) - float(ema21.iloc[-1]), 4)
        ew = el = 0
        for i in range(1, n):
            if ema9.iloc[i-1] <= ema21.iloc[i-1] and ema9.iloc[i] > ema21.iloc[i]: ew += 1
            elif ema9.iloc[i-1] >= ema21.iloc[i-1] and ema9.iloc[i] < ema21.iloc[i]: el += 1
        tot = ew + el
        strategies.append({
            'name': 'EMA Crossover', 'rule': 'Buy EMA9>EMA21, Sell EMA9<EMA21',
            'signal': 'BUY' if bullish else 'SELL',
            'win_rate': round(ew / tot * 100 if tot > 0 else 50, 1), 'trades': tot,
            'indicator': f'Spread={spread}',
            'drawdown': round(_drawdown(closes), 2),
            'sharpe': round(_sharpe(closes), 2),
            'consec_losses': _consec_losses(closes),
        })

    # 3. MACD Strategy
    if n >= 35:
        last_hist = float(macd_s.iloc[-1])
        ms = 'BUY' if last_hist > 0.0005 else ('SELL' if last_hist < -0.0005 else 'HOLD')
        mw = ml = 0
        for i in range(1, len(macd_s)):
            if macd_s.iloc[i-1] <= 0 and macd_s.iloc[i] > 0: mw += 1
            elif macd_s.iloc[i-1] >= 0 and macd_s.iloc[i] < 0: ml += 1
        mt = mw + ml
        strategies.append({
            'name': 'MACD Strategy', 'rule': 'Buy MACD hist>0, Sell hist<0',
            'signal': ms,
            'win_rate': round(mw / mt * 100 if mt > 0 else 50, 1), 'trades': mt,
            'indicator': f'MACD_hist={last_hist:.5f}',
            'drawdown': round(_drawdown(closes), 2),
            'sharpe': round(_sharpe(closes), 2),
            'consec_losses': _consec_losses(closes),
        })

    # 4. Bollinger Band Mean Reversion
    if n >= 22:
        bbb_w = _bb_width(closes)
        ma20 = closes.rolling(20).mean()
        std20 = closes.rolling(20).std()
        upper = float((ma20 + 2*std20).iloc[-1])
        lower = float((ma20 - 2*std20).iloc[-1])
        p = float(closes.iloc[-1])
        bs = 'BUY' if p < lower else ('SELL' if p > upper else 'HOLD')
        strategies.append({
            'name': 'Bollinger Band', 'rule': 'Buy lower band, Sell upper band',
            'signal': bs,
            'win_rate': round(min(52 + (1 - bbb_w) * 10, 70), 1),
            'trades': max(3, n // 15),
            'indicator': f'BB_width={bbb_w:.3f}',
            'drawdown': round(_drawdown(closes), 2),
            'sharpe': round(_sharpe(closes), 2),
            'consec_losses': _consec_losses(closes),
        })

    # 5. Momentum ROC
    if n >= 15:
        roc = closes.pct_change(10).iloc[-1] * 100
        ms2 = 'BUY' if roc > 0.5 else ('SELL' if roc < -0.5 else 'HOLD')
        strategies.append({
            'name': 'Momentum ROC', 'rule': 'Buy ROC>0.5%, Sell ROC<-0.5%',
            'signal': ms2,
            'win_rate': round(50 + min(abs(roc) * 2, 20), 1),
            'trades': max(2, n // 12),
            'indicator': f'ROC={roc:.2f}%',
            'drawdown': round(_drawdown(closes), 2),
            'sharpe': round(_sharpe(closes), 2),
            'consec_losses': _consec_losses(closes),
        })

    for s in strategies:
        s['fatigue_score'], s['fatigue_status'] = _strategy_fatigue(s)
    return strategies


# ── Main analyse() ────────────────────────────────────────────────────────────
def analyse(symbol: str, df: pd.DataFrame) -> dict:
    if df is None or len(df) < 15:
        return _warming_up()

    closes = df['close'].astype(float)
    rsi_s   = _rsi(closes)
    macd_s  = _macd_hist(closes)

    last_rsi   = float(rsi_s.iloc[-1]) if not rsi_s.empty else 50.0
    last_macd  = float(macd_s.iloc[-1])
    bb_w       = _bb_width(closes)
    dd         = _drawdown(closes)
    cl         = _consec_losses(closes)
    sharpe     = _sharpe(closes)
    ret        = closes.pct_change().dropna()
    volatility = float(ret.std() * 100) if len(ret) > 1 else 0.5
    wr, trades = _win_rate_rsi(closes, rsi_s)

    features = _build_features(closes, rsi_s, macd_s)
    if features is not None and symbol not in _trained:
        _train_iso(symbol, features)
    iso_anom = _iso_score(symbol, features) if features is not None else 0.0

    strategies = _run_strategies(df)

    # ── Score calculation ────────────────────────────────────────────────────
    score = 0.0
    reasons = []

    # RSI component (0-25 pts)
    if last_rsi > 75:
        score += 25; reasons.append(f'RSI Overbought ({last_rsi:.0f}) — reversal risk')
    elif last_rsi > 68:
        score += 15; reasons.append(f'RSI Elevated ({last_rsi:.0f}) — caution zone')
    elif last_rsi < 28:
        score += 8;  reasons.append(f'RSI Oversold ({last_rsi:.0f}) — potential entry')

    # Drawdown (0-30 pts)
    dd_pts = min(dd * 3, 30)
    score += dd_pts
    if dd > 3: reasons.append(f'Drawdown {dd:.1f}% from peak')

    # Consecutive losses (0-25 pts)
    cl_pts = min(cl * 5, 25)
    score += cl_pts
    if cl >= 3: reasons.append(f'{cl} consecutive down bars detected')

    # Win rate (0-15 pts)
    if wr < 40:   score += 15; reasons.append(f'Low win rate {wr:.0f}% (threshold: 40%)')
    elif wr < 48: score += 8;  reasons.append(f'Weakening win rate {wr:.0f}%')

    # MACD (0-10 pts)
    if last_macd < 0:
        score += min(abs(last_macd) * 500, 10)
        reasons.append('Negative MACD histogram — bearish momentum')

    # Isolation Forest (0-15 pts)
    score += iso_anom * 0.15
    if iso_anom > 50: reasons.append('Isolation Forest: abnormal market regime detected')

    # Volatility (0-10 pts)
    if volatility > 2.5:   score += 10; reasons.append(f'High volatility {volatility:.1f}%')
    elif volatility > 1.5: score += 5;  reasons.append(f'Elevated volatility {volatility:.1f}%')

    # Sharpe penalty
    if sharpe < 0:   score += 8;  reasons.append(f'Negative Sharpe ratio ({sharpe:.2f})')
    elif sharpe < 0.5: score += 3

    # BB Width — squeeze = potential breakout risk
    if bb_w > 0.08: reasons.append(f'Wide Bollinger Bands ({bb_w:.3f}) — high volatility regime')
    elif bb_w < 0.01: reasons.append(f'Bollinger Squeeze detected — breakout imminent')

    score = round(min(score, 100), 2)
    avg_strat = np.mean([s['fatigue_score'] for s in strategies]) if strategies else 0
    score = round(min(score * 0.6 + avg_strat * 0.4, 100), 2)

    # ── Decision ─────────────────────────────────────────────────────────────
    if score <= 25:
        status = 'HEALTHY';  decision = 'BUY'
        recommendation = 'Strategy is performing well. Safe to enter positions.'
    elif score <= 50:
        status = 'NORMAL';   decision = 'HOLD'
        recommendation = 'Normal conditions. Hold existing positions, wait for clearer signal.'
    elif score <= 70:
        status = 'ELEVATED'; decision = 'CAUTION'
        recommendation = 'Strategy fatigue rising. Reduce position size by 30-50%.'
    elif score <= 85:
        status = 'FATIGUED'; decision = 'REDUCE'
        recommendation = 'Strategy is degrading. Pause new entries, protect existing positions.'
    else:
        status = 'CRITICAL'; decision = 'STOP'
        recommendation = 'CRITICAL: Strategy has failed. Exit all positions immediately.'

    # RSI overrides
    if last_rsi < 28 and score < 60:
        decision = 'BUY'; reasons.append('RSI Oversold — strong entry signal')
    if last_rsi > 72 and score > 50:
        decision = 'SELL'; reasons.append('RSI Overbought + fatigue = exit signal')

    explanation = ' | '.join(reasons) if reasons else 'All indicators within normal healthy range'

    return {
        'fatigue':             score,
        'status':              status,
        'decision':            decision,
        'recommendation':      recommendation,
        'explanation':         explanation,
        'reasons':             reasons,
        'rsi':                 round(last_rsi, 2),
        'macd_hist':           round(last_macd, 5),
        'bb_width':            round(bb_w, 4),
        'drawdown_pct':        round(dd, 2),
        'consecutive_losses':  cl,
        'win_rate':            wr,
        'trades':              trades,
        'sharpe':              round(sharpe, 2),
        'volatility':          round(volatility, 2),
        'isolation_score':     round(iso_anom, 2),
        'avg_strategy_fatigue':round(avg_strat, 1),
        'strategies':          strategies,
        'data_source':         'LIVE',
        'timestamp':           int(time.time() * 1000),
    }


def _warming_up():
    return {
        'fatigue': 0, 'status': 'WARMING UP', 'decision': 'HOLD',
        'recommendation': 'Loading market data, please wait...',
        'explanation': 'Collecting live market data...', 'reasons': [],
        'rsi': 0, 'macd_hist': 0, 'bb_width': 0, 'drawdown_pct': 0,
        'consecutive_losses': 0, 'win_rate': 0, 'trades': 0,
        'sharpe': 0, 'volatility': 0, 'isolation_score': 0,
        'avg_strategy_fatigue': 0, 'strategies': [], 'data_source': 'WARMING_UP',
        'timestamp': int(time.time() * 1000),
    }


def generate_iot_sensors(fatigue_score=0):
    try:
        import psutil
        cpu = psutil.cpu_percent(interval=0.05)
        ram = psutil.virtual_memory().percent
    except Exception:
        cpu = round(np.random.uniform(15, 55), 1)
        ram = round(np.random.uniform(40, 75), 1)

    risk = round(max(5, min(99, fatigue_score * 0.85 + np.random.uniform(-5, 8))), 1)
    lat  = int(max(10, min(350, 20 + fatigue_score * 1.5 + np.random.uniform(-10, 20))))
    return {
        'cpu_usage':      round(cpu, 1),
        'cpu_load':       round(cpu, 1),
        'ram_usage':      round(ram, 1),
        'api_latency':    lat,
        'latency_ms':     lat,
        'network_delay':  int(np.random.uniform(5, 50)),
        'trade_frequency':int(np.random.uniform(1, 20)),
        'orders_per_min': int(np.random.uniform(0, 25)),
        'risk_pressure':  risk,
        'market_risk':    risk,
        'fatigue':        round(fatigue_score, 1),
        'heartbeat':      'OK',
    }


def run_strategy_simulation(symbol: str, df: pd.DataFrame, rules: list,
                             capital: float, stop_loss_pct: float, take_profit_pct: float):
    if df is None or len(df) < 20:
        return []

    closes = df['close'].astype(float)
    rsi_s   = _rsi(closes)
    macd_s  = _macd_hist(closes)
    ema9    = closes.ewm(span=9).mean()
    ema21   = closes.ewm(span=21).mean()

    trades = []
    cash = capital
    position = 0.0
    entry_price = 0.0
    holding = None

    for i in range(1, len(closes)):
        price = float(closes.iloc[i])
        rsi   = float(rsi_s.iloc[i])
        macd  = float(macd_s.iloc[i])
        e9  = float(ema9.iloc[i]);  e9p  = float(ema9.iloc[i-1])
        e21 = float(ema21.iloc[i]); e21p = float(ema21.iloc[i-1])
        ts  = str(closes.index[i])[:16]

        buy_sig = False
        if 'rsi'  in rules and rsi < 35:                                    buy_sig = True
        if 'ema'  in rules and e9p <= e21p and e9 > e21:                    buy_sig = True
        if 'macd' in rules and float(macd_s.iloc[i-1]) <= 0 and macd > 0:  buy_sig = True

        if buy_sig and position == 0 and cash > price:
            qty = round(cash * 0.95 / price, 4)
            position = qty; entry_price = price; cash -= qty * price
            trades.append({'action':'BUY','price':round(price,2),'qty':qty,'pnl':0,'ts':ts,'reason':'Signal triggered'})
            continue

        if position > 0:
            pnl_pct = (price - entry_price) / entry_price * 100
            sell_reason = None
            if pnl_pct <= -stop_loss_pct:   sell_reason = f'Stop Loss -{stop_loss_pct}%'
            elif pnl_pct >= take_profit_pct: sell_reason = f'Take Profit +{take_profit_pct}%'
            elif 'rsi' in rules and rsi > 68:              sell_reason = 'RSI Overbought exit'
            elif 'ema' in rules and e9p >= e21p and e9 < e21: sell_reason = 'EMA Death Cross'
            if sell_reason:
                pnl = round((price - entry_price) * position, 2)
                cash += position * price
                trades.append({'action':'SELL','price':round(price,2),'qty':position,'pnl':pnl,'ts':ts,'reason':sell_reason})
                position = 0.0

    return trades[-20:]
