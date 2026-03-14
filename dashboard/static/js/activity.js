// dashboard/static/js/activity.js
// Activity log: recent trades from trades.db (paginated)

const PAGE_SIZE = 10;
let _currentPage = 1;
let _cachedActivity = [];

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

function activityTable(items, page) {
    const headers = `
        <tr>
          <th>Time</th><th>City</th><th>Action</th>
          <th class="num">Price</th><th class="num">Qty</th><th>Outcome</th><th class="num">P&amp;L</th>
        </tr>`;

    if (!items || items.length === 0) {
        return `
        <table class="data-table">
          <thead>${headers}</thead>
          <tbody class="table-empty">
            <tr><td colspan="7">No activity yet</td></tr>
          </tbody>
        </table>`.trim();
    }

    const total = items.length;
    const totalPages = Math.ceil(total / PAGE_SIZE);
    const safePage = Math.max(1, Math.min(page, totalPages));
    const start = (safePage - 1) * PAGE_SIZE;
    const slice = items.slice(start, start + PAGE_SIZE);

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

        return `
        <tr>
          <td class="mono" style="white-space:nowrap">${fmtTime(t.time)}</td>
          <td>${t.city || t.ticker}</td>
          <td class="${actionClass}">${sideLabel}</td>
          <td class="num mono">${t.price}\u00a2</td>
          <td class="num mono">${t.qty}</td>
          <td>${outcomeHtml}</td>
          <td class="num mono">${pnlHtml}</td>
        </tr>`.trim();
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

    return `
    <table class="data-table">
      <thead>${headers}</thead>
      <tbody>${rows}</tbody>
    </table>${pagination}`.trim();
}

function attachPaginationHandlers() {
    const container = document.getElementById('activity-table');
    if (!container) return;
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
        el.innerHTML = activityTable(_cachedActivity, _currentPage);
        attachPaginationHandlers();
    }
}

function renderActivity(data) {
    _cachedActivity = data || [];
    _currentPage = 1;
    rerender();
}

export { renderActivity };
