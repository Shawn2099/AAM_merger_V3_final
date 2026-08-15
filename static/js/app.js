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
    filterRows() {
      const q = this.query.toLowerCase().trim();
      const rows = document.querySelectorAll('#po-table-body tr.po-row');
      rows.forEach((row) => {
        const po = (row.dataset.po || '').toLowerCase();
        const vendor = (row.dataset.vendor || '').toLowerCase();
        const status = (row.dataset.status || '').toLowerCase();
        if (!q || po.includes(q) || vendor.includes(q) || status.includes(q)) {
          row.style.display = '';
        } else {
          row.style.display = 'none';
        }
      });
    },
  }));
});

// HTMX Global Event Listeners for Rich Feedback
document.addEventListener('htmx:responseError', function (event) {
  const xhr = event.detail.xhr;
  const store = window.Alpine && window.Alpine.store('toasts');
  if (!store) return;

  if (xhr.status === 409) {
    store.warning('⏳ Action blocked: PO Set is currently locked by a background operation. Please wait a moment.');
  } else if (xhr.status === 422) {
    store.error('❌ Validation error: ' + (xhr.responseText || 'Invalid data submitted.'));
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
