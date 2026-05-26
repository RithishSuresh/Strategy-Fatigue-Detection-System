// ═══════════════════════════════════════════════════════════
// AIoT Terminal - Pro Dashboard Script
// ═══════════════════════════════════════════════════════════
'use strict';

let currentSymbol = 'BTC-USD';
let lastPrice = 0;
let tvWidget = null;
let socket = null;
let switchStockController = null;
let alertList = [];
let recentSearches = JSON.parse(localStorage.getItem('aiot_recent_searches') || '[]');
let fatigueHistory = { labels: [], scores: [], risks: [] };
let fatigueChart = null;

const $ = id => document.getElementById(id);

// ─── UTILS ──────────────────────────────────────────────────
function formatNum(n, dec = 2) {
    if (n === null || n === undefined || isNaN(n)) return '—';
    return parseFloat(n).toFixed(dec);
}

function showToast(msg, type = 'info') {
    const c = $('toastContainer');
    if (!c) return;
    const el = document.createElement('div');
    const colors = { success: 'bg-accent', error: 'bg-danger', info: 'bg-primary', warn: 'bg-warn' };
    el.className = `${colors[type] || 'bg-primary'} text-white text-sm px-4 py-3 rounded-lg shadow-2xl transition-opacity duration-300 toast flex items-center gap-2 font-medium border border-white/10`;
    el.innerHTML = `<span>${msg}</span>`;
    c.appendChild(el);
    setTimeout(() => { el.style.opacity = '0'; setTimeout(() => el.remove(), 300); }, 4000);
}

// ─── CLOCK ──────────────────────────────────────────────────
setInterval(() => {
    if($('liveTime')) $('liveTime').textContent = new Date().toLocaleTimeString('en-IN', { hour12: false });
}, 1000);

// ─── PANEL NAVIGATION ───────────────────────────────────────
function showPanel(name) {
    ['chart', 'ai', 'portfolio', 'iot', 'strategies'].forEach(p => {
        const el = $('panel-' + p);
        if (el) el.classList.toggle('hidden', p !== name);
    });
    document.querySelectorAll('.sidebar-item').forEach(el => el.classList.remove('active'));
    const navEl = $('nav-' + name);
    if (navEl) navEl.classList.add('active');
    
    if (name === 'portfolio') loadPortfolio();
    if (name === 'iot') loadSensors();
    if (name === 'strategies' && window._lastMetrics) renderStrategyPanel(window._lastMetrics);
}

// ─── CHART INIT ─────────────────────────────────────────────
function initChart() {
    if (typeof TradingView === 'undefined') {
        console.error('TradingView script not ready');
        return;
    }
    
    let tvSym = 'BINANCE:BTCUSDT';
    
    tvWidget = new TradingView.widget({
        "autosize": true,
        "symbol": tvSym,
        "interval": "5",
        "timezone": "Asia/Kolkata",
        "theme": "dark",
        "style": "1",
        "locale": "en",
        "enable_publishing": false,
        "backgroundColor": "rgba(10, 14, 23, 0.8)",
        "gridColor": "rgba(255, 255, 255, 0.04)",
        "hide_top_toolbar": false,
        "hide_legend": false,
        "save_image": false,
        "container_id": "tradingview_chart",
        "studies": [
            "RSI@tv-basicstudies",
            "MACD@tv-basicstudies"
        ],
        "toolbar_bg": "rgba(10, 14, 23, 0.8)",
        "hide_side_toolbar": false,
        "allow_symbol_change": false
    });
}

function updateChartSymbol(sym) {
    if (!tvWidget || !tvWidget.chart) {
        $('tradingview_chart').innerHTML = '';
        initChart();
        return;
    }
    
    let tvSym = sym;
    if (sym.includes('-USD')) tvSym = 'BINANCE:' + sym.replace('-USD', 'USDT');
    else if (sym.endsWith('.NS')) tvSym = 'NSE:' + sym.replace('.NS', '');
    else if (sym.endsWith('.BO')) tvSym = 'BSE:' + sym.replace('.BO', '');
    else if (sym.endsWith('.L')) tvSym = 'LSE:' + sym.replace('.L', '');
    else if (sym.endsWith('.DE')) tvSym = 'XETR:' + sym.replace('.DE', '');
    else if (sym === '^GSPC') tvSym = 'SP:SPX';
    else if (sym === '^NSEI') tvSym = 'NSE:NIFTY';
    else if (sym === '^DJI') tvSym = 'DJ:DJI';
    else if (sym === '^IXIC') tvSym = 'NASDAQ:IXIC';
    else if (sym.includes('=X')) tvSym = 'FX_IDC:' + sym.replace('=X', '');
    else tvSym = sym; // TradingView usually handles raw standard US symbols like AAPL, TSLA perfectly without the exchange prefix.

    try {
        tvWidget.chart().setSymbol(tvSym);
    } catch(e) {
        console.warn("TV Widget not ready, re-initing.");
        $('tradingview_chart').innerHTML = '';
        initChart();
    }
}

