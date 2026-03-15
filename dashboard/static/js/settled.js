// dashboard/static/js/settled.js
// Settled trades: summary + paginated grid

const PAGE_SIZE = 10;
let _currentPage = 1;
let _cachedTrades = [];
let _sortState = { key: 'time', dir: 'desc', abs: false };

const COLUMNS = [
    { key: 'time',    label: 'Time',    num: false },
    { key: 'city',    label: 'City',    num: false },
    { key: 'side',    label: 'Side',    num: false },
    { key: 'price',   label: 'Entry',   num: true  },
    { key: 'qty',     label: 'Qty',     num: true  },
    { key: 'outcome', label: 'Result',  num: false },
    { key: 'pnl',     label: 'P&L',     num: true  },
];

const COLS_CSS = COLUMNS.map(c => c.num ? 'auto' : '1fr').join(' ');

function fmtTime(isoStr) {
    if (!isoStr) return '\u2014';
    try {
        const d = new Date(isoStr);
        const mm = String(d.getMonth() + 1).padStart(2, '0');
        const dd = String(d.getDate()).padStart(2, '0');
        const hh = String(d.getHours()).padStart(2, '0');
        const mi = String(d.getMinutes()).padStart(2, '0');
        return `${mm}/${dd} ${hh}${mi}`;
    } catch (_) { return isoStr; }
}

function renderSummary(summary) {
    const el = document.getElementById('settled-summary');
    if (!el) return;
    const wins = summary.win || { count: 0, pnl: 0 };
    const losses = summary.loss || { count: 0, pnl: 0 };
    const total = wins.count + losses.count;
    const hitRate = total > 0 ? (wins.count / total * 100).toFixed(1) : '0.0';
    const netPnl = wins.pnl + losses.pnl;
    el.innerHTML = `
        <div class="metric-row">
            <div class="metric-card ${netPnl >= 0 ? 'metric-positive' : 'metric-negative'}">
                <div class="metric-label">Net P&L</div>
                <div class="metric-value mono">${netPnl >= 0 ? '+' : ''}$${Math.abs(netPnl).toFixed(2)}</div>
            </div>
            <div class="metric-card metric-neutral">
                <div class="metric-label">Record</div>
                <div class="metric-value mono">${wins.count}W / ${losses.count}L</div>
            </div>
            <div class="metric-card metric-neutral">
                <div class="metric-label">Hit Rate</div>
                <div class="metric-value mono">${hitRate}%</div>
            </div>
            <div class="metric-card metric-neutral">
                <div class="metric-label">Avg Win / Loss</div>
                <div class="metric-value mono" style="font-size:16px;">$${wins.count > 0 ? (wins.pnl / wins.count).toFixed(2) : '0'} / $${losses.count > 0 ? (losses.pnl / losses.count).toFixed(2) : '0'}</div>
            </div>
        </div>`;
}

function settledGrid(items, page) {
    const st = _sortState;
    const headers = COLUMNS.map(c => {
        const arrow = st.key === c.key ? (st.dir === 'asc' ? ' \u2191' : ' \u2193') : '';
        return `<span${c.num ? ' class="num"' : ''} data-sort-key="${c.key}" style="cursor:pointer;user-select:none">${c.label}${arrow}</span>`;
    }).join('');

    if (!items || items.length === 0) {
        return `<div class="data-grid" style="--cols: ${COLS_CSS}">
            <div class="dg-head">${headers}</div>
            <div class="dg-empty">No settled trades yet</div>
        </div>`;
    }

    const sorted = [...items].sort((a, b) => {
        let va = a[st.key], vb = b[st.key];
        if (va === null || va === undefined) va = st.num ? -Infinity : '';
        if (vb === null || vb === undefined) vb = st.num ? -Infinity : '';
        if (typeof va === 'string') return st.dir === 'asc' ? va.localeCompare(vb) : vb.localeCompare(va);
        return st.dir === 'asc' ? va - vb : vb - va;
    });

    const total = sorted.length;
    const totalPages = Math.ceil(total / PAGE_SIZE);
    const safePage = Math.max(1, Math.min(page, totalPages));
    const start = (safePage - 1) * PAGE_SIZE;
    const slice = sorted.slice(start, start + PAGE_SIZE);

    const rows = slice.map(t => {
        const resultClass = t.outcome === 'win' ? 'val-positive' : 'val-negative';
        const pnlClass = t.pnl > 0 ? 'val-positive' : t.pnl < 0 ? 'val-negative' : 'val-neutral';
        return `<div class="dg-row">
          <span class="mono" data-label="Time" style="white-space:nowrap">${fmtTime(t.time)}</span>
          <span data-label="City">${t.city}</span>
          <span data-label="Side">${t.side}</span>
          <span class="num mono" data-label="Entry">${t.price}\u00a2</span>
          <span class="num mono" data-label="Qty">${t.qty}</span>
          <span class="${resultClass}" data-label="Result">${t.outcome === 'win' ? 'WIN' : 'LOSS'}</span>
          <span class="num mono ${pnlClass}" data-label="P&L">${t.pnl >= 0 ? '+' : ''}$${Math.abs(t.pnl).toFixed(2)}</span>
        </div>`;
    }).join('\n');

    let pagination = '';
    if (totalPages > 1) {
        pagination = `
        <div class="pagination" data-paginator="settled">
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

function attachHandlers() {
    const container = document.getElementById('settled-table');
    if (!container) return;
    container.querySelectorAll('[data-sort-key]').forEach(el => {
        el.addEventListener('click', () => {
            const key = el.dataset.sortKey;
            if (_sortState.key === key) {
                _sortState = { key, dir: _sortState.dir === 'desc' ? 'asc' : 'desc', abs: false };
            } else {
                const col = COLUMNS.find(c => c.key === key);
                _sortState = { key, dir: col && col.num ? 'desc' : 'asc', abs: false };
            }
            _currentPage = 1;
            rerender();
        });
    });
    container.querySelectorAll('[data-paginator="settled"] .pagination-btn').forEach(btn => {
        btn.addEventListener('click', () => {
            const page = parseInt(btn.dataset.page);
            if (!isNaN(page)) { _currentPage = page; rerender(); }
        });
    });
}

function rerender() {
    const el = document.getElementById('settled-table');
    if (el) { el.innerHTML = settledGrid(_cachedTrades, _currentPage); attachHandlers(); }
}

function renderSettled(data) {
    renderSummary(data.summary || {});
    _cachedTrades = data.trades || [];
    _currentPage = 1;
    rerender();
}

export { renderSettled };
