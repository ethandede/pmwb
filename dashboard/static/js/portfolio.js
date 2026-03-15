// dashboard/static/js/portfolio.js
// Renders Open Positions: City | Side | Qty | Entry | P&L (5 cols, sortable, paginated)

const PAGE_SIZE = 10;
let _currentPage = 1;
let _cachedPositions = [];
let _sortState = { key: 'settles', dir: 'asc', abs: false };

const COLUMNS = [
    { key: 'settles', label: 'Settles', num: false },
    { key: 'city',    label: 'City',    num: false },
    { key: 'side',    label: 'Side',    num: false },
    { key: 'qty',     label: 'Qty',     num: true  },
    { key: 'entry',   label: 'Entry',   num: true  },
    { key: 'pnl',     label: 'P&L',     num: true  },
];

function fmtDollar(n, signed = false) {
    const abs = Math.abs(n).toFixed(2);
    if (signed && n > 0) return `+$${abs}`;
    if (n < 0)           return `-$${abs}`;
    return `$${abs}`;
}

function positionsTable(positions, page) {
    const st = _sortState;
    const headers = COLUMNS.map(c => {
        const arrow = st.key === c.key ? (st.dir === 'asc' ? ' \u2191' : ' \u2193') : '';
        return `<th${c.num ? ' class="num"' : ''} data-sort-key="${c.key}" style="cursor:pointer;user-select:none">${c.label}${arrow}</th>`;
    }).join('');

    if (!positions || positions.length === 0) {
        return `
        <table class="data-table">
          <thead><tr>${headers}</tr></thead>
          <tbody class="table-empty">
            <tr><td colspan="6">No open positions</td></tr>
          </tbody>
        </table>`.trim();
    }

    // Sort
    const sorted = [...positions].sort((a, b) => {
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

    const rows = slice.map(p => {
        const pnlClass = p.pnl > 0 ? 'val-positive' : p.pnl < 0 ? 'val-negative' : 'val-neutral';
        const rowClass = p.pnl > 0 ? 'pnl-positive' : p.pnl < 0 ? 'pnl-negative' : '';
        const settles = p.settles ? p.settles.slice(5) : '\u2014';  // "03-14" from "2026-03-14"
        return `
        <tr class="${rowClass}">
          <td class="mono">${settles}</td>
          <td>${p.city}</td>
          <td>${p.side}</td>
          <td class="num mono">${p.qty}</td>
          <td class="num mono">${p.entry > 0 ? fmtDollar(p.entry) : '\u2014'}</td>
          <td class="num mono ${pnlClass}">${p.entry > 0 ? fmtDollar(p.pnl, true) : '\u2014'}</td>
        </tr>`.trim();
    }).join('\n');

    let pagination = '';
    if (totalPages > 1) {
        pagination = `
        <div class="pagination" data-paginator="positions">
            <span class="pagination-info">Showing ${start + 1}\u2013${Math.min(start + PAGE_SIZE, total)} of ${total}</span>
            <div class="pagination-buttons">
                <button class="pagination-btn" data-page="${safePage - 1}" ${safePage <= 1 ? 'disabled' : ''}>\u2190 Prev</button>
                <button class="pagination-btn" data-page="${safePage + 1}" ${safePage >= totalPages ? 'disabled' : ''}>Next \u2192</button>
            </div>
        </div>`;
    }

    return `
    <table class="data-table">
      <thead><tr>${headers}</tr></thead>
      <tbody>${rows}</tbody>
    </table>${pagination}`.trim();
}

function attachHandlers() {
    const container = document.getElementById('open-positions-table');
    if (!container) return;

    container.querySelectorAll('th[data-sort-key]').forEach(th => {
        th.addEventListener('click', () => {
            const key = th.dataset.sortKey;
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

    container.querySelectorAll('[data-paginator="positions"] .pagination-btn').forEach(btn => {
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
    const el = document.getElementById('open-positions-table');
    if (el) {
        el.innerHTML = positionsTable(_cachedPositions, _currentPage);
        attachHandlers();
    }
}

function renderPortfolio(data) {
    const { open_positions = [] } = data;
    _cachedPositions = open_positions;
    _currentPage = 1;
    rerender();
}

export { renderPortfolio };