let stockSwitchTimeout;

function switchSymbol(sym) {
    sym = sym.toUpperCase().trim();
    if (!sym || sym === currentSymbol) return;
    currentSymbol = sym;
    lastPrice = 0;
    
    if($('activeSymbol')) $('activeSymbol').textContent = sym;
    if($('activePrice')) $('activePrice').textContent = '—';
    if($('priceChange')) $('priceChange').textContent = '—';
    
    showOverlay(`Loading ${sym}...`);
    
    if (switchStockController) switchStockController.abort();
    switchStockController = new AbortController();
    
    clearTimeout(stockSwitchTimeout);
    stockSwitchTimeout = setTimeout(() => {
        updateChartSymbol(sym);
        sendSubscribe();
        showPanel('chart');
    }, 0);
}

// ─── OVERLAY ────────────────────────────────────────────────
function showOverlay(msg) {
    const ov = $('chartOverlay');
    if (ov) { ov.style.display = 'flex'; if($('overlayMsg')) $('overlayMsg').textContent = msg; }
}
function hideOverlay() {
    const ov = $('chartOverlay');
    if (ov) ov.style.display = 'none';
}

// ─── SOCKETS ────────────────────────────────────────────────
function initSocket() {
    if (typeof io === 'undefined') return;
    socket = io({ transports: ['websocket', 'polling'] });

    socket.on('connect', () => {
        $('connDot').className = 'w-2 h-2 rounded-full bg-accent pulse-dot';
        $('connText').textContent = 'Live';
        sendSubscribe();
    });

    socket.on('disconnect', () => {
        $('connDot').className = 'w-2 h-2 rounded-full bg-danger pulse-dot';
        $('connText').textContent = 'Disconnected';
    });

    socket.on('subscribed', d => console.log('Subscribed to', d.symbol));
    
    socket.on('history_data', d => {
        if (d.symbol === currentSymbol) hideOverlay();
    });

    socket.on('market_error', d => {
        if (d.symbol === currentSymbol) {
            hideOverlay();
            showToast(`${d.symbol}: ${d.message}`, 'error');
        }
    });

    socket.on('price_update', d => {
        updateTicker(d.symbol, d.price);
        if (d.symbol !== currentSymbol) return;

        const price = parseFloat(d.price);
        if (price !== lastPrice && lastPrice > 0) {
            const diff = price - lastPrice;
            const pct = (diff / lastPrice) * 100;
            const cls = diff > 0 ? 'text-accent' : 'text-danger';
            
            const ap = $('activePrice');
            if(ap) {
                ap.classList.remove('flash-up', 'flash-down');
                void ap.offsetWidth; // trigger reflow
                ap.classList.add(diff > 0 ? 'flash-up' : 'flash-down');
                ap.textContent = formatNum(price, 2);
            }
            
            const pc = $('priceChange');
            if(pc) {
                pc.textContent = `${diff > 0 ? '+' : ''}${formatNum(diff, 2)} (${formatNum(pct, 2)}%)`;
                pc.className = `text-sm font-medium ${cls}`;
            }
        } else if (lastPrice === 0) {
            if($('activePrice')) $('activePrice').textContent = formatNum(price, 2);
        }
        
        lastPrice = price;
        hideOverlay();
        if (d.metrics) updateMetrics(d.metrics);
        if (d.sensors) updateSensors(d.sensors);
    });
}

function sendSubscribe() {
    if (!socket) return;
    const wlSymbols = Array.from(document.querySelectorAll('[id^="wl-"]')).map(el => el.id.replace('wl-', ''));
    socket.emit('subscribe', { symbol: currentSymbol, watchlist: wlSymbols });
}

function updateTicker(sym, price) {
    const el = $('wl-' + sym);
    if (el) el.textContent = formatNum(price, 2);
}

