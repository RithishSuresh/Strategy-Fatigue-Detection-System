"""
app.py — Premium AIoT Strategy Fatigue Detection Terminal
Flask + SocketIO backend with SQLite database.
"""
import os, time, logging, threading
from functools import wraps
from flask import Flask, render_template, request, jsonify, session, redirect, url_for, flash
from flask_socketio import SocketIO, emit

from database import init_db, get_db
from market_data import fetch_history, fetch_live_price, search_symbols
from ai_engine import analyse, generate_iot_sensors, run_strategy_simulation

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

app = Flask(__name__)
app.secret_key = 'aiot_fatigue_2026_premium_key'
socketio = SocketIO(app, async_mode='threading', cors_allowed_origins='*', logger=False, engineio_logger=False)

# ── State ─────────────────────────────────────────────────────────────────────
CLIENT_STATE: dict = {}   # sid → {main, watchlist}
MARKET_CACHE: dict = {}   # symbol → {df, last_price, metrics, sensors, tick}
_lock = threading.Lock()

init_db()

# ── Auth Helpers ──────────────────────────────────────────────────────────────
def login_required(f):
    @wraps(f)
    def dec(*args, **kwargs):
        if 'user_id' not in session:
            if request.is_json:
                return jsonify({'error': 'Authentication required'}), 401
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return dec

# ── Routes: Auth & Pages ──────────────────────────────────────────────────────
@app.route('/')
def index():
    if 'user_id' in session:
        return redirect(url_for('dashboard'))
    return render_template('landing.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')
        conn = get_db()
        user = conn.execute('SELECT * FROM users WHERE username=?', (username,)).fetchone()
        conn.close()
        
        from werkzeug.security import check_password_hash
        if user and check_password_hash(user['password'], password):
            session['user_id'] = user['id']
            session['username'] = user['username']
            return redirect(url_for('dashboard'))
        flash('Invalid credentials.', 'error')
    return render_template('login.html')

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')
        from werkzeug.security import generate_password_hash
        if not username or not password:
            flash('All fields required.', 'error')
            return render_template('register.html')
        try:
            conn = get_db()
            conn.execute('INSERT INTO users (username, password) VALUES (?, ?)',
                         (username, generate_password_hash(password)))
            conn.commit()
            conn.close()
            flash('Account created! Please login.', 'success')
            return redirect(url_for('login'))
        except Exception:
            flash('Username already exists.', 'error')
    return render_template('register.html')

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('index'))

@app.route('/dashboard')
@login_required
def dashboard():
    conn = get_db()
    user = conn.execute('SELECT id, username, balance FROM users WHERE id=?', (session['user_id'],)).fetchone()
    wl_rows = conn.execute('SELECT symbol FROM watchlist WHERE user_id=?', (session['user_id'],)).fetchall()
    conn.close()
    
    watchlist = [r['symbol'] for r in wl_rows]
    if not watchlist:
        watchlist = ['BTC-USD', 'RELIANCE.NS', 'TSLA', 'AAPL', 'ETH-USD']
        
    return render_template('dashboard.html', user=dict(user), watchlist=watchlist)

# ── API: Market & Search ──────────────────────────────────────────────────────
@app.route('/api/search')
def api_search():
    q = request.args.get('q', '').strip()
    return jsonify(search_symbols(q))

@app.route('/api/movers')
def api_movers():
    items = []
    with _lock:
        for sym, cache in MARKET_CACHE.items():
            price = cache.get('last_price', 0)
            df = cache.get('df')
            if price > 0 and df is not None and not df.empty:
                open_p = float(df['open'].iloc[0])
                if open_p > 0:
                    chg = round((price - open_p) / open_p * 100, 2)
                    items.append({'symbol': sym, 'price': price, 'change': chg})
    
    seen = set()
    unique_items = []
    for item in items:
        if item['symbol'] not in seen:
            seen.add(item['symbol'])
            unique_items.append(item)
            
    gainers = [i for i in unique_items if i['change'] > 0]
    losers = [i for i in unique_items if i['change'] < 0]
    
    gainers.sort(key=lambda x: x['change'], reverse=True)
    losers.sort(key=lambda x: x['change'])
    
    return jsonify({'gainers': gainers[:5], 'losers': losers[:5]})

