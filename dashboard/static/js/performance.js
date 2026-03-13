// dashboard/static/js/performance.js
// Equity curve and model accuracy charts for the performance tab.

// ─── Plotly theme constants (mirrors markets.js) ─────────────────────────────

const PLOTLY_LAYOUT = {
    paper_bgcolor: 'rgba(0,0,0,0)',
    plot_bgcolor: '#1a2332',
    font: { color: 'rgba(255,255,255,0.85)', family: 'Roboto, sans-serif', size: 12 },
    margin: { l: 60, r: 20, t: 30, b: 40 },
    xaxis: { gridcolor: '#2a3a4e', zerolinecolor: '#2a3a4e' },
    yaxis: { gridcolor: '#2a3a4e', zerolinecolor: '#2a3a4e' },
};

const PLOTLY_CONFIG = { displayModeBar: false, responsive: true };

// ─── Helpers ─────────────────────────────────────────────────────────────────

/**
 * Replace the inner HTML of a container with a centered empty-state message.
 * @param {string} containerId
 * @param {string} message
 */
function showEmpty(containerId, message) {
    const el = document.getElementById(containerId);
    if (!el) return;
    el.innerHTML = `<div style="
        display:flex; align-items:center; justify-content:center;
        height:100%; min-height:160px;
        color:rgba(255,255,255,0.4); font-size:0.9rem; text-align:center;
        padding: 2rem;
    ">${message}</div>`;
}

// ─── Equity Curve ─────────────────────────────────────────────────────────────

/**
 * Render a Plotly line+area chart for the equity curve.
 * @param {Array<{date: string, equity: number, realized_pnl: number, fees: number}>} curve
 * @param {string} containerId
 */
function equityChart(curve, containerId) {
    if (!curve || curve.length === 0) {
        showEmpty(containerId, 'No data yet — equity curve will appear after first daily snapshot');
        return;
    }

    const dates   = curve.map(d => d.date);
    const equities = curve.map(d => d.equity);

    const trace = {
        x: dates,
        y: equities,
        type: 'scatter',
        mode: 'lines',
        name: 'Equity',
        line: {
            color: '#10b981',
            width: 2,
        },
        fill: 'tozeroy',
        fillcolor: 'rgba(16, 185, 129, 0.30)',
        hovertemplate: '<b>%{x}</b><br>Equity: $%{y:,.2f}<extra></extra>',
    };

    const layout = {
        ...PLOTLY_LAYOUT,
        xaxis: {
            ...PLOTLY_LAYOUT.xaxis,
            title: { text: 'Date', standoff: 8 },
            type: 'date',
        },
        yaxis: {
            ...PLOTLY_LAYOUT.yaxis,
            title: { text: 'Equity ($)', standoff: 8 },
            tickprefix: '$',
        },
        showlegend: false,
    };

    Plotly.newPlot(containerId, [trace], layout, PLOTLY_CONFIG);
}

// ─── Model Accuracy ───────────────────────────────────────────────────────────

/**
 * Render a Plotly scatter chart of predicted probability vs. actual outcome.
 * Points are colored green (correct) or red (incorrect).
 * @param {Array<{ticker: string, city: string, market_type: string, predicted: number, market: number, actual: number, settled: string}>} outcomes
 * @param {string} containerId
 */
function accuracyChart(outcomes, containerId) {
    if (!outcomes || outcomes.length === 0) {
        showEmpty(containerId, 'No data yet — model accuracy will appear after markets settle');
        return;
    }

    // Separate into correct / incorrect for distinct colored traces
    const correct   = outcomes.filter(d => (d.predicted > 0.5 && d.actual === 1) || (d.predicted < 0.5 && d.actual === 0));
    const incorrect = outcomes.filter(d => !((d.predicted > 0.5 && d.actual === 1) || (d.predicted < 0.5 && d.actual === 0)));

    // Small deterministic jitter so overlapping points are visible
    function jitter(val, seed) {
        // Seedable pseudo-random offset in ±0.025
        const s = Math.sin(seed * 127.1 + val * 311.7) * 43758.5453;
        return (s - Math.floor(s) - 0.5) * 0.05;
    }

    function buildTrace(rows, color, name) {
        return {
            x: rows.map(d => d.predicted),
            y: rows.map((d, i) => d.actual + jitter(d.actual, i)),
            mode: 'markers',
            type: 'scatter',
            name,
            marker: {
                color,
                size: 8,
                opacity: 0.85,
                line: { color: 'rgba(0,0,0,0.3)', width: 1 },
            },
            text: rows.map(d => `${d.city} (${d.market_type})<br>Predicted: ${(d.predicted * 100).toFixed(1)}%<br>Actual: ${d.actual}<br>Settled: ${d.settled}`),
            hovertemplate: '%{text}<extra></extra>',
        };
    }

    const traceCorrect   = buildTrace(correct,   '#10b981', 'Correct');
    const traceIncorrect = buildTrace(incorrect, '#ef4444', 'Incorrect');

    // Diagonal reference line: y = x from (0,0) to (1,1)
    const traceDiag = {
        x: [0, 1],
        y: [0, 1],
        mode: 'lines',
        type: 'scatter',
        name: 'Perfect calibration',
        line: { color: 'rgba(180,180,180,0.5)', width: 1, dash: 'dash' },
        hoverinfo: 'skip',
    };

    const layout = {
        ...PLOTLY_LAYOUT,
        xaxis: {
            ...PLOTLY_LAYOUT.xaxis,
            title: { text: 'Predicted probability', standoff: 8 },
            range: [-0.05, 1.05],
            tickformat: ',.0%',
        },
        yaxis: {
            ...PLOTLY_LAYOUT.yaxis,
            title: { text: 'Actual outcome', standoff: 8 },
            range: [-0.3, 1.3],
            tickvals: [0, 1],
            ticktext: ['0 (No)', '1 (Yes)'],
        },
        legend: {
            bgcolor: 'rgba(0,0,0,0)',
            bordercolor: 'rgba(255,255,255,0.1)',
            borderwidth: 1,
        },
    };

    Plotly.newPlot(containerId, [traceDiag, traceCorrect, traceIncorrect], layout, PLOTLY_CONFIG);
}

// ─── Main entry point ─────────────────────────────────────────────────────────

/**
 * Render all performance charts from the /api/performance response payload.
 * @param {{ equity_curve: Array, model_accuracy: Array }} data
 */
function renderPerformance(data) {
    equityChart(data.equity_curve,    'equity-chart');
    accuracyChart(data.model_accuracy, 'accuracy-chart');
}

// ─── Exports ──────────────────────────────────────────────────────────────────

export { renderPerformance };
