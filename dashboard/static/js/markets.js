// dashboard/static/js/markets.js
// Market grids and charts: City | Range | Edge | Signal | Confidence (5 cols, paginated)

const PLOTLY_LAYOUT = {
    paper_bgcolor: 'rgba(0,0,0,0)',
    plot_bgcolor: '#1a2332',
    font: { color: 'rgba(255,255,255,0.85)', family: 'Roboto, sans-serif', size: 12 },
    margin: { l: 10, r: 10, t: 30, b: 10 },
    xaxis: { gridcolor: '#2a3a4e', zerolinecolor: '#2a3a4e', automargin: true },
    yaxis: { gridcolor: '#2a3a4e', zerolinecolor: '#2a3a4e', automargin: true },
};

const PLOTLY_CONFIG = { displayModeBar: false, responsive: true };
const PAGE_SIZE = 10;
window._signalCounts = window._signalCounts || {};

const _sortState = {};
const _pageState = {};
const _marketData = {};

function fmtScanTime(isoStr) {
    try {
        const d = new Date(isoStr);
        const mm = String(d.getMonth() + 1).padStart(2, '0');
        const dd = String(d.getDate()).padStart(2, '0');
        const hh = String(d.getHours()).padStart(2, '0');
        const mi = String(d.getMinutes()).padStart(2, '0');
        const ss = String(d.getSeconds()).padStart(2, '0');
        return `${mm}/${dd} ${hh}${mi}:${ss}`;
    } catch (_) { return isoStr; }
}

const COLUMNS = [
    { key: 'city',       label: 'City',       num: false },
    { key: 'threshold',  label: 'Range',      num: false },
    { key: 'edge',       label: 'Edge',       num: true  },
    { key: 'direction',  label: 'Signal',     num: false },
    { key: 'confidence', label: 'Confidence', num: true  },
];

const COLS_CSS = COLUMNS.map(c => c.num ? 'auto' : '1fr').join(' ');

function marketsGrid(markets, type) {
    const st = _sortState[type] || { key: 'edge', dir: 'desc', abs: true };
    const page = _pageState[type] || 1;

    const headers = COLUMNS.map(c => {
        const arrow = st.key === c.key ? (st.dir === 'asc' ? ' \u2191' : ' \u2193') : '';
        return `<span${c.num ? ' class="num"' : ''} data-sort-key="${c.key}" style="cursor:pointer;user-select:none">${c.label}${arrow}</span>`;
    }).join('');

    if (!markets || markets.length === 0) {
        return `<div class="data-grid" data-market-type="${type}" style="--cols: ${COLS_CSS}">
            <div class="dg-head">${headers}</div>
            <div class="dg-empty">No markets available</div>
        </div>`;
    }

    const sorted = [...markets].sort((a, b) => {
        let va = a[st.key], vb = b[st.key];
        if (st.abs) { va = Math.abs(va); vb = Math.abs(vb); }
        if (typeof va === 'string') return st.dir === 'asc' ? va.localeCompare(vb) : vb.localeCompare(va);
        return st.dir === 'asc' ? va - vb : vb - va;
    });

    const total = sorted.length;
    const totalPages = Math.ceil(total / PAGE_SIZE);
    const safePage = Math.max(1, Math.min(page, totalPages));
    const start = (safePage - 1) * PAGE_SIZE;
    const slice = sorted.slice(start, start + PAGE_SIZE);

    const rows = slice.map(m => {
        const absEdge = Math.abs(m.edge);
        const signal = m.edge > 0 ? 'BUY YES' : 'BUY NO';
        const edgeDisplay = `+${(absEdge * 100).toFixed(1)}%`;
        let confClass;
        if (m.confidence >= 60) confClass = 'val-positive';
        else if (m.confidence >= 40) confClass = 'val-amber';
        else confClass = 'val-negative';

        // Mobile: Row1 City Range Edge Conf | Row2 Signal(full)
        return `<div class="dg-row">
          <span>${m.city}</span>
          <span class="mono">${m.threshold}</span>
          <span class="num mono val-positive">${edgeDisplay}</span>
          <span style="--mo:99" data-mob="full">${signal}</span>
          <span class="num mono ${confClass}">${m.confidence.toFixed(1)}</span>
        </div>`;
    }).join('\n');

    let pagination = '';
    if (totalPages > 1) {
        pagination = `
        <div class="pagination" data-paginator="${type}">
            <span class="pagination-info">Showing ${start + 1}\u2013${Math.min(start + PAGE_SIZE, total)} of ${total}</span>
            <div class="pagination-buttons">
                <button class="pagination-btn" data-page="${safePage - 1}" ${safePage <= 1 ? 'disabled' : ''}>\u2190 Prev</button>
                <button class="pagination-btn" data-page="${safePage + 1}" ${safePage >= totalPages ? 'disabled' : ''}>Next \u2192</button>
            </div>
        </div>`;
    }

    return `<div class="data-grid" data-market-type="${type}" style="--cols: ${COLS_CSS}">
        <div class="dg-head">${headers}</div>
        ${rows}
        ${pagination}
    </div>`;
}