# ── API: Watchlist ────────────────────────────────────────────────────────────
@app.route('/api/watchlist/add', methods=['POST'])
@login_required
def api_watchlist_add():
    data = request.get_json() or {}
    symbol = data.get('symbol', '').upper().strip()
    if not symbol: return jsonify({'error': 'Symbol required'}), 400
    conn = get_db()
    try:
        conn.execute('INSERT OR IGNORE INTO watchlist (user_id, symbol) VALUES (?,?)', (session['user_id'], symbol))
        conn.commit()
    finally:
        conn.close()
    return jsonify({'success': True, 'symbol': symbol})

@app.route('/api/watchlist/remove', methods=['POST'])
@login_required
def api_watchlist_remove():
    data = request.get_json() or {}
    symbol = data.get('symbol', '').upper().strip()
    conn = get_db()
    conn.execute('DELETE FROM watchlist WHERE user_id=? AND symbol=?', (session['user_id'], symbol))
    conn.commit()
    conn.close()
    return jsonify({'success': True})

# ── API: Portfolio & Trade ────────────────────────────────────────────────────
@app.route('/api/portfolio')
@login_required
def api_portfolio():
    conn = get_db()
    user = conn.execute('SELECT balance FROM users WHERE id=?', (session['user_id'],)).fetchone()
    holdings = conn.execute('SELECT symbol, qty, avg_price FROM portfolio WHERE user_id=? AND qty>0', (session['user_id'],)).fetchall()
    conn.close()
    
    res = []
    total_val = 0
    for h in holdings:
        sym = h['symbol']
        live_price = fetch_live_price(sym) or h['avg_price']
        val = live_price * h['qty']
        pnl = (live_price - h['avg_price']) * h['qty']
        res.append({
            'symbol': sym, 'qty': h['qty'], 'avg_price': round(h['avg_price'], 2),
            'live_price': round(live_price, 2), 'pnl': round(pnl, 2), 'value': round(val, 2)
        })
        total_val += val
        
    return jsonify({
        'balance': round(user['balance'], 2),
        'holdings': res,
        'total_value': round(user['balance'] + total_val, 2)
    })

@app.route('/api/trade', methods=['POST'])
@login_required
def api_trade():
    data = request.get_json() or {}
    symbol = data.get('symbol', '').upper()
    action = data.get('action', '').upper()
    qty    = float(data.get('qty', 0))
    strategy = data.get('strategy', 'MANUAL')

    if not symbol or action not in ('BUY', 'SELL') or qty <= 0:
        return jsonify({'error': 'Invalid parameters'}), 400

    try:
        cache = MARKET_CACHE.get(symbol)
        if cache and cache.get('last_price'):
            price = cache['last_price']
        else:
            price = fetch_live_price(symbol)
            
        if not price: return jsonify({'error': 'Cannot fetch live price'}), 400

        price = round(price, 2)
        total = round(price * qty, 2)
        
        conn = get_db()
        user = conn.execute('SELECT balance FROM users WHERE id=?', (session['user_id'],)).fetchone()
        
        if not user:
            return jsonify({'error': 'Session corrupted. Please log out and log in again.'}), 401

        if action == 'BUY':
            if user['balance'] < total:
                return jsonify({'error': f'Insufficient balance. Need {total}'}), 400
            conn.execute('UPDATE users SET balance=balance-? WHERE id=?', (total, session['user_id']))
            ext = conn.execute('SELECT qty, avg_price FROM portfolio WHERE user_id=? AND symbol=?', (session['user_id'], symbol)).fetchone()
            if ext:
                nq = ext['qty'] + qty
                na = ((ext['avg_price'] * ext['qty']) + total) / nq
                conn.execute('UPDATE portfolio SET qty=?, avg_price=? WHERE user_id=? AND symbol=?', (nq, na, session['user_id'], symbol))
            else:
                conn.execute('INSERT INTO portfolio (user_id,symbol,qty,avg_price) VALUES (?,?,?,?)', (session['user_id'], symbol, qty, price))
        else:
            h = conn.execute('SELECT qty,avg_price FROM portfolio WHERE user_id=? AND symbol=?', (session['user_id'], symbol)).fetchone()
            if not h or h['qty'] < qty:
                return jsonify({'error': 'Insufficient holdings'}), 400
            conn.execute('UPDATE users SET balance=balance+? WHERE id=?', (total, session['user_id']))
            conn.execute('UPDATE portfolio SET qty=? WHERE user_id=? AND symbol=?', (h['qty'] - qty, session['user_id'], symbol))

        conn.execute('INSERT INTO trades (user_id,symbol,action,qty,price,total,strategy) VALUES (?,?,?,?,?,?,?)',
                     (session['user_id'], symbol, action, qty, price, total, strategy))
        conn.commit()
    except Exception as e:
        logger.error(f"Trade error: {e}")
        return jsonify({'error': f"Internal server error: {str(e)}"}), 500
    finally:
        try: conn.close()
        except: pass
    return jsonify({'success': True, 'price': price, 'total': total, 'action': action, 'message': 'Trade executed'})

