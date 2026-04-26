"""
app.py - AIoT Strategy Fatigue Detection System for Algorithmic Trading
Main Flask application with SocketIO live streaming.
"""
import os
import time
import logging
import threading
from functools import wraps

from flask import (Flask, render_template, request, jsonify,
                   session, redirect, url_for, flash)
from flask_socketio import SocketIO, emit
import pandas as pd
import numpy as np

from database import init_db, get_db
from market_data import fetch_history, fetch_live_price, fetch_market_movers
from ai_engine import analyse, generate_iot_sensors

# ─────────────────────────── App Setup ────────────────────────────────────────
logging.basicConfig(level=logging.INFO,
                    format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

app = Flask(__name__)
app.secret_key = 'aiot_pro_terminal_2026_ultra_secure'
socketio = SocketIO(app, async_mode='threading', cors_allowed_origins='*',
                    logger=False, engineio_logger=False)

# ─────────────────────────── State ────────────────────────────────────────────
# sid -> {main: symbol, watchlist: [symbols]}
CLIENT_STATE: dict[str, dict] = {}
MARKET_CACHE: dict[str, dict] = {}   # symbol -> {history_df, last_price, metrics, sensors, tick}
_lock = threading.Lock()

# ─────────────────────────── DB init ──────────────────────────────────────────
init_db()

# ─────────────────────────── Auth Helpers ─────────────────────────────────────
def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'user_id' not in session:
            if request.is_json:
                return jsonify({'error': 'Authentication required'}), 401
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated


# ─────────────────────────── Auth Routes ──────────────────────────────────────
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
        if user and check_password_hash(dict(user)['password'], password):
            session['user_id'] = dict(user)['id']
            session['username'] = dict(user)['username']
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


# ─────────────────────────── Dashboard ────────────────────────────────────────
@app.route('/dashboard')
@login_required
def dashboard():
    conn = get_db()
    user = conn.execute('SELECT id, username, balance FROM users WHERE id=?',
                        (session['user_id'],)).fetchone()
    watchlist = conn.execute('SELECT symbol FROM watchlist WHERE user_id=?',
                             (session['user_id'],)).fetchall()
    conn.close()
    default_watchlist = [r['symbol'] for r in watchlist] or ['BTC-USD', 'AAPL', 'RELIANCE.NS']
    return render_template('dashboard.html', user=dict(user), watchlist=default_watchlist)


# ─────────────────────────── REST APIs ────────────────────────────────────────
@app.route('/api/sensors')
def api_sensors():
    sensors = generate_iot_sensors()
    return jsonify({'sensors': sensors})


@app.route('/api/movers')
def api_movers():
    return jsonify(fetch_market_movers())


@app.route('/api/portfolio')
@login_required
def api_portfolio():
    conn = get_db()
    user = conn.execute('SELECT balance FROM users WHERE id=?', (session['user_id'],)).fetchone()
    holdings = conn.execute(
        'SELECT symbol, qty, avg_price FROM portfolio WHERE user_id=? AND qty>0',
        (session['user_id'],)).fetchall()
    conn.close()
    result = []
    for h in holdings:
        sym = h['symbol']
        cached = MARKET_CACHE.get(sym, {})
        live_price = cached.get('last_price') or h['avg_price']
        pnl = round((live_price - h['avg_price']) * h['qty'], 2)
        result.append({
            'symbol': sym, 'qty': h['qty'],
            'avg_price': round(h['avg_price'], 2),
            'live_price': round(live_price, 2),
            'pnl': pnl, 'value': round(live_price * h['qty'], 2)
        })
    return jsonify({
        'balance': round(user['balance'], 2), 'holdings': result,
        'total_value': round(user['balance'] + sum(r['value'] for r in result), 2)
    })


@app.route('/api/trade', methods=['POST'])
@login_required
def api_trade():
    data = request.get_json()
    symbol = data.get('symbol', '').upper()
    action = data.get('action', '').upper()
    qty = float(data.get('qty', 0))

    if not symbol or action not in ('BUY', 'SELL') or qty <= 0:
        return jsonify({'error': 'Invalid trade parameters'}), 400

    cached = MARKET_CACHE.get(symbol, {})
    price = cached.get('last_price') or fetch_live_price(symbol)
    if not price:
        return jsonify({'error': 'Cannot fetch live price'}), 400

    price = round(price, 2)
    total = round(price * qty, 2)
    conn = get_db()
    user = conn.execute('SELECT balance FROM users WHERE id=?', (session['user_id'],)).fetchone()

    if action == 'BUY':
        if user['balance'] < total:
            conn.close()
            return jsonify({'error': 'Insufficient balance'}), 400
        conn.execute('UPDATE users SET balance=balance-? WHERE id=?', (total, session['user_id']))
        existing = conn.execute(
            'SELECT qty, avg_price FROM portfolio WHERE user_id=? AND symbol=?',
            (session['user_id'], symbol)).fetchone()
        if existing:
            new_qty = existing['qty'] + qty
            new_avg = ((existing['avg_price'] * existing['qty']) + total) / new_qty
            conn.execute('UPDATE portfolio SET qty=?, avg_price=? WHERE user_id=? AND symbol=?',
                         (new_qty, new_avg, session['user_id'], symbol))
        else:
            conn.execute('INSERT INTO portfolio (user_id, symbol, qty, avg_price) VALUES (?,?,?,?)',
                         (session['user_id'], symbol, qty, price))
    elif action == 'SELL':
        holding = conn.execute(
            'SELECT qty, avg_price FROM portfolio WHERE user_id=? AND symbol=?',
            (session['user_id'], symbol)).fetchone()
        if not holding or holding['qty'] < qty:
            conn.close()
            return jsonify({'error': 'Insufficient holdings'}), 400
        conn.execute('UPDATE users SET balance=balance+? WHERE id=?', (total, session['user_id']))
        conn.execute('UPDATE portfolio SET qty=? WHERE user_id=? AND symbol=?',
                     (holding['qty'] - qty, session['user_id'], symbol))

    conn.execute('INSERT INTO trades (user_id, symbol, action, qty, price, total) VALUES (?,?,?,?,?,?)',
                 (session['user_id'], symbol, action, qty, price, total))
    conn.commit()
    conn.close()
    return jsonify({'success': True, 'price': price, 'total': total, 'action': action})


@app.route('/api/history')
@login_required
def api_history():
    conn = get_db()
    trades = conn.execute(
        'SELECT symbol, action, qty, price, total, timestamp FROM trades WHERE user_id=? ORDER BY id DESC LIMIT 50',
        (session['user_id'],)).fetchall()
    conn.close()
    return jsonify([dict(t) for t in trades])


@app.route('/api/watchlist', methods=['GET', 'POST', 'DELETE'])
@login_required
def api_watchlist():
    if request.method == 'GET':
        conn = get_db()
        wl = conn.execute('SELECT symbol FROM watchlist WHERE user_id=?', (session['user_id'],)).fetchall()
        conn.close()
        return jsonify([r['symbol'] for r in wl])
    data = request.get_json()
    symbol = data.get('symbol', '').upper()
    conn = get_db()
    try:
        if request.method == 'POST':
            conn.execute('INSERT OR IGNORE INTO watchlist (user_id, symbol) VALUES (?,?)',
                         (session['user_id'], symbol))
        elif request.method == 'DELETE':
            conn.execute('DELETE FROM watchlist WHERE user_id=? AND symbol=?',
                         (session['user_id'], symbol))
        conn.commit()
    finally:
        conn.close()
    return jsonify({'success': True})


# ─────────────────────────── SocketIO Events ──────────────────────────────────
@socketio.on('connect')
def on_connect():
    sid = request.sid
    with _lock:
        CLIENT_STATE[sid] = {'main': 'BTC-USD', 'watchlist': []}
    logger.info(f"Client connected: {sid}")


@socketio.on('disconnect')
def on_disconnect():
    sid = request.sid
    with _lock:
        CLIENT_STATE.pop(sid, None)
    logger.info(f"Client disconnected: {sid}")


@socketio.on('subscribe')
def on_subscribe(data):
    """Client tells us which symbol is the main chart and what watchlist to track."""
    sid = request.sid
    main_symbol = data.get('symbol', 'BTC-USD').upper()
    watchlist = [s.upper() for s in data.get('watchlist', [])]

    with _lock:
        CLIENT_STATE[sid] = {'main': main_symbol, 'watchlist': watchlist}

    logger.info(f"Client {sid} → main={main_symbol}, wl={watchlist}")
    emit('subscribed', {'symbol': main_symbol})

    # Clear existing chart history so it reloads
    with _lock:
        MARKET_CACHE.pop(main_symbol, None)


# ─────────────────────────── Background Market Loop ───────────────────────────
def _get_all_active_symbols():
    """Return {main_symbol: set_of_sids} and {wl_symbol: set_of_sids}."""
    main_map = {}
    wl_map = {}
    with _lock:
        for sid, state in CLIENT_STATE.items():
            m = state.get('main', 'BTC-USD')
            main_map.setdefault(m, set()).add(sid)
            for sym in state.get('watchlist', []):
                wl_map.setdefault(sym, set()).add(sid)
    return main_map, wl_map


def _build_chart_payload(symbol: str, df: pd.DataFrame):
    """Convert DataFrame to chart-ready list with LOCAL timestamps."""
    tz_offset_s = 5 * 3600 + 30 * 60  # IST = UTC+5:30
    chart = []
    for ts, row in df.iterrows():
        utc_s = int(ts.timestamp())
        local_s = utc_s + tz_offset_s
        chart.append({
            'time': local_s,
            'open':  round(float(row['open']),  4),
            'high':  round(float(row['high']),  4),
            'low':   round(float(row['low']),   4),
            'close': round(float(row['close']), 4),
            'volume': int(row['volume']),
        })
    return chart


def _process_main(symbol: str, sids: set):
    """Process the main chart symbol — history load + AI + streaming."""
    cache = MARKET_CACHE.get(symbol)

    # ── Initial load ──────────────────────────────────────────────────────────
    if cache is None:
        logger.info(f"Initial load for {symbol}...")
        df = fetch_history(symbol)
        if df is None or df.empty:
            for sid in sids:
                socketio.emit('market_error',
                              {'symbol': symbol, 'message': f'No data for {symbol}. Market may be closed.'},
                              to=sid)
            return

        metrics = analyse(symbol, df)
        sensors = generate_iot_sensors()
        chart = _build_chart_payload(symbol, df)
        last_price = round(float(df['close'].iloc[-1]), 4)

        MARKET_CACHE[symbol] = {
            'df': df, 'last_price': last_price,
            'metrics': metrics, 'sensors': sensors, 'tick': 0
        }

        payload_hist = {'symbol': symbol, 'data': chart}
        payload_price = {
            'symbol': symbol, 'price': last_price,
            'metrics': metrics, 'sensors': sensors,
            'time': int(time.time() * 1000)
        }
        for sid in sids:
            socketio.emit('history_data', payload_hist, to=sid)
            socketio.emit('price_update', payload_price, to=sid)
        return

    # ── Subsequent ticks ──────────────────────────────────────────────────────
    live_price = fetch_live_price(symbol)
    if live_price is None:
        return

    live_price = round(live_price, 4)
    cache['tick'] = cache.get('tick', 0) + 1

    df = cache['df']
    if not df.empty:
        df.iloc[-1, df.columns.get_loc('close')] = live_price
        df.iloc[-1, df.columns.get_loc('high')] = max(float(df.iloc[-1]['high']), live_price)
        df.iloc[-1, df.columns.get_loc('low')] = min(float(df.iloc[-1]['low']), live_price)

    # Re-analyse every 3 ticks for fresher AI metrics
    if cache['tick'] % 3 == 0:
        cache['metrics'] = analyse(symbol, df)
        cache['sensors'] = generate_iot_sensors()

    cache['last_price'] = live_price

    tz_offset_s = 5 * 3600 + 30 * 60
    local_time_s = int(time.time()) + tz_offset_s

    payload = {
        'symbol': symbol,
        'price': live_price,
        'metrics': cache['metrics'],
        'sensors': cache['sensors'],
        'time': int(time.time() * 1000),
        'local_time_s': local_time_s,
    }
    for sid in sids:
        socketio.emit('price_update', payload, to=sid)


def _process_watchlist(symbol: str, sids: set):
    """Process watchlist symbols — live price only, no history/AI."""
    live_price = fetch_live_price(symbol)
    if live_price is None:
        return
    live_price = round(live_price, 4)
    MARKET_CACHE.setdefault(symbol, {})['last_price'] = live_price
    payload = {'symbol': symbol, 'price': live_price, 'time': int(time.time() * 1000)}
    for sid in sids:
        socketio.emit('price_update', payload, to=sid)


def _market_loop():
    logger.info("Market streaming engine started.")
    while True:
        try:
            main_map, wl_map = _get_all_active_symbols()

            for symbol, sids in main_map.items():
                try:
                    _process_main(symbol, sids)
                except Exception as e:
                    logger.error(f"Main loop error [{symbol}]: {e}")

            for symbol, sids in wl_map.items():
                if symbol in main_map:
                    continue  # already processed as main
                try:
                    _process_watchlist(symbol, sids)
                except Exception:
                    pass

            time.sleep(1)
        except Exception as e:
            logger.error(f"Market loop error: {e}")
            time.sleep(2)


_thread = threading.Thread(target=_market_loop, daemon=True)
_thread.start()


# ─────────────────────────── Run ──────────────────────────────────────────────
if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    socketio.run(app, host='127.0.0.1', port=port,
                 debug=False, use_reloader=False,
                 allow_unsafe_werkzeug=True)
