const LANDING_SYMBOLS = {
    MAIN: '^NSEI', // Nifty 50 or use AAPL if preferred. Using NSEI as it was in the screenshot
    BTC: 'BTC-USD',
    INR: 'INR=X',
    SP: '^GSPC'
};

// State
const charts = {};
const chartData = {};
let socket = null;
let lastPrices = {};

document.addEventListener('DOMContentLoaded', () => {
    initCharts();
    initSocket();
});

function initCharts() {
    // Shared options for mini charts
    const miniOptions = {
        responsive: true, maintainAspectRatio: false, animation: { duration: 0 },
        layout: { padding: 0 },
        scales: { x: { display: false }, y: { display: false, min: 'auto', max: 'auto' } },
        plugins: { legend: { display: false }, tooltip: { enabled: false } },
        elements: { point: { radius: 0, hitRadius: 10 }, line: { tension: 0.1, borderWidth: 2 } }
    };

    // Main Chart Option
    const mainOptions = { ...miniOptions };
    mainOptions.scales.y = { position: 'right', grid: { color: '#1e222d' }, ticks: { color: '#d1d5db' }, border: { display: false } };
    mainOptions.plugins.tooltip = { mode: 'index', intersect: false, backgroundColor: 'rgba(19, 23, 34, 0.9)', titleColor: '#fff', bodyColor: '#fff' };

    // Create Main Chart
    const ctxMain = document.getElementById('marketSummaryChart').getContext('2d');
    chartData[LANDING_SYMBOLS.MAIN] = { labels: [], prices: [] };
    charts[LANDING_SYMBOLS.MAIN] = new Chart(ctxMain, {
        type: 'line',
        data: { labels: chartData[LANDING_SYMBOLS.MAIN].labels, datasets: [{ label: 'Price', data: chartData[LANDING_SYMBOLS.MAIN].prices, borderColor: '#f23645', backgroundColor: 'rgba(242, 54, 69, 0.1)', fill: true }] },
        options: mainOptions
    });

    // Create Mini Charts
    const createMini = (id, symbol, color) => {
        const ctx = document.getElementById(id).getContext('2d');
        chartData[symbol] = { labels: [], prices: [] };
        charts[symbol] = new Chart(ctx, {
            type: 'line',
            data: { labels: chartData[symbol].labels, datasets: [{ data: chartData[symbol].prices, borderColor: color, backgroundColor: `${color}20`, fill: true }] },
            options: miniOptions
        });
    };

    createMini('miniChartBtc', LANDING_SYMBOLS.BTC, '#2962ff');
    createMini('miniChartInr', LANDING_SYMBOLS.INR, '#089981');
    createMini('miniChartSp', LANDING_SYMBOLS.SP, '#f59e0b');
}

function initSocket() {
    socket = io();
    
    socket.on('connect', () => {
        // Subscribe to landing page symbols
        Object.values(LANDING_SYMBOLS).forEach(sym => {
            socket.emit('subscribe', {symbol: sym});
        });
    });

    socket.on('history_data', (data) => {
        const sym = data.symbol;
        if (charts[sym]) {
            chartData[sym].labels.length = 0;
            chartData[sym].prices.length = 0;
            data.data.forEach(d => {
                chartData[sym].labels.push(d.time);
                chartData[sym].prices.push(d.close);
            });
            charts[sym].update();
            updateUI(sym, d.close, 0); // initial render
        }
    });

    socket.on('price_update', (data) => {
        const sym = data.symbol;
        if (charts[sym]) {
            const price = parseFloat(data.price);
            const time = data.time;
            
            chartData[sym].labels.push(time);
            chartData[sym].prices.push(price);
            if (chartData[sym].labels.length > 50) {
                chartData[sym].labels.shift();
                chartData[sym].prices.shift();
            }
            
            const last = lastPrices[sym] || price;
            updateUI(sym, price, price - last);
            lastPrices[sym] = price;
            
            // Dynamic color for main chart
            if (sym === LANDING_SYMBOLS.MAIN) {
                const isUp = price >= chartData[sym].prices[0];
                charts[sym].data.datasets[0].borderColor = isUp ? '#089981' : '#f23645';
                charts[sym].data.datasets[0].backgroundColor = isUp ? 'rgba(8, 153, 129, 0.1)' : 'rgba(242, 54, 69, 0.1)';
            }
            
            charts[sym].update();
        }
        
        // Update indices list dynamically if it's a known index
        updateIndicesList(sym, data.price);
    });
}

