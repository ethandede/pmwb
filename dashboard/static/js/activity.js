// dashboard/static/js/activity.js
// Activity log: recent trades (sortable, paginated grid)

const PAGE_SIZE = 10;
let _currentPage = 1;
let _cachedActivity = [];
let _sortState = { key: 'time', dir: 'desc', abs: false };

const COLUMNS = [
    { key: 'time',       label: 'Time',     num: false },
    { key: 'city',       label: 'City',     num: false },
    { key: 'action',     label: 'Action',   num: false },
    { key: 'price',      label: 'Price',    num: true  },
    { key: 'qty',        label: 'Qty',      num: true  },
    { key: 'edge',       label: 'Edge',     num: true  },
    { key: 'confidence', label: 'Conf',     num: true  },
    { key: 'outcome',    label: 'Outcome',  num: false },
    { key: 'pnl',        label: 'P&L',      num: true  },
];

const COLS_CSS = COLUMNS.map(c => c.num ? 'auto' : '1fr').join(' ');

function fmtTime(isoStr) {
    if (!isoStr) return '\u2014';
    try {
        const d = new Date(isoStr);
        return d.toLocaleString('en-US', {
            month: 'short', day: 'numeric',
            hour: '2-digit', minute: '2-digit',
        });
    } catch (_) { return isoStr; }
}

function fmtEdge(n) {
    if (n === null || n === undefined) return '\u2014';
    const pct = (n * 100).toFixed(1);
    return n >= 0 ? `+${pct}%` : `${pct}%`;
}

function activityGrid(items, page) {
    const st = _sortState;
    const headers = COLUMNS.map(c => {
        const arrow = st.key === c.key ? (st.dir === 'asc' ? ' \u2191' : ' \u2193') : '';
        return `<span${c.num ? ' class="num"' : ''} data-sort-key="${c.key}" style="cursor:pointer;user-select:none">${c.label}${arrow}</span>`;
    }).join('');

    if (!items || items.length === 0) {
        return `<div class="data-grid" style="--cols: ${COLS_CSS}">
            <div class="dg-head">${headers}</div>
            <div class="dg-empty">No activity yet</div>
        </div>`;
    }

    const sorted = [...items].sort((a, b) => {
        let va = a[st.key], vb = b[st.key];
        if (va === null || va === undefined) va = st.num ? -Infinity : '';
        if (vb === null || vb === undefined) vb = st.num ? -Infinity : '';
        if (st.abs) { va = Math.abs(va); vb = Math.abs(vb); }
        if (typeof va === 'string') return st.dir === 'asc' ? va.localeCompare(vb) : vb.localeCompare(va);
        return st.dir === 'asc' ? va - vb : vb - va;
    });

    const total = sorted.length;
    const totalPages = Math.ceil(total / PAGE_SIZE);
    const safePage = Math.max(1, Math.min(page, totalPages));
    const start = (safePage - 1) * PAGE_SIZE;
    const slice = sorted.slice(start, start + PAGE_SIZE);

    const rows = slice.map(t => {
        const actionClass = t.action === 'BUY' ? 'val-positive' : 'val-negative';
        const sideLabel = `${t.action} ${t.side}`;

        let outcomeHtml = '\u2014';
        if (t.outcome === 'win') outcomeHtml = '<span class="val-positive">WIN</span>';
        else if (t.outcome === 'loss') outcomeHtml = '<span class="val-negative">LOSS</span>';
        else if (t.outcome === 'exited') outcomeHtml = '<span style="color:rgba(255,255,255,0.45)">EXITED</span>';

        let pnlHtml = '\u2014';
        if (t.pnl !== null && t.pnl !== undefined) {
            const cls = t.pnl > 0 ? 'val-positive' : t.pnl < 0 ? 'val-negative' : 'val-neutral';
            const sign = t.pnl > 0 ? '+' : '';
            pnlHtml = `<span class="${cls}">${sign}$${Math.abs(t.pnl).toFixed(2)}</span>`;
        }

        const edgeClass = t.edge > 0 ? 'val-positive' : t.edge < 0 ? 'val-negative' : '';
        const confClass = t.confidence >= 60 ? 'val-positive' : t.confidence >= 40 ? 'val-amber' : t.confidence !== null ? 'val-negative' : '';

        return `<div class="dg-row">
          <span class="mono" data-label="Time" style="white-space:nowrap">${fmtTime(t.time)}</span>
          <span data-label="City">${t.city || t.ticker}</span>
          <span class="${actionClass}" data-label="Action">${sideLabel}</span>
          <span class="num mono" data-label="Price">${t.price}\u00a2</span>
          <span class="num mono" data-label="Qty">${t.qty}</span>
          <span class="num mono ${edgeClass}" data-label="Edge">${fmtEdge(t.edge)}</span>
          <span class="num mono ${confClass}" data-label="Conf">${t.confidence !== null && t.confidence !== undefined ? t.confidence.toFixed(1) : '\u2014'}</span>
          <span data-label="Outcome">${outcomeHtml}</span>
          <span class="num mono" data-label="P&L">${pnlHtml}</span>
        </div>`;
    }).join('\n');

    let pagination = '';
    if (totalPages > 1) {
        pagination = `
        <div class="pagination" data-paginator="activity">
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
    const container = document.getElementById('activity-table');
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

    container.querySelectorAll('[data-paginator="activity"] .pagination-btn').forEach(btn => {
        btn.addEventListener('click', () => {
            const page = parseInt(btn.dataset.page);
            if (!isNaN(page)) {
                _currentPage = page;
                rerender();
            }
        });
    });
}

function rerender() {
    const el = document.getElementById('activity-table');
    if (el) {
        el.innerHTML = activityGrid(_cachedActivity, _currentPage);
        attachHandlers();
    }
}

function renderActivity(data) {
    _cachedActivity = data || [];
    _currentPage = 1;
    rerender();
}

export { renderActivity };
