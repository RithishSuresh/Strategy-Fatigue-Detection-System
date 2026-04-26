// ═══════════════════════════════════════════════════════════
// AIoT Terminal - Dashboard Script (script.js)
// ═══════════════════════════════════════════════════════════
'use strict';

// ─── State ────────────────────────────────────────────────
let currentSymbol = 'BTC-USD';
let lastPrice = 0;
let mainChart = null;
let candleSeries = null;
let volumeSeries = null;
let chartData = [];
let socket = null;

// ─── DOM helper ───────────────────────────────────────────
const $ = id => document.getElementById(id);

// ─── Live Clock (local time) ──────────────────────────────
function updateClock() {
    const now = new Date();
    $('liveTime').textContent = now.toLocaleTimeString('en-IN', { hour12: false });
}
setInterval(updateClock, 1000);
updateClock();

// ─── Panel Switcher ───────────────────────────────────────
function showPanel(name) {
    ['chart', 'ai', 'portfolio', 'iot'].forEach(p => {
        $('panel-' + p).classList.toggle('hidden', p !== name);
    });
    document.querySelectorAll('.sidebar-item').forEach(el => el.classList.remove('active'));
    if (event && event.currentTarget) event.currentTarget.classList.add('active');
    if (name === 'portfolio') loadPortfolio();
    if (name === 'iot') loadSensors();
}

// ─── Chart Init ───────────────────────────────────────────
function initChart() {
    const container = $('mainChart');
    if (!container || typeof LightweightCharts === 'undefined') {
        console.error('LightweightCharts not ready');
        return;
    }

    mainChart = LightweightCharts.createChart(container, {
        layout: { background: { type: 'solid', color: '#0a0e17' }, textColor: '#94a3b8' },
        grid: { vertLines: { color: '#1a1f2e' }, horzLines: { color: '#1a1f2e' } },
        crosshair: { mode: LightweightCharts.CrosshairMode.Normal },
        rightPriceScale: { borderColor: '#1e2330', scaleMargins: { top: 0.1, bottom: 0.25 } },
        timeScale: {
            borderColor: '#1e2330',
            timeVisible: true,
            secondsVisible: false,
            tickMarkFormatter: (time) => {
                const d = new Date(time * 1000);
                return d.toLocaleTimeString('en-IN', { hour: '2-digit', minute: '2-digit', hour12: false });
            }
        },
        width: container.clientWidth,
        height: container.clientHeight,
    });

    candleSeries = mainChart.addCandlestickSeries({
        upColor: '#089981', downColor: '#f23645',
        borderVisible: false,
        wickUpColor: '#089981', wickDownColor: '#f23645',
    });

    volumeSeries = mainChart.addHistogramSeries({
        color: '#2962ff26',
        priceFormat: { type: 'volume' },
        priceScaleId: 'vol',
    });
    mainChart.priceScale('vol').applyOptions({
        scaleMargins: { top: 0.8, bottom: 0 },
    });

    new ResizeObserver(() => {
        if (!mainChart) return;
        const rect = container.getBoundingClientRect();
        mainChart.applyOptions({ width: rect.width, height: rect.height });
    }).observe(container);
}

