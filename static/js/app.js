/**
 * AAM Merger V3 — Client-side Interactivity & Alpine.js Store
 */

document.addEventListener('alpine:init', () => {
  // 1. Universal Toast Notification Store
  Alpine.store('toasts', {
    items: [],
    add(message, type = 'info', duration = 4000) {
      const id = Date.now() + Math.random().toString(36).substring(2, 6);
      this.items.push({ id, message, type });
      if (duration > 0) {
        setTimeout(() => {
          this.remove(id);
        }, duration);
      }
    },
    remove(id) {
      this.items = this.items.filter((item) => item.id !== id);
    },
    success(msg) {
      this.add(msg, 'success', 3500);
    },
    error(msg) {
      this.add(msg, 'error', 6000);
    },
    warning(msg) {
      this.add(msg, 'warning', 5000);
    },
    info(msg) {
      this.add(msg, 'info', 4000);
    },
  });

  // 2. Global PDF Drawer Store
  Alpine.store('pdfDrawer', {
    isOpen: false,
    url: '',
    title: 'Document Preview',
    open(url, title = 'Document Preview') {
      this.url = url;
      this.title = title;
      this.isOpen = true;
    },
    close() {
      this.isOpen = false;
      this.url = '';
    },
  });

  // 3. Client Table Search Component
  Alpine.data('tableSearch', () => ({
    query: '',
    init() {
      // The table body is polled with hx-swap every 2 s. The new rows carry
      // no display style, so a filter that was typed into the (unswapped)
      // input would silently stop applying. Re-run it after each swap.
      this._reapplyAfterSwap = (event) => {
        if (event.target && event.target.id === 'po-table') this.filterRows();
      };
      document.addEventListener('htmx:afterSwap', this._reapplyAfterSwap);
    },
    destroy() {
      if (this._reapplyAfterSwap) {
        document.removeEventListener('htmx:afterSwap', this._reapplyAfterSwap);
      }
    },
    filterRows() {
      const q = this.query.toLowerCase().trim();
      const rows = document.querySelectorAll('#po-table-body tr.po-row');
      rows.forEach((row) => {
        const po = (row.dataset.po || '').toLowerCase();
        const status = (row.dataset.status || '').toLowerCase();
        if (!q || po.includes(q) || status.includes(q)) {
          row.style.display = '';
        } else {
          row.style.display = 'none';
        }
      });
    },
  }));
});

// HTMX Global Event Listeners for Rich Feedback

/**
 * The server's own explanation for a refused action, if it sent one.
 *
 * FastAPI puts the operator-facing message in a JSON `detail` field. The
 * generic fallback used to throw it away, so every 409 read "locked" even
 * when the real reason was "no SI number" or "already merged" (audit P2-8).
 */
function serverDetail(xhr) {
  try {
    const body = JSON.parse(xhr.responseText || '');
    if (body && typeof body.detail === 'string' && body.detail.trim()) {
      return body.detail.trim();
    }
  } catch (e) {
    /* not JSON: no detail to show */
  }
  return '';
}

document.addEventListener('htmx:responseError', function (event) {
  const xhr = event.detail.xhr;
  const store = window.Alpine && window.Alpine.store('toasts');
  if (!store) return;

  if (xhr.status === 409) {
    const detail = serverDetail(xhr);
    if (detail && /in progress|locked/i.test(detail)) {
      store.warning('⏳ ' + detail + ' Please wait a moment.');
    } else if (detail) {
      store.warning('⏳ Action blocked: ' + detail);
    } else {
      store.warning('⏳ Action blocked: the server refused this request. Check the PO Set status and try again.');
    }
  } else if (xhr.status === 422) {
    const detail = serverDetail(xhr);
    store.error('❌ Validation error: ' + (detail || 'the server rejected this submission.'));
  } else if (xhr.status === 404) {
    store.error('❌ Not found: The requested record does not exist.');
  } else {
    store.error('❌ Server error (' + xhr.status + '). Please check server logs.');
  }
});

document.addEventListener('htmx:sendError', function () {
  const store = window.Alpine && window.Alpine.store('toasts');
  if (store) {
    store.error('⚠️ Network connection failed. Server might be restarting.');
  }
});

document.addEventListener('htmx:afterRequest', function (event) {
  const xhr = event.detail.xhr;
  if (!xhr) return;
  const store = window.Alpine && window.Alpine.store('toasts');
  if (!store) return;

  // Check custom action headers or endpoints
  const path = event.detail.pathInfo ? event.detail.pathInfo.requestPath : '';

  if (xhr.status >= 200 && xhr.status < 300) {
    if (path.endsWith('/toggle_customs')) {
      store.info('🛡️ Customs requirement setting updated.');
    } else if (path.endsWith('/redo_extract')) {
      store.success('⚡ Document extraction queued.');
    } else if (path.endsWith('/redo_match')) {
      store.success('🔄 Line-item reconciliation re-matched.');
    } else if (/\/merge$/.test(path)) {
      // Gated merge, not Force Merge: report the outcome the server decided.
      let body = {};
      try {
        body = JSON.parse(xhr.responseText);
      } catch (e) {
        /* non-JSON response; fall through to the generic message */
      }
            if (body.status === 'merged') {
        store.success('Merged: ' + (body.merged_output_path || 'packet written'));
      } else if (body.status === 'quarantined') {
        store.warning('Still quarantined: ' + (body.detail || body.reason || 'open the set for the reason') +
          '. Use Re-Match to re-enter reconciliation, or Force Merge to override.');
      } else if (body.status === 'pending') {
        store.info('Still pending: ' + (body.detail || body.reason || 'waiting on a document') +
          '. Add the missing document, then press Merge Now again.');
      } else {
        store.warning('Not merged - still ' + (body.status || 'unresolved') +
          (body.reason ? ' (' + body.reason.replace(/_/g, ' ') + ')' : '') +
          '. Review the reason on the set page.');
      }
    } else if (path.endsWith('/force_merge')) {
      store.success('🚀 Force merge completed and audit log recorded.');
    } else if (path === '/sync') {
      store.success('⚡ Sync scan triggered.');
    }
  }
});

// Close drawer on Escape key
document.addEventListener('keydown', function (e) {
  if (e.key === 'Escape') {
    const drawer = window.Alpine && window.Alpine.store('pdfDrawer');
    if (drawer && drawer.isOpen) {
      drawer.close();
    }
    const modals = document.querySelectorAll('.modal-backdrop.open');
    modals.forEach((m) => m.classList.remove('open'));
  }
});


// Keep the Scan button honest. `/sync/status` is the authority; the POST
// returns as soon as the background thread starts, so a one-shot indicator
// stayed enabled while the sweep was still running.
async function refreshSyncState() {
  if (typeof document === 'undefined' || typeof document.getElementById !== 'function') return;
  if (typeof fetch !== 'function') return;
  const btn = document.getElementById('sync-btn');
  if (!btn) return;
  try {
    const response = await fetch('/sync/status', { cache: 'no-store' });
    if (!response.ok) return;
    const state = await response.json();
    btn.disabled = !!state.running;
    const spinner = document.getElementById('sync-spinner');
    if (spinner) spinner.style.display = state.running ? 'inline-flex' : 'none';
  } catch (e) {
    /* a status poll failure must not break the dashboard */
  }
}
if (typeof window !== 'undefined' && typeof window.addEventListener === 'function') {
  window.addEventListener('load', () => {
    refreshSyncState();
    if (typeof setInterval === 'function') {
      setInterval(refreshSyncState, 5000);
    }
  });
}
