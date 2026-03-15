// dashboard/static/js/resting.js
// Resting orders grid

function renderResting(data) {
    const el = document.getElementById('resting-table');
    if (!el) return;

    const colsCSS = '1fr 1fr 1.5fr auto auto 1fr';

    if (!data || data.length === 0) {
        el.innerHTML = `<div class="data-grid" style="--cols: ${colsCSS}">
            <div class="dg-head">
                <span>City</span><span>Action</span><span>Contract</span>
                <span class="num">Qty</span><span class="num">Price</span><span>Created</span>
            </div>
            <div class="dg-empty">No resting orders</div>
        </div>`;
        return;
    }

    const headers = `<div class="dg-head">
        <span>City</span><span>Action</span><span>Contract</span>
        <span class="num">Qty</span><span class="num">Price</span><span>Created</span>
    </div>`;

    const rows = data.map(o => {
        const actionClass = o.action === 'BUY' ? 'val-positive' : 'val-negative';
        return `<div class="dg-row">
            <span data-label="City">${o.city}</span>
            <span class="${actionClass}" data-label="Action">${o.action} ${o.side}</span>
            <span class="mono" data-label="Contract">${o.contract || '\u2014'}</span>
            <span class="num mono" data-label="Qty">${o.remaining}</span>
            <span class="num mono" data-label="Price">${o.price}\u00a2</span>
            <span class="mono" data-label="Created" style="white-space:nowrap">${o.created}</span>
        </div>`;
    }).join('\n');

    el.innerHTML = `<div class="data-grid" style="--cols: ${colsCSS}">${headers}${rows}</div>`;
}

export { renderResting };
