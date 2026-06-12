/* Tronic POS — Point of Sale Logic */

const cart = [];
let currentCategory = '';
let ecoRef = '';
let ecoPhone = '';
let pendingSaleId = null;

const VAT_REGISTERED = document.querySelector('meta[name="vat_registered"]')?.content === '1';

// ── Product Loading ──────────────────────────────────────────────────────────

async function loadProducts(q = '', cat = '') {
  const grid = document.getElementById('productGrid');
  const params = new URLSearchParams({ q, category: cat });
  try {
    const res = await fetch('/api/products/search?' + params);
    const products = await res.json();
    renderProducts(products);
  } catch (e) {
    grid.innerHTML = '<div class="text-danger p-3">Failed to load products</div>';
  }
}

function renderProducts(products) {
  const grid = document.getElementById('productGrid');
  if (!products.length) {
    grid.innerHTML = '<div class="text-center text-muted py-5 w-100"><i class="bi bi-search" style="font-size:2rem"></i><p class="mt-2">No products found</p></div>';
    return;
  }
  grid.innerHTML = products.map(p => {
    const low = p.stock_quantity <= p.min_stock_level;
    return `
      <div class="product-card" onclick="addToCart(${p.id},'${escHtml(p.name)}',${p.selling_price},${p.cost_price},'${p.tax_type}')">
        <div class="prod-name">${escHtml(p.name)}</div>
        <div class="prod-price">$${Number(p.selling_price).toFixed(2)}</div>
        <div class="prod-stock ${low ? 'low' : ''}">
          ${low ? '⚠ ' : ''}Stock: ${p.stock_quantity} ${p.unit}
        </div>
      </div>`;
  }).join('');
}

// ── Cart ─────────────────────────────────────────────────────────────────────

function addToCart(id, name, price, cost, taxType) {
  const existing = cart.find(i => i.id === id);
  if (existing) {
    existing.qty += 1;
  } else {
    cart.push({ id, name, price, cost, taxType, qty: 1 });
  }
  renderCart();
}

function renderCart() {
  const container = document.getElementById('cartItems');
  const empty = document.getElementById('emptyCart');

  if (!cart.length) {
    container.innerHTML = '';
    container.appendChild(empty);
    empty.classList.remove('d-none');
    updateTotals();
    return;
  }
  empty.classList.add('d-none');

  container.innerHTML = cart.map((item, idx) => `
    <div class="cart-item">
      <div class="cart-item-name">${escHtml(item.name)}<br>
        <small class="text-muted">$${item.price.toFixed(2)} each</small>
      </div>
      <button class="qty-btn" onclick="changeQty(${idx},-1)">−</button>
      <input class="qty-input" type="number" min="1" value="${item.qty}"
        onchange="setQty(${idx},this.value)">
      <button class="qty-btn" onclick="changeQty(${idx},1)">+</button>
      <div class="cart-item-price">$${(item.price * item.qty).toFixed(2)}</div>
      <button class="btn btn-sm text-danger p-0 ms-1" onclick="removeItem(${idx})">
        <i class="bi bi-x-circle"></i>
      </button>
    </div>`).join('');

  updateTotals();
}

function changeQty(idx, delta) {
  cart[idx].qty = Math.max(1, cart[idx].qty + delta);
  renderCart();
}

function setQty(idx, val) {
  const q = parseInt(val);
  if (q > 0) cart[idx].qty = q;
  renderCart();
}

function removeItem(idx) {
  cart.splice(idx, 1);
  renderCart();
}

function calcTotals() {
  const discount = parseFloat(document.getElementById('discountInput').value) || 0;
  let subtotal = 0, tax = 0;
  for (const item of cart) {
    const lineTotal = item.price * item.qty;
    // VAT is included in selling price if registered
    const rate = getVatRate(item.taxType);
    const lineTax = rate > 0 ? (lineTotal * rate) / (100 + rate) : 0;
    tax += lineTax;
    subtotal += lineTotal - lineTax;
  }
  const total = subtotal + tax - discount;
  return { subtotal, tax, discount, total: Math.max(0, total) };
}

function getVatRate(taxType) {
  if (!VAT_REGISTERED) return 0;
  const baseRate = parseFloat(document.querySelector('meta[name="vat_rate"]')?.content || 15);
  return taxType === 'standard' ? baseRate : 0;
}

