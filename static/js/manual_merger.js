/**
 * Manual PDF merger — staging, reorder, upload and download.
 *
 * Contract with the server (src/app/api/routes/manual_merger.py):
 *   multipart `files` parts are appended in DISPLAY ORDER, so the server's
 *   `order` field is unnecessary.  Errors are JSON:
 *   { detail, code, filename? }.  Success is an application/pdf attachment.
 *
 * No innerHTML anywhere: vendor filenames are untrusted input.
 */
(function () {
  'use strict';

  var root = document.getElementById('manual-merger');
  if (!root) return;

  var MAX_FILES = Number(root.dataset.maxFiles) || 20;
  var MAX_BYTES = Number(root.dataset.maxBytes) || 100 * 1024 * 1024;
  var MAX_MB = Number(root.dataset.maxMb) || Math.floor(MAX_BYTES / (1024 * 1024));

  var els = {
    form: document.getElementById('merge-form'),
    input: document.getElementById('file-input'),
    dropzone: document.getElementById('dropzone'),
    list: document.getElementById('file-list'),
    empty: document.getElementById('mm-empty'),
    summary: document.getElementById('mm-summary'),
    add: document.getElementById('mm-add'),
    clear: document.getElementById('mm-clear'),
    output: document.getElementById('output_filename'),
    button: document.getElementById('merge-btn'),
    buttonLabel: document.getElementById('merge-btn-label'),
    alert: document.getElementById('mm-alert'),
    alertTitle: document.getElementById('mm-alert-title'),
    alertText: document.getElementById('mm-alert-text'),
    alertClose: document.getElementById('mm-alert-close'),
    progress: document.getElementById('mm-progress'),
    progressBar: document.getElementById('mm-progress-bar'),
    status: document.getElementById('mm-status'),
    nameHint: document.getElementById('mm-name-hint'),
    namePreview: document.getElementById('mm-name-preview'),
    nameError: document.getElementById('mm-name-error'),
  };

  var state = {
    items: [],
    busy: false,
    dragId: null,
    nextId: 1,
  };

  // ------------------------------------------------------------- utilities

  function formatSize(bytes) {
    if (!Number.isFinite(bytes) || bytes < 0) return '0 B';
    if (bytes < 1024) return bytes + ' B';
    if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + ' KB';
    return (bytes / (1024 * 1024)).toFixed(1) + ' MB';
  }

  function plural(count, word) {
    return count + ' ' + word + (count === 1 ? '' : 's');
  }

  function isPdf(file) {
    return /\.pdf$/i.test(String((file && file.name) || ''));
  }

  function totalBytes() {
    return state.items.reduce(function (sum, item) {
      return sum + item.size;
    }, 0);
  }

  // --------------------------------------------------------- output name

  var INVALID_NAME_CHARS = '<>:"/\\|?*';
  var RESERVED_NAME = /^(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(\.|$)/i;
  var MAX_NAME_STEM = 100;

  function displayChar(ch) {
    var code = ch.charCodeAt(0);
    if (ch === '"') return 'the double quote';
    if (ch === '\\') return 'the backslash';
    if (ch === ' ') return 'the space';
    if (code < 32 || code === 127) return 'a control character';
    return '"' + ch + '"';
  }

  function joinWords(items) {
    if (items.length <= 1) return items.join('');
    if (items.length === 2) return items[0] + ' and ' + items[1];
    return items.slice(0, -1).join(', ') + ', and ' + items[items.length - 1];
  }

  function finalPdfName(raw) {
    var text = String(raw || '').trim().replace(/\s+/g, ' ');
    if (!text) return '';
    var stem = /\.pdf$/i.test(text) ? text.slice(0, -4) : text;
    return stem + '.pdf';
  }

  function nameProblem(raw) {
    var text = String(raw || '').trim();
    if (!text) return 'Please give the merged file a name.';

    var stem = /\.pdf$/i.test(text) ? text.slice(0, -4) : text;
    if (!stem) return 'Please add a name before .pdf.';
    if (stem.length > MAX_NAME_STEM) {
      return 'Please keep the name to ' + MAX_NAME_STEM + ' characters or fewer (before .pdf).';
    }

    var bad = [];
    for (var i = 0; i < stem.length; i += 1) {
      var ch = stem.charAt(i);
      var code = ch.charCodeAt(0);
      if (
        (INVALID_NAME_CHARS.indexOf(ch) !== -1 || code < 32 || code === 127) &&
        bad.indexOf(ch) === -1
      ) {
        bad.push(ch);
      }
    }
    if (bad.length) {
      return 'Please remove ' + joinWords(bad.map(displayChar)) + ' from the file name.';
    }

    if (stem.charAt(0) === '.' || stem.charAt(stem.length - 1) === '.') {
      return "The name can't start or end with a dot.";
    }
    if (stem.charAt(0) === ' ' || stem.charAt(stem.length - 1) === ' ') {
      return "The name can't start or end with a space.";
    }
    if (RESERVED_NAME.test(stem)) {
      return 'That name is reserved by Windows — please choose another.';
    }
    return '';
  }

  function updateNameState() {
    var problem = nameProblem(els.output.value);
    els.nameError.hidden = !problem;
    els.nameError.textContent = problem || '';
    els.nameHint.hidden = Boolean(problem);
    els.output.classList.toggle('is-invalid', Boolean(problem));
    els.output.setAttribute('aria-invalid', problem ? 'true' : 'false');
    els.namePreview.textContent = problem ? '' : finalPdfName(els.output.value);
    updateSummary();
  }

  function setStatus(text, kind) {
    els.status.textContent = text || '';
    els.status.className = 'mm-status' + (kind ? ' is-' + kind : '');
  }

  function toast(kind, message) {
    try {
      var store = window.Alpine && window.Alpine.store && window.Alpine.store('toasts');
      if (store && typeof store[kind] === 'function') store[kind](message);
    } catch (_err) {
      /* toasts are a courtesy; never break the merge for one */
    }
  }

  // ----------------------------------------------------------------- alert

  function showAlert(title, text, kind, filename) {
    els.alertTitle.textContent = title;
    els.alertText.textContent = text;
    els.alert.classList.toggle('is-warning', kind === 'warning');
    els.alert.hidden = false;

    if (filename) {
      var marked = false;
      state.items.forEach(function (item) {
        if (item.name === filename) {
          item.error = text;
          marked = true;
        }
      });
      if (marked) render();
    }
    els.alert.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
  }

  function hideAlert() {
    els.alert.hidden = true;
  }

  function dismissAlert() {
    hideAlert();
    var changed = false;
    state.items.forEach(function (item) {
      if (item.error) {
        item.error = null;
        changed = true;
      }
    });
    if (changed) render();
  }

  // ------------------------------------------------------------------ list

  function iconButton(label, title, onClick, disabled) {
    var button = document.createElement('button');
    button.type = 'button';
    button.className = 'mm-icon-btn';
    button.textContent = label;
    button.title = title;
    button.setAttribute('aria-label', title);
    button.disabled = Boolean(disabled);
    button.addEventListener('click', onClick);
    return button;
  }

  function buildRow(item, index) {
    var li = document.createElement('li');
    li.className = 'mm-file' + (item.error ? ' has-error' : '');
    li.dataset.id = String(item.id);
    li.draggable = !state.busy;

    var handle = document.createElement('span');
    handle.className = 'mm-handle';
    handle.textContent = '⋮⋮';
    handle.title = 'Drag to reorder';
    handle.setAttribute('aria-hidden', 'true');

    var icon = document.createElement('span');
    icon.className = 'mm-file-icon';
    icon.textContent = 'PDF';
    icon.setAttribute('aria-hidden', 'true');

    var body = document.createElement('div');
    body.className = 'mm-file-body';

    var name = document.createElement('span');
    name.className = 'mm-file-name';
    name.textContent = item.name;
    name.title = item.name;

    var meta = document.createElement('span');
    meta.className = 'mm-file-meta';
    meta.textContent = '#' + (index + 1) + ' · ' + formatSize(item.size);

    body.append(name, meta);

    if (item.error) {
      var error = document.createElement('span');
      error.className = 'mm-file-error';
      error.textContent = item.error;
      body.appendChild(error);
    }

    var actions = document.createElement('div');
    actions.className = 'mm-actions';
    actions.append(
      iconButton('↑', 'Move ' + item.name + ' up', function () {
        moveItem(item.id, -1);
      }, index === 0 || state.busy),
      iconButton('↓', 'Move ' + item.name + ' down', function () {
        moveItem(item.id, 1);
      }, index === state.items.length - 1 || state.busy),
      iconButton('✕', 'Remove ' + item.name, function () {
        removeItem(item.id);
      }, state.busy)
    );
    actions.lastChild.classList.add('mm-remove');

    li.append(handle, icon, body, actions);

    // --- internal reorder -------------------------------------------------
    li.addEventListener('dragstart', function (event) {
      state.dragId = item.id;
      li.classList.add('is-dragging');
      if (event.dataTransfer) {
        event.dataTransfer.effectAllowed = 'move';
        try {
          event.dataTransfer.setData('text/plain', String(item.id));
        } catch (_err) {
          /* Firefox needs the setData; some browsers refuse it */
        }
      }
    });

    li.addEventListener('dragend', function () {
      state.dragId = null;
      li.classList.remove('is-dragging');
      clearDropHighlights();
    });

    li.addEventListener('dragover', function (event) {
      event.preventDefault();
      if (state.dragId) li.classList.add('is-over');
    });

    li.addEventListener('dragleave', function () {
      li.classList.remove('is-over');
    });

    li.addEventListener('drop', function (event) {
      var dropped = event.dataTransfer && event.dataTransfer.files;

      // A file from the OS is ADDED, never treated as a reorder. This is the
      // regression that used to move the first staged file instead.
      if (dropped && dropped.length) {
        event.preventDefault();
        event.stopPropagation();
        state.dragId = null;
        clearDropHighlights();
        addFiles(dropped);
        return;
      }

      event.preventDefault();
      event.stopPropagation();
      var fromId = state.dragId;
      if (!fromId && event.dataTransfer) {
        fromId = Number(event.dataTransfer.getData('text/plain'));
      }
      state.dragId = null;
      clearDropHighlights();
      if (fromId && fromId !== item.id) moveBefore(fromId, item.id);
    });

    return li;
  }

  function render(focusId) {
    var fragment = document.createDocumentFragment();
    state.items.forEach(function (item, index) {
      fragment.appendChild(buildRow(item, index));
    });
    els.list.replaceChildren(fragment);
    els.empty.hidden = state.items.length > 0;
    updateSummary();

    if (focusId) {
      var row = els.list.querySelector('[data-id="' + focusId + '"]');
      var buttons = row ? row.querySelectorAll('.mm-icon-btn') : [];
      for (var i = 0; i < buttons.length; i += 1) {
        if (!buttons[i].disabled) {
          buttons[i].focus();
          break;
        }
      }
    }
  }

  function clearDropHighlights() {
    var over = els.list.querySelectorAll('.mm-file.is-over');
    for (var i = 0; i < over.length; i += 1) over[i].classList.remove('is-over');
  }

  function moveItem(id, delta) {
    if (state.busy) return;
    var index = state.items.findIndex(function (item) {
      return item.id === id;
    });
    var target = index + delta;
    if (index < 0 || target < 0 || target >= state.items.length) return;
    var moved = state.items.splice(index, 1)[0];
    state.items.splice(target, 0, moved);
    render(id);
  }

  function moveBefore(fromId, targetId) {
    if (state.busy) return;
    var fromIndex = state.items.findIndex(function (item) {
      return item.id === fromId;
    });
    var toIndex = state.items.findIndex(function (item) {
      return item.id === targetId;
    });
    if (fromIndex < 0 || toIndex < 0 || fromIndex === toIndex) return;
    var moved = state.items.splice(fromIndex, 1)[0];
    state.items.splice(toIndex, 0, moved);
    render();
  }

  function removeItem(id) {
    if (state.busy) return;
    state.items = state.items.filter(function (item) {
      return item.id !== id;
    });
    hideAlert();
    render();
  }

  function clearAll() {
    if (!state.items.length || state.busy) return;
    if (!window.confirm('Remove all staged files?')) return;
    state.items = [];
    hideAlert();
    setStatus('');
    els.progress.hidden = true;
    render();
  }

  function addFiles(fileList) {
    if (state.busy) return;
    var incoming = Array.prototype.slice.call(fileList || []);
    if (!incoming.length) return;

    var rejectedType = [];
    var rejectedLimit = 0;
    var added = 0;

    incoming.forEach(function (file) {
      if (!isPdf(file)) {
        rejectedType.push(file.name);
        return;
      }
      if (
        state.items.length >= MAX_FILES ||
        totalBytes() + file.size > MAX_BYTES
      ) {
        rejectedLimit += 1;
        return;
      }
      state.items.push({
        id: state.nextId,
        file: file,
        name: file.name,
        size: file.size,
        error: null,
      });
      state.nextId += 1;
      added += 1;
    });

    if (added && !rejectedType.length && !rejectedLimit) {
      state.items.forEach(function (item) {
        item.error = null;
      });
      hideAlert();
      setStatus('');
    }

    render();

    if (rejectedType.length || rejectedLimit) {
      var parts = [];
      if (rejectedType.length) {
        var sample = rejectedType.slice(0, 3).join(', ');
        if (rejectedType.length > 3) sample += ', …';
        parts.push(
          plural(rejectedType.length, 'file') +
            ' skipped — only PDF files can be merged (' + sample + ').'
        );
      }
      if (rejectedLimit) {
        parts.push(
          plural(rejectedLimit, 'file') +
            " couldn't be added — you can merge up to " + MAX_FILES + ' files and ' + MAX_MB + ' MB at a time.'
        );
      }
      showAlert("Some files couldn't be added", parts.join(' '), 'warning');
    }
  }

  function updateSummary() {
    var count = state.items.length;
    var total = totalBytes();

    els.summary.textContent = plural(count, 'file') + ' · ' + formatSize(total);

    var overCount = count > MAX_FILES;
    var overBytes = total > MAX_BYTES;
    var nameIssue = nameProblem(els.output.value);
    els.button.disabled =
      state.busy || count === 0 || overCount || overBytes || Boolean(nameIssue);

    if (state.busy) return;
    if (count === 0) {
      setStatus('Add at least one PDF to get started.');
    } else if (overCount) {
      setStatus('You can merge up to ' + MAX_FILES + ' files at a time.', 'error');
    } else if (overBytes) {
      setStatus('The total size is over ' + MAX_MB + ' MB — remove a file or use smaller ones.', 'error');
    } else if (nameIssue) {
      setStatus('Please fix the file name to continue.', 'error');
    } else {
      setStatus('Ready to merge ' + plural(count, 'file') + ' in this order.');
    }
  }

  function setBusy(busy) {
    state.busy = busy;
    els.buttonLabel.textContent = busy ? 'Merging…' : 'Merge & download';
    els.add.disabled = busy;
    els.clear.disabled = busy;
    els.output.disabled = busy;
    els.input.disabled = busy;
    els.progress.hidden = !busy;
    updateSummary();
  }

  function setProgress(percent) {
    var value = Math.max(0, Math.min(100, Math.round(percent)));
    els.progressBar.style.width = value + '%';
    els.progressBar.setAttribute('aria-valuenow', String(value));
  }

  // ---------------------------------------------------------------- upload

  function filenameFromDisposition(header) {
    if (!header) return '';
    var encoded = /filename\*\s*=\s*UTF-8''([^;]+)/i.exec(header);
    if (encoded) {
      try {
        return decodeURIComponent(encoded[1].trim());
      } catch (_err) {
        /* fall through to the plain form */
      }
    }
    var quoted = /filename\s*=\s*"([^"]*)"/i.exec(header);
    if (quoted) return quoted[1];
    var plain = /filename\s*=\s*([^;]+)/i.exec(header);
    return plain ? plain[1].trim() : '';
  }

  function fallbackName() {
    var typed = (els.output.value || '').trim();
    if (!typed) return 'merged-document.pdf';
    return /\.pdf$/i.test(typed) ? typed : typed + '.pdf';
  }

  function saveBlob(blob, name) {
    var url = URL.createObjectURL(blob);
    var link = document.createElement('a');
    link.href = url;
    link.download = name || 'merged-document.pdf';
    link.rel = 'noopener';
    document.body.appendChild(link);
    link.click();
    link.remove();
    window.setTimeout(function () {
      URL.revokeObjectURL(url);
    }, 4000);
  }

  function readError(blob) {
    if (!blob || typeof blob.text !== 'function') {
      showAlert(
        "The merge didn't finish",
        "Something went wrong and we didn't get a readable reply. Please try again."
      );
      return;
    }
    blob.text().then(function (text) {
      var payload = null;
      try {
        payload = JSON.parse(text);
      } catch (_err) {
        payload = null;
      }
      var detail = payload && payload.detail;
      if (Array.isArray(detail)) {
        detail = detail
          .map(function (item) {
            return item && item.msg ? item.msg : 'Invalid upload';
          })
          .join('; ');
      }
      showAlert(
        "The merge didn't finish",
        detail || "We couldn't merge those files. Please try again.",
        'error',
        payload && payload.filename
      );
    });
  }

  function submit(event) {
    event.preventDefault();
    if (state.busy) return;

    if (!state.items.length) {
      showAlert('No PDFs yet', 'Please add at least one PDF first.');
      return;
    }
    if (state.items.length > MAX_FILES) {
      showAlert('Too many files', 'You can merge up to ' + MAX_FILES + ' files at a time.');
      return;
    }
    if (totalBytes() > MAX_BYTES) {
      showAlert('Files too large', 'The total size limit is ' + MAX_MB + ' MB per merge.');
      return;
    }

    var nameIssue = nameProblem(els.output.value);
    if (nameIssue) {
      showAlert('Please check the file name', nameIssue);
      els.output.focus();
      return;
    }

    var count = state.items.length;
    var formData = new FormData();
    // Append in display order: the server merges in multipart order. Do not
    // depend on a FileList or a DataTransfer being mutable.
    state.items.forEach(function (item) {
      formData.append('files', item.file, item.name);
    });
    formData.append('output_filename', finalPdfName(els.output.value));

    var xhr = new XMLHttpRequest();
    xhr.open('POST', els.form.getAttribute('action') || '/manual/merge', true);
    xhr.setRequestHeader('Accept', 'application/json');
    xhr.responseType = 'blob';

    xhr.upload.onprogress = function (progress) {
      if (progress.lengthComputable) {
        setProgress((progress.loaded / progress.total) * 80);
      }
    };
    xhr.onprogress = function (progress) {
      if (progress.lengthComputable) {
        setProgress(80 + (progress.loaded / progress.total) * 20);
      }
    };

    xhr.onload = function () {
      var type = xhr.getResponseHeader('Content-Type') || '';
      if (
        xhr.status >= 200 &&
        xhr.status < 300 &&
        type.indexOf('application/pdf') !== -1
      ) {
        var name = filenameFromDisposition(
          xhr.getResponseHeader('Content-Disposition')
        );
        saveBlob(xhr.response, name || fallbackName());
        setProgress(100);
        setBusy(false);
        setStatus(
          'Done — ' + plural(count, 'file') + ' merged. Your download has started.',
          'success'
        );
        toast('success', 'Done — ' + plural(count, 'file') + ' merged.');
        return;
      }
      setBusy(false);
      readError(xhr.response);
    };

    xhr.onerror = function () {
      setBusy(false);
      showAlert(
        'Connection problem',
        "The server didn't respond. Check the connection and try again."
      );
    };

    setBusy(true);
    setProgress(0);
    xhr.send(formData);
  }

  // ----------------------------------------------------------------- wiring

  els.input.addEventListener('change', function () {
    addFiles(els.input.files);
    els.input.value = '';
  });

  els.dropzone.addEventListener('click', function () {
    els.input.click();
  });

  els.dropzone.addEventListener('keydown', function (event) {
    if (event.key === 'Enter' || event.key === ' ' || event.key === 'Spacebar') {
      event.preventDefault();
      els.input.click();
    }
  });

  els.add.addEventListener('click', function () {
    els.input.click();
  });

  els.clear.addEventListener('click', clearAll);
  els.alertClose.addEventListener('click', dismissAlert);
  els.form.addEventListener('submit', submit);

  els.output.addEventListener('input', updateNameState);
  els.output.addEventListener('blur', function () {
    if (!nameProblem(els.output.value)) {
      els.output.value = finalPdfName(els.output.value);
    }
    updateNameState();
  });

  // A file dropped anywhere on the page is added; nothing may navigate the
  // tab to the PDF (that would also discard the staged list).
  document.addEventListener('dragover', function (event) {
    if (event.dataTransfer && event.dataTransfer.files && event.dataTransfer.files.length) {
      event.preventDefault();
      els.dropzone.classList.add('is-dragging');
    } else if (state.dragId) {
      event.preventDefault();
    }
  });

  document.addEventListener('dragleave', function (event) {
    if (event.relatedTarget === null) els.dropzone.classList.remove('is-dragging');
  });

  document.addEventListener('drop', function (event) {
    event.preventDefault();
    els.dropzone.classList.remove('is-dragging');
    state.dragId = null;
    if (event.dataTransfer && event.dataTransfer.files && event.dataTransfer.files.length) {
      addFiles(event.dataTransfer.files);
    }
  });

  els.dropzone.addEventListener('dragover', function (event) {
    if (event.dataTransfer && event.dataTransfer.files && event.dataTransfer.files.length) {
      event.preventDefault();
      els.dropzone.classList.add('is-dragging');
    }
  });

  els.dropzone.addEventListener('dragleave', function () {
    els.dropzone.classList.remove('is-dragging');
  });

  els.dropzone.addEventListener('drop', function () {
    els.dropzone.classList.remove('is-dragging');
  });

  window.addEventListener('beforeunload', function (event) {
    if (state.busy) {
      event.preventDefault();
      event.returnValue = '';
    }
  });

  render();
  updateNameState();
})();
