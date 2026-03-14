// dashboard/static/js/portfolio.js
// Renders Open Positions: City | Side | Qty | Entry | P&L (5 cols, paginated)

const PAGE_SIZE = 10;
let _currentPage = 1;
let _cachedPositions = [];

function fmtDollar(n, signed = false) {
    const abs = Math.abs(n).toFixed(2);
    if (signed && n > 0) return `+$${abs}`;
    if (n < 0)           return `-$${abs}`;
    return `$${abs}`;
}

function positionsTable(positions, page) {
    const headers = `
        <tr>
          <th>City</th><th>Side</th>
          <th class="num">Qty</th><th class="num">Entry</th><th class="num">P&amp;L</th>
        </tr>`;

    if (!positions || positions.length === 0) {
        return `
        <table class="data-table">
          <thead>${headers}</thead>
          <tbody class="table-empty">
            <tr><td colspan="5">No open positions</td></tr>
          </tbody>
        </table>`.trim();
    }

    const total = positions.length;
    const totalPages = Math.ceil(total / PAGE_SIZE);
    const safePage = Math.max(1, Math.min(page, totalPages));
    const start = (safePage - 1) * PAGE_SIZE;
    const slice = positions.slice(start, start + PAGE_SIZE);

    const rows = slice.map(p => {
        const pnlClass = p.pnl > 0 ? 'val-positive' : p.pnl < 0 ? 'val-negative' : 'val-neutral';
        const rowClass = p.pnl > 0 ? 'pnl-positive' : p.pnl < 0 ? 'pnl-negative' : '';
        return `
        <tr class="${rowClass}">
          <td>${p.city || p.ticker}</td>
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
      <thead>${headers}</thead>
      <tbody>${rows}</tbody>
    </table>${pagination}`.trim();
}

function attachPaginationHandlers() {
    const container = document.getElementById('open-positions-table');
    if (!container) return;
    container.querySelectorAll('[data-paginator="positions"] .pagination-btn').forEach(btn => {
        btn.addEventListener('click', () => {
            const page = parseInt(btn.dataset.page);
            if (!isNaN(page)) {
                _currentPage = page;
                renderPortfolio({ open_positions: _cachedPositions });
            }
        });
    });
}

function renderPortfolio(data) {
    const { open_positions = [] } = data;
    _cachedPositions = open_positions;

    const posTableEl = document.getElementById('open-positions-table');
    if (posTableEl) {
        posTableEl.innerHTML = positionsTable(open_positions, _currentPage);
        attachPaginationHandlers();
    }
}

export { renderPortfolio };