// ─── AI METRICS & UI ────────────────────────────────────────
window._lastMetrics = null;
let lastAlertedStatus = '';

function updateMetrics(m) {
    window._lastMetrics = m;
    const score = m.fatigue || 0;
    const status = m.status || 'WARMING UP';
    const decision = m.decision || 'HOLD';
    
    // Top Bar
    if ($('rsiVal')) $('rsiVal').textContent = formatNum(m.rsi, 1);
    if ($('fatigueVal')) $('fatigueVal').textContent = formatNum(score, 1);
    if ($('ddVal')) $('ddVal').textContent = `-${formatNum(m.drawdown_pct, 1)}%`;
    if ($('bbVal')) $('bbVal').textContent = formatNum(m.bb_width, 4);
    
    const decEl = $('decisionVal');
    if (decEl) {
        decEl.textContent = decision;
        decEl.className = `font-bold ${decision==='BUY'?'text-accent':decision==='STOP'?'text-danger':decision==='REDUCE'?'text-orange-400':'text-warn'}`;
    }

    const color = score <= 30 ? '#089981' : score <= 55 ? '#f59e0b' : score <= 80 ? '#f97316' : '#f23645';
    
    // Chart Panel Fatigue Bar
    if ($('fatigueBar')) { $('fatigueBar').style.width = `${score}%`; $('fatigueBar').style.background = color; }
    if ($('fatigueScore')) $('fatigueScore').textContent = formatNum(score, 1);
    if ($('fatigueExplain')) $('fatigueExplain').textContent = m.recommendation || '';
    
    const tag = $('fatigueTag');
    if (tag) {
        tag.textContent = status;
        tag.style.background = color + '33';
        tag.style.color = color;
    }

    // AI Panel
    if ($('ai-iso')) $('ai-iso').textContent = formatNum(m.isolation_score, 1);
    if ($('ai-rsi')) $('ai-rsi').textContent = formatNum(m.rsi, 1);
    if ($('ai-dd')) $('ai-dd').textContent = `-${formatNum(m.drawdown_pct, 1)}%`;
    if ($('ai-vol')) $('ai-vol').textContent = formatNum(m.volatility, 1) + '%';
    if ($('ai-score')) $('ai-score').textContent = formatNum(score, 1);
    
    if ($('ai-reasons')) {
        $('ai-reasons').innerHTML = (m.reasons && m.reasons.length) 
            ? m.reasons.map(r => `<div class="flex gap-2 items-start"><span class="text-primary">▸</span>${r}</div>`).join('')
            : '<div class="text-gray-500 italic">All indicators normal.</div>';
    }
    
    if ($('ai-bar')) { $('ai-bar').style.width = score + '%'; $('ai-bar').style.background = color; }
    
    if ($('ai-status')) {
        $('ai-status').textContent = status;
        $('ai-status').style.background = color + '33';
        $('ai-status').style.color = color;
    }
    
    const gaugePath = $('ai-gauge-path');
    if (gaugePath) {
        const offset = ((100 - Math.min(100, Math.max(0, score))) / 100) * 125.6;
        gaugePath.style.strokeDashoffset = offset;
        gaugePath.style.stroke = color;
    }

    if ($('ai-confidence')) $('ai-confidence').textContent = Math.round(70 + (Math.abs(score - 50) / 50) * 25) + '%';

    // Alerts
    if (status !== lastAlertedStatus && status !== 'WARMING UP') {
        pushAlert(`AI ALERT: ${currentSymbol} status changed to ${status} — Action: ${decision}`, score > 60 ? 'danger' : 'info');
        lastAlertedStatus = status;
    }
    
    if (!$('panel-strategies').classList.contains('hidden')) renderStrategyPanel(m);
    
    // Update IoT/Diagnostics Panel
    updateFatigueDiagnostics(m);
}