function attachHandlers(type) {
    const container = document.getElementById(`${type}-table`);
    if (!container) return;
    container.querySelectorAll('[data-sort-key]').forEach(el => {
        el.addEventListener('click', () => {
            const key = el.dataset.sortKey;
            const prev = _sortState[type] || { key: 'edge', dir: 'desc', abs: true };
            if (prev.key === key) {
                _sortState[type] = { key, dir: prev.dir === 'desc' ? 'asc' : 'desc', abs: false };
            } else {
                const col = COLUMNS.find(c => c.key === key);
                _sortState[type] = { key, dir: col && col.num ? 'desc' : 'asc', abs: false };
            }
            _pageState[type] = 1;
            rerender(type);
        });
    });
    container.querySelectorAll('.pagination-btn').forEach(btn => {
        btn.addEventListener('click', () => {
            const page = parseInt(btn.dataset.page);
            if (!isNaN(page)) { _pageState[type] = page; rerender(type); }
        });
    });
}

function rerender(type) {
    const container = document.getElementById(`${type}-table`);
    if (!container || !_marketData[type]) return;
    container.innerHTML = marketsGrid(_marketData[type], type);
    attachHandlers(type);
}

function edgeBarChart(markets, containerId) {
    const el = document.getElementById(containerId);
    if (!el) return;
    if (!markets || markets.length === 0) { el.innerHTML = '<div class="chart-empty">No data</div>'; return; }
    const sorted = [...markets].sort((a, b) => Math.abs(a.edge) - Math.abs(b.edge));
    const edges = sorted.map(m => parseFloat((Math.abs(m.edge) * 100).toFixed(2)));
    const cities = sorted.map(m => m.city);
    const colors = sorted.map(m => m.edge >= 0 ? '#2ecc71' : '#e74c3c');
    const maxEdge = Math.max(...edges);
    Plotly.react(el, [{ type: 'bar', orientation: 'h', x: edges, y: cities, marker: { color: colors }, hovertemplate: '%{y}: %{x:.1f}%<extra></extra>' }], {
        ...PLOTLY_LAYOUT,
        xaxis: { ...PLOTLY_LAYOUT.xaxis, title: { text: 'Edge (%)', font: { color: 'rgba(255,255,255,0.6)', size: 11 } }, tickformat: '.1f', range: [0, maxEdge * 1.15] },
        yaxis: { ...PLOTLY_LAYOUT.yaxis, automargin: true },
        bargap: 0.3,
    }, PLOTLY_CONFIG);
}

function confidenceScatter(markets, containerId, labelId) {
    const el = document.getElementById(containerId);
    if (!el) return;
    const labelEl = document.getElementById(labelId);
    if (labelEl) labelEl.textContent = 'Confidence vs Edge';
    if (!markets || markets.length === 0) { el.innerHTML = '<div class="chart-empty">No data</div>'; return; }
    const edges = markets.map(m => parseFloat((Math.abs(m.edge) * 100).toFixed(2)));
    const confs = markets.map(m => m.confidence);
    const cities = markets.map(m => m.city);
    const colors = markets.map(m => m.edge >= 0 ? '#2ecc71' : '#e74c3c');
    const sortedEdges = [...edges].sort((a, b) => a - b);
    const p90 = sortedEdges[Math.floor(sortedEdges.length * 0.9)] || sortedEdges[sortedEdges.length - 1];
    Plotly.react(el, [{ type: 'scatter', mode: 'markers+text', x: edges, y: confs, text: cities, textposition: 'top center', textfont: { color: 'rgba(255,255,255,0.7)', size: 10 }, marker: { color: colors, size: 10, line: { color: 'rgba(255,255,255,0.3)', width: 1 } }, hovertemplate: '%{text}<br>Edge: %{x:.1f}%<br>Conf: %{y:.1f}<extra></extra>' }], {
        ...PLOTLY_LAYOUT,
        xaxis: { ...PLOTLY_LAYOUT.xaxis, title: { text: 'Edge (%)', font: { color: 'rgba(255,255,255,0.6)', size: 11 } }, tickformat: '.1f', range: [0, Math.max(p90 * 1.2, 10)] },
        yaxis: { ...PLOTLY_LAYOUT.yaxis, title: { text: 'Confidence', font: { color: 'rgba(255,255,255,0.6)', size: 11 } }, range: [Math.min(...confs) - 2, Math.max(...confs) + 3] },
    }, PLOTLY_CONFIG);
}

