// dashboard/static/js/markets.js
// Market tables and charts rendering module.
// Handles both precip and temp market sections via a shared renderMarkets(data, type) function.

// ─── Plotly theme constants ─────────────────────────────────────────────────

const PLOTLY_LAYOUT = {
    paper_bgcolor: 'rgba(0,0,0,0)',
    plot_bgcolor: '#1a2332',
    font: { color: 'rgba(255,255,255,0.85)', family: 'Roboto, sans-serif', size: 12 },
    margin: { l: 60, r: 20, t: 30, b: 40 },
    xaxis: { gridcolor: '#2a3a4e', zerolinecolor: '#2a3a4e' },
    yaxis: { gridcolor: '#2a3a4e', zerolinecolor: '#2a3a4e' },
};

const PLOTLY_CONFIG = { displayModeBar: false, responsive: true };

// ─── Global signal count store ──────────────────────────────────────────────

window._signalCounts = window._signalCounts || {};

// ─── Formatters ─────────────────────────────────────────────────────────────

/**
 * Format a 0–1 probability as a percentage string (e.g. 0.721 → "72.1%").
 * @param {number} n
 * @returns {string}
 */
function fmtProb(n) {
    return `${(n * 100).toFixed(1)}%`;
}

/**
 * Format an edge value (0–1 range) as a signed percentage string (e.g. 0.111 → "+11.1%").
 * @param {number} n
 * @returns {string}
 */
function fmtEdge(n) {
    const pct = (n * 100).toFixed(1);
    return n >= 0 ? `+${pct}%` : `${pct}%`;
}

/**
 * Format a scan_time ISO string to a human-readable local time.
 * @param {string} isoStr
 * @returns {string}
 */
function fmtScanTime(isoStr) {
    try {
        const d = new Date(isoStr);
        return d.toLocaleString('en-US', {
            month: 'short', day: 'numeric', year: 'numeric',
            hour: '2-digit', minute: '2-digit', second: '2-digit',
        });
    } catch (_) {
        return isoStr;
    }
}

// ─── Table builder ──────────────────────────────────────────────────────────

/**
 * Build the markets data table HTML string.
 * Sorted by absolute edge descending.
 *
 * Columns: City | Ticker | Threshold | Model | Market | Edge | Direction | Confidence
 *
 * @param {Array} markets
 * @returns {string}
 */
function marketsTable(markets) {
    if (!markets || markets.length === 0) {
        return `
        <table class="data-table">
          <thead>
            <tr>
              <th>City</th><th>Ticker</th><th>Threshold</th>
              <th class="num">Model</th><th class="num">Market</th>
              <th class="num">Edge</th><th>Direction</th><th class="num">Confidence</th>
            </tr>
          </thead>
          <tbody class="table-empty">
            <tr><td colspan="8">No markets available</td></tr>
          </tbody>
        </table>`.trim();
    }

    // Sort by absolute edge descending
    const sorted = [...markets].sort((a, b) => Math.abs(b.edge) - Math.abs(a.edge));

    const rows = sorted.map(m => {
        // Edge color
        const edgeClass = m.edge > 0 ? 'val-positive' : m.edge < 0 ? 'val-negative' : 'val-neutral';

        // Confidence color
        let confClass;
        if (m.confidence >= 60) {
            confClass = 'val-positive';
        } else if (m.confidence >= 40) {
            confClass = 'val-amber';
        } else {
            confClass = 'val-negative';
        }

        return `
        <tr>
          <td>${m.city}</td>
          <td class="mono">${m.ticker}</td>
          <td>${m.threshold}</td>
          <td class="num mono">${fmtProb(m.model_prob)}</td>
          <td class="num mono">${fmtProb(m.market_price)}</td>
          <td class="num mono ${edgeClass}">${fmtEdge(m.edge)}</td>
          <td>${m.direction}</td>
          <td class="num mono ${confClass}">${m.confidence}</td>
        </tr>`.trim();
    }).join('\n');

    return `
    <table class="data-table">
      <thead>
        <tr>
          <th>City</th><th>Ticker</th><th>Threshold</th>
          <th class="num">Model</th><th class="num">Market</th>
          <th class="num">Edge</th><th>Direction</th><th class="num">Confidence</th>
        </tr>
      </thead>
      <tbody>${rows}</tbody>
    </table>`.trim();
}

// ─── Edge bar chart ─────────────────────────────────────────────────────────

/**
 * Render a Plotly horizontal bar chart showing edge by city.
 * Bars are green for positive edge, red for negative.
 *
 * @param {Array}  markets     - market objects with .city and .edge
 * @param {string} containerId - DOM element id
 */