function updateFatigueDiagnostics(m) {
    const score = m.fatigue || 0;
    const status = m.status || 'NORMAL';
    const color = score <= 30 ? '#089981' : score <= 55 ? '#f59e0b' : score <= 80 ? '#f97316' : '#f23645';

    if ($('iot-fatigue-val')) $('iot-fatigue-val').textContent = formatNum(score, 0);
    if ($('iot-status-tag')) {
        $('iot-status-tag').textContent = status;
        $('iot-status-tag').style.color = color;
        $('iot-status-tag').style.borderColor = color + '44';
        $('iot-status-tag').style.background = color + '11';
    }

    const gaugePath = $('iot-gauge-path');
    if (gaugePath) {
        const offset = ((100 - Math.min(100, Math.max(0, score))) / 100) * 125.6;
        gaugePath.style.strokeDashoffset = offset;
    }

    // Update Chart
    if (fatigueChart) {
        const now = new Date().toLocaleTimeString('en-IN', { hour12: false, minute: '2-digit', second: '2-digit' });
        fatigueChart.data.labels.push(now);
        fatigueChart.data.datasets[0].data.push(score);
        fatigueChart.data.datasets[1].data.push(m.isolation_score || (score * 0.8));

        if (fatigueChart.data.labels.length > 20) {
            fatigueChart.data.labels.shift();
            fatigueChart.data.datasets[0].data.shift();
            fatigueChart.data.datasets[1].data.shift();
        }
        fatigueChart.update('none');
    }

    // Diagnostics Log
    if ($('diagnosticsLog') && m.reasons && m.reasons.length) {
        const log = $('diagnosticsLog');
        m.reasons.forEach(r => {
            if (!log.innerText.includes(r)) {
                const item = document.createElement('div');
                item.className = 'flex items-start gap-2 text-[11px] border-b border-white/5 pb-2 animate-in fade-in slide-in-from-left-2 duration-300';
                item.innerHTML = `<span class="text-primary mt-0.5">●</span><div class="flex-1"><span class="text-gray-400 font-mono mr-2">${new Date().toLocaleTimeString()}</span><span class="text-white">${r}</span></div>`;
                log.prepend(item);
                if (log.children.length > 15) log.lastElementChild.remove();
            }
        });
    }
}

function initFatigueChart() {
    const ctx = $('fatigueChart');
    if (!ctx) return;
    
    fatigueChart = new Chart(ctx, {
        type: 'line',
        data: {
            labels: [],
            datasets: [
                {
                    label: 'Fatigue Score',
                    data: [],
                    borderColor: '#2962ff',
                    backgroundColor: 'rgba(41, 98, 255, 0.1)',
                    borderWidth: 2,
                    tension: 0.4,
                    fill: true,
                    pointRadius: 0
                },
                {
                    label: 'Risk Anomaly',
                    data: [],
                    borderColor: '#f23645',
                    borderDash: [5, 5],
                    borderWidth: 1.5,
                    tension: 0.4,
                    pointRadius: 0
                }
            ]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: { legend: { display: false } },
            scales: {
                x: { display: false },
                y: {
                    min: 0, max: 100,
                    grid: { color: 'rgba(255,255,255,0.03)' },
                    ticks: { color: '#64748b', font: { size: 10, family: 'JetBrains Mono' } }
                }
            },
            animation: { duration: 0 }
        }
    });
}

function renderStrategyPanel(m) {
    const grid = $('strategyGrid');
    if (!grid || !m.strategies) return;
    
    const sigColors = {BUY:'text-accent',SELL:'text-danger',HOLD:'text-warn'};
    const statColors = {HEALTHY:'#089981',WARNING:'#f59e0b',FATIGUED:'#f97316',CRITICAL:'#f23645'};
    
    grid.innerHTML = m.strategies.map(s => {
        const sc = s.fatigue_score || 0;
        const col = statColors[s.fatigue_status] || '#94a3b8';
        const cl = s.consec_losses || 0;
        return `<div class="glass-panel p-5 h-full flex flex-col" style="border-top:3px solid ${col}">
          <div class="flex justify-between items-center mb-3 shrink-0">
            <span class="font-bold text-white text-[15px] truncate mr-2 flex-1">${s.name}</span>
            <span class="tag shrink-0" style="background:${col}22;color:${col}">${s.fatigue_status}</span>
          </div>
          <div class="text-xs text-gray-400 mb-4 min-h-[32px] line-clamp-2 shrink-0">${s.rule}</div>
          
          <div class="mb-4 shrink-0">
            <div class="flex justify-between text-xs mb-1">
                <span class="text-gray-500">Fatigue Score</span>
                <span class="font-mono font-bold" style="color:${col}">${sc}</span>
            </div>
            <div class="h-1.5 bg-white/10 rounded-full overflow-hidden">
                <div class="h-full rounded-full transition-all duration-500" style="width:${sc}%;background:${col}"></div>
            </div>
          </div>
          
          <div class="grid grid-cols-2 gap-3 text-xs mb-4 p-3 bg-white/5 rounded-lg border border-white/5 shrink-0">
            <div class="overflow-hidden"><span class="text-gray-500 block mb-0.5 truncate">Win Rate</span><span class="text-white font-mono truncate block">${s.win_rate}%</span></div>
            <div class="overflow-hidden"><span class="text-gray-500 block mb-0.5 truncate">Drawdown</span><span class="text-danger font-mono truncate block">-${s.drawdown}%</span></div>
            <div class="overflow-hidden"><span class="text-gray-500 block mb-0.5 truncate">Indicator</span><span class="text-blue-400 font-mono truncate block" title="${s.indicator}">${s.indicator}</span></div>
            <div class="overflow-hidden"><span class="text-gray-500 block mb-0.5 truncate">Loss Streak</span><span class="${cl>=3?'text-danger':'text-white'} font-mono truncate block">${cl}</span></div>
          </div>
          
          <div class="mt-auto flex justify-between items-center pt-3 border-t border-white/10 shrink-0">
            <span class="text-xs text-gray-500 uppercase tracking-wider">Live Signal</span>
            <span class="font-bold text-sm ${sigColors[s.signal]||'text-white'} bg-white/5 px-3 py-1 rounded border border-white/5 shadow-inner">${s.signal}</span>
          </div>
        </div>`;
    }).join('');
}

