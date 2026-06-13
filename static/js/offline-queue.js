/* Tronic POS — Offline Sale Queue
 * Saves cash/split sales to IndexedDB when the server is unreachable.
 * Automatically flushes them to /api/sale when the device comes back online.
 */

const _IDB_NAME = 'tronic_offline';
const _IDB_STORE = 'pending_sales';
let _idb = null;

async function _openIDB() {
  if (_idb) return _idb;
  return new Promise((resolve, reject) => {
    const req = indexedDB.open(_IDB_NAME, 1);
    req.onupgradeneeded = e => {
      const db = e.target.result;
      if (!db.objectStoreNames.contains(_IDB_STORE)) {
        db.createObjectStore(_IDB_STORE, { autoIncrement: true });
      }
    };
    req.onsuccess = e => { _idb = e.target.result; resolve(_idb); };
    req.onerror = () => reject(req.error);
  });
}

async function queueSale(payload) {
  const db = await _openIDB();
  return new Promise((resolve, reject) => {
    const tx = db.transaction(_IDB_STORE, 'readwrite');
    const store = tx.objectStore(_IDB_STORE);
    const record = { ...payload, _queued_at: new Date().toISOString() };
    const req = store.add(record);
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error);
  });
}

async function _getPending() {
  const db = await _openIDB();
  const sales = await new Promise((resolve, reject) => {
    const tx = db.transaction(_IDB_STORE, 'readonly');
    const req = tx.objectStore(_IDB_STORE).getAll();
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error);
  });
  const keys = await new Promise((resolve, reject) => {
    const tx = db.transaction(_IDB_STORE, 'readonly');
    const req = tx.objectStore(_IDB_STORE).getAllKeys();
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error);
  });
  return { sales, keys };
}

async function _deleteRecord(key) {
  const db = await _openIDB();
  return new Promise((resolve, reject) => {
    const tx = db.transaction(_IDB_STORE, 'readwrite');
    const req = tx.objectStore(_IDB_STORE).delete(key);
    req.onsuccess = () => resolve();
    req.onerror = () => reject(req.error);
  });
}

async function pendingCount() {
  const db = await _openIDB();
  return new Promise((resolve, reject) => {
    const tx = db.transaction(_IDB_STORE, 'readonly');
    const req = tx.objectStore(_IDB_STORE).count();
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error);
  });
}

async function updateSyncBadge() {
  const count = await pendingCount();
  const badge = document.getElementById('offline-sync-badge');
  if (!badge) return;
  if (count > 0) {
    badge.textContent = count;
    badge.classList.remove('d-none');
  } else {
    badge.classList.add('d-none');
  }
}

function _showSyncToast(message, type = 'success') {
  const div = document.createElement('div');
  div.className = `alert alert-${type} alert-dismissible fade show position-fixed bottom-0 end-0 m-3`;
  div.style.zIndex = 9999;
  div.innerHTML = `<i class="bi bi-cloud-upload me-2"></i>${message}
    <button type="button" class="btn-close" data-bs-dismiss="alert"></button>`;
  document.body.appendChild(div);
  setTimeout(() => div.remove(), 5000);
}

async function flushQueue() {
  const { sales, keys } = await _getPending();
  if (sales.length === 0) return;

  let synced = 0;
  for (let i = 0; i < sales.length; i++) {
    const payload = { ...sales[i] };
    delete payload._queued_at;
    try {
      const res = await fetch('/api/sale', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
      if (res.ok) {
        await _deleteRecord(keys[i]);
        synced++;
      }
    } catch {
      break; // still offline — try again later
    }
  }

  await updateSyncBadge();
  if (synced > 0) {
    _showSyncToast(`${synced} offline sale${synced > 1 ? 's' : ''} synced successfully.`);
  }
}

// ── Connectivity monitoring ───────────────────────────────────────────────────

function _setOfflineBanner(offline) {
  const banner = document.getElementById('offline-banner');
  if (banner) banner.classList.toggle('d-none', !offline);
}

window.addEventListener('online', async () => {
  _setOfflineBanner(false);
  await flushQueue();
});

window.addEventListener('offline', () => {
  _setOfflineBanner(true);
});

document.addEventListener('DOMContentLoaded', async () => {
  _setOfflineBanner(!navigator.onLine);
  await updateSyncBadge();
  if (navigator.onLine) {
    await flushQueue();
  }
});