function updateTotals() {
  const t = calcTotals();
  document.getElementById('cartSubtotal').textContent = '$' + t.subtotal.toFixed(2);
  document.getElementById('cartTax').textContent = '$' + t.tax.toFixed(2);
  document.getElementById('cartDiscount').textContent = '-$' + t.discount.toFixed(2);
  document.getElementById('cartTotal').textContent = '$' + t.total.toFixed(2);
}

// ── Payment — Cash ────────────────────────────────────────────────────────────

document.getElementById('payCash').addEventListener('click', () => {
  if (!cart.length) return showAlert('Cart is empty!', 'warning');
  const { total } = calcTotals();
  document.getElementById('cashDue').textContent = '$' + total.toFixed(2);
  document.getElementById('cashTendered').value = total.toFixed(2);
  document.getElementById('cashChange').textContent = '$0.00';
  // Quick amount buttons
  const quickDiv = document.getElementById('quickAmounts');
  const quickVals = [Math.ceil(total), Math.ceil(total / 5) * 5, Math.ceil(total / 10) * 10,
                     Math.ceil(total / 20) * 20, Math.ceil(total / 50) * 50].filter((v,i,a) => a.indexOf(v)===i && v >= total).slice(0,4);
  quickDiv.innerHTML = quickVals.map(v =>
    `<button class="btn btn-outline-success btn-sm" onclick="setTendered(${v})">$${v}</button>`
  ).join('');
  new bootstrap.Modal(document.getElementById('cashModal')).show();
});

function setTendered(amount) {
  document.getElementById('cashTendered').value = amount.toFixed(2);
  calcChange();
}

function calcChange() {
  const due = calcTotals().total;
  const tendered = parseFloat(document.getElementById('cashTendered').value) || 0;
  const change = Math.max(0, tendered - due);
  document.getElementById('cashChange').textContent = '$' + change.toFixed(2);
  document.getElementById('completeCash').disabled = tendered < due;
}

document.getElementById('cashTendered').addEventListener('input', calcChange);

document.getElementById('completeCash').addEventListener('click', async () => {
  const { total, discount } = calcTotals();
  const tendered = parseFloat(document.getElementById('cashTendered').value) || 0;
  const change = Math.max(0, tendered - total);
  await completeSale({ payment_method: 'cash', amount_paid: tendered, change_given: change, discount_amount: discount });
  bootstrap.Modal.getInstance(document.getElementById('cashModal')).hide();
});

// ── Payment — EcoCash ─────────────────────────────────────────────────────────

document.getElementById('payEco').addEventListener('click', () => {
  if (!cart.length) return showAlert('Cart is empty!', 'warning');
  const { total } = calcTotals();
  document.getElementById('ecoDue').textContent = '$' + total.toFixed(2);
  document.getElementById('ecoPhone').value = '';
  document.getElementById('ecoStatus').classList.add('d-none');
  document.getElementById('ecoSuccess').classList.add('d-none');
  document.getElementById('ecoForm').classList.remove('d-none');
  document.getElementById('sendEcoCash').classList.remove('d-none');
  document.getElementById('completeEco').classList.add('d-none');
  new bootstrap.Modal(document.getElementById('ecoModal')).show();
});

document.getElementById('sendEcoCash').addEventListener('click', async () => {
  const { total, discount } = calcTotals();
  ecoPhone = document.getElementById('ecoPhone').value.trim();
  if (!ecoPhone) return showAlert('Enter customer phone number', 'danger');
  ecoRef = 'ECO-' + Date.now();

  document.getElementById('ecoForm').classList.add('d-none');
  document.getElementById('sendEcoCash').classList.add('d-none');
  document.getElementById('ecoCancelBtn').disabled = true;
  const statusDiv = document.getElementById('ecoStatus');
  statusDiv.classList.remove('d-none');
  statusDiv.className = 'mt-3 ecocash-status pending';
  document.getElementById('ecoStatusText').textContent = 'Sending payment request to ' + ecoPhone + '…';

  try {
    const res = await fetch('/api/ecocash/initiate', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ phone: ecoPhone, amount: total, reference: ecoRef })
    });
    const data = await res.json();

    if (data.success) {
      document.getElementById('ecoStatusText').textContent =
        data.message || 'Waiting for customer to enter PIN…';
      // Poll for status
      pollEcoCash(ecoRef, total, discount);
    } else {
      statusDiv.className = 'mt-3 ecocash-status failed';
      document.getElementById('ecoStatusText').textContent = data.message || 'Payment failed';
      document.getElementById('sendEcoCash').classList.remove('d-none');
      document.getElementById('ecoForm').classList.remove('d-none');
      document.getElementById('ecoCancelBtn').disabled = false;
    }
  } catch (e) {
    statusDiv.className = 'mt-3 ecocash-status failed';
    document.getElementById('ecoStatusText').textContent = 'Network error. Try again.';
    document.getElementById('sendEcoCash').classList.remove('d-none');
    document.getElementById('ecoCancelBtn').disabled = false;
  }
});