// ─── PORTFOLIO & TRADE ──────────────────────────────────────
async function loadPortfolio() {
    try {
        const data = await fetch('/api/portfolio').then(r => r.json());
        
        ['p-balance', 'balanceDisplay'].forEach(id => {
            if($(id)) $(id).textContent = '₹' + data.balance.toLocaleString('en-IN', { minimumFractionDigits: 2 });
        });
        
        const holdVal = data.holdings.reduce((s, h) => s + h.value, 0);
        if ($('p-holdings')) $('p-holdings').textContent = '₹' + holdVal.toLocaleString('en-IN', { minimumFractionDigits: 2 });
        if ($('totalValue')) $('totalValue').textContent = 'Total: ₹' + data.total_value.toLocaleString('en-IN', { minimumFractionDigits: 2 });
        
        const totalPnL = data.holdings.reduce((s, h) => s + h.pnl, 0);
        if ($('p-pnl')) {
            $('p-pnl').textContent = (totalPnL >= 0 ? '+' : '') + '₹' + Math.abs(totalPnL).toLocaleString('en-IN', { minimumFractionDigits: 2 });
            $('p-pnl').className = `text-xl font-bold mono ${totalPnL >= 0 ? 'text-accent' : 'text-danger'}`;
        }
        
        if ($('holdingsTable')) {
            $('holdingsTable').innerHTML = data.holdings.length === 0
                ? '<div class="p-6 text-center text-gray-500 text-sm">Portfolio is empty</div>'
                : data.holdings.map(h => `
                    <div class="flex items-center justify-between p-4 border-b border-border/50 hover:bg-white/5 cursor-pointer transition-colors" onclick="switchSymbol('${h.symbol}')">
                        <div>
                            <div class="font-bold text-white">${h.symbol}</div>
                            <div class="text-xs text-gray-500 mt-0.5">${h.qty} qty @ ₹${h.avg_price} avg</div>
                        </div>
                        <div class="text-right">
                            <div class="mono text-white font-medium">₹${h.live_price}</div>
                            <div class="text-xs mt-0.5 ${h.pnl >= 0 ? 'text-accent' : 'text-danger'} font-medium">
                                ${h.pnl >= 0 ? '▲ +' : '▼ '}₹${Math.abs(h.pnl)}
                            </div>
                        </div>
                    </div>`).join('');
        }
        
        const hist = await fetch('/api/history').then(r => r.json());
        if ($('tradeHistory')) {
            $('tradeHistory').innerHTML = hist.length === 0
                ? '<div class="p-6 text-center text-gray-500 text-sm">No trades executed</div>'
                : hist.slice(0, 15).map(t => {
                    const localTime = t.timestamp ? new Date(t.timestamp.replace(' ', 'T') + 'Z').toLocaleString('en-IN', { hour12: true }) : '';
                    return `
                    <div class="flex items-center justify-between p-3 border-b border-border/50">
                        <div class="flex items-center gap-3">
                            <span class="w-12 text-center text-[10px] font-bold py-1 rounded border ${t.action === 'BUY' ? 'text-accent border-accent/30 bg-accent/10' : 'text-danger border-danger/30 bg-danger/10'}">${t.action}</span>
                            <div>
                                <span class="font-bold text-white text-sm">${t.symbol}</span>
                                <div class="text-[11px] text-gray-500 mt-0.5">Strategy: ${t.strategy}</div>
                            </div>
                        </div>
                        <div class="text-right">
                            <div class="mono text-white text-sm">₹${t.total}</div>
                            <div class="text-[10px] text-gray-500 mt-0.5">${t.qty} @ ₹${t.price} <span class="block text-[9px] text-gray-600 mt-0.5">${localTime}</span></div>
                        </div>
                    </div>`;
                }).join('');
        }
    } catch(e) {
        console.error('Portfolio error:', e);
        if ($('holdingsTable')) $('holdingsTable').innerHTML = '<div class="p-6 text-center text-gray-500 text-sm">Data temporarily unavailable</div>';
        if ($('tradeHistory')) $('tradeHistory').innerHTML = '<div class="p-6 text-center text-gray-500 text-sm">Data temporarily unavailable</div>';
    }
}

