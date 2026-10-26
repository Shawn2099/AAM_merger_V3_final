/**
 * AAM Merger V3 — Client-side Interactivity & Alpine.js Store
 */

document.addEventListener('alpine:init', () => {
  // 1. Universal Toast Notification Store
  //
  // `remember` exists because most actions reload the page they were fired
  // from, which used to destroy the only confirmation the operator got
  // (F-07). A remembered toast is replayed once by the page that reloads,
  // and only on the same URL, so normal navigation cannot resurrect it.
  Alpine.store('toasts', {
    items: [],
    add(message, type = 'info', duration = 4000, remember = false) {
      const id = Date.now() + Math.random().toString(36).substring(2, 6);
      this.items.push({ id, message, type });
      if (remember) rememberToast(message, type);
      if (duration > 0) {
        setTimeout(() => {
          this.remove(id);
        }, duration);
      }
    },
    remove(id) {
      this.items = this.items.filter((item) => item.id !== id);
    },
    success(msg, remember = false) {
      this.add(msg, 'success', 3500, remember);
    },
    error(msg, remember = false) {
      this.add(msg, 'error', 6000, remember);
    },
    warning(msg, remember = false) {
      this.add(msg, 'warning', 5000, remember);
    },
    info(msg, remember = false) {
      this.add(msg, 'info', 4000, remember);
    },
  });

  // 2. Global PDF Drawer Store
  Alpine.store('pdfDrawer', {
    isOpen: false,
    url: '',
    title: 'Document Preview',
    trigger: null,
    open(url, title = 'Document Preview', trigger = null) {
      this.trigger = trigger || (document.activeElement && document.activeElement !== document.body ? document.activeElement : null);
      this.url = url;
      this.title = title;
      this.isOpen = true;
    },
    close() {
      this.isOpen = false;
      this.url = '';
      const el = this.trigger;
      this.trigger = null;
      if (el && typeof el.focus === 'function') {
        el.focus();
      }
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

//: Where an action's result is parked for exactly one reload.
const LAST_TOAST_KEY = 'aam.lastToast';
//: Older than this, the reason for replaying is gone (shorten if a stale
//: toast ever shows up on an unrelated page).
const LAST_TOAST_MAX_AGE_MS = 15000;

function currentLocationKey() {
  if (typeof location === 'undefined') return '';
  return location.pathname + location.search;
}

/** Park a toast for the reload the same action is about to trigger. */
function rememberToast(message, type) {
  try {
    sessionStorage.setItem(
      LAST_TOAST_KEY,
      JSON.stringify({ message, type, path: currentLocationKey(), t: Date.now() })
    );
  } catch (e) {
    /* private mode / storage full: the toast is still shown on this page */
  }
}

/** Replay the parked toast, once, on the page that reloaded. */
const TOAST_TYPES = ['success', 'info', 'warning', 'error'];

function replayRememberedToast() {
  let saved = null;
  try {
    saved = JSON.parse(sessionStorage.getItem(LAST_TOAST_KEY) || 'null');
    sessionStorage.removeItem(LAST_TOAST_KEY);
  } catch (e) {
    return;
  }
  if (!saved || typeof saved.message !== 'string') return;
  if (!TOAST_TYPES.includes(saved.type)) return;
  if (saved.path && saved.path !== currentLocationKey()) return;
  if (Date.now() - Number(saved.t || 0) > LAST_TOAST_MAX_AGE_MS) return;
  const store = window.Alpine && window.Alpine.store('toasts');
  if (store) store.add(saved.message, saved.type);
}

document.addEventListener('alpine:initialized', replayRememberedToast);

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
    // No leading icon: the toast template already renders one for its type.
    store.error('Validation error: ' + (detail || 'the server rejected this submission.'));
  } else if (xhr.status === 404) {
    store.error('Not found: The requested record does not exist.');
  } else {
    store.error('Server error (' + xhr.status + '). Please check server logs.');
  }
});

document.addEventListener('htmx:sendError', function () {
  const store = window.Alpine && window.Alpine.store('toasts');
  if (store) {
    store.error('Network connection failed. Server might be restarting.');
  }
});

// HX-Refresh reloads the page from the response (the no-PO reclassify path
// does this). htmx 1.9 reloads before `htmx:beforeSwap` fires, so the
// message row the server sent would never be seen. `beforeOnLoad` is the one
// event that still runs: keep its text as a toast, read as text (never HTML).
document.addEventListener('htmx:beforeOnLoad', function (event) {
  const xhr = event.detail && event.detail.xhr;
  if (!xhr || !xhr.getResponseHeader) return;
  if (xhr.getResponseHeader('HX-Refresh') !== 'true') return;
  const store = window.Alpine && window.Alpine.store('toasts');
  if (!store) return;
  try {
    // A bare `<tr>` is discarded by the HTML parser, so give it a table to
    // live in. DOMParser output is inert and only `textContent` is read.
    const doc = new DOMParser().parseFromString(
      '<table><tbody>' + (xhr.responseText || '') + '</tbody></table>',
      'text/html'
    );
    const cell = doc.querySelector('td[colspan]');
    const text = (cell && cell.textContent ? cell.textContent : '').replace(/\s+/g, ' ').trim();
    if (text) store.warning(text, true);
  } catch (e) {
    /* the refresh still happens; only the explanation is lost */
  }
});

function formatReason(raw) {
  if (!raw || typeof raw !== 'string') return '';
  const REASONS = {
    missing_dn_document: 'Awaiting Delivery Note (DN)',
    missing_si_document: 'Awaiting Sales Invoice (SI)',
    missing_po_document: 'Awaiting Purchase Order (PO)',
    multiple_po_documents: 'Multiple PO files attached',
    multiple_dn_documents: 'Multiple Delivery Notes attached',
    multiple_si_documents: 'Multiple Sales Invoices attached',
    po_reference_mismatch: 'Conflicting PO reference',
    quantity_mismatch: 'Quantity discrepancy between documents',
    blocked_customs: 'Missing Customs / Shipping documents',
    unmatched_vendor_line: 'Vendor line not found in Purchase Order',
  };
  const trimmed = raw.trim();
  if (REASONS[trimmed]) return REASONS[trimmed];
  return trimmed.replace(/_/g, ' ');
}

document.addEventListener('htmx:afterRequest', function (event) {
  const xhr = event.detail.xhr;
  if (!xhr) return;
  const store = window.Alpine && window.Alpine.store('toasts');
  if (!store) return;

  // Check custom action headers or endpoints
  const path = event.detail.pathInfo ? event.detail.pathInfo.requestPath : '';

  if (xhr.status >= 200 && xhr.status < 300) {
    if (path.endsWith('/toggle_customs')) {
      let tbody = {};
      try {
        tbody = JSON.parse(xhr.responseText);
      } catch (e) {
        /* non-JSON response; fall through to the generic message */
      }
      if (tbody.append_pending) {
        store.warning('Customs requirement saved, but the merged packet was NOT rebuilt: ' +
          (tbody.append_error || 'unknown error') + '. It will retry on the next sync.');
      } else {
        // The page reloads next; the toast travels with it.
        store.info('🛡️ Customs requirement setting updated.', true);
      }
    } else if (path.endsWith('/redo_extract')) {
      let body = {};
      try {
        body = JSON.parse(xhr.responseText);
      } catch (e) {
        /* non-JSON: fall through to the generic message */
      }
      const results = Array.isArray(body.extractions) ? body.extractions : [];
      const failed = results.filter(
        (item) => item && (item.error || item.status !== 'valid')
      );
      const firstError = failed.length
        ? failed[0].error || 'status ' + (failed[0].status || 'unknown')
        : '';
      if (results.length === 0) {
        store.info('Re-Extract: this set has no AI-read documents to re-read.', true);
      } else if (failed.length === results.length) {
        store.error(
          'Extraction failed for all ' + results.length + ' document(s): ' + firstError,
          true
        );
      } else if (failed.length > 0) {
        store.warning(
          'Read ' + (results.length - failed.length) + ' of ' + results.length +
            ' document(s); ' + failed.length + ' failed: ' + firstError,
          true
        );
      } else {
        store.success('Re-read ' + results.length + ' document(s).', true);
      }
    } else if (path.endsWith('/redo_match')) {
      // Say what matching decided, not only that it ran (I-43).
      let body = {};
      try {
        body = JSON.parse(xhr.responseText);
      } catch (e) {
        /* non-JSON response; fall through to the generic message */
      }
      const rec = body.reconciliation || {};
      const why = formatReason(rec.detail || rec.reason || '');
      if (rec.status === 'merged') {
        store.success('Re-matched: the packet is merged.', true);
      } else if (rec.status === 'pending') {
        store.info('Re-matched: still waiting' + (why ? ' (' + why + ')' : '') + '.', true);
      } else if (rec.status === 'mismatched' || rec.status === 'quarantined') {
        store.warning('Re-matched: ' + rec.status.replace('_', ' ') +
          (why ? ' (' + why + ')' : '') + '.', true);
      } else {
        store.success('Re-matched.', true);
      }
    } else if (/\/merge$/.test(path)) {
      // Gated merge, not Force Merge: report the outcome the server decided.
      let body = {};
      try {
        body = JSON.parse(xhr.responseText);
      } catch (e) {
        /* non-JSON response; fall through to the generic message */
      }
      const why = formatReason(body.detail || body.reason || '');
      if (body.status === 'merged') {
        // The operator needs the file name, not a server path (F-20).
        const name = String(body.merged_output_path || '').split(/[\\/]/).pop();
        store.success('Merged: ' + (name || 'packet written'), true);
      } else if (body.status === 'quarantined') {
        store.warning('Still quarantined: ' + (why || 'open the set for the reason') +
          '. Use Re-Match to re-enter reconciliation, or Force Merge to override.', true);
      } else if (body.status === 'pending') {
        store.info('Still pending: ' + (why || 'waiting on a document') +
          '. Add the missing document, then press Merge Now again.', true);
      } else {
        store.warning('Not merged - still ' + (body.status || 'unresolved') + ': ' + (why || 'open the set for the reason') +
          '. Review the reason on the set page.', true);
      }
    } else if (path.endsWith('/force_merge')) {
      store.success('🚀 Force merge completed and audit log recorded.', true);
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

// ---------------------------------------------------------------------------
// Modal keyboard focus (F-08)
//
// The dialogs are plain divs toggled by a class, opened from many buttons in
// the templates. Rather than edit every button, watch the backdrop's class:
// on open, remember what had focus and move focus inside; on close, give it
// back. Tab is confined to the open dialog, which is what `aria-modal="true"`
// already promises assistive technology.
// ---------------------------------------------------------------------------

const FOCUSABLE_SELECTOR = [
  'a[href]',
  'button:not([disabled])',
  'input:not([disabled]):not([type="hidden"])',
  'select:not([disabled])',
  'textarea:not([disabled])',
  '[tabindex]:not([tabindex="-1"])',
].join(', ');

const modalTriggers = new WeakMap();

function visibleFocusables(root) {
  return Array.from(root.querySelectorAll(FOCUSABLE_SELECTOR)).filter((el) => {
    if (el.hidden || el.getAttribute('aria-hidden') === 'true') return false;
    const style = window.getComputedStyle(el);
    return (
      style.display !== 'none' &&
      style.visibility !== 'hidden' &&
      el.getClientRects().length > 0
    );
  });
}

function takeModalFocus(modal) {
  const active = document.activeElement;
  modalTriggers.set(modal, active instanceof HTMLElement ? active : null);
  const dialog = modal.querySelector('.modal-dialog') || modal;
  if (!dialog.hasAttribute('tabindex')) dialog.setAttribute('tabindex', '-1');
  // A dialog that exists to collect text starts in the text field; any other
  // starts at its first control, and an empty one at the dialog itself.
  const field = dialog.querySelector('textarea:not([disabled])');
  const target = field || visibleFocusables(dialog)[0] || dialog;
  target.focus();
}

function releaseModalFocus(modal) {
  const trigger = modalTriggers.get(modal);
  modalTriggers.delete(modal);
  if (trigger && document.contains(trigger) && typeof trigger.focus === 'function') {
    trigger.focus();
  }
}

const openModalElements = new Set();

function watchModalClasses() {
  new MutationObserver((mutations) => {
    for (const mutation of mutations) {
      const el = mutation.target;
      if (!(el instanceof Element) || !el.classList.contains('modal-backdrop')) continue;
      const isOpen = el.classList.contains('open');
      if (isOpen && !openModalElements.has(el)) {
        openModalElements.add(el);
        takeModalFocus(el);
      } else if (!isOpen && openModalElements.has(el)) {
        openModalElements.delete(el);
        releaseModalFocus(el);
      }
    }
  }).observe(document.body, { attributes: true, attributeFilter: ['class'], subtree: true });
}

// This file is loaded in <head>, where `document.body` does not exist yet.
if (document.body) {
  watchModalClasses();
} else {
  document.addEventListener('DOMContentLoaded', watchModalClasses);
}

document.addEventListener('keydown', function (e) {
  if (e.key !== 'Tab') return;
  const modal = document.querySelector('.modal-backdrop.open');
  if (!modal) return;
  const items = visibleFocusables(modal);
  if (items.length === 0) {
    e.preventDefault();
    return;
  }
  const first = items[0];
  const last = items[items.length - 1];
  const active = document.activeElement;
  if (!modal.contains(active)) {
    e.preventDefault();
    first.focus();
  } else if (e.shiftKey && active === first) {
    e.preventDefault();
    last.focus();
  } else if (!e.shiftKey && active === last) {
    e.preventDefault();
    first.focus();
  }
});


// ---------------------------------------------------------------------------
// Real-time Ingestion Progress & Service Health Polling (FR-CONC-3)
// ---------------------------------------------------------------------------

let _wasSyncRunning = false;
let _completionHideTimeout = null;

function formatSeconds(sec) {
  if (!sec || sec < 0) return '0s';
  const m = Math.floor(sec / 60);
  const s = Math.floor(sec % 60);
  if (m === 0) return s + 's';
  return m + 'm ' + s + 's';
}

function updateHealthIndicator(isOnline) {
  const dot = document.getElementById('health-dot');
  const text = document.getElementById('health-text');
  if (dot) {
    if (isOnline) {
      dot.classList.remove('offline');
    } else {
      dot.classList.add('offline');
    }
  }
  if (text) {
    text.textContent = isOnline ? 'Service Online' : 'Service Offline';
    text.style.color = isOnline ? '#94a3b8' : '#ef4444';
  }
}

async function refreshSyncState() {
  if (typeof document === 'undefined') return;
  if (typeof fetch !== 'function') return;

  try {
    const response = await fetch('/sync/status', { cache: 'no-store' });
    if (!response.ok) {
      updateHealthIndicator(false);
      return;
    }
    updateHealthIndicator(true);

    const state = await response.json();
    const isRunning = !!state.running;

    // 1. Update Scan Button & Small Spinner (Dashboard)
    const btn = document.getElementById('sync-btn');
    if (btn) btn.disabled = isRunning;
    const spinner = document.getElementById('sync-spinner');
    if (spinner) spinner.style.display = isRunning ? 'inline-flex' : 'none';

    // 2. Update Global Navbar Sync Pill
    const navPill = document.getElementById('nav-sync-pill');
    const navText = document.getElementById('nav-sync-text');
    if (navPill && navText) {
      if (isRunning) {
        navPill.style.display = 'inline-flex';
        navText.textContent = `Sync: ${state.percent || 0}% (${state.current || 0}/${state.total || 0})`;
      } else {
        navPill.style.display = 'none';
      }
    }

    // 3. Update Dashboard Progress Card
    const card = document.getElementById('sync-progress-card');
    if (card) {
      if (isRunning) {
        _wasSyncRunning = true;
        if (_completionHideTimeout) {
          clearTimeout(_completionHideTimeout);
          _completionHideTimeout = null;
        }
        card.style.display = 'block';

        // Phase Title
        const phaseTitle = document.getElementById('sync-progress-phase');
        if (phaseTitle) {
          if (state.phase === 'scanning') {
            phaseTitle.textContent = 'Scanning Input Folder & Checking File Stability…';
          } else if (state.phase === 'reconciling') {
            phaseTitle.textContent = 'Grouping Documents & 3-Way Reconciling…';
          } else {
            phaseTitle.textContent = `AI Vision Extraction in Progress (${state.current || 0} of ${state.total || 0} files)`;
          }
        }

        // Percent & Progress Bar
        const percent = Math.min(100, Math.max(0, Number(state.percent) || 0));
        const percentEl = document.getElementById('sync-progress-percent');
        if (percentEl) percentEl.textContent = percent + '%';
        const bar = document.getElementById('sync-progress-bar');
        if (bar) bar.style.width = percent + '%';

        // Current File & Count
        const currentFileEl = document.getElementById('sync-current-file');
        if (currentFileEl) currentFileEl.textContent = state.current_file || 'Preparing…';
        const countInfoEl = document.getElementById('sync-count-info');
        if (countInfoEl) countInfoEl.textContent = `(${state.current || 0} of ${state.total || 0} files)`;

        // Timers & Counters
        const elapsedEl = document.getElementById('sync-elapsed');
        if (elapsedEl) elapsedEl.textContent = formatSeconds(state.elapsed_seconds);
        const etaEl = document.getElementById('sync-eta');
        if (etaEl) {
          if (state.current > 0 && state.eta_seconds > 0) {
            etaEl.textContent = '~' + formatSeconds(state.eta_seconds);
          } else if (state.current === 0) {
            etaEl.textContent = 'calculating…';
          } else {
            etaEl.textContent = 'almost done';
          }
        }
        const extractedEl = document.getElementById('sync-extracted');
        if (extractedEl) extractedEl.textContent = state.extracted || 0;
        const errorsEl = document.getElementById('sync-errors');
        if (errorsEl) errorsEl.textContent = state.errors || 0;
      } else if (_wasSyncRunning) {
        // Just transitioned from running -> finished
        _wasSyncRunning = false;
        const phaseTitle = document.getElementById('sync-progress-phase');
        if (phaseTitle) phaseTitle.textContent = '✅ Ingestion Sync Complete';
        const percentEl = document.getElementById('sync-progress-percent');
        if (percentEl) percentEl.textContent = '100%';
        const bar = document.getElementById('sync-progress-bar');
        if (bar) {
          bar.style.width = '100%';
          bar.style.background = '#10b981';
        }
        const spinnerIcon = document.getElementById('sync-spinner-icon');
        if (spinnerIcon) spinnerIcon.style.display = 'none';

        _completionHideTimeout = setTimeout(() => {
          if (card) card.style.display = 'none';
          if (bar) bar.style.background = 'var(--primary)';
          if (spinnerIcon) spinnerIcon.style.display = 'inline-block';
        }, 8000);
      } else {
        card.style.display = 'none';
      }
    }
  } catch (e) {
    updateHealthIndicator(false);
  }
}

if (typeof window !== 'undefined' && typeof window.addEventListener === 'function') {
  window.addEventListener('load', () => {
    refreshSyncState();
    if (typeof setInterval === 'function') {
      setInterval(refreshSyncState, 3500);
    }
  });
}