// ─── Socket ───────────────────────────────────────────────
function initSocket() {
    if (typeof io === 'undefined') { console.error('Socket.IO not loaded'); return; }
    socket = io({ transports: ['websocket', 'polling'] });

    socket.on('connect', () => {
        console.log('Socket connected:', socket.id);
        $('connDot').className = 'w-2 h-2 rounded-full bg-accent pulse-dot';
        $('connText').textContent = 'Live';
        sendSubscribe();
    });

    socket.on('disconnect', () => {
        $('connDot').className = 'w-2 h-2 rounded-full bg-red-500 pulse-dot';
        $('connText').textContent = 'Disconnected';
    });

    socket.on('subscribed', d => {
        console.log('Subscribed to', d.symbol);
        showOverlay(`Loading ${d.symbol}...`);
    });

    socket.on('market_error', d => {
        console.warn('Market error:', d);
        if (d.symbol === currentSymbol) {
            showOverlay(d.message || 'Data unavailable');
        }
        showToast(`${d.symbol}: ${d.message}`, 'error');
    });

    socket.on('history_data', d => {
        console.log('history_data received for', d.symbol, '— candles:', d.data.length);
        if (d.symbol !== currentSymbol) return;

        chartData = d.data
            .filter((v, i, a) => a.findIndex(x => x.time === v.time) === i)
            .sort((a, b) => a.time - b.time);

        if (candleSeries) candleSeries.setData(chartData);
        if (volumeSeries) {
            volumeSeries.setData(chartData.map(c => ({
                time: c.time,
                value: c.volume,
                color: c.close >= c.open ? '#08998140' : '#f2364540'
            })));
        }
        hideOverlay();
        mainChart && mainChart.timeScale().fitContent();
    });

    socket.on('price_update', d => {
        // Always update watchlist ticker for any symbol
        updateTicker(d.symbol, d.price);

        if (d.symbol !== currentSymbol) return;

        const price = parseFloat(d.price);
        // Use server-provided local time (IST pre-computed)
        const timeS = d.local_time_s || (Math.floor(Date.now() / 1000));

        // Flash price display
        if (price !== lastPrice && lastPrice > 0) {
            const cls = price > lastPrice ? 'flash-up' : 'flash-down';
            $('activePrice').classList.remove('flash-up', 'flash-down');
            void $('activePrice').offsetWidth;
            $('activePrice').classList.add(cls);
            const diff = price - lastPrice;
            const pct = (diff / lastPrice) * 100;
            $('priceChange').textContent = `${diff > 0 ? '+' : ''}${diff.toFixed(4)} (${pct.toFixed(2)}%)`;
            $('priceChange').className = `text-sm font-medium ${price > lastPrice ? 'text-accent' : 'text-danger'}`;

            // Push a live price change alert
            const arrow = diff > 0 ? '▲' : '▼';
            pushAlert(`${d.symbol} ${arrow} ${formatNum(price, 2)} (${pct >= 0 ? '+' : ''}${pct.toFixed(2)}%)`, diff > 0 ? 'info' : 'warn');
        }
        $('activePrice').textContent = formatNum(price, 2);
        lastPrice = price;

        // Update chart candle — use floor-to-minute boundary for correct bucketing
        const minuteS = Math.floor(timeS / 60) * 60;
        if (chartData.length > 0) {
            const last = chartData[chartData.length - 1];
            const lastMinuteS = Math.floor(last.time / 60) * 60;
            if (minuteS > lastMinuteS) {
                // New minute — new candle
                const nc = { time: minuteS, open: price, high: price, low: price, close: price, volume: 0 };
                chartData.push(nc);
                if (candleSeries) candleSeries.update(nc);
                if (volumeSeries) volumeSeries.update({ time: minuteS, value: 0, color: '#08998140' });
            } else {
                // Same minute — update last candle
                last.high = Math.max(last.high, price);
                last.low = Math.min(last.low, price);
                last.close = price;
                if (candleSeries) candleSeries.update({ time: last.time, open: last.open, high: last.high, low: last.low, close: last.close });
                if (volumeSeries) volumeSeries.update({
                    time: last.time, value: last.volume || 0,
                    color: last.close >= last.open ? '#08998140' : '#f2364540'
                });
            }
        }

        hideOverlay();
        if (d.metrics) updateMetrics(d.metrics);
        if (d.sensors) updateSensors(d.sensors);
    });
}

function sendSubscribe() {
    const wlSymbols = Array.from(document.querySelectorAll('[id^="wl-"]'))
        .map(el => el.id.replace('wl-', ''));
    socket.emit('subscribe', { symbol: currentSymbol, watchlist: wlSymbols });
}

// ─── Overlay helpers ──────────────────────────────────────
function showOverlay(msg) {
    const ov = $('chartOverlay');
    if (ov) { ov.style.display = 'flex'; $('overlayMsg').textContent = msg; }
}
function hideOverlay() {
    const ov = $('chartOverlay');
    if (ov) ov.style.display = 'none';
}