async function executeTrade(action) {
    const qty = parseFloat($('tradeQty').value);
    if (isNaN(qty) || qty <= 0) return showToast('Enter valid quantity', 'warn');
    
    try {
        const res = await fetch('/api/trade', {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ symbol: currentSymbol, action, qty, strategy: 'MANUAL' })
        });
        
        const text = await res.text();
        let data;
        try {
            data = JSON.parse(text);
        } catch(parseErr) {
            console.error('Non-JSON response:', text);
            if (text.includes('login') || text.includes('<!DOCTYPE html>')) {
                 return showToast('Session expired. Please refresh and log in.', 'error');
            }
            return showToast('Server Error: ' + text.substring(0, 40), 'error');
        }

        if (res.ok && data.success) {
            showToast(`Successfully ${action.toLowerCase()}ed ${qty} ${currentSymbol}`, 'success');
            loadPortfolio();
        } else {
            showToast(data.error || 'Trade rejected', 'error');
        }
    } catch(e) { 
        console.error(e);
        showToast('Network error: Unable to reach server', 'error'); 
    }
}

// ─── WATCHLIST & SEARCH ─────────────────────────────────────
async function addToWatchlist() {
    const sym = prompt('Enter Stock Symbol (e.g., TSLA, RELIANCE.NS):');
    if (!sym) return;
    const cleanSym = sym.toUpperCase().trim();
    
    await fetch('/api/watchlist/add', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ symbol: cleanSym })
    });
    
    if (!$('wl-row-' + cleanSym) && $('watchlistEl')) {
        const div = document.createElement('div');
        div.className = 'flex items-center justify-between p-2.5 rounded-lg hover:bg-white/10 cursor-pointer group transition-colors mb-1';
        div.id = 'wl-row-' + cleanSym;
        div.innerHTML = `
          <div class="flex-1 font-semibold text-white text-[13px]" onclick="switchSymbol('${cleanSym}')">${cleanSym}</div>
          <div class="flex items-center gap-2">
            <span class="text-xs text-gray-400 mono font-medium" id="wl-${cleanSym}">—</span>
            <button onclick="removeFromWatchlist('${cleanSym}', event)" class="opacity-0 group-hover:opacity-100 text-gray-500 hover:text-danger p-1 transition-opacity">✕</button>
          </div>
        `;
        $('watchlistEl').appendChild(div);
    }
    sendSubscribe();
    switchSymbol(cleanSym);
}

async function removeFromWatchlist(sym, e) {
    if (e) e.stopPropagation();
    await fetch('/api/watchlist/remove', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ symbol: sym })
    });
    if ($('wl-row-' + sym)) $('wl-row-' + sym).remove();
    sendSubscribe();
}

let searchTimeout;
let searchController;
const searchCache = {};

