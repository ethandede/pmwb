// dashboard/static/js/portfolio.js
// Renders the Open Positions section from /api/portfolio response data.

// ─── Formatters ────────────────────────────────────────────────────────────

function fmtDollar(n, signed = false) {
    const abs = Math.abs(n).toFixed(2);
    if (signed && n > 0) return `+$${abs}`;
    if (n < 0)           return `-$${abs}`;
    return `$${abs}`;
}

// ─── HTML builders ─────────────────────────────────────────────────────────

function positionsTable(positions) {
    if (!positions || positions.length === 0) {
        return `
        <table class="data-table">
          <thead>
            <tr>
              <th>Ticker</th><th>City</th><th>Side</th>
              <th class="num">Qty</th><th class="num">Entry</th>
              <th class="num">Exposure</th><th class="num">P&amp;L</th><th class="num">Fees</th>
            </tr>
          </thead>
          <tbody class="table-empty">
            <tr><td colspan="8">No open positions</td></tr>
          </tbody>
        </table>`.trim();
    }

    const rows = positions.map(p => {
        const rowClass = p.pnl > 0 ? 'pnl-positive' : p.pnl < 0 ? 'pnl-negative' : '';
        const pnlClass = p.pnl > 0 ? 'val-positive' : p.pnl < 0 ? 'val-negative' : 'val-neutral';
        return `
        <tr class="${rowClass}">
          <td class="mono">${p.ticker}</td>
          <td>${p.city}</td>
          <td>${p.side}</td>
          <td class="num mono">${p.qty}</td>
          <td class="num mono">${fmtDollar(p.entry)}</td>
          <td class="num mono">${fmtDollar(p.exposure)}</td>
          <td class="num mono ${pnlClass}">${fmtDollar(p.pnl, true)}</td>
          <td class="num mono">${fmtDollar(p.fees)}</td>
        </tr>`.trim();
    }).join('\n');

    return `
    <table class="data-table">
      <thead>
        <tr>
          <th>Ticker</th><th>City</th><th>Side</th>
          <th class="num">Qty</th><th class="num">Entry</th>
          <th class="num">Exposure</th><th class="num">P&amp;L</th><th class="num">Fees</th>
        </tr>
      </thead>
      <tbody>${rows}</tbody>
    </table>`.trim();
}

// ─── Main render ───────────────────────────────────────────────────────────

function renderPortfolio(data) {
    const { open_positions = [] } = data;

    // Positions table
    const posTableEl = document.getElementById('open-positions-table');
    if (posTableEl) {
        posTableEl.innerHTML = positionsTable(open_positions);
    }

    // Summary caption
    const captionEl = document.getElementById('open-positions-caption');
    if (captionEl) {
        const costBasis  = open_positions.reduce((s, p) => s + p.entry * p.qty, 0);
        const exposure   = open_positions.reduce((s, p) => s + p.exposure, 0);
        const unrealized = open_positions.reduce((s, p) => s + p.pnl, 0);
        const feesTotal  = open_positions.reduce((s, p) => s + p.fees, 0);

        captionEl.innerHTML = `
        <span class="caption">
          Cost basis: ${fmtDollar(costBasis)} &nbsp;|&nbsp;
          Exposure: ${fmtDollar(exposure)} &nbsp;|&nbsp;
          Unrealized: <span class="${unrealized >= 0 ? 'val-positive' : 'val-negative'}">${fmtDollar(unrealized, true)}</span> &nbsp;|&nbsp;
          Fees: ${fmtDollar(feesTotal)}
        </span>`.trim();
    }
}

// ─── Exports ────────────────────────────────────────────────────────────────

export { renderPortfolio };
