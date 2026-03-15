// dashboard/static/js/resting.js
// Resting orders table

function renderResting(data) {
    const el = document.getElementById('resting-table');
    if (!el) return;

    if (!data || data.length === 0) {
        el.innerHTML = `<div class="table-wrap"><table class="data-table"><tbody class="table-empty"><tr><td>No resting orders</td></tr></tbody></table></div>`;
        return;
    }

    const headers = `<tr>
        <th>City</th><th>Action</th><th>Contract</th>
        <th class="num">Qty</th><th class="num">Price</th><th>Created</th>
    </tr>`;

    const rows = data.map(o => {
        const actionClass = o.action === 'BUY' ? 'val-positive' : 'val-negative';
        return `<tr>
            <td>${o.city}</td>
            <td class="${actionClass}">${o.action} ${o.side}</td>
            <td class="mono">${o.contract || '\u2014'}</td>
            <td class="num mono">${o.remaining}</td>
            <td class="num mono">${o.price}\u00a2</td>
            <td class="mono" style="white-space:nowrap">${o.created}</td>
        </tr>`;
    }).join('\n');

    el.innerHTML = `<div class="table-wrap"><table class="data-table"><thead>${headers}</thead><tbody>${rows}</tbody></table></div>`;
}

export { renderResting };
