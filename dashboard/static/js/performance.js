// dashboard/static/js/performance.js
// Triptych card renderers: equity sparkline, positions count, unrealized P&L.

const PLOTLY_LAYOUT = {
    paper_bgcolor: 'rgba(0,0,0,0)',
    plot_bgcolor: 'rgba(0,0,0,0)',
    font: { color: 'rgba(255,255,255,0.85)', family: 'Roboto, sans-serif', size: 12 },
    margin: { l: 0, r: 0, t: 0, b: 0 },
    xaxis: { visible: false },
    yaxis: { visible: false },
};

const PLOTLY_CONFIG = { displayModeBar: false, responsive: true, staticPlot: true };

function equitySparkline(curve, containerId) {
    const el = document.getElementById(containerId);
    if (!el || !curve || curve.length === 0) return;

    const dates = curve.map(d => d.date);
    const equities = curve.map(d => d.equity);

    const trace = {
        x: dates, y: equities, type: 'scatter', mode: 'lines',
        line: { color: '#10b981', width: 2 },
        fill: 'tozeroy', fillcolor: 'rgba(16, 185, 129, 0.15)',
        hoverinfo: 'skip',
    };

    const layout = {
        ...PLOTLY_LAYOUT, height: 50,
        yaxis: { visible: false, range: [Math.min(...equities) * 0.98, Math.max(...equities) * 1.02] },
    };

    Plotly.newPlot(el, [trace], layout, PLOTLY_CONFIG);
}

async function renderHealthBadge(containerId) {
    const el = document.getElementById(containerId);
    if (!el) return;

    try {
        const resp = await fetch('/api/health');
        const data = await resp.json();
        const status = data.status || data.overall || 'unknown';
        const label = status.toUpperCase();
        el.innerHTML = `<div class="health-badge health-${status}">${label}</div>`;
    } catch (_) {
        el.innerHTML = '<span style="color:rgba(255,255,255,0.3);font-size:11px;">Health check unavailable</span>';
    }
}

function renderTriptych(portfolio, performance, config) {
    const { balance = {}, open_positions = [], live_positions = [], paper_positions = [], mode = 'LIVE' } = portfolio;
    const maxPositions = (config && config.max_positions_total) || 15;

    // Card 1: Equity
    const eqVal = document.getElementById('tri-equity');
    const eqDet = document.getElementById('tri-equity-detail');
    if (eqVal) eqVal.textContent = `$${(balance.equity || 0).toFixed(2)}`;
    if (eqDet) eqDet.textContent = `$${(balance.cash || 0).toFixed(0)} cash + $${(balance.positions || 0).toFixed(0)} positions`;

    // Card 2: Open Positions count — show live positions in paper mode if they exist
    const posVal = document.getElementById('tri-positions');
    const posDet = document.getElementById('tri-positions-detail');
    const displayPositions = mode === 'PAPER' && live_positions.length > 0 ? live_positions : open_positions;
    const totalValue = displayPositions.reduce((s, p) => s + (p.value || 0), 0);
    const paperCount = paper_positions.length;
    if (posVal) posVal.textContent = `${displayPositions.length} / ${maxPositions}`;
    if (posDet) {
        let detail = totalValue > 0 ? `$${totalValue.toFixed(2)} market value` : 'No open exposure';
        if (mode === 'PAPER' && paperCount > 0) detail += ` + ${paperCount} paper`;
        posDet.textContent = detail;
    }

    // Card 3: Unrealized P&L (from trades.db cost basis vs current exposure)
    const pnlVal = document.getElementById('tri-pnl');
    const pnlDet = document.getElementById('tri-pnl-detail');
    const unrealized = open_positions.reduce((s, p) => s + (p.pnl || 0), 0);
    const totalFees = open_positions.reduce((s, p) => s + (p.fees || 0), 0);
    if (pnlVal) {
        pnlVal.textContent = `${unrealized >= 0 ? '+' : ''}$${unrealized.toFixed(2)}`;
        pnlVal.className = `triptych-value ${unrealized >= 0 ? 'val-positive' : 'val-negative'}`;
    }
    if (pnlDet) pnlDet.textContent = totalFees > 0 ? `Fees: $${totalFees.toFixed(2)}` : '';

    // Sparkline
    if (performance && performance.equity_curve && performance.equity_curve.length > 0) {
        equitySparkline(performance.equity_curve, 'equity-sparkline');
    }

    // Health badge
    renderHealthBadge('health-badge');
}

function renderFeeSummary(data) {
    const el = document.getElementById('fee-summary');
    if (!el || !data) return;

    const total = data.maker_trades + data.taker_trades;
    const makerPct = total > 0 ? (data.maker_trades / total * 100).toFixed(0) : '0';

    el.innerHTML = `
        <div class="metric-row">
            <div class="metric-card metric-negative">
                <div class="metric-label">Total Fees</div>
                <div class="metric-value mono">$${data.total_fees_paid.toFixed(2)}</div>
            </div>
            <div class="metric-card metric-positive">
                <div class="metric-label">Fee Savings</div>
                <div class="metric-value mono">$${data.fee_savings.toFixed(2)}</div>
            </div>
            <div class="metric-card metric-neutral">
                <div class="metric-label">Maker Rate</div>
                <div class="metric-value mono">${data.maker_trades}/${total} (${makerPct}%)</div>
            </div>
        </div>`;
}

function renderFeeChart(data) {
    const el = document.getElementById('fee-chart');
    if (!el || !data || data.length === 0) return;

    const dates = data.map(d => d.date);
    const pnl = data.map(d => d.cumulative_pnl);
    const fees = data.map(d => d.cumulative_fees);

    const tracePnl = {
        x: dates, y: pnl, type: 'scatter', mode: 'lines',
        name: 'Realized P&L',
        line: { color: '#10b981', width: 2 },
    };
    const traceFees = {
        x: dates, y: fees, type: 'scatter', mode: 'lines',
        name: 'Cumulative Fees',
        line: { color: '#ef4444', width: 2, dash: 'dot' },
    };

    const layout = {
        paper_bgcolor: 'rgba(0,0,0,0)',
        plot_bgcolor: 'rgba(0,0,0,0)',
        font: { color: 'rgba(255,255,255,0.85)', family: 'Roboto, sans-serif', size: 12 },
        margin: { l: 50, r: 20, t: 10, b: 40 },
        xaxis: { showgrid: false, color: 'rgba(255,255,255,0.4)' },
        yaxis: {
            showgrid: true, gridcolor: 'rgba(255,255,255,0.08)',
            color: 'rgba(255,255,255,0.4)',
            tickprefix: '$',
        },
        legend: {
            orientation: 'h', y: -0.15,
            font: { size: 11 },
        },
        showlegend: true,
    };

    Plotly.newPlot(el, [tracePnl, traceFees], layout, PLOTLY_CONFIG);
}

export { renderTriptych, renderFeeSummary, renderFeeChart };