@app.route('/api/history')
@login_required
def api_history():
    conn = get_db()
    trades = conn.execute('SELECT * FROM trades WHERE user_id=? ORDER BY id DESC LIMIT 50', (session['user_id'],)).fetchall()
    conn.close()
    return jsonify([dict(t) for t in trades])

# ── API: AI & Node-RED ────────────────────────────────────────────────────────
@app.route('/api/metrics')
def api_metrics():
    """Endpoint for Node-RED to fetch live AI telemetry."""
    target_sym = None
    with _lock:
        for sid, state in CLIENT_STATE.items():
            if state.get('main'):
                target_sym = state['main']
                break
        
        if not target_sym and MARKET_CACHE:
            target_sym = list(MARKET_CACHE.keys())[0]
            
        cache = MARKET_CACHE.get(target_sym, {}) if target_sym else {}
        
    m = cache.get('metrics')
    if m:
        s = cache.get('sensors', generate_iot_sensors(m.get('fatigue', 0)))
        return jsonify({
            'success': True, 'symbol': target_sym,
            'metrics': {
                'fatigue': m.get('fatigue', 0),
                'status': m.get('status', 'WARMING UP'),
                'decision': m.get('decision', 'HOLD'),
                'explanation': m.get('explanation', '')
            },
            'sensors': s,
            'last_price': cache.get('last_price', 0),
            'timestamp': int(time.time() * 1000)
        })
            
    # Fallback if no data yet
    return jsonify({
        'success': True, 'symbol': target_sym or '—',
        'metrics': {
            'fatigue': 0, 'status': 'WARMING UP',
            'decision': 'HOLD', 'explanation': 'Waiting for market data...'
        },
        'sensors': generate_iot_sensors(0), 'last_price': 0
    })

@app.route('/api/sensors')
def api_sensors():
    fatigue = 0
    with _lock:
        for cache in MARKET_CACHE.values():
            if cache.get('metrics'):
                fatigue = cache['metrics'].get('fatigue', 0)
                break
    return jsonify({'sensors': generate_iot_sensors(fatigue)})

@app.route('/api/fatigue')
def api_fatigue():
    fatigue = 0
    with _lock:
        for cache in MARKET_CACHE.values():
            if cache.get('metrics'):
                fatigue = cache['metrics'].get('fatigue', 0)
                break
    return jsonify({'fatigue': fatigue, 'value': fatigue})

@app.route('/api/strategy/run', methods=['POST'])
@login_required
def api_strategy_run():
    data = request.get_json() or {}
    symbol = data.get('symbol', 'BTC-USD').upper()
    rules = data.get('rules', ['rsi'])
    cap = float(data.get('capital', 10000))
    sl = float(data.get('stop_loss', 2.0))
    tp = float(data.get('take_profit', 5.0))

    df = MARKET_CACHE.get(symbol, {}).get('df')
    if df is None:
        df = fetch_history(symbol)
        
    if df is None or df.empty:
        return jsonify({'error': 'No data available for symbol'}), 400

    sim_trades = run_strategy_simulation(symbol, df, rules, cap, sl, tp)
    total_pnl = sum(t.get('pnl', 0) for t in sim_trades if t['action'] == 'SELL')
    wins = sum(1 for t in sim_trades if t['action'] == 'SELL' and t.get('pnl', 0) > 0)
    sells = sum(1 for t in sim_trades if t['action'] == 'SELL')

    return jsonify({
        'success': True,
        'symbol': symbol,
        'trades': sim_trades,
        'summary': {
            'total_trades': len(sim_trades),
            'total_pnl': round(total_pnl, 2),
            'win_rate': round(wins / sells * 100 if sells > 0 else 0, 1)
        }
    })

# ── SocketIO Market Streamer ──────────────────────────────────────────────────
@socketio.on('connect')
def on_connect():
    sid = request.sid
    with _lock:
        CLIENT_STATE[sid] = {'main': 'BTC-USD', 'watchlist': []}
    logger.info(f"Client connected: {sid}")

@socketio.on('disconnect')
def on_disconnect():
    with _lock:
        CLIENT_STATE.pop(request.sid, None)