// ─── Metrics ──────────────────────────────────────────────
let lastAlertedStatus = '';
function updateMetrics(m) {
    const score = m.fatigue || 0;
    const status = m.status || 'WARMING UP';
    const decision = m.decision || 'HOLD';

    if ($('rsiVal'))     $('rsiVal').textContent = formatNum(m.rsi, 1);
    if ($('fatigueVal')) $('fatigueVal').textContent = formatNum(score, 1);
    if ($('ddVal'))      $('ddVal').textContent = `-${formatNum(m.drawdown_pct, 1)}%`;
    if ($('bbVal'))      $('bbVal').textContent = formatNum(m.bb_width, 4);

    const dec = $('decisionVal');
    if (dec) {
        dec.textContent = decision;
        dec.className = decision === 'BUY' ? 'font-bold text-accent' :
                        decision === 'EXIT' ? 'font-bold text-danger' : 'font-bold text-warn';
    }

    const color = score <= 30 ? '#089981' : score <= 60 ? '#f59e0b' : score <= 80 ? '#f97316' : '#f23645';
    const bar = $('fatigueBar');
    if (bar) { bar.style.width = score + '%'; bar.style.background = color; }
    if ($('fatigueScore')) $('fatigueScore').textContent = formatNum(score, 1);
    if ($('fatigueExplain')) $('fatigueExplain').textContent = m.explanation || '';

    const tag = $('fatigueTag');
    if (tag) {
        tag.textContent = status;
        const tc = {
            HEALTHY:  ['rgba(8,153,129,.2)', '#089981'],
            WARNING:  ['rgba(245,158,11,.2)', '#f59e0b'],
            FATIGUED: ['rgba(249,115,22,.2)', '#f97316'],
            CRITICAL: ['rgba(242,54,69,.2)', '#f23645']
        };
        const [bg, fg] = tc[status] || ['rgba(100,100,100,.2)', '#94a3b8'];
        tag.style.background = bg; tag.style.color = fg;
    }

    // AI panel
    if ($('ai-ae'))     $('ai-ae').textContent = formatNum(m.autoencoder_score, 1);
    if ($('ai-iso'))    $('ai-iso').textContent = formatNum(m.isolation_score, 1);
    if ($('ai-rsi'))    $('ai-rsi').textContent = formatNum(m.rsi, 1);
    if ($('ai-dd'))     $('ai-dd').textContent = `-${formatNum(m.drawdown_pct, 1)}%`;
    if ($('ai-score'))  $('ai-score').textContent = formatNum(score, 1);
    if ($('ai-explain')) $('ai-explain').textContent = m.explanation || '';
    const aiBar = $('ai-bar');
    if (aiBar) { aiBar.style.width = score + '%'; aiBar.style.background = color; }
    const aiStatus = $('ai-status');
    if (aiStatus) { aiStatus.textContent = status; aiStatus.style.color = color; }

    // Status change alert
    if (status !== lastAlertedStatus && status !== 'WARMING UP') {
        const alertType = status === 'HEALTHY' ? 'info' : status === 'WARNING' ? 'warn' : 'danger';
        pushAlert(`🤖 AI: ${currentSymbol} is ${status} — ${decision} (Score: ${formatNum(score,1)})`, alertType);
        lastAlertedStatus = status;
    }
    // High fatigue alert always
    if (score > 75) {
        pushAlert(`⚠️ ${currentSymbol}: Fatigue ${formatNum(score,1)} — ${m.explanation || decision}`, score > 85 ? 'danger' : 'warn');
    }
    // RSI extremes
    if (m.rsi > 72) pushAlert(`📈 ${currentSymbol}: RSI Overbought (${formatNum(m.rsi,1)})`, 'warn');
    if (m.rsi < 30) pushAlert(`📉 ${currentSymbol}: RSI Oversold (${formatNum(m.rsi,1)})`, 'info');
}

// ─── Sensors ──────────────────────────────────────────────
function updateSensors(s) {
    if (!s) return;
    const set = (id, val) => { const el = $(id); if (el) el.textContent = val; };
    const bar = (id, pct) => { const el = $(id); if (el) el.style.width = Math.min(pct, 100) + '%'; };
    set('s-cpu', s.cpu_usage + '%');   bar('sb-cpu', s.cpu_usage);
    set('s-ram', s.ram_usage + '%');   bar('sb-ram', s.ram_usage);
    set('s-lat', s.api_latency + 'ms'); bar('sb-lat', s.api_latency / 2);
    set('s-risk', s.risk_pressure + '%'); bar('sb-risk', s.risk_pressure);
    set('s-opm', s.orders_per_min);
    set('s-tf', s.trade_frequency);
    set('s-nd', s.network_delay + 'ms');
    set('s-hb', s.heartbeat === 'OK' ? 'OK ✓' : 'ERROR ✗');
}

// ─── Ticker / Watchlist ───────────────────────────────────
function updateTicker(symbol, price) {
    const el = $('wl-' + symbol);
    if (el) el.textContent = formatNum(price, 2);
}

// ─── Symbol Switch ────────────────────────────────────────
function switchSymbol(sym) {
    sym = sym.toUpperCase().trim();
    if (!sym || sym === currentSymbol) return;
    currentSymbol = sym;
    lastPrice = 0;
    chartData = [];
    if (candleSeries) candleSeries.setData([]);
    if (volumeSeries) volumeSeries.setData([]);
    $('activeSymbol').textContent = sym;
    $('activePrice').textContent = '—';
    $('priceChange').textContent = '—';
    showOverlay(`Switching to ${sym}...`);
    sendSubscribe();

    // Switch to chart panel
    ['chart', 'ai', 'portfolio', 'iot'].forEach(p => $('panel-' + p).classList.toggle('hidden', p !== 'chart'));
    document.querySelectorAll('.sidebar-item').forEach((el, i) => el.classList.toggle('active', i === 0));
}

