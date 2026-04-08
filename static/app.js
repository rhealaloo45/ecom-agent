/* ── PriceSync Frontend Controller ── */

const API = '';
let selectedProductId = null;
let currentResults = null;

// ── Init ──
document.addEventListener('DOMContentLoaded', loadProducts);

async function loadProducts() {
    try {
        const res = await fetch(`${API}/products`);
        const products = await res.json();
        renderProductList(products);
        document.getElementById('productCount').textContent = `${products.length} items`;
    } catch (e) {
        showToast('Failed to load products', 'error');
    }
}

function renderProductList(products) {
    const list = document.getElementById('productList');
    list.innerHTML = products.map(p => `
        <div class="product-card ${p.id === selectedProductId ? 'active' : ''}"
             id="pc-${p.id}" onclick="selectProduct('${p.id}')">
            <div class="pc-name">${esc(p.name)}</div>
            <div class="pc-meta">
                <span class="pc-price">₹${fmt(p.current_price)}</span>
                <span class="pc-status ${p.status.toLowerCase()}">${p.status}</span>
            </div>
            ${p.last_updated ? `<div class="pc-updated">Updated ${p.last_updated}</div>` : ''}
        </div>
    `).join('');
}

async function selectProduct(pid) {
    selectedProductId = pid;
    currentResults = null;

    // Update active state
    document.querySelectorAll('.product-card').forEach(c => c.classList.remove('active'));
    const card = document.getElementById(`pc-${pid}`);
    if (card) card.classList.add('active');

    // Clear previous results display
    document.getElementById('resultsSection').style.display = 'none';
    document.getElementById('logSection').style.display = 'none';

    try {
        const res = await fetch(`${API}/select_product`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ product_id: pid })
        });
        const product = await res.json();
        showProductDetail(product);
    } catch (e) {
        showToast('Failed to select product', 'error');
    }
}

function showProductDetail(p) {
    document.getElementById('emptyState').style.display = 'none';
    document.getElementById('productDetail').style.display = 'block';
    document.getElementById('resultsSection').style.display = 'none';
    document.getElementById('logSection').style.display = 'none';

    document.getElementById('detailCategory').textContent = p.category;
    document.getElementById('detailName').textContent = p.name;
    document.getElementById('detailPrice').textContent = `₹${fmt(p.current_price)}`;
    document.getElementById('detailCost').textContent = `₹${fmt(p.cost_price)}`;
}

// ── Run Agent ──
async function runAgent() {
    if (!selectedProductId) return;

    const btn = document.getElementById('btnCheck');
    btn.disabled = true;
    btn.innerHTML = '<span class="spinner-border spinner-border-sm"></span> Analyzing...';

    const logSection = document.getElementById('logSection');
    const logBody = document.getElementById('logBody');
    const logSpinner = document.getElementById('logSpinner');
    const resultsSection = document.getElementById('resultsSection');

    logSection.style.display = 'block';
    resultsSection.style.display = 'none';
    logBody.innerHTML = '';
    logSpinner.style.display = '';

    setGlobalStatus('working', 'Analyzing...');

    // Simulate progressive logs while waiting
    const fakeLogs = [
        '📦 Loading product details...',
        '🔍 Scraping Amazon...',
        '🔍 Scraping Flipkart...',
        '🔍 Scraping Google Shopping...',
        '📊 Analyzing demand signals...',
        '🤖 Running AI pricing strategy...',
        '🛡️ Validating guardrails...',
    ];

    let logIdx = 0;
    const logInterval = setInterval(() => {
        if (logIdx < fakeLogs.length) {
            appendLog(fakeLogs[logIdx]);
            logIdx++;
        }
    }, 1200);

    try {
        const res = await fetch(`${API}/run-agent`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ product_id: selectedProductId })
        });

        clearInterval(logInterval);
        const data = await res.json();

        // Replace logs with real ones
        logBody.innerHTML = '';
        if (data.logs) {
            data.logs.forEach(l => appendLog(l));
        }
        logSpinner.style.display = 'none';

        if (data.error && !data.competitor_data?.length) {
            showToast(`Agent error: ${data.error}`, 'error');
            setGlobalStatus('error', 'Error');
        } else {
            currentResults = data;
            displayResults(data);
            setGlobalStatus('idle', 'Ready');
            showToast('Analysis complete', 'success');
        }
    } catch (e) {
        clearInterval(logInterval);
        logSpinner.style.display = 'none';
        appendLog(`❌ Error: ${e.message}`);
        showToast('Agent failed', 'error');
        setGlobalStatus('error', 'Error');
    }

    btn.disabled = false;
    btn.innerHTML = '<i class="bi bi-search"></i> Check Competitors';
    loadProducts(); // Refresh statuses
}