function initSearch() {
    const input = $('symbolSearch');
    const dropdown = $('searchDropdown');
    
    if (!input || !dropdown) return;
    
    input.addEventListener('input', async (e) => {
        const q = e.target.value.trim();
        if (!q) {
            dropdown.innerHTML = '';
            dropdown.classList.add('hidden');
            return;
        }
        
        dropdown.innerHTML = '<div class="p-4 text-sm text-gray-500 text-center flex items-center justify-center gap-2"><div class="w-4 h-4 border-2 border-primary border-t-transparent rounded-full animate-spin"></div>Searching...</div>';
        dropdown.classList.remove('hidden');

        if (searchCache[q]) {
            renderSearchDropdown(searchCache[q], dropdown);
            return;
        }

        clearTimeout(searchTimeout);
        if (searchController) searchController.abort();
        searchController = new AbortController();

        searchTimeout = setTimeout(async () => {
            try {
                const res = await fetch('/api/search?q=' + q, { signal: searchController.signal }).then(r=>r.json());
                searchCache[q] = res;
                renderSearchDropdown(res, dropdown);
            } catch(err) {
                if (err.name !== 'AbortError') {
                    dropdown.innerHTML = '<div class="p-4 text-sm text-danger text-center">Data temporarily unavailable</div>';
                }
            }
        }, 300);
    });
    
    document.addEventListener('click', (e) => {
        if (!input.contains(e.target) && !dropdown.contains(e.target)) {
            dropdown.classList.add('hidden');
        }
    });
}

function renderSearchDropdown(res, dropdown) {
    if (res && res.length > 0) {
        dropdown.innerHTML = res.map(s => `
            <div class="px-4 py-3 border-b border-border/50 hover:bg-primary/20 cursor-pointer flex justify-between items-center transition-colors" onclick="selectSearch('${s.symbol}')">
                <div>
                    <div class="font-bold text-white">${s.symbol}</div>
                    <div class="text-xs text-gray-500 mt-0.5">${s.name}</div>
                </div>
                <span class="text-[10px] uppercase tracking-wider font-semibold text-primary/70 bg-primary/10 px-2 py-1 rounded">${s.type}</span>
            </div>
        `).join('');
    } else {
        dropdown.innerHTML = '<div class="p-4 text-sm text-gray-500 text-center">No symbols found</div>';
    }
}

window.selectSearch = function(sym) {
    switchSymbol(sym);
    $('symbolSearch').value = '';
    $('searchDropdown').classList.add('hidden');
}

// ─── MARKET MOVERS & ALERTS ─────────────────────────────────
async function loadMovers() {
    try {
        const data = await fetch('/api/movers').then(r => r.json());
        const render = (id, arr, isGain) => {
            const el = $(id);
            if (!el) return;
            el.innerHTML = arr.map(m => `
                <div class="flex justify-between items-center py-1.5 cursor-pointer hover:bg-white/5 px-2 rounded -mx-2 transition-colors" onclick="switchSymbol('${m.symbol}')">
                    <span class="text-xs font-semibold text-gray-300">${m.symbol}</span>
                    <span class="text-xs mono font-medium ${isGain ? 'text-accent' : 'text-danger'}">${isGain?'+':''}${m.change}%</span>
                </div>
            `).join('');
        };
        render('gainers', data.gainers, true);
        render('losers', data.losers, false);
    } catch(e) {
        console.error('Movers API Error:', e);
        if ($('gainers')) $('gainers').innerHTML = '<div class="text-xs text-gray-500">Data temporarily unavailable</div>';
        if ($('losers')) $('losers').innerHTML = '<div class="text-xs text-gray-500">Data temporarily unavailable</div>';
    }
}

function pushAlert(msg, type = 'info') {
    alertList.unshift({ msg, type, t: new Date().toLocaleTimeString('en-IN', { hour12: false }) });
    if (alertList.length > 15) alertList.pop();
    const el = $('alertsEl');
    if (!el) return;
    
    const styles = {
        danger: 'border-l-4 border-l-danger bg-danger/10 text-red-200',
        warn: 'border-l-4 border-l-warn bg-warn/10 text-orange-200',
        info: 'border-l-4 border-l-primary bg-primary/10 text-blue-200'
    };
    
    el.innerHTML = alertList.map(a => `
        <div class="p-3 mb-2 rounded-r-lg ${styles[a.type]} text-xs shadow-sm">
            <div class="leading-relaxed font-medium">${a.msg}</div>
            <div class="text-[10px] mt-1.5 opacity-70 font-mono">${a.t}</div>
        </div>
    `).join('');
}