@socketio.on('subscribe')
def on_subscribe(data):
    sid = request.sid
    main = data.get('symbol', 'BTC-USD').upper()
    wl = [s.upper() for s in data.get('watchlist', [])]
    with _lock:
        CLIENT_STATE[sid] = {'main': main, 'watchlist': wl}
        cache = MARKET_CACHE.get(main)
        
    emit('subscribed', {'symbol': main})
    
    # If we already have cached data, send it instantly so the UI updates immediately
    if cache and cache.get('last_price'):
        payload = {
            'symbol': main, 
            'price': cache['last_price'], 
            'metrics': cache.get('metrics', {}), 
            'sensors': cache.get('sensors', {}), 
            'time': int(time.time()*1000)
        }
        emit('price_update', payload)
        emit('history_data', {'symbol': main})
    else:
        # If not cached, spawn a thread to fetch it instantly without waiting for the 2s market loop
        threading.Thread(target=_process_main, args=(main, [sid]), daemon=True).start()

def _active_symbols():
    m_map, w_map = {}, {}
    with _lock:
        for sid, state in CLIENT_STATE.items():
            m = state.get('main', 'BTC-USD')
            m_map.setdefault(m, set()).add(sid)
            for s in state.get('watchlist', []):
                w_map.setdefault(s, set()).add(sid)
    return m_map, w_map

def _process_main(symbol, sids):
    cache = MARKET_CACHE.get(symbol)
    
    if cache is None or 'df' not in cache or cache['df'] is None:
        df = fetch_history(symbol)
        if df is None or df.empty:
            for sid in sids:
                socketio.emit('market_error', {'symbol': symbol, 'message': 'Market data unavailable'}, to=sid)
            return
            
        metrics = analyse(symbol, df)
        sensors = generate_iot_sensors(metrics.get('fatigue', 0))
        price = float(df['close'].iloc[-1])
        
        with _lock:
            MARKET_CACHE[symbol] = {'df': df, 'last_price': price, 'metrics': metrics, 'sensors': sensors, 'tick': 0}
            
        payload = {'symbol': symbol, 'price': price, 'metrics': metrics, 'sensors': sensors, 'time': int(time.time()*1000)}
        for sid in sids:
            socketio.emit('price_update', payload, to=sid)
            socketio.emit('history_data', {'symbol': symbol}, to=sid) # Tells frontend overlay to hide
        return

    # Update tick
    live = fetch_live_price(symbol)
    if not live: return
    
    with _lock:
        cache['tick'] = cache.get('tick', 0) + 1
        tick = cache['tick']
        df = cache['df']
        
        # Pseudo-live update the last candle
        if not df.empty:
            df.iloc[-1, df.columns.get_loc('close')] = live
            df.iloc[-1, df.columns.get_loc('high')] = max(float(df.iloc[-1]['high']), live)
            df.iloc[-1, df.columns.get_loc('low')] = min(float(df.iloc[-1]['low']), live)

        # Run heavy AI analysis every ~5 ticks
        if tick % 5 == 0:
            cache['metrics'] = analyse(symbol, df)
            cache['sensors'] = generate_iot_sensors(cache['metrics'].get('fatigue', 0))
            
        cache['last_price'] = live

    payload = {
        'symbol': symbol, 'price': live,
        'metrics': cache['metrics'], 'sensors': cache['sensors'],
        'time': int(time.time()*1000)
    }
    for sid in sids:
        socketio.emit('price_update', payload, to=sid)

def _process_wl(symbol, sids):
    live = fetch_live_price(symbol)
    if not live: return
    with _lock:
        MARKET_CACHE.setdefault(symbol, {})['last_price'] = live
    payload = {'symbol': symbol, 'price': live, 'time': int(time.time()*1000)}
    for sid in sids:
        socketio.emit('price_update', payload, to=sid)

def _market_loop():
    logger.info("Premium Market Streaming Engine started.")
    while True:
        try:
            main_map, wl_map = _active_symbols()
            for sym, sids in main_map.items():
                _process_main(sym, sids)
            for sym, sids in wl_map.items():
                if sym not in main_map:
                    _process_wl(sym, sids)
            time.sleep(2) # 2s polling for fast real-time feel
        except Exception as e:
            logger.error(f"Market loop error: {e}")
            time.sleep(3)

threading.Thread(target=_market_loop, daemon=True).start()

# ── Entry Point ───────────────────────────────────────────────────────────────
if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    socketio.run(app, host='127.0.0.1', port=port, debug=False, use_reloader=False, allow_unsafe_werkzeug=True)
