// dashboard/static/js/app.js
import { renderPortfolio } from './portfolio.js';
import { renderMarkets } from './markets.js';
import { renderTriptych } from './performance.js';

async function fetchJSON(url, timeout = 30000) {
    const controller = new AbortController();
    const id = setTimeout(() => controller.abort(), timeout);
    try {
        const resp = await fetch(url, { signal: controller.signal });
        clearTimeout(id);
        if (!resp.ok) {
            const err = await resp.json().catch(() => ({}));
            throw new Error(err.error || `HTTP ${resp.status}`);
        }
        return await resp.json();
    } catch (e) {
        clearTimeout(id);
        throw e;
    }
}

async function refreshAll() {
    const btn = document.getElementById('refresh-btn');
    btn.classList.add('spinning');

    // Fetch portfolio and performance together for the triptych
    const [portfolioResult, performanceResult] = await Promise.allSettled([
        fetchJSON('/api/portfolio'),
        fetchJSON('/api/performance'),
    ]);

    // Render triptych if both loaded
    if (portfolioResult.status === 'fulfilled' && performanceResult.status === 'fulfilled') {
        renderTriptych(portfolioResult.value, performanceResult.value);
        renderPortfolio(portfolioResult.value);
    } else if (portfolioResult.status === 'fulfilled') {
        renderPortfolio(portfolioResult.value);
    }

    // Markets and config in parallel
    await Promise.allSettled([
        loadSection('/api/markets/temp', (d) => renderMarkets(d, 'temp'), 'temp-table'),
        loadSection('/api/markets/precip', (d) => renderMarkets(d, 'precip'), 'precip-table'),
        loadConfig(),
    ]);

    btn.classList.remove('spinning');
    document.getElementById('header-timestamp').textContent =
        new Date().toLocaleString('en-US', { month: 'short', day: 'numeric', year: 'numeric', hour: '2-digit', minute: '2-digit', second: '2-digit' });
}

async function loadSection(url, renderFn, containerId, timeout) {
    const container = document.getElementById(containerId);
    try {
        const data = await fetchJSON(url, timeout);
        renderFn(data);
    } catch (e) {
        if (container) {
            container.innerHTML = `<div class="error-msg">Failed to load: ${e.message}</div>`;
        }
    }
}

async function loadConfig() {
    try {
        const cfg = await fetchJSON('/api/config');
        const badge = document.getElementById('mode-badge');
        badge.textContent = cfg.mode;
        badge.className = `we-mode-badge we-mode-${cfg.mode.toLowerCase()}`;

        // Risk controls footer
        const rc = document.getElementById('risk-controls');
        rc.innerHTML = `
            <div class="risk-footer-title">Risk Controls</div>
            <div class="risk-grid">
                <div class="risk-item"><strong>Edge:</strong> ${(cfg.edge_gate * 100).toFixed(0)}%</div>
                <div class="risk-item"><strong>Conf:</strong> ${cfg.confidence_gate}</div>
                <div class="risk-item"><strong>Kelly:</strong> ${cfg.kelly_range[0]}x\u2013${cfg.kelly_range[1]}x</div>
                <div class="risk-item"><strong>$/order:</strong> $${cfg.max_order_usd.toFixed(0)}</div>
                <div class="risk-item"><strong>$/scan:</strong> $${cfg.scan_budget_usd.toFixed(0)}</div>
                <div class="risk-item"><strong>Drawdown:</strong> ${(cfg.drawdown_threshold * 100).toFixed(0)}%</div>
            </div>`;
    } catch (e) {
        console.error('Config load failed:', e);
    }
}

// Force rescan handlers
async function forceRescan(marketType) {
    const btn = document.getElementById(`${marketType}-rescan`);
    btn.textContent = 'Scanning...';
    btn.disabled = true;
    try {
        const data = await fetchJSON(`/api/markets/${marketType}?force=true`, 90000);
        renderMarkets(data, marketType);
    } catch (e) {
        console.error(`Rescan ${marketType} failed:`, e);
    } finally {
        btn.textContent = 'Force Rescan';
        btn.disabled = false;
    }
}

// Init
document.addEventListener('DOMContentLoaded', () => {
    document.getElementById('refresh-btn').addEventListener('click', refreshAll);
    document.getElementById('precip-rescan').addEventListener('click', () => forceRescan('precip'));
    document.getElementById('temp-rescan').addEventListener('click', () => forceRescan('temp'));
    refreshAll();
});

export { fetchJSON };