function displayResults(data) {
    const section = document.getElementById('resultsSection');
    section.style.display = 'block';

    // Competitor table
    const tbody = document.getElementById('compTableBody');
    const competitors = data.competitor_data || [];
    document.getElementById('compCount').textContent = competitors.length;

    if (competitors.length === 0) {
        tbody.innerHTML = '<tr><td colspan="5" style="text-align:center;color:var(--text-muted);padding:20px;">No competitor data scraped. Sites may be blocking requests.</td></tr>';
    } else {
        tbody.innerHTML = competitors.map(c => {
            const stockClass = c.stock_status?.toLowerCase().includes('low') ? 'low-stock' :
                               c.stock_status?.toLowerCase().includes('out') ? 'out-stock' : 'in-stock';
            return `<tr>
                <td><strong>${esc(c.source)}</strong></td>
                <td class="price-cell">₹${fmt(c.price)}</td>
                <td><span class="stock-badge ${stockClass}">${esc(c.stock_status)}</span></td>
                <td>${esc(c.seller_type || '')}</td>
                <td><a href="${esc(c.url || '#')}" target="_blank" class="btn btn-sm btn-outline-primary">View</a></td>
            </tr>`;
        }).join('');
    }

    // Demand insights
    const demand = data.demand || {};
    const signals = demand.signals || {};
    document.getElementById('demandScore').textContent = demand.demand_score ?? '—';
    document.getElementById('demandTrend').textContent = demand.trend || '—';
    document.getElementById('demandVelocity').textContent = demand.review_velocity || '—';
    document.getElementById('demandAvg').textContent = signals.avg_competitor_price ? `₹${fmt(signals.avg_competitor_price)}` : '—';
    document.getElementById('demandCompCount').textContent = signals.competitor_count ?? '—';
    document.getElementById('demandScarcity').textContent = signals.scarcity_index ?? '—';

    // Recommendation
    const rec = data.recommendation || {};
    const recPrice = rec.recommended_price;
    const deltaPct = rec.delta_pct;
    const deltaHtml = deltaPct != null ? `<span style="font-size: 0.6em; color: ${deltaPct < 0 ? '#10b981' : '#ef4444'}; margin-left: 8px;">${deltaPct > 0 ? '+' : ''}${deltaPct}%</span>` : '';
    document.getElementById('recPrice').innerHTML = recPrice ? `₹${fmt(recPrice)}${deltaHtml}` : '—';
    const conf = rec.confidence ?? 0;
    document.getElementById('confFill').style.width = `${(conf * 100).toFixed(0)}%`;
    document.getElementById('confVal').textContent = `${(conf * 100).toFixed(0)}%`;
    document.getElementById('recStrategy').textContent = rec.strategy || '—';
    const sourceText = rec.source === 'local_ai' ? 'Local AI' : rec.source === 'fallback' ? 'Fallback / Heuristic' : rec.source === 'error' ? 'Error / Unavailable' : 'LLM / AI Model';
    document.getElementById('recSource').textContent = sourceText;
    document.getElementById('recReasoning').textContent = rec.error || rec.reasoning || 'No reasoning provided.';

    // Guardrails
    const gr = data.guardrail_results || {};
    const rules = gr.rules || {};
    const allPass = gr.all_pass;
    const guardrailBadge = document.getElementById('guardrailBadge');
    const guardrailList = document.getElementById('guardrailList');
    if (recPrice) {
        guardrailBadge.textContent = allPass ? 'All Passed' : 'Issues Found';
        guardrailBadge.className = `badge ${allPass ? 'bg-success' : 'bg-warning text-dark'}`;
        guardrailList.innerHTML = Object.entries(rules).map(([key, rule]) => `
            <div class="guardrail-item">
                <div class="guardrail-icon ${rule.pass ? 'pass' : 'fail'}">
                    <i class="bi ${rule.pass ? 'bi-check' : 'bi-x'}"></i>
                </div>
                <div class="guardrail-info">
                    <div class="guardrail-label">${esc(rule.label)}</div>
                    <div class="guardrail-detail">${esc(rule.detail)}</div>
                </div>
            </div>
        `).join('');
    } else {
        guardrailBadge.textContent = 'N/A';
        guardrailBadge.className = 'badge bg-secondary';
        guardrailList.innerHTML = '<div style="text-align:center;color:var(--text-muted);padding:20px;">No price recommendation to validate.</div>';
    }

    // Show/hide approval buttons
    document.getElementById('recActions').style.display = recPrice ? 'flex' : 'none';
}

