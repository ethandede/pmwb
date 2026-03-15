// dashboard/static/js/portfolio.js
// Renders positions: Live (settling) + Paper sections when in paper mode

const PAGE_SIZE = 10;
let _currentPage = 1;
let _liveCurrentPage = 1;
let _cachedPositions = [];
let _cachedLivePositions = [];
let _sortState = { key: 'settles', dir: 'asc', abs: false };
let _liveSortState = { key: 'settles', dir: 'asc', abs: false };

const COLUMNS = [
    { key: 'settles',       label: 'Event',    num: false },
    { key: 'city',          label: 'City',     num: false },
    { key: 'bet',           label: 'Bet',      num: false },
    { key: 'forecast_high', label: 'Fcast',    num: true  },
    { key: 'likely',        label: 'Likely',   num: false },
    { key: 'if_win',        label: 'If Win',   num: true  },
    { key: 'if_lose',       label: 'If Lose',  num: true  },
];

const COLS_CSS = COLUMNS.map(c => c.num ? 'auto' : '1fr').join(' ');

function fmtDollar(n, signed = false) {
    const abs = Math.abs(n).toFixed(2);
    if (signed && n > 0) return `+$${abs}`;
    if (n < 0)           return `-$${abs}`;
    return `$${abs}`;
}

function buildGrid(positions, page, sortState, paginatorId) {
    const st = sortState;
    const headers = COLUMNS.map(c => {
        const arrow = st.key === c.key ? (st.dir === 'asc' ? ' \u2191' : ' \u2193') : '';
        return `<span${c.num ? ' class="num"' : ''} data-sort-key="${c.key}" style="cursor:pointer;user-select:none">${c.label}${arrow}</span>`;
    }).join('');

    if (!positions || positions.length === 0) {
        return `<div class="data-grid" style="--cols: ${COLS_CSS}">
            <div class="dg-head">${headers}</div>
            <div class="dg-empty">No open positions</div>
        </div>`;
    }

    positions.forEach(p => {
        p.bet = `${p.side} ${p.contract || ''}`.trim();
        const cost = p.entry || 0;
        p.if_win = cost > 0 ? (1.0 - cost) * p.qty : p.qty * 0.50;
        p.if_lose = cost > 0 ? -(cost * p.qty) : 0;
    });

    const sorted = [...positions].sort((a, b) => {
        let va = a[st.key], vb = b[st.key];
        if (va === null || va === undefined) va = typeof vb === 'string' ? '' : -Infinity;
        if (vb === null || vb === undefined) vb = typeof va === 'string' ? '' : -Infinity;
        if (st.abs) { va = Math.abs(va); vb = Math.abs(vb); }
        if (typeof va === 'string') return st.dir === 'asc' ? va.localeCompare(vb) : vb.localeCompare(va);
        return st.dir === 'asc' ? va - vb : vb - va;
    });

    const total = sorted.length;
    const totalPages = Math.ceil(total / PAGE_SIZE);
    const safePage = Math.max(1, Math.min(page, totalPages));
    const start = (safePage - 1) * PAGE_SIZE;
    const slice = sorted.slice(start, start + PAGE_SIZE);

    const rows = slice.map(p => {
        const settles = p.settles ? p.settles.slice(5) : '\u2014';
        const fcast = p.forecast_high !== null ? `${p.forecast_high}\u00b0` : '\u2014';
        const rowClass = p.likely === 'WIN' ? 'pnl-positive' : p.likely === 'LOSS' ? 'pnl-negative' : '';
        return `<div class="dg-row ${rowClass}">
          <span class="mono" data-label="Event">${settles}</span>
          <span data-label="City">${p.city}</span>
          <span class="mono" data-label="Bet">${p.bet}</span>
          <span class="num mono" data-label="Fcast">${fcast}</span>
          <span class="${p.likely === 'WIN' ? 'val-positive' : p.likely === 'LOSS' ? 'val-negative' : ''}" data-label="Likely">${p.likely || '\u2014'}</span>
          <span class="num mono val-positive" data-label="If Win">+${fmtDollar(p.if_win)}</span>
          <span class="num mono val-negative" data-label="If Lose">${fmtDollar(p.if_lose)}</span>
        </div>`;
    }).join('\n');

    let pagination = '';
    if (totalPages > 1) {
        pagination = `
        <div class="pagination" data-paginator="${paginatorId}">
            <span class="pagination-info">Showing ${start + 1}\u2013${Math.min(start + PAGE_SIZE, total)} of ${total}</span>
            <div class="pagination-buttons">
                <button class="pagination-btn" data-page="${safePage - 1}" ${safePage <= 1 ? 'disabled' : ''}>\u2190 Prev</button>
                <button class="pagination-btn" data-page="${safePage + 1}" ${safePage >= totalPages ? 'disabled' : ''}>Next \u2192</button>
            </div>
        </div>`;
    }

    return `<div class="data-grid" style="--cols: ${COLS_CSS}">
        <div class="dg-head">${headers}</div>
        ${rows}
        ${pagination}
    </div>`;
}