// ─── IOT & SENSORS ──────────────────────────────────────────
function loadSensors() {
    fetch('/api/sensors').then(r=>r.json()).then(d => updateSensors(d.sensors)).catch((e)=>{
        console.error('Sensors API Error:', e);
    });
}
function updateSensors(s) {
    if (!s) return;
    const set = (id, val) => { if($(id)) $(id).textContent = val; };
    const bar = (id, pct) => { if($(id)) $(id).style.width = Math.min(pct, 100) + '%'; };
    
    set('s-cpu', s.cpu_usage + '%');   bar('sb-cpu', s.cpu_usage);
    set('s-ram', s.ram_usage + '%');   bar('sb-ram', s.ram_usage);
    set('s-lat', s.api_latency + 'ms'); bar('sb-lat', s.api_latency / 3);
    set('s-risk', s.risk_pressure + '%'); bar('sb-risk', s.risk_pressure);
    
    set('s-opm', s.orders_per_min);
    set('s-tf', s.trade_frequency);
    set('s-nd', s.network_delay + 'ms');
    set('s-hb', s.heartbeat === 'OK' ? 'OK ✓' : 'ERROR ✗');
}

// ─── STRATEGY SIMULATION ────────────────────────────────────
async function runStrategyDemo() {
    const symbol = $('demoSymbol').value.toUpperCase().trim();
    const rulesStr = $('demoRules').value;
    const rules = rulesStr.split(',');
    
    if (!symbol) return showToast('Enter symbol for backtest', 'warn');
    
    const ex = $('botExecutions');
    ex.innerHTML = `<div class="flex flex-col items-center justify-center h-full text-primary">
        <div class="w-6 h-6 border-2 border-primary border-t-transparent rounded-full animate-spin mb-3"></div>
        <div class="text-xs font-semibold animate-pulse">Running Simulation...</div>
    </div>`;
    
    try {
        const res = await fetch('/api/strategy/run', {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ symbol, rules, capital: 100000, stop_loss: 2.0, take_profit: 5.0 })
        });
        const data = await res.json();
        
        if (data.error) {
            ex.innerHTML = `<div class="p-4 text-center text-danger text-sm font-semibold">${data.error}</div>`;
            return;
        }

        const sum = data.summary;
        $('demoTotalTrades').textContent = sum.total_trades;
        $('demoWinRate').textContent = sum.win_rate + '%';
        $('demoTotalPnl').textContent = '₹' + sum.total_pnl;
        $('demoTotalPnl').className = `text-lg font-bold mono ${sum.total_pnl >= 0 ? 'text-accent' : 'text-danger'}`;
        
        if (data.trades.length === 0) {
            ex.innerHTML = '<div class="p-6 text-center text-gray-500 text-sm">No trades triggered by rules in this period</div>';
        } else {
            ex.innerHTML = data.trades.map(t => `
                <div class="flex justify-between items-center p-3 border-b border-white/5 hover:bg-white/5 transition-colors">
                    <div class="flex flex-col gap-1 w-24">
                        <span class="text-[10px] font-bold px-2 py-0.5 rounded text-center ${t.action==='BUY'?'bg-accent/20 text-accent':'bg-danger/20 text-danger'}">${t.action}</span>
                        <span class="text-[10px] text-gray-500 font-mono">${t.ts.split(' ')[1] || t.ts}</span>
                    </div>
                    <div class="text-white font-mono font-medium flex-1 text-center">₹${t.price}</div>
                    <div class="w-32 text-right flex flex-col justify-center">
                        ${t.action === 'SELL' 
                            ? `<span class="font-mono font-bold ${t.pnl>=0?'text-accent':'text-danger'}">${t.pnl>=0?'+':''}₹${t.pnl}</span>` 
                            : `<span class="text-[10px] text-primary bg-primary/10 px-2 py-1 rounded inline-block truncate w-full" title="${t.reason}">${t.reason}</span>`}
                    </div>
                </div>
            `).join('');
            ex.scrollTop = ex.scrollHeight;
        }
    } catch(e) {
        ex.innerHTML = '<div class="p-4 text-center text-danger text-sm">Network error during backtest</div>';
    }
}

// ─── INITIALIZATION ─────────────────────────────────────────
document.addEventListener('DOMContentLoaded', () => {
    initChart();
    initSocket();
    initSearch();
    initFatigueChart();
    loadMovers();
    
    setInterval(loadMovers, 60000);
    setInterval(loadPortfolio, 10000);
    
    // Initial welcome alert
    setTimeout(() => {
        pushAlert("System Initialized. AI Engine active.", "info");
        pushAlert("Connect Node-RED on port 1880 for IoT metrics.", "warn");
    }, 1000);
});