function edgeHeatmap(history, containerId, labelId) {
    const el = document.getElementById(containerId);
    if (!el) return;
    const labelEl = document.getElementById(labelId);
    if (labelEl) labelEl.textContent = 'Edge Heatmap \u2014 City \u00d7 Date';
    const citySet = new Set(); const dateSet = new Set();
    history.forEach(h => { citySet.add(h.city); dateSet.add(h.scan_date); });
    const cities = [...citySet].sort(); const dates = [...dateSet].sort();
    const lookup = {}; history.forEach(h => { lookup[`${h.city}|${h.scan_date}`] = h.edge; });
    const z = cities.map(city => dates.map(date => { const v = lookup[`${city}|${date}`]; return v !== undefined ? parseFloat((v * 100).toFixed(2)) : null; }));
    Plotly.react(el, [{ type: 'heatmap', x: dates, y: cities, z, zmid: 0, colorscale: [[0, '#e74c3c'], [0.5, '#1a2332'], [1, '#2ecc71']], colorbar: { tickformat: '+.0f', ticksuffix: '%', thickness: 12, len: 0.8, tickfont: { color: 'rgba(255,255,255,0.7)', size: 10 } }, hovertemplate: '%{y} on %{x}<br>Edge: %{z:+.1f}%<extra></extra>' }], {
        ...PLOTLY_LAYOUT,
        xaxis: { ...PLOTLY_LAYOUT.xaxis, type: 'category', automargin: true },
        yaxis: { ...PLOTLY_LAYOUT.yaxis, automargin: true },
    }, PLOTLY_CONFIG);
}

async function renderMarkets(data, type) {
    const { scan_time, markets = [] } = data;
    _marketData[type] = markets;
    _pageState[type] = _pageState[type] || 1;
    const scanTimeEl = document.getElementById(`${type}-scan-time`);
    if (scanTimeEl) scanTimeEl.textContent = scan_time ? `Last scan: ${fmtScanTime(scan_time)}` : '';
    const tableEl = document.getElementById(`${type}-table`);
    if (tableEl) { tableEl.innerHTML = marketsGrid(markets, type); attachHandlers(type); }
    edgeBarChart(markets, `${type}-edge-chart`);
    const heatmapId = `${type}-heatmap-chart`; const labelId = `${type}-chart2-label`;
    try {
        const resp = await fetch(`/api/markets/${type}/history`);
        if (resp.ok) {
            const history = await resp.json();
            const uniqueDates = new Set(history.map(h => h.scan_date));
            if (uniqueDates.size >= 3) edgeHeatmap(history, heatmapId, labelId);
            else confidenceScatter(markets, heatmapId, labelId);
        } else { confidenceScatter(markets, heatmapId, labelId); }
    } catch (_) { confidenceScatter(markets, heatmapId, labelId); }
    const tradeworthy = markets.filter(m => m.edge > 0.07 && m.confidence >= 50);
    const badgeEl = document.getElementById(`${type}-signal-badge`);
    if (badgeEl) {
        const count = tradeworthy.length;
        const label = count === 1 ? '1 trade-worthy signal' : `${count} trade-worthy signals`;
        const variant = count > 0 ? 'signal-badge signal-badge-active' : 'signal-badge signal-badge-none';
        badgeEl.innerHTML = `<span class="${variant}">${label}</span>`;
    }
    window._signalCounts[type] = { total: markets.length, tradeworthy: tradeworthy.length };
}

export { renderMarkets };
