// dashboard/static/js/app.js
import { renderPortfolio } from './portfolio.js?v=12';
import { renderMarkets } from './markets.js?v=12';
import { renderTriptych } from './performance.js?v=12';
import { renderActivity } from './activity.js?v=12';

let _configCache = null;

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

    const [portfolioResult, performanceResult, configResult] = await Promise.allSettled([
        fetchJSON('/api/portfolio'),
        fetchJSON('/api/performance'),
        loadConfig(),
    ]);

    const portfolio = portfolioResult.status === 'fulfilled' ? portfolioResult.value : null;
    const performance = performanceResult.status === 'fulfilled' ? performanceResult.value : null;

    if (portfolio) {
        renderTriptych(portfolio, performance, _configCache);
        renderPortfolio(portfolio);
    }

    await Promise.allSettled([
        loadSection('/api/markets/temp', (d) => renderMarkets(d, 'temp'), 'temp-table'),
        loadSection('/api/markets/precip', (d) => renderMarkets(d, 'precip'), 'precip-table'),
        loadSection('/api/activity', renderActivity, 'activity-table'),
    ]);

    btn.classList.remove('spinning');
    document.getElementById('header-timestamp').textContent =
        new Date().toLocaleString('en-US', { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit', second: '2-digit' });
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
        _configCache = cfg;

        const badge = document.getElementById('mode-badge');
        badge.textContent = cfg.mode;
        badge.className = `we-mode-badge we-mode-${cfg.mode.toLowerCase()}`;

        const rc = document.getElementById('risk-controls');
        if (rc) {
            const items = [
                { label: 'Edge Gate', value: `${((cfg.edge_gate || 0) * 100).toFixed(0)}%` },
                { label: 'Confidence', value: `${cfg.confidence_gate || 0}` },
                { label: 'Kelly', value: cfg.kelly_range ? `${cfg.kelly_range[0]}x\u2013${cfg.kelly_range[1]}x` : '\u2014' },
                { label: '$/order', value: `$${(cfg.max_order_usd || 0).toFixed(0)}` },
                { label: '$/scan', value: `$${(cfg.scan_budget_usd || 0).toFixed(0)}` },
            ];
            if (cfg.drawdown_threshold) {
                items.push({ label: 'Drawdown', value: `${(cfg.drawdown_threshold * 100).toFixed(0)}%` });
            }

            rc.innerHTML = `
                <div class="risk-footer-title">Risk Controls</div>
                <div class="risk-grid">
                    ${items.map(i => `<div class="risk-item"><strong>${i.label}:</strong> ${i.value}</div>`).join('')}
                </div>`;
        }

        return cfg;
    } catch (e) {
        console.error('Config load failed:', e);
        return null;
    }
}

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

document.addEventListener('DOMContentLoaded', () => {
    document.getElementById('refresh-btn').addEventListener('click', refreshAll);
    document.getElementById('precip-rescan').addEventListener('click', () => forceRescan('precip'));
    document.getElementById('temp-rescan').addEventListener('click', () => forceRescan('temp'));
    refreshAll();
});

export { fetchJSON };
