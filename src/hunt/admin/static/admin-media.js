/*
 * Hunt admin: standalone media manager page (<prefix>/media).
 *
 * Loaded from the page with <script src>, after admin.js, because the admin
 * Content-Security-Policy does not allow inline scripts. The URL prefix and
 * CSRF token come from window._huntAdmin, which admin.js fills from the JSON
 * config block in the layout.
 *
 * This file is served without authentication: it must never contain secrets
 * or per-user data.
 */
(function() {
    'use strict';
    if (!window._huntAdmin) return;
    var prefix = window._huntAdmin.prefix;
    var grid = document.getElementById('media-grid');
    if (!grid) return;
    var emptyEl = document.getElementById('media-empty');
    var loadingEl = document.getElementById('media-loading');
    var fileInput = document.getElementById('media-file-input');
    var progressEl = document.getElementById('upload-progress');
    var progressText = document.getElementById('upload-progress-text');
    var flashEl = document.getElementById('media-flash');
    var copyToast = document.getElementById('copy-toast');
    var toastTimer;

    function showToast(msg) {
        clearTimeout(toastTimer);
        copyToast.textContent = msg;
        copyToast.classList.remove('hidden');
        toastTimer = setTimeout(function() { copyToast.classList.add('hidden'); }, 2500);
    }

    function showFlash(msg, type) {
        var cls = type === 'error'
            ? 'rounded-lg bg-red-500/10 ring-1 ring-inset ring-red-500/20 px-4 py-3 text-sm text-red-300'
            : 'rounded-lg bg-green-500/10 ring-1 ring-inset ring-green-500/20 px-4 py-3 text-sm text-green-300';
        flashEl.className = cls;
        flashEl.textContent = msg;
        flashEl.classList.remove('hidden');
        setTimeout(function() { flashEl.classList.add('hidden'); }, 4000);
    }

    function formatSize(bytes) {
        if (bytes < 1024) return bytes + ' B';
        if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + ' KB';
        return (bytes / (1024 * 1024)).toFixed(1) + ' MB';
    }

    function buildCard(file) {
        var card = document.createElement('div');
        card.className = 'media-card ring-1 ring-white/10';
        card.dataset.path = file.path;
        card.dataset.url = file.url;

        var img = document.createElement('img');
        img.src = file.url;
        img.alt = file.name;
        img.className = 'w-full h-full object-cover';
        img.addEventListener('error', function() {
            img.style.display = 'none';
            var ph = document.createElement('div');
            ph.className = 'w-full h-full flex items-center justify-center text-gray-500';
            ph.innerHTML = '<svg class="size-8" fill="none" viewBox="0 0 24 24" stroke-width="1.5" stroke="currentColor"><path stroke-linecap="round" stroke-linejoin="round" d="m2.25 15.75 5.159-5.159a2.25 2.25 0 0 1 3.182 0l5.159 5.159m-1.5-1.5 1.409-1.409a2.25 2.25 0 0 1 3.182 0l2.909 2.909m-18 3.75h16.5a1.5 1.5 0 0 0 1.5-1.5V6a1.5 1.5 0 0 0-1.5-1.5H3.75A1.5 1.5 0 0 0 2.25 6v12a1.5 1.5 0 0 0 1.5 1.5Zm10.5-11.25h.008v.008h-.008V8.25Zm.375 0a.375.375 0 1 1-.75 0 .375.375 0 0 1 .75 0Z"/></svg>';
            card.appendChild(ph);
        });
        card.appendChild(img);

        var overlay = document.createElement('div');
        overlay.className = 'media-overlay';
        // Built with textContent: a file name is user-controlled and must
        // never be parsed as HTML.
        [
            ['text-xs text-white font-medium text-center px-2 break-all', String(file.name)],
            ['text-xs text-gray-300', formatSize(file.size)],
            ['text-xs text-indigo-300', 'Click to copy URL']
        ].forEach(function(part) {
            var span = document.createElement('span');
            span.className = part[0];
            span.textContent = part[1];
            overlay.appendChild(span);
        });
        card.appendChild(overlay);

        var delBtn = document.createElement('button');
        delBtn.type = 'button';
        delBtn.className = 'media-delete-btn';
        delBtn.title = 'Delete';
        delBtn.innerHTML = '<svg class="size-3.5 text-white" viewBox="0 0 20 20" fill="currentColor"><path fill-rule="evenodd" d="M8.75 1A2.75 2.75 0 006 3.75v.443c-.795.077-1.584.176-2.365.298a.75.75 0 10.23 1.482l.149-.022.841 10.518A2.75 2.75 0 007.596 19h4.807a2.75 2.75 0 002.742-2.53l.841-10.52.149.023a.75.75 0 00.23-1.482A41.03 41.03 0 0014 4.193V3.75A2.75 2.75 0 0011.25 1h-2.5zM10 4c.84 0 1.673.025 2.5.075V3.75c0-.69-.56-1.25-1.25-1.25h-2.5c-.69 0-1.25.56-1.25 1.25v.325C8.327 4.025 9.16 4 10 4zM8.58 7.72a.75.75 0 00-1.5.06l.3 7.5a.75.75 0 101.5-.06l-.3-7.5zm4.34.06a.75.75 0 10-1.5-.06l-.3 7.5a.75.75 0 101.5.06l.3-7.5z" clip-rule="evenodd"/></svg>';
        delBtn.addEventListener('click', function(e) {
            e.stopPropagation();
            deleteFile(file.path, card);
        });
        card.appendChild(delBtn);

        card.addEventListener('click', function() {
            var cb = window._huntAdmin.mediaCallback;
            if (typeof cb === 'function') {
                cb(file.url, file.name);
            } else {
                navigator.clipboard.writeText(file.url).then(function() {
                    showToast('URL copied to clipboard');
                }).catch(function() {
                    showToast(file.url);
                });
            }
        });

        return card;
    }

    function loadFiles(page) {
        page = (typeof page === 'number' && page > 0) ? page : 1;
        fetch(prefix + '/media/api?page=' + page)
            .then(function(r) { return r.json(); })
            .then(function(data) {
                loadingEl.classList.add('hidden');
                if (page === 1) {
                    var cards = grid.querySelectorAll('.media-card');
                    cards.forEach(function(c) { c.remove(); });
                }
                var oldMore = document.getElementById('media-load-more');
                if (oldMore) oldMore.remove();
                if (!data.files || !data.files.length) {
                    if (page === 1) emptyEl.classList.remove('hidden');
                } else {
                    emptyEl.classList.add('hidden');
                    data.files.forEach(function(f) {
                        grid.insertBefore(buildCard(f), emptyEl);
                    });
                    if (data.last_page && page < data.last_page) {
                        var more = document.createElement('button');
                        more.type = 'button';
                        more.id = 'media-load-more';
                        more.className = 'rounded-md bg-white/10 px-3 py-2 text-sm font-semibold text-white hover:bg-white/20';
                        more.textContent = 'Load more';
                        more.addEventListener('click', function() { loadFiles(page + 1); });
                        grid.parentNode.insertBefore(more, grid.nextSibling);
                    }
                }
            })
            .catch(function() {
                loadingEl.classList.add('hidden');
                showFlash('Failed to load media.', 'error');
            });
    }

    function deleteFile(path, card) {
        if (!confirm('Delete this file?')) return;
        fetch(prefix + '/media/delete', {
            method: 'POST',
            headers: {'Content-Type': 'application/x-www-form-urlencoded', 'X-CSRF-TOKEN': window._huntAdmin.csrfToken},
            body: 'path=' + encodeURIComponent(path)
        })
        .then(function(r) {
            // Only remove the card when the server confirms the delete.
            if (!r.ok) throw new Error('delete failed');
            return r.json();
        })
        .then(function() {
            card.remove();
            var remaining = grid.querySelectorAll('.media-card');
            if (!remaining.length) emptyEl.classList.remove('hidden');
            showFlash('File deleted.', 'success');
        })
        .catch(function() { showFlash('Failed to delete file.', 'error'); });
    }

    function uploadFiles(files) {
        var queue = Array.from(files);
        var done = 0;

        function next() {
            if (!queue.length) {
                progressEl.classList.add('hidden');
                loadFiles();
                return;
            }
            var file = queue.shift();
            progressText.textContent = 'Uploading ' + file.name + '…';
            progressEl.classList.remove('hidden');

            var fd = new FormData();
            fd.append('file', file);
            fd.append('_token', window._huntAdmin.csrfToken);
            fetch(prefix + '/media/upload', {method: 'POST', body: fd})
                .then(function(r) {
                    if (!r.ok) return r.json().then(function(e) { throw new Error(e.error || 'Upload failed'); });
                    return r.json();
                })
                .then(function() { done++; next(); })
                .catch(function(err) { showFlash(err.message || 'Upload failed.', 'error'); next(); });
        }
        next();
    }

    fileInput.addEventListener('change', function() {
        if (fileInput.files.length) uploadFiles(fileInput.files);
        fileInput.value = '';
    });

    // Drag-and-drop on the grid
    var mainArea = document.querySelector('.lg\\:pl-72') || document.body;
    mainArea.addEventListener('dragover', function(e) { e.preventDefault(); });
    mainArea.addEventListener('drop', function(e) {
        e.preventDefault();
        if (e.dataTransfer.files.length) uploadFiles(e.dataTransfer.files);
    });

    loadFiles();
})();