function edgeBarChart(markets, containerId) {
    const el = document.getElementById(containerId);
    if (!el) return;

    if (!markets || markets.length === 0) {
        el.innerHTML = '<div class="chart-empty">No data</div>';
        return;
    }

    // Sort by edge ascending so highest positive is at top in horizontal bar
    const sorted = [...markets].sort((a, b) => a.edge - b.edge);

    const edges = sorted.map(m => parseFloat((m.edge * 100).toFixed(2)));
    const cities = sorted.map(m => m.city);
    const colors = edges.map(e => e >= 0 ? '#2ecc71' : '#e74c3c');

    const trace = {
        type: 'bar',
        orientation: 'h',
        x: edges,
        y: cities,
        marker: { color: colors },
        hovertemplate: '%{y}: %{x:+.1f}%<extra></extra>',
    };

    const layout = {
        ...PLOTLY_LAYOUT,
        xaxis: {
            ...PLOTLY_LAYOUT.xaxis,
            title: { text: 'Edge (%)', font: { color: 'rgba(255,255,255,0.6)', size: 11 } },
            tickformat: '+.1f',
        },
        yaxis: {
            ...PLOTLY_LAYOUT.yaxis,
            automargin: true,
        },
        bargap: 0.3,
    };

    Plotly.react(el, [trace], layout, PLOTLY_CONFIG);
}

// ─── Edge heatmap ───────────────────────────────────────────────────────────

/**
 * Render a Plotly heatmap of edge over time (x=date, y=city, z=edge).
 * Called when history has 3+ unique dates.
 *
 * @param {Array}  history     - objects with .city, .scan_date, .edge
 * @param {string} containerId
 * @param {string} labelId     - element id for the chart2 label
 */
function edgeHeatmap(history, containerId, labelId) {
    const el = document.getElementById(containerId);
    if (!el) return;

    const labelEl = document.getElementById(labelId);
    if (labelEl) labelEl.textContent = 'Edge Heatmap — City \u00d7 Date';

    // Build a 2D structure: cities × dates
    const citySet = new Set();
    const dateSet = new Set();
    history.forEach(h => { citySet.add(h.city); dateSet.add(h.scan_date); });

    const cities = [...citySet].sort();
    const dates  = [...dateSet].sort();

    // z[i][j] = edge for cities[i] on dates[j] (null if missing)
    const lookup = {};
    history.forEach(h => { lookup[`${h.city}|${h.scan_date}`] = h.edge; });

    const z = cities.map(city =>
        dates.map(date => {
            const v = lookup[`${city}|${date}`];
            return v !== undefined ? parseFloat((v * 100).toFixed(2)) : null;
        })
    );

    const trace = {
        type: 'heatmap',
        x: dates,
        y: cities,
        z,
        colorscale: [
            [0,   '#e74c3c'],
            [0.5, '#1a2332'],
            [1,   '#2ecc71'],
        ],
        zmid: 0,
        colorbar: {
            tickformat: '+.0f',
            ticksuffix: '%',
            thickness: 12,
            len: 0.8,
            tickfont: { color: 'rgba(255,255,255,0.7)', size: 10 },
        },
        hovertemplate: '%{y} on %{x}<br>Edge: %{z:+.1f}%<extra></extra>',
    };

    const layout = {
        ...PLOTLY_LAYOUT,
        xaxis: { ...PLOTLY_LAYOUT.xaxis, type: 'category', automargin: true },
        yaxis: { ...PLOTLY_LAYOUT.yaxis, automargin: true },
    };

    Plotly.react(el, [trace], layout, PLOTLY_CONFIG);
}

// ─── Confidence-vs-Edge scatter ─────────────────────────────────────────────

/**
 * Render a Plotly scatter of confidence vs edge.
 * Called when history has fewer than 3 unique dates.
 *
 * @param {Array}  markets     - market objects with .city, .edge, .confidence
 * @param {string} containerId
 * @param {string} labelId
 */
function confidenceScatter(markets, containerId, labelId) {
    const el = document.getElementById(containerId);
    if (!el) return;

    const labelEl = document.getElementById(labelId);
    if (labelEl) labelEl.textContent = 'Confidence vs Edge';

    if (!markets || markets.length === 0) {
        el.innerHTML = '<div class="chart-empty">No data</div>';
        return;
    }

    const edges  = markets.map(m => parseFloat((m.edge * 100).toFixed(2)));
    const confs  = markets.map(m => m.confidence);
    const cities = markets.map(m => m.city);
    const colors = edges.map(e => e >= 0 ? '#2ecc71' : '#e74c3c');

    const trace = {
        type: 'scatter',
        mode: 'markers+text',
        x: edges,
        y: confs,
        text: cities,
        textposition: 'top center',
        textfont: { color: 'rgba(255,255,255,0.7)', size: 10 },
        marker: {
            color: colors,
            size: 10,
            line: { color: 'rgba(255,255,255,0.3)', width: 1 },
        },
        hovertemplate: '%{text}<br>Edge: %{x:+.1f}%<br>Conf: %{y}<extra></extra>',
    };

    const layout = {
        ...PLOTLY_LAYOUT,
        xaxis: {
            ...PLOTLY_LAYOUT.xaxis,
            title: { text: 'Edge (%)', font: { color: 'rgba(255,255,255,0.6)', size: 11 } },
            tickformat: '+.1f',
        },
        yaxis: {
            ...PLOTLY_LAYOUT.yaxis,
            title: { text: 'Confidence', font: { color: 'rgba(255,255,255,0.6)', size: 11 } },
        },
    };

    Plotly.react(el, [trace], layout, PLOTLY_CONFIG);
}

