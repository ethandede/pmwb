// dashboard/static/js/portfolio.js
// Renders the Portfolio section from /api/portfolio response data.

// ─── Formatters ────────────────────────────────────────────────────────────

/**
 * Format a number as a dollar string.
 * Positive values get a leading '+', negative get '-'.
 * Zero is formatted without a sign prefix.
 *
 * @param {number} n
 * @param {boolean} [signed=false] - include +/- prefix
 * @returns {string}
 */
function fmtDollar(n, signed = false) {
    const abs = Math.abs(n).toFixed(2);
    if (signed && n > 0) return `+$${abs}`;
    if (n < 0)           return `-$${abs}`;
    return `$${abs}`;
}

/**
 * Format a percentage to one decimal place.
 * @param {number} n
 * @returns {string}
 */
function fmtPct(n) {
    return `${Number(n).toFixed(1)}%`;
}

// ─── HTML builders ─────────────────────────────────────────────────────────

/**
 * Build a metric card HTML string.
 *
 * @param {string} label   - Upper label text
 * @param {string} value   - Main displayed value
 * @param {string} subtitle
 * @param {string} variant - One of: 'neutral' | 'positive' | 'negative' | 'accent'
 * @returns {string}
 */
function metricCard(label, value, subtitle, variant = 'neutral') {
    return `
    <div class="metric-card metric-${variant}">
      <div class="metric-label">${label}</div>
      <div class="metric-value">${value}</div>
      <div class="metric-subtitle">${subtitle}</div>
    </div>`.trim();
}

/**
 * Build the open-positions <table> HTML string (no wrapper).
 *
 * @param {Array} positions
 * @returns {string}
 */
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

/**
 * Build the resting-orders <table> HTML string (no wrapper).
 *
 * @param {Array} orders
 * @returns {string}
 */
function ordersTable(orders) {
    if (!orders || orders.length === 0) {
        return `
        <table class="data-table">
          <thead>
            <tr>
              <th>Ticker</th><th>Action</th><th>Side</th>
              <th class="num">Remaining</th><th class="num">Price</th><th>Created</th>
            </tr>
          </thead>
          <tbody class="table-empty">
            <tr><td colspan="6">No resting orders</td></tr>
          </tbody>
        </table>`.trim();
    }

    const rows = orders.map(o => `
    <tr>
      <td class="mono">${o.ticker}</td>
      <td>${o.action}</td>
      <td>${o.side}</td>
      <td class="num mono">${o.remaining}</td>
      <td class="num mono">${fmtDollar(o.price)}</td>
      <td>${o.created}</td>
    </tr>`.trim()).join('\n');

    return `
    <table class="data-table">
      <thead>
        <tr>
          <th>Ticker</th><th>Action</th><th>Side</th>
          <th class="num">Remaining</th><th class="num">Price</th><th>Created</th>
        </tr>
      </thead>
      <tbody>${rows}</tbody>
    </table>`.trim();
}

// ─── Main render ───────────────────────────────────────────────────────────

/**
 * Render the full Portfolio section from an /api/portfolio response.
 *
 * Expected shape:
 *   { balance, settled, open_positions, resting_orders }
 *
 * @param {Object} data
 */
function renderPortfolio(data) {
    const { balance, settled, open_positions = [], resting_orders = [] } = data;

    // ── 1. Balance row ──────────────────────────────────────────────────
    const balanceEl = document.getElementById('portfolio-balance');
    if (balanceEl) {
        balanceEl.innerHTML = `
          ${metricCard('Cash', fmtDollar(balance.cash), `${fmtPct(balance.deployed_pct)} deployed`, 'neutral')}
          ${metricCard('Positions', fmtDollar(balance.positions), `${open_positions.length} open`, 'neutral')}
          ${metricCard('Equity', fmtDollar(balance.equity), 'Total value', 'accent')}
          ${metricCard('Deployed', fmtPct(balance.deployed_pct), 'Of total equity', 'neutral')}
        `.trim();
    }

    // ── 2. Settled performance row ──────────────────────────────────────
    const settledEl = document.getElementById('portfolio-settled');
    if (settledEl) {
        const grossPnlVariant = settled.gross_pnl >= 0 ? 'positive' : 'negative';
        const netPnlVariant   = settled.net_pnl   >= 0 ? 'positive' : 'negative';

        // Fee drag as % of gross (guard against division by zero)
        const feePct = settled.gross_pnl !== 0
            ? `${((settled.fees / Math.abs(settled.gross_pnl)) * 100).toFixed(0)}% of gross`
            : '—';

        settledEl.innerHTML = `
          ${metricCard('Gross P&amp;L', fmtDollar(settled.gross_pnl, true), `${settled.total_settled} settled`, grossPnlVariant)}
          ${metricCard('Fees Paid', fmtDollar(settled.fees), feePct, 'neutral')}
          ${metricCard('Net P&amp;L', fmtDollar(settled.net_pnl, true), 'After fees', netPnlVariant)}
          ${metricCard('Hit Rate', fmtPct(settled.hit_rate), `${settled.wins}W / ${settled.losses}L`, 'neutral')}
        `.trim();
    }

    // ── 3. Open positions table ─────────────────────────────────────────
    const posTableEl = document.getElementById('open-positions-table');
    if (posTableEl) {
        posTableEl.innerHTML = positionsTable(open_positions);
    }

    // ── 4. Summary caption ──────────────────────────────────────────────
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

    // ── 5. Resting orders table ─────────────────────────────────────────
    const ordTableEl = document.getElementById('resting-orders-table');
    if (ordTableEl) {
        ordTableEl.innerHTML = ordersTable(resting_orders);
    }

    // Update resting count badge
    const restingCountEl = document.getElementById('resting-count');
    if (restingCountEl) {
        restingCountEl.textContent = resting_orders.length;
    }
}

// ─── Exports ────────────────────────────────────────────────────────────────

export { renderPortfolio };