// ─── Portfolio ────────────────────────────────────────────
async function loadPortfolio() {
    try {
        const data = await fetch('/api/portfolio').then(r => r.json());
        if ($('p-balance')) $('p-balance').textContent = '₹' + data.balance.toLocaleString('en-IN', { minimumFractionDigits: 2 });
        if ($('balanceDisplay')) $('balanceDisplay').textContent = '₹' + data.balance.toLocaleString('en-IN', { minimumFractionDigits: 2 });
        const holdVal = data.holdings.reduce((s, h) => s + h.value, 0);
        if ($('p-holdings')) $('p-holdings').textContent = '₹' + holdVal.toLocaleString('en-IN', { minimumFractionDigits: 2 });
        if ($('totalValue')) $('totalValue').textContent = 'Total: ₹' + data.total_value.toLocaleString('en-IN', { minimumFractionDigits: 2 });
        const totalPnL = data.holdings.reduce((s, h) => s + h.pnl, 0);
        if ($('p-pnl')) {
            $('p-pnl').textContent = (totalPnL >= 0 ? '+' : '') + '₹' + Math.abs(totalPnL).toLocaleString('en-IN', { minimumFractionDigits: 2 });
            $('p-pnl').className = `text-lg font-bold mono ${totalPnL >= 0 ? 'text-accent' : 'text-danger'}`;
        }
        const ht = $('holdingsTable');
        if (ht) {
            ht.innerHTML = data.holdings.length === 0
                ? '<div class="px-4 py-6 text-center text-gray-500 text-sm">No holdings yet</div>'
                : data.holdings.map(h => `
                    <div class="flex items-center justify-between px-4 py-2 text-sm hover:bg-white/5 cursor-pointer" onclick="switchSymbol('${h.symbol}')">
                        <div><div class="font-semibold">${h.symbol}</div><div class="text-xs text-gray-500">${h.qty} @ ₹${h.avg_price}</div></div>
                        <div class="text-right"><div class="mono">₹${h.live_price}</div><div class="text-xs ${h.pnl >= 0 ? 'text-accent' : 'text-danger'}">${h.pnl >= 0 ? '+' : ''}₹${h.pnl}</div></div>
                    </div>`).join('');
        }
        const hist = await fetch('/api/history').then(r => r.json());
        const tht = $('tradeHistory');
        if (tht) {
            tht.innerHTML = hist.length === 0
                ? '<div class="px-4 py-6 text-center text-gray-500 text-sm">No trades yet</div>'
                : hist.slice(0, 15).map(t => `
                    <div class="flex items-center justify-between px-4 py-2 text-xs">
                        <div class="flex items-center gap-2">
                            <span class="tag ${t.action === 'BUY' ? 'text-accent bg-accent/20' : 'text-danger bg-danger/20'}">${t.action}</span>
                            <span class="font-semibold">${t.symbol}</span>
                            <span class="text-gray-400">${t.qty} × ₹${t.price}</span>
                        </div>
                        <div class="text-right"><div class="mono">₹${t.total}</div><div class="text-gray-500">${new Date(t.timestamp).toLocaleTimeString()}</div></div>
                    </div>`).join('');
        }
    } catch(e) { console.error('Portfolio load error:', e); }
}

// ─── Trade ────────────────────────────────────────────────
async function executeTrade(action) {
    const qty = parseFloat($('tradeQty').value);
    if (isNaN(qty) || qty <= 0) { showToast('Enter a valid quantity', 'error'); return; }
    try {
        const res = await fetch('/api/trade', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ symbol: currentSymbol, action, qty })
        });
        const data = await res.json();
        if (data.success) {
            showToast(`${action} ${qty} ${currentSymbol} @ ₹${data.price} ✓`, 'success');
            loadPortfolio();
        } else {
            showToast(data.error || 'Trade failed', 'error');
        }
    } catch(e) { showToast('Network error', 'error'); }
}

// ─── Watchlist ────────────────────────────────────────────
async function addToWatchlist() {
    const input = $('symbolSearch');
    const sym = (input ? input.value : prompt('Enter symbol (e.g. AAPL):')).toUpperCase().trim();
    if (!sym) return;
    await fetch('/api/watchlist', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ symbol: sym })
    });
    const wlEl = $('watchlistEl');
    if (wlEl && !$('wl-' + sym)) {
        const div = document.createElement('div');
        div.className = 'watchlist-row';
        div.onclick = () => switchSymbol(sym);
        div.innerHTML = `<span class="text-xs font-semibold text-white">${sym}</span><span class="text-xs text-gray-500 mono" id="wl-${sym}">—</span>`;
        wlEl.appendChild(div);
    }
    if (input) input.value = '';
    sendSubscribe(); // re-subscribe with new watchlist
    switchSymbol(sym);
}