// ─── Signal summary footer ──────────────────────────────────────────────────

/**
 * Update the #signal-summary footer element with counts stored in window._signalCounts.
 */
function updateSignalSummary() {
    const el = document.getElementById('signal-summary');
    if (!el) return;

    const pc = window._signalCounts.precip || { total: 0, tradeworthy: 0 };
    const tc = window._signalCounts.temp   || { total: 0, tradeworthy: 0 };

    el.innerHTML = `
        <div class="metric-card metric-neutral">
            <div class="metric-label">Precip Signals</div>
            <div class="metric-value mono">${pc.total}</div>
            <div class="metric-subtitle">${pc.tradeworthy} trade-worthy</div>
        </div>
        <div class="metric-card metric-neutral">
            <div class="metric-label">Temp Signals</div>
            <div class="metric-value mono">${tc.total}</div>
            <div class="metric-subtitle">${tc.tradeworthy} trade-worthy</div>
        </div>
        <div class="metric-card metric-accent">
            <div class="metric-label">Trade-Worthy</div>
            <div class="metric-value mono">${pc.tradeworthy + tc.tradeworthy}</div>
            <div class="metric-subtitle">Total signals</div>
        </div>`;
}

// ─── Main entry ─────────────────────────────────────────────────────────────

/**
 * Render the full markets section for a given type.
 *
 * @param {Object} data - Response from /api/markets/{type}
 *   { scan_time, markets: [ { city, ticker, threshold, model_prob, market_price,
 *                              edge, direction, confidence, method, days_left } ] }
 * @param {string} type - "precip" or "temp"
 */
async function renderMarkets(data, type) {
    const { scan_time, markets = [] } = data;

    // 1. Scan timestamp
    const scanTimeEl = document.getElementById(`${type}-scan-time`);
    if (scanTimeEl) {
        scanTimeEl.textContent = scan_time ? `Last scan: ${fmtScanTime(scan_time)}` : '';
    }

    // 2. Data table
    const tableEl = document.getElementById(`${type}-table`);
    if (tableEl) {
        tableEl.innerHTML = marketsTable(markets);
    }

    // 3. Edge bar chart
    edgeBarChart(markets, `${type}-edge-chart`);

    // 4. Heatmap or scatter — fetch history first
    const heatmapId = `${type}-heatmap-chart`;
    const labelId   = `${type}-chart2-label`;

    try {
        const resp = await fetch(`/api/markets/${type}/history`);
        if (resp.ok) {
            const history = await resp.json();
            const uniqueDates = new Set(history.map(h => h.scan_date));
            if (uniqueDates.size >= 3) {
                edgeHeatmap(history, heatmapId, labelId);
            } else {
                confidenceScatter(markets, heatmapId, labelId);
            }
        } else {
            // Fallback to scatter on API error
            confidenceScatter(markets, heatmapId, labelId);
        }
    } catch (_) {
        // Fallback to scatter on network error
        confidenceScatter(markets, heatmapId, labelId);
    }

    // 5. Signal badge — trade-worthy: edge > 7% AND confidence >= 55
    const tradeworthy = markets.filter(m => m.edge > 0.07 && m.confidence >= 55);
    const badgeEl = document.getElementById(`${type}-signal-badge`);
    if (badgeEl) {
        const count = tradeworthy.length;
        const label = count === 1 ? '1 trade-worthy signal' : `${count} trade-worthy signals`;
        const badgeVariant = count > 0 ? 'signal-badge signal-badge-active' : 'signal-badge signal-badge-none';
        badgeEl.innerHTML = `<span class="${badgeVariant}">${label}</span>`;
    }

    // 6. Store signal counts for footer
    window._signalCounts[type] = {
        total: markets.length,
        tradeworthy: tradeworthy.length,
    };

    // Update footer summary after every render
    updateSignalSummary();
}

// ─── Exports ─────────────────────────────────────────────────────────────────

export { renderMarkets };