// ── Apply Price ──
async function applyPrice() {
    if (!currentResults || !selectedProductId) return;
    const newPrice = currentResults.recommendation?.recommended_price;
    if (!newPrice) return;

    try {
        const res = await fetch(`${API}/apply-price`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ product_id: selectedProductId, new_price: newPrice })
        });
        const updated = await res.json();
        if (updated.error) {
            showToast(updated.error, 'error');
            return;
        }

        // Update right panel price
        document.getElementById('detailPrice').textContent = `₹${fmt(updated.current_price)}`;

        // Hide approval buttons
        document.getElementById('recActions').style.display = 'none';

        // Refresh product list
        loadProducts();
        showToast(`Price updated to ₹${fmt(newPrice)}`, 'success');
    } catch (e) {
        showToast('Failed to apply price', 'error');
    }
}

function rejectPrice() {
    document.getElementById('recActions').style.display = 'none';
    showToast('Recommendation rejected', 'info');
}

// ── Utilities ──
function appendLog(msg) {
    const logBody = document.getElementById('logBody');
    const div = document.createElement('div');
    div.className = 'log-entry';
    div.textContent = msg;
    logBody.appendChild(div);
    logBody.scrollTop = logBody.scrollHeight;
}

function setGlobalStatus(type, text) {
    const el = document.getElementById('globalStatus');
    el.innerHTML = `<span class="status-dot ${type}"></span> ${text}`;
}

function fmt(n) {
    if (n == null) return '—';
    return Number(n).toLocaleString('en-IN', { maximumFractionDigits: 2 });
}

function esc(s) {
    if (!s) return '';
    const d = document.createElement('div');
    d.textContent = s;
    return d.innerHTML;
}

// ── Toast ──
function showToast(msg, type = 'info') {
    let container = document.querySelector('.toast-container');
    if (!container) {
        container = document.createElement('div');
        container.className = 'toast-container';
        document.body.appendChild(container);
    }
    const toast = document.createElement('div');
    toast.className = `toast-msg ${type}`;
    const icon = type === 'success' ? 'bi-check-circle' : type === 'error' ? 'bi-exclamation-circle' : 'bi-info-circle';
    toast.innerHTML = `<i class="bi ${icon}"></i> ${esc(msg)}`;
    container.appendChild(toast);
    setTimeout(() => toast.remove(), 3000);
}