function attachSortHandlers(container, sortState, onSort) {
    if (!container) return;
    container.querySelectorAll('[data-sort-key]').forEach(el => {
        el.addEventListener('click', () => {
            const key = el.dataset.sortKey;
            if (sortState.key === key) {
                sortState.dir = sortState.dir === 'desc' ? 'asc' : 'desc';
            } else {
                const col = COLUMNS.find(c => c.key === key);
                sortState.key = key;
                sortState.dir = col && col.num ? 'desc' : 'asc';
                sortState.abs = false;
            }
            onSort();
        });
    });
}

function attachPaginationHandlers(container, paginatorId, onPage) {
    if (!container) return;
    container.querySelectorAll(`[data-paginator="${paginatorId}"] .pagination-btn`).forEach(btn => {
        btn.addEventListener('click', () => {
            const page = parseInt(btn.dataset.page);
            if (!isNaN(page)) onPage(page);
        });
    });
}

function rerenderPaper() {
    const el = document.getElementById('paper-positions-table');
    if (el) {
        el.innerHTML = buildGrid(_cachedPositions, _currentPage, _sortState, 'paper-positions');
        attachSortHandlers(el, _sortState, () => { _currentPage = 1; rerenderPaper(); });
        attachPaginationHandlers(el, 'paper-positions', (p) => { _currentPage = p; rerenderPaper(); });
    }
}

function rerenderLive() {
    const el = document.getElementById('live-positions-table');
    if (el) {
        el.innerHTML = buildGrid(_cachedLivePositions, _liveCurrentPage, _liveSortState, 'live-positions');
        attachSortHandlers(el, _liveSortState, () => { _liveCurrentPage = 1; rerenderLive(); });
        attachPaginationHandlers(el, 'live-positions', (p) => { _liveCurrentPage = p; rerenderLive(); });
    }
}

function renderPortfolio(data) {
    const mode = data.mode || 'LIVE';
    const livePositions = data.live_positions || [];
    const paperPositions = data.paper_positions || [];
    const openPositions = data.open_positions || [];

    const container = document.getElementById('open-positions-table');
    if (!container) return;

    if (mode === 'PAPER') {
        _cachedPositions = paperPositions.length > 0 ? paperPositions : openPositions;
        _cachedLivePositions = livePositions;
        _currentPage = 1;
        _liveCurrentPage = 1;

        let html = '';
        if (livePositions.length > 0) {
            html += `<div class="positions-section">
                <h3 class="positions-section-label">Live Positions \u2014 Settling <span class="positions-count">${livePositions.length}</span></h3>
                <p class="section-desc">Real positions placed before paper mode. Will clear once Kalshi settles them.</p>
                <div id="live-positions-table"></div>
            </div>
            <hr class="we-divider">`;
        }
        html += `<div class="positions-section">
            <h3 class="positions-section-label">Paper Positions <span class="positions-count">${_cachedPositions.length}</span></h3>
            <div id="paper-positions-table"></div>
        </div>`;

        container.innerHTML = html;
        if (livePositions.length > 0) rerenderLive();
        rerenderPaper();
    } else {
        _cachedPositions = openPositions;
        _currentPage = 1;
        container.innerHTML = `<div id="paper-positions-table"></div>`;
        rerenderPaper();
    }
}

export { renderPortfolio };
