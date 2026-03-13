// dashboard/static/js/performance.js
// Triptych card renderers: equity sparkline, daily P&L bars, health badge.

// ─── Plotly theme constants (mirrors markets.js) ─────────────────────────────

const PLOTLY_LAYOUT = {
    paper_bgcolor: 'rgba(0,0,0,0)',
    plot_bgcolor: 'rgba(0,0,0,0)',
    font: { color: 'rgba(255,255,255,0.85)', family: 'Roboto, sans-serif', size: 12 },
    margin: { l: 0, r: 0, t: 0, b: 0 },
    xaxis: { visible: false },
    yaxis: { visible: false },
};

const PLOTLY_CONFIG = { displayModeBar: false, responsive: true, staticPlot: true };

// ─── Equity Sparkline ────────────────────────────────────────────────────────

function equitySparkline(curve, containerId) {
    const el = document.getElementById(containerId);
    if (!el || !curve || curve.length === 0) return;

    const dates = curve.map(d => d.date);
    const equities = curve.map(d => d.equity);

    const trace = {
        x: dates,
        y: equities,
        type: 'scatter',
        mode: 'lines',
        line: { color: '#10b981', width: 2 },
        fill: 'tozeroy',
        fillcolor: 'rgba(16, 185, 129, 0.15)',
        hoverinfo: 'skip',
    };

    const layout = {
        ...PLOTLY_LAYOUT,
        height: 50,
        yaxis: { visible: false, range: [Math.min(...equities) * 0.98, Math.max(...equities) * 1.02] },
    };

    Plotly.newPlot(el, [trace], layout, PLOTLY_CONFIG);
}

// ─── Daily P&L Bar Chart ─────────────────────────────────────────────────────

function dailyPnlChart(settledDaily, containerId) {
    const el = document.getElementById(containerId);
    if (!el || !settledDaily || settledDaily.length === 0) return;

    const dates = settledDaily.map(d => d.date);
    const pnls = settledDaily.map(d => d.pnl);
    const colors = pnls.map(p => p >= 0 ? '#10b981' : '#ef4444');
    const hoverText = settledDaily.map(d =>
        `${d.date}<br>${d.wins}W / ${d.losses}L<br>P&L: $${d.pnl.toFixed(2)}`
    );

    const trace = {
        x: dates,
        y: pnls,
        type: 'bar',
        marker: { color: colors, line: { width: 0 } },
        hovertemplate: '%{text}<extra></extra>',
        text: hoverText,
    };

    const layout = {
        ...PLOTLY_LAYOUT,
        height: 50,
        xaxis: { visible: false },
        yaxis: { visible: false, zeroline: true, zerolinecolor: 'rgba(255,255,255,0.15)', zerolinewidth: 1 },
        bargap: 0.3,
    };

    Plotly.newPlot(el, [trace], layout, PLOTLY_CONFIG);
}

// ─── Health Badge ────────────────────────────────────────────────────────────

async function renderHealthBadge(containerId) {
    const el = document.getElementById(containerId);
    if (!el) return;

    try {
        const resp = await fetch('/api/health');
        const data = await resp.json();

        const colors = { healthy: '#10b981', warn: '#f59e0b', critical: '#ef4444' };
        const labels = {
            healthy: 'HEALTHY',
            warn: `${data.warning_count} WARNING${data.warning_count !== 1 ? 'S' : ''}`,
            critical: `${data.critical_count} CRITICAL`,
        };

        const color = colors[data.overall] || '#666';
        const label = labels[data.overall] || data.overall.toUpperCase();

        // Build detail list for tooltip
        let details = '';
        if (data.overall !== 'healthy') {
            const problems = [];
            for (const [, checks] of Object.entries(data.checks)) {
                for (const c of checks) {
                    if (c.status !== 'ok') {
                        problems.push(`${c.name}: ${c.detail}`);
                    }
                }
            }
            details = `<div class="health-details" style="margin-top:6px;font-size:10px;color:rgba(255,255,255,0.5);line-height:1.5;">${problems.map(p => `<div>${p}</div>`).join('')}</div>`;
        }

        el.innerHTML = `<div style="display:inline-flex;align-items:center;gap:6px;padding:4px 12px;border-radius:12px;background:${color}22;border:1px solid ${color}55;color:${color};font-family:'JetBrains Mono',monospace;font-size:11px;font-weight:600;">${label}</div>${details}`;
    } catch (e) {
        el.innerHTML = '<span style="color:rgba(255,255,255,0.3);font-size:11px;">Health check unavailable</span>';
    }
}

// ─── Triptych Renderer ───────────────────────────────────────────────────────

function renderTriptych(portfolio, performance) {
    const { balance, settled } = portfolio;

    // Equity card
    const eqVal = document.getElementById('tri-equity');
    const eqDet = document.getElementById('tri-equity-detail');
    if (eqVal) eqVal.textContent = `$${balance.equity.toFixed(2)}`;
    if (eqDet) eqDet.textContent = `$${balance.cash.toFixed(0)} cash + $${balance.positions.toFixed(0)} positions`;

    // Record card
    const recVal = document.getElementById('tri-record');
    const recDet = document.getElementById('tri-record-detail');
    if (recVal) recVal.textContent = `${settled.wins}W / ${settled.losses}L`;
    if (recDet) recDet.textContent = `${settled.hit_rate.toFixed(1)}% hit rate`;

    // P&L card
    const pnlVal = document.getElementById('tri-pnl');
    const pnlDet = document.getElementById('tri-pnl-detail');
    if (pnlVal) {
        const pnl = settled.net_pnl;
        pnlVal.textContent = `${pnl >= 0 ? '+' : ''}$${pnl.toFixed(2)}`;
        pnlVal.className = `triptych-value ${pnl >= 0 ? 'val-positive' : 'val-negative'}`;
    }
    if (pnlDet) pnlDet.textContent = `Fees: $${settled.fees.toFixed(2)}`;

    // Sparkline
    if (performance.equity_curve && performance.equity_curve.length > 0) {
        equitySparkline(performance.equity_curve, 'equity-sparkline');
    }

    // Daily P&L bars
    if (performance.settled_daily && performance.settled_daily.length > 0) {
        dailyPnlChart(performance.settled_daily, 'daily-pnl-chart');
    }

    // Health badge
    renderHealthBadge('health-badge');
}

// ─── Exports ──────────────────────────────────────────────────────────────────

export { renderTriptych };