async function pollEcoCash(ref, total, discount, attempts = 0) {
  if (attempts > 20) {
    document.getElementById('ecoStatus').className = 'mt-3 ecocash-status failed';
    document.getElementById('ecoStatusText').textContent = 'Timeout — customer did not respond. Try again.';
    document.getElementById('sendEcoCash').classList.remove('d-none');
    document.getElementById('ecoForm').classList.remove('d-none');
    document.getElementById('ecoCancelBtn').disabled = false;
    return;
  }
  await sleep(2000);
  try {
    const res = await fetch('/api/ecocash/status/' + ref);
    const data = await res.json();
    if (data.status === 'completed') {
      document.getElementById('ecoStatus').classList.add('d-none');
      const successDiv = document.getElementById('ecoSuccess');
      successDiv.classList.remove('d-none');
      document.getElementById('ecoRef').textContent = 'EcoCash Ref: ' + (data.ecocash_reference || ref);
      document.getElementById('completeEco').classList.remove('d-none');
      document.getElementById('ecoCancelBtn').disabled = false;
    } else if (data.status === 'failed') {
      document.getElementById('ecoStatus').className = 'mt-3 ecocash-status failed';
      document.getElementById('ecoStatusText').textContent = data.message || 'Payment declined by customer.';
      document.getElementById('sendEcoCash').classList.remove('d-none');
      document.getElementById('ecoCancelBtn').disabled = false;
    } else {
      pollEcoCash(ref, total, discount, attempts + 1);
    }
  } catch (e) {
    pollEcoCash(ref, total, discount, attempts + 1);
  }
}

document.getElementById('completeEco').addEventListener('click', async () => {
  const { total, discount } = calcTotals();
  await completeSale({
    payment_method: 'ecocash', amount_paid: total, discount_amount: discount,
    ecocash_number: ecoPhone, ecocash_ref: ecoRef
  });
  bootstrap.Modal.getInstance(document.getElementById('ecoModal')).hide();
});

// ── Payment — Split ───────────────────────────────────────────────────────────

document.getElementById('paySplit').addEventListener('click', () => {
  if (!cart.length) return showAlert('Cart is empty!', 'warning');
  const { total } = calcTotals();
  document.getElementById('splitDue').textContent = '$' + total.toFixed(2);
  document.getElementById('splitCash').value = '0';
  document.getElementById('splitEco').value = total.toFixed(2);
  document.getElementById('splitPhone').value = '';
  updateSplitRemaining();
  new bootstrap.Modal(document.getElementById('splitModal')).show();
});

function updateSplitRemaining() {
  const { total } = calcTotals();
  const cash = parseFloat(document.getElementById('splitCash').value) || 0;
  const eco = parseFloat(document.getElementById('splitEco').value) || 0;
  const rem = total - cash - eco;
  document.getElementById('splitRemaining').textContent = '$' + rem.toFixed(2);
  document.getElementById('completeSplit').disabled = Math.abs(rem) > 0.01;
}

document.getElementById('splitCash').addEventListener('input', updateSplitRemaining);
document.getElementById('splitEco').addEventListener('input', updateSplitRemaining);