// ─── Market Movers ────────────────────────────────────────
async function loadMovers() {
    try {
        const data = await fetch('/api/movers').then(r => r.json());
        const render = (elId, arr) => {
            const el = $(elId);
            if (!el || !arr) return;
            el.innerHTML = arr.map(m => `
                <div class="flex items-center justify-between py-0.5 cursor-pointer hover:opacity-80" onclick="switchSymbol('${m.symbol}')">
                    <span class="font-semibold text-white text-xs">${m.symbol}</span>
                    <span class="mono text-xs ${m.change >= 0 ? 'text-accent' : 'text-danger'}">${m.change >= 0 ? '+' : ''}${m.change}%</span>
                </div>`).join('');
        };
        render('gainers', data.gainers || []);
        render('losers', data.losers || []);
    } catch(e) { console.warn('Movers error', e); }
}

// ─── IoT Sensors ──────────────────────────────────────────
async function loadSensors() {
    try {
        const data = await fetch('/api/sensors').then(r => r.json());
        updateSensors(data.sensors);
    } catch(e) {}
}

// ─── Alerts ───────────────────────────────────────────────
const alertList = [];
function pushAlert(msg, type = 'info') {
    alertList.unshift({ msg, type, t: new Date().toLocaleTimeString() });
    if (alertList.length > 10) alertList.pop();
    const el = $('alertsEl');
    if (!el) return;
    const colors = { danger: 'border-danger/50 bg-danger/10', warn: 'border-warn/50 bg-warn/10', info: 'border-primary/50 bg-primary/10' };
    el.innerHTML = alertList.map(a => `
        <div class="border rounded-lg p-2 text-xs ${colors[a.type] || colors.info}">
            <div class="text-gray-200">${a.msg}</div>
            <div class="text-gray-500 mt-0.5">${a.t}</div>
        </div>`).join('');
}

// ─── Helpers ──────────────────────────────────────────────
function formatNum(n, dec = 2) {
    if (n === null || n === undefined || isNaN(n)) return '—';
    return parseFloat(n).toFixed(dec);
}

function showToast(msg, type = 'info') {
    const c = $('toastContainer');
    if (!c) return;
    const el = document.createElement('div');
    const colors = { success: 'bg-accent', error: 'bg-danger', info: 'bg-primary', warn: 'bg-warn' };
    el.className = `${colors[type] || 'bg-primary'} text-white text-sm px-4 py-2 rounded-lg shadow-lg transition-opacity duration-300 toast`;
    el.textContent = msg;
    c.appendChild(el);
    setTimeout(() => { el.style.opacity = '0'; setTimeout(() => el.remove(), 300); }, 3500);
}

async function exportCSV() {
    const data = await fetch('/api/history').then(r => r.json());
    const rows = [['Symbol', 'Action', 'Qty', 'Price', 'Total', 'Time']];
    data.forEach(t => rows.push([t.symbol, t.action, t.qty, t.price, t.total, t.timestamp]));
    const a = document.createElement('a');
    a.href = 'data:text/csv,' + encodeURIComponent(rows.map(r => r.join(',')).join('\n'));
    a.download = 'trades.csv';
    a.click();
}

// ─── Init ─────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', () => {
    initChart();
    initSocket();
    loadMovers();
    setInterval(loadMovers, 60000);
    setInterval(loadPortfolio, 15000);

    const searchEl = $('symbolSearch');
    if (searchEl) {
        // Instant switch on datalist selection or Enter
        searchEl.addEventListener('input', () => {
            const val = searchEl.value.trim().toUpperCase();
            // Switch immediately when a datalist option is exactly matched
            const opts = Array.from(document.querySelectorAll('#stockList option')).map(o => o.value);
            if (opts.includes(val)) { switchSymbol(val); searchEl.value = ''; }
        });
        searchEl.addEventListener('keydown', e => {
            if (e.key === 'Enter') {
                const val = searchEl.value.trim().toUpperCase();
                if (val) { switchSymbol(val); searchEl.value = ''; }
            }
        });
        searchEl.addEventListener('change', () => {
            const val = searchEl.value.trim().toUpperCase();
            if (val) { switchSymbol(val); searchEl.value = ''; }
        });
    }
});
