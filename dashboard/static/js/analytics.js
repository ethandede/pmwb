// dashboard/static/js/analytics.js
// Scorecard, trends, and parameter health rendering

function fmtPnl(n) {
    if (n === null || n === undefined) return '\u2014';
    const sign = n >= 0 ? '+' : '';
    return `${sign}$${Math.abs(n).toFixed(2)}`;
}

function renderScorecard(data) {
    const el = document.getElementById('scorecard-content');
    if (!el) return;

    const today = data.today || data.yesterday;
    const label = data.today ? 'Today' : 'Yesterday';
    const r7 = data.rolling_7d;

    if (!today && !r7) {
        el.innerHTML = '<p class="section-desc">No settlement data yet. Analytics populate after trades settle.</p>';
        return;
    }

    let html = '<div class="metric-row">';

    if (today) {
        const pnlClass = today.net_pnl >= 0 ? 'metric-positive' : 'metric-negative';
        html += `
            <div class="metric-card ${pnlClass}">
                <div class="metric-label">${label} P&L</div>
                <div class="metric-value mono">${fmtPnl(today.net_pnl)}</div>
            </div>
            <div class="metric-card metric-neutral">
                <div class="metric-label">${label} Record</div>
                <div class="metric-value mono">${today.wins}W / ${today.losses}L</div>
            </div>`;
    }

    if (r7) {
        const total7 = (r7.wins || 0) + (r7.losses || 0);
        const hr7 = total7 > 0 ? ((r7.wins || 0) / total7 * 100).toFixed(1) : '0.0';
        html += `
            <div class="metric-card metric-neutral">
                <div class="metric-label">7-Day Hit Rate</div>
                <div class="metric-value mono">${hr7}%</div>
                <div class="metric-subtitle">${r7.wins || 0}W / ${r7.losses || 0}L</div>
            </div>
            <div class="metric-card ${(r7.pnl || 0) >= 0 ? 'metric-positive' : 'metric-negative'}">
                <div class="metric-label">7-Day P&L</div>
                <div class="metric-value mono">${fmtPnl(r7.pnl)}</div>
            </div>`;
    }

    html += '</div>';
    el.innerHTML = html;
}

function renderTrends(data) {
    const el = document.getElementById('trends-content');
    if (!el) return;

    const daily = data.daily || [];
    if (daily.length < 2) {
        el.innerHTML = '<p class="section-desc">Need at least 2 days of data for trends.</p>';
        return;
    }

    const dates = daily.map(d => d.date);
    const hitRates = daily.map(d => d.hit_rate !== null ? (d.hit_rate * 100) : null);
    const pnls = daily.map(d => d.net_pnl);

    const layout = {
        paper_bgcolor: 'rgba(0,0,0,0)', plot_bgcolor: '#1a2332',
        font: { color: 'rgba(255,255,255,0.85)', family: 'Roboto, sans-serif', size: 12 },
        margin: { l: 10, r: 10, t: 10, b: 10 },
        xaxis: { gridcolor: '#2a3a4e', automargin: true },
        yaxis: { gridcolor: '#2a3a4e', automargin: true },
    };
    const config = { displayModeBar: false, responsive: true };

    el.innerHTML = '<div class="chart-row"><div class="chart-card"><div class="chart-label">Hit Rate %</div><div id="trend-hitrate"></div></div><div class="chart-card"><div class="chart-label">Daily P&L</div><div id="trend-pnl"></div></div></div>';

    Plotly.newPlot('trend-hitrate', [{
        x: dates, y: hitRates, type: 'scatter', mode: 'lines+markers',
        line: { color: '#10b981', width: 2 }, marker: { size: 5 },
    }], { ...layout, yaxis: { ...layout.yaxis, title: { text: '%', font: { color: 'rgba(255,255,255,0.6)', size: 11 } } } }, config);

    const pnlColors = pnls.map(p => p >= 0 ? '#10b981' : '#ef4444');
    Plotly.newPlot('trend-pnl', [{
        x: dates, y: pnls, type: 'bar', marker: { color: pnlColors },
    }], { ...layout, yaxis: { ...layout.yaxis, title: { text: '$', font: { color: 'rgba(255,255,255,0.6)', size: 11 } } } }, config);
}

function renderRecommendations(data) {
    const el = document.getElementById('recommendations-content');
    if (!el) return;

    if (!data || data.length === 0) {
        el.innerHTML = '<p class="section-desc">No parameter recommendations. System operating within expected ranges.</p>';
        return;
    }

    const rows = data.map(r => {
        const confClass = r.confidence === 'high' ? 'val-negative' : r.confidence === 'medium' ? 'val-amber' : 'val-neutral';
        return `<tr>
            <td>${r.param_name}</td>
            <td class="mono">${r.current_value}</td>
            <td class="mono val-positive">${r.suggested_value}</td>
            <td>${r.reason}</td>
            <td class="num">${r.sample_size}</td>
            <td class="${confClass}">${r.confidence.toUpperCase()}</td>
        </tr>`;
    }).join('');

    el.innerHTML = `
        <div class="table-wrap">
        <table class="data-table">
            <thead><tr><th>Parameter</th><th>Current</th><th>Suggested</th><th>Reason</th><th class="num">Trades</th><th>Priority</th></tr></thead>
            <tbody>${rows}</tbody>
        </table>
        </div>`;
}

function renderActions(data) {
    const el = document.getElementById('actions-content');
    if (!el) return;

    const s = data.summary || {};
    const recent = data.recent || [];

    const holds = s.hold || 0;
    const exits = s.exit || 0;
    const fortifies = s.fortify || 0;
    const spreadBlocked = s.spread_blocked || 0;
    const total = holds + exits + fortifies;

    let html = `<div class="metric-row metric-row-5">
        <div class="metric-card metric-neutral">
            <div class="metric-label">Hold</div>
            <div class="metric-value mono">${holds}</div>
        </div>
        <div class="metric-card metric-accent">
            <div class="metric-label">Fortify</div>
            <div class="metric-value mono">${fortifies}</div>
        </div>
        <div class="metric-card ${exits > 0 ? 'metric-negative' : 'metric-neutral'}">
            <div class="metric-label">Exit</div>
            <div class="metric-value mono">${exits}</div>
        </div>
        <div class="metric-card ${spreadBlocked > 0 ? 'metric-negative' : 'metric-neutral'}">
            <div class="metric-label">Spread Blocked</div>
            <div class="metric-value mono">${spreadBlocked}</div>
        </div>
        <div class="metric-card metric-neutral">
            <div class="metric-label">Total Decisions</div>
            <div class="metric-value mono">${total}</div>
        </div>
    </div>`;

    if (recent.length > 0) {
        const rows = recent.map(r => {
            const actionClass = r.action === 'fortify' ? 'val-positive' : r.action === 'exit' ? 'val-negative' : '';
            const time = r.timestamp ? r.timestamp.slice(5, 16).replace('T', ' ') : '\u2014';
            return `<tr>
                <td class="mono" style="white-space:nowrap">${time}</td>
                <td>${r.city || r.ticker}</td>
                <td class="${actionClass}">${r.action.toUpperCase()}</td>
                <td>${r.reason}</td>
            </tr>`;
        }).join('');

        html += `<div class="table-wrap" style="margin-top:16px;">
            <table class="data-table">
                <thead><tr><th>Time</th><th>City</th><th>Action</th><th>Reason</th></tr></thead>
                <tbody>${rows}</tbody>
            </table>
        </div>`;
    }

    el.innerHTML = html;
}

export { renderScorecard, renderTrends, renderRecommendations, renderActions };