document.getElementById('completeSplit').addEventListener('click', async () => {
  const { total, discount } = calcTotals();
  const cash = parseFloat(document.getElementById('splitCash').value) || 0;
  const eco = parseFloat(document.getElementById('splitEco').value) || 0;
  const phone = document.getElementById('splitPhone').value.trim();
  if (eco > 0 && !phone) return showAlert('Enter EcoCash phone number', 'danger');

  // If EcoCash portion > 0, initiate EcoCash first
  if (eco > 0) {
    const splitRef = 'SPLIT-' + Date.now();
    const res = await fetch('/api/ecocash/initiate', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ phone, amount: eco, reference: splitRef })
    });
    const data = await res.json();
    if (!data.success) return showAlert('EcoCash error: ' + data.message, 'danger');
    ecoRef = splitRef;
    ecoPhone = phone;
  }

  await completeSale({
    payment_method: 'split', amount_paid: total, discount_amount: discount,
    cash_amount: cash, ecocash_amount: eco,
    ecocash_number: phone, ecocash_ref: ecoRef
  });
  bootstrap.Modal.getInstance(document.getElementById('splitModal')).hide();
});

// ── Complete Sale ─────────────────────────────────────────────────────────────

async function completeSale(paymentData) {
  const { discount } = calcTotals();
  const items = cart.map(item => ({
    product_id: item.id, quantity: item.qty
  }));
  const payload = {
    items,
    ...paymentData,
    cashier: document.getElementById('cashierInput').value || 'Admin',
  };

  try {
    const res = await fetch('/api/sale', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload)
    });
    const data = await res.json();
    if (data.success) {
      pendingSaleId = data.sale_id;
      document.getElementById('receiptRef').textContent = data.reference;
      const change = data.change || 0;
      document.getElementById('receiptChange').innerHTML =
        change > 0 ? `Change: <strong class="text-success">$${change.toFixed(2)}</strong>` : '';
      document.getElementById('receiptPrintBtn').href = '/receipt/' + data.sale_id;
      new bootstrap.Modal(document.getElementById('receiptModal')).show();
      clearCartData();
    } else {
      showAlert('Sale failed: ' + (data.error || 'Unknown error'), 'danger');
    }
  } catch (e) {
    showAlert('Network error. Please try again.', 'danger');
  }
}

// ── Controls ──────────────────────────────────────────────────────────────────

document.getElementById('clearCart').addEventListener('click', () => {
  if (cart.length && !confirm('Clear cart?')) return;
  clearCartData();
});

document.getElementById('receiptClose').addEventListener('click', () => {
  bootstrap.Modal.getInstance(document.getElementById('receiptModal')).hide();
});

document.getElementById('discountInput').addEventListener('input', updateTotals);

function clearCartData() {
  cart.length = 0;
  renderCart();
}

// Search
let searchTimer;
document.getElementById('productSearch').addEventListener('input', e => {
  clearTimeout(searchTimer);
  searchTimer = setTimeout(() => loadProducts(e.target.value, currentCategory), 300);
});
document.getElementById('clearSearch').addEventListener('click', () => {
  document.getElementById('productSearch').value = '';
  loadProducts('', currentCategory);
});

// Category filter
document.getElementById('catFilter').addEventListener('click', e => {
  const btn = e.target.closest('[data-cat]');
  if (!btn) return;
  document.querySelectorAll('#catFilter .btn').forEach(b => {
    b.className = b === btn ? 'btn btn-sm btn-primary active' : 'btn btn-sm btn-outline-secondary';
  });
  currentCategory = btn.dataset.cat;
  loadProducts(document.getElementById('productSearch').value, currentCategory);
});

// ── Helpers ───────────────────────────────────────────────────────────────────

function escHtml(str) {
  return String(str).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}
function sleep(ms) { return new Promise(r => setTimeout(r, ms)); }
function showAlert(msg, type) {
  const div = document.createElement('div');
  div.className = `alert alert-${type} alert-dismissible fade show position-fixed top-0 start-50 translate-middle-x mt-3`;
  div.style.zIndex = 9999;
  div.innerHTML = msg + '<button type="button" class="btn-close" data-bs-dismiss="alert"></button>';
  document.body.appendChild(div);
  setTimeout(() => div.remove(), 4000);
}

// Init
loadProducts();
// Generate cart ref
document.getElementById('cartRef').textContent = '#' + Math.random().toString(36).substr(2,6).toUpperCase();