function updateUI(sym, price, diff) {
    const isUp = diff >= 0;
    const color = isUp ? 'text-[#089981]' : 'text-[#f23645]';
    const sign = isUp ? '+' : '';
    
    let pEl, cEl;
    if (sym === LANDING_SYMBOLS.MAIN) { pEl = document.getElementById('mainIdxPrice'); cEl = document.getElementById('mainIdxChange'); }
    else if (sym === LANDING_SYMBOLS.BTC) { pEl = document.getElementById('miniBtcPrice'); cEl = document.getElementById('miniBtcChange'); }
    else if (sym === LANDING_SYMBOLS.INR) { pEl = document.getElementById('miniInrPrice'); cEl = document.getElementById('miniInrChange'); }
    else if (sym === LANDING_SYMBOLS.SP) { pEl = document.getElementById('miniSpPrice'); cEl = document.getElementById('miniSpChange'); }
    
    if (pEl) {
        pEl.textContent = price.toLocaleString(undefined, {minimumFractionDigits: 2, maximumFractionDigits: 2});
        if (diff !== 0) {
            cEl.textContent = `${sign}${diff.toFixed(2)}`;
            cEl.className = `text-lg font-bold ${color}`;
        }
    }
}

// Manage the right-side list of indices
const indicesMap = {
    '^NSEI': { name: 'Nifty 50', code: 'NSEI', price: 0, diff: 0 },
    '^BSESN': { name: 'Sensex', code: 'SENSEX', price: 0, diff: 0 },
    '^GSPC': { name: 'S&P 500', code: 'SPX', price: 0, diff: 0 },
    '^IXIC': { name: 'Nasdaq 100', code: 'NDX', price: 0, diff: 0 },
    'BTC-USD': { name: 'Bitcoin', code: 'BTC', price: 0, diff: 0 }
};

// Initial subscribe for side list
document.addEventListener('DOMContentLoaded', () => {
    setTimeout(() => {
        if(socket) {
            Object.keys(indicesMap).forEach(sym => socket.emit('subscribe', {symbol: sym}));
        }
    }, 1000);
});

function updateIndicesList(sym, price) {
    if (indicesMap[sym]) {
        const item = indicesMap[sym];
        item.diff = item.price > 0 ? price - item.price : 0;
        item.price = price;
        renderIndices();
    }
}

function renderIndices() {
    const container = document.getElementById('indicesList');
    if(!container) return;
    
    let html = '';
    Object.keys(indicesMap).forEach(sym => {
        const data = indicesMap[sym];
        if (data.price === 0) return;
        const isUp = data.diff >= 0;
        const color = isUp ? 'text-[#089981]' : 'text-[#f23645]';
        
        html += `
        <div class="flex justify-between items-center py-2 border-b border-gray-800/50 hover:bg-gray-800/20 cursor-pointer">
            <div class="flex items-center gap-3">
                <div class="w-8 h-8 rounded-full bg-gray-800 flex items-center justify-center text-xs font-bold text-white">${data.code.substring(0,3)}</div>
                <div>
                    <div class="font-bold text-sm text-white">${data.name}</div>
                    <div class="text-[10px] text-gray-500 bg-gray-800 inline-block px-1 rounded">${data.code}</div>
                </div>
            </div>
            <div class="text-right">
                <div class="font-bold text-sm text-white">${data.price.toLocaleString(undefined, {minimumFractionDigits: 2, maximumFractionDigits: 2})}</div>
                <div class="text-xs font-bold ${color}">${isUp?'+':''}${data.diff.toFixed(2)}</div>
            </div>
        </div>`;
    });
    container.innerHTML = html;
}
