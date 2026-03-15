// dashboard/static/js/app.js
import { renderPortfolio } from './portfolio.js?v=23';
import { renderMarkets } from './markets.js?v=23';
import { renderTriptych } from './performance.js?v=23';
import { renderActivity } from './activity.js?v=23';
import { renderSettled } from './settled.js?v=23';
import { renderScorecard, renderTrends, renderRecommendations, renderActions } from './analytics.js?v=23';
import { renderResting } from './resting.js?v=23';

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
        loadSection('/api/resting', renderResting, 'resting-table'),
        loadSection('/api/settled', renderSettled, 'settled-table'),
        loadSection('/api/analytics/scorecard', renderScorecard, 'scorecard-content'),
        loadSection('/api/analytics/trends', renderTrends, 'trends-content'),
        loadSection('/api/analytics/actions', renderActions, 'actions-content'),
        loadSection('/api/analytics/recommendations', renderRecommendations, 'recommendations-content'),
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
                    ${items.map(i => `<div class="risk-item"><div class="risk-item-label">${i.label}</div><div class="risk-item-value">${i.value}</div></div>`).join('')}
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

// --- Sidebar navigation ---
function initNav() {
    const sidebar = document.getElementById('sidebar');
    const toggle = document.getElementById('sidebar-toggle');
    const mobileOpen = document.getElementById('sidebar-open');
    const backdrop = document.getElementById('sidebar-backdrop');
    const navItems = document.querySelectorAll('.we-nav-item');

    // Collapse/expand
    toggle.addEventListener('click', () => {
        sidebar.classList.toggle('collapsed');
        localStorage.setItem('sidebar-collapsed', sidebar.classList.contains('collapsed'));
    });

    // Mobile hamburger
    if (mobileOpen) {
        mobileOpen.addEventListener('click', () => {
            sidebar.classList.toggle('mobile-open');
            backdrop?.classList.toggle('visible');
        });
    }

    // Close on backdrop tap
    if (backdrop) {
        backdrop.addEventListener('click', () => {
            sidebar.classList.remove('mobile-open');
            backdrop.classList.remove('visible');
        });
    }

    // Restore collapsed state
    if (localStorage.getItem('sidebar-collapsed') === 'true') {
        sidebar.classList.add('collapsed');
    }

    // View switching
    navItems.forEach(item => {
        item.addEventListener('click', (e) => {
            e.preventDefault();
            const viewId = item.dataset.view;

            // Update active nav
            navItems.forEach(n => n.classList.remove('active'));
            item.classList.add('active');

            // Show active view
            document.querySelectorAll('.we-view').forEach(v => v.classList.remove('active'));
            const view = document.getElementById(`view-${viewId}`);
            if (view) view.classList.add('active');

            // Close mobile sidebar + backdrop
            sidebar.classList.remove('mobile-open');
            backdrop?.classList.remove('visible');

            // Remember
            localStorage.setItem('active-view', viewId);

            // Trigger Plotly resize for charts that may now be visible
            setTimeout(() => window.dispatchEvent(new Event('resize')), 100);
        });
    });

    // Restore last active view
    const saved = localStorage.getItem('active-view');
    if (saved) {
        const item = document.querySelector(`.we-nav-item[data-view="${saved}"]`);
        if (item) item.click();
    }
}

document.addEventListener('DOMContentLoaded', () => {
    initNav();
    document.getElementById('refresh-btn').addEventListener('click', refreshAll);
    document.getElementById('precip-rescan').addEventListener('click', () => forceRescan('precip'));
    document.getElementById('temp-rescan').addEventListener('click', () => forceRescan('temp'));
    refreshAll();
    setInterval(() => { if (!document.hidden) refreshAll(); }, 60000);
});

export { fetchJSON };
