/*
 * Hunt admin panel behaviour.
 *
 * The admin Content-Security-Policy is `script-src 'self'`: no inline
 * <script> blocks and no inline event-handler attributes run. Everything the
 * panel does in the browser therefore lives in this file (and in
 * admin-media.js for the standalone media page) and is wired up from data
 * attributes:
 *
 *   data-confirm="text"            on a <form>: ask before submitting
 *   data-hunt-action="name"        click behaviour (see CLICK_ACTIONS)
 *   data-hunt-change="name"        change behaviour (see CHANGE_ACTIONS)
 *   data-hunt-bulk                 on a bulk-action <form>: fill its "ids" input
 *   data-hunt-autocomplete="attr"  searchable BelongsTo picker
 *   data-hunt-btm="attr"           BelongsToMany picker
 *   data-hunt-markdown             <textarea> turned into an EasyMDE editor
 *
 * Per-page values (URL prefix, resource key, CSRF token) come from the
 * <script type="application/json" id="hunt-admin-config"> block in the layout.
 *
 * This file is served without authentication: it must never contain secrets
 * or per-user data.
 */
(function (root) {
    'use strict';

    // ------------------------------------------------------------------
    // Pure helpers (also exported for the Node unit tests)
    // ------------------------------------------------------------------

    function escapeHtml(value) {
        return String(value)
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#39;');
    }

    /** Parse the JSON config block. Returns {} when it is missing or malformed. */
    function parseConfig(text) {
        if (!text) return {};
        try {
            var data = JSON.parse(text);
            return data && typeof data === 'object' && !Array.isArray(data) ? data : {};
        } catch (e) {
            return {};
        }
    }

    function joinIds(values) {
        return values.map(String).join(',');
    }

    function splitIds(csv) {
        return csv ? String(csv).split(',') : [];
    }

    /** Add an id to a comma separated list. Returns null when it is already there. */
    function addId(csv, id) {
        var ids = splitIds(csv);
        if (ids.indexOf(String(id)) >= 0) return null;
        ids.push(String(id));
        return ids.join(',');
    }

    function removeId(csv, id) {
        return splitIds(csv).filter(function (x) { return x !== String(id); }).join(',');
    }

    /** The current URL with per_page replaced and the page reset to 1. */
    function perPageUrl(href, value) {
        var url = new URL(href);
        url.searchParams.set('per_page', value);
        url.searchParams.set('page', '1');
        return url.toString();
    }

    function relationSearchUrl(prefix, resourceKey, field, query) {
        return prefix + '/resources/' + resourceKey + '/search-relation?field=' +
            encodeURIComponent(field) + '&q=' + encodeURIComponent(query);
    }

    function globalSearchUrl(prefix, query) {
        return prefix + '/search?q=' + encodeURIComponent(query);
    }

    /** HTML for the global search dropdown. Every server value is escaped. */
    function renderSearchResults(groups) {
        return (groups || []).map(function (g) {
            var items = (g.items || []).map(function (r) {
                return '<a class="search-result-item" href="' + escapeHtml(r.url) + '">' + escapeHtml(r.title) + '</a>';
            }).join('');
            return '<div class="search-group">' +
                '<div class="search-group-label"><span>' + escapeHtml(g.resource) + '</span>' +
                '<a href="' + escapeHtml(g.resource_url) + '">View all</a></div>' +
                items +
                '</div>';
        }).join('');
    }

    var BADGE_BASE = 'inline-flex items-center rounded-md px-2 py-1 text-xs font-medium ring-1 ring-inset cursor-pointer transition-colors ';

    function toggleBadgeClass(value) {
        return BADGE_BASE + (value ? 'bg-green-500/10 text-green-400 ring-green-500/20' : 'bg-gray-400/10 text-gray-400 ring-gray-400/20');
    }

    function mediaStatusText(total) {
        return total + ' file' + (total !== 1 ? 's' : '');
    }

    function markdownImage(name, url) {
        return '![' + name + '](' + url + ')';
    }

    /** Escape text for the (disabled) Markdown preview: never rendered as HTML. */
    function escapeForPreview(text) {
        return String(text).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
    }

    /**
     * Shared confirmation prompt. Returns true when the submit may go ahead.
     * The message comes from a data-confirm attribute, so server-side text is
     * never interpolated into script.
     */
    function confirmSubmit(event, confirmFn) {
        var form = event.target;
        if (!form || !form.getAttribute) return true;
        var msg = form.getAttribute('data-confirm');
        if (msg && !confirmFn(msg)) {
            event.preventDefault();
            event.stopPropagation();
            return false;
        }
        return true;
    }

    /**
     * Toggle a boolean field. The badge only changes when the server answers
     * 2xx with a boolean value; anything else reports a failure.
     */
    function toggleField(btn, deps) {
        return deps.fetch(btn.dataset.toggleUrl, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', 'X-CSRF-TOKEN': deps.csrfToken },
            body: JSON.stringify({ attribute: btn.dataset.attribute })
        }).then(function (r) {
            if (!r.ok) throw new Error('toggle failed');
            return r.json();
        }).then(function (data) {
            if (typeof data.value !== 'boolean') return;
            btn.textContent = data.value ? 'Yes' : 'No';
            btn.className = toggleBadgeClass(data.value);
        }).catch(function () {
            deps.alert('The field could not be updated.');
        });
    }

    var api = {
        escapeHtml: escapeHtml,
        parseConfig: parseConfig,
        joinIds: joinIds,
        splitIds: splitIds,
        addId: addId,
        removeId: removeId,
        perPageUrl: perPageUrl,
        relationSearchUrl: relationSearchUrl,
        globalSearchUrl: globalSearchUrl,
        renderSearchResults: renderSearchResults,
        toggleBadgeClass: toggleBadgeClass,
        mediaStatusText: mediaStatusText,
        markdownImage: markdownImage,
        escapeForPreview: escapeForPreview,
        confirmSubmit: confirmSubmit,
        toggleField: toggleField
    };

    if (typeof module !== 'undefined' && module.exports) {
        module.exports = api;
    }
    if (!root || !root.document) return;

    // ------------------------------------------------------------------
    // Browser wiring
    // ------------------------------------------------------------------

    var document = root.document;
    var configEl = document.getElementById('hunt-admin-config');
    var config = parseConfig(configEl ? configEl.textContent : '');

    var admin = root._huntAdmin = root._huntAdmin || {};
    admin.prefix = typeof config.prefix === 'string' ? config.prefix : '';
    admin.resourceKey = typeof config.resourceKey === 'string' ? config.resourceKey : '';
    admin.csrfToken = typeof config.csrfToken === 'string' ? config.csrfToken : '';
    admin.util = api;

    function byId(id) { return document.getElementById(id); }

    function closestWith(target, attribute) {
        return target && target.closest ? target.closest('[' + attribute + ']') : null;
    }

    // --- sidebar, profile menu, navigation groups ----------------------

    function openMobileSidebar() {
        var el = byId('mobile-sidebar');
        if (el) el.classList.add('open');
        document.body.style.overflow = 'hidden';
    }

    function closeMobileSidebar() {
        var el = byId('mobile-sidebar');
        if (el) el.classList.remove('open');
        document.body.style.overflow = '';
    }

    function toggleProfileMenu() {
        var menu = byId('profile-menu');
        if (menu) menu.classList.toggle('open');
    }

    function readNavState() {
        try { return JSON.parse(root.localStorage.getItem('_hunt_nav') || '{}') || {}; } catch (e) { return {}; }
    }

    function setChevron(btn, collapsed) {
        var chevron = btn.querySelector('.nav-chevron');
        if (chevron) chevron.style.transform = collapsed ? 'rotate(-90deg)' : '';
    }

    function toggleNavGroup(btn) {
        var list = btn.nextElementSibling;
        if (!list) return;
        var collapsed = list.classList.toggle('collapsed');
        setChevron(btn, collapsed);
        try {
            var state = readNavState();
            state[btn.dataset.group] = collapsed;
            root.localStorage.setItem('_hunt_nav', JSON.stringify(state));
        } catch (e) { /* storage unavailable: the group still toggles */ }
    }

    function restoreNavGroups() {
        var state = readNavState();
        document.querySelectorAll('[data-group]').forEach(function (btn) {
            var list = btn.nextElementSibling;
            if (!list) return;
            var hasActive = list.querySelector('a.text-white') !== null;
            if (!hasActive && state[btn.dataset.group] === true) {
                list.classList.add('collapsed');
                setChevron(btn, true);
            }
        });
    }

    // --- media manager modal -------------------------------------------

    var MEDIA_PLACEHOLDER_ICON = '<svg style="width:2rem;height:2rem;" fill="none" viewBox="0 0 24 24" stroke-width="1.5" stroke="currentColor"><path stroke-linecap="round" stroke-linejoin="round" d="m2.25 15.75 5.159-5.159a2.25 2.25 0 0 1 3.182 0l5.159 5.159m-1.5-1.5 1.409-1.409a2.25 2.25 0 0 1 3.182 0l2.909 2.909M3.75 21h16.5M10.5 8.25h.008"/></svg>';
    var mediaCallback = null;

    function mediaMessage(text, color) {
        var el = document.createElement('div');
        el.style.cssText = 'grid-column:1/-1;text-align:center;padding:3rem 0;font-size:.875rem;color:' + color + ';';
        el.textContent = text;
        return el;
    }

    function setMediaStatus(msg) {
        var status = byId('_hunt-media-status');
        if (status) status.textContent = msg;
    }

    function buildMediaCard(file) {
        var card = document.createElement('div');
        card.style.cssText = 'position:relative;aspect-ratio:1;background:rgba(255,255,255,.05);border-radius:.5rem;overflow:hidden;border:1px solid rgba(255,255,255,.1);cursor:pointer;';
        var img = document.createElement('img');
        img.src = file.url;
        img.alt = file.name;
        img.style.cssText = 'width:100%;height:100%;object-fit:cover;';
        img.addEventListener('error', function () {
            img.style.display = 'none';
            var ph = document.createElement('div');
            ph.style.cssText = 'width:100%;height:100%;display:flex;align-items:center;justify-content:center;color:#4b5563;';
            ph.innerHTML = MEDIA_PLACEHOLDER_ICON;
            card.appendChild(ph);
        });
        card.appendChild(img);
        var overlay = document.createElement('div');
        overlay.style.cssText = 'position:absolute;inset:0;background:rgba(0,0,0,.6);opacity:0;transition:opacity .15s;display:flex;align-items:flex-end;padding:.375rem;';
        var nameEl = document.createElement('span');
        nameEl.style.cssText = 'font-size:.625rem;color:#fff;word-break:break-all;line-height:1.2;';
        // A file name is user-controlled and must never be parsed as HTML.
        nameEl.textContent = file.name;
        overlay.appendChild(nameEl);
        card.appendChild(overlay);
        card.addEventListener('mouseenter', function () { overlay.style.opacity = '1'; });
        card.addEventListener('mouseleave', function () { overlay.style.opacity = '0'; });
        card.addEventListener('click', function () {
            if (typeof mediaCallback === 'function') {
                mediaCallback(file.url, file.name);
                closeMedia();
            }
        });
        return card;
    }

    function loadMedia(page) {
        var grid = byId('_hunt-media-grid');
        if (!grid) return;
        page = (typeof page === 'number' && page > 0) ? page : 1;
        if (page === 1) {
            setMediaStatus('');
            grid.textContent = '';
            var loading = mediaMessage('Loading…', '#6b7280');
            loading.id = '_hunt-media-loading';
            grid.appendChild(loading);
        }
        root.fetch(admin.prefix + '/media/api?page=' + page)
            .then(function (r) { return r.json(); })
            .then(function (data) {
                if (page === 1) grid.textContent = '';
                var oldMore = byId('_hunt-media-more');
                if (oldMore) oldMore.remove();
                if (!data.files || !data.files.length) {
                    if (page === 1) grid.appendChild(mediaMessage('No media uploaded yet.', '#6b7280'));
                    return;
                }
                data.files.forEach(function (f) { grid.appendChild(buildMediaCard(f)); });
                var total = (typeof data.total === 'number') ? data.total : data.files.length;
                setMediaStatus(mediaStatusText(total));
                if (data.last_page && page < data.last_page) {
                    var more = document.createElement('button');
                    more.type = 'button';
                    more.id = '_hunt-media-more';
                    more.textContent = 'Load more';
                    more.style.cssText = 'grid-column:1/-1;padding:.5rem;border-radius:.375rem;background:rgba(255,255,255,.07);border:none;cursor:pointer;color:#d1d5db;font-size:.8125rem;';
                    more.addEventListener('click', function () { loadMedia(page + 1); });
                    grid.appendChild(more);
                }
            })
            .catch(function () {
                grid.textContent = '';
                grid.appendChild(mediaMessage('Failed to load media.', '#f87171'));
            });
    }

    function uploadMedia(files) {
        var queue = Array.from(files);
        function next() {
            if (!queue.length) { loadMedia(); return; }
            var file = queue.shift();
            setMediaStatus('Uploading ' + file.name + '…');
            var fd = new root.FormData();
            fd.append('file', file);
            fd.append('_token', admin.csrfToken);
            root.fetch(admin.prefix + '/media/upload', { method: 'POST', body: fd })
                .then(function (r) { return r.json(); })
                .then(function () { next(); })
                .catch(function () { setMediaStatus('Upload failed: ' + file.name); next(); });
        }
        next();
    }

    function openMediaManager(cb) {
        var modal = byId('_hunt-media-modal');
        if (!modal) return;
        mediaCallback = cb || null;
        modal.style.display = 'block';
        document.body.style.overflow = 'hidden';
        loadMedia();
    }

    function closeMedia() {
        var modal = byId('_hunt-media-modal');
        if (modal) modal.style.display = 'none';
        document.body.style.overflow = '';
        mediaCallback = null;
    }

    admin.openMediaManager = openMediaManager;
    admin._closeMedia = closeMedia;

    // --- resource index ------------------------------------------------

    function getSelectedIds() {
        return joinIds(Array.from(document.querySelectorAll('.row-select:checked')).map(function (el) { return el.value; }));
    }

    function openActionsModal() {
        if (!getSelectedIds()) { root.alert('Please select at least one record.'); return; }
        var modal = byId('actions-modal');
        if (modal) modal.classList.remove('hidden');
    }

    function closeActionsModal() {
        var modal = byId('actions-modal');
        if (modal) modal.classList.add('hidden');
    }

    // --- delegated click / change / submit -----------------------------

    var CLICK_ACTIONS = {
        'sidebar-open': openMobileSidebar,
        'sidebar-close': closeMobileSidebar,
        'profile-toggle': toggleProfileMenu,
        'nav-group-toggle': toggleNavGroup,
        'media-close': closeMedia,
        'actions-open': openActionsModal,
        'actions-close': closeActionsModal,
        'toggle-field': function (el) {
            toggleField(el, {
                fetch: root.fetch.bind(root),
                csrfToken: admin.csrfToken,
                alert: function (msg) { root.alert(msg); }
            });
        },
        'toggle-exception': function (el) {
            var body = el.querySelector('.exception-body');
            if (body) body.classList.toggle('hidden');
        },
        'dialog-open': function (el) {
            var dialog = byId(el.getAttribute('data-hunt-target'));
            if (dialog && typeof dialog.showModal === 'function') dialog.showModal();
        },
        'media-pick': function (el) {
            var targetId = el.getAttribute('data-hunt-target');
            openMediaManager(function (url) {
                var input = byId(targetId);
                if (input) input.value = url;
            });
        },
        'copy-value': function (el) {
            var input = byId(el.getAttribute('data-hunt-target'));
            if (input && input.value && root.navigator.clipboard) root.navigator.clipboard.writeText(input.value);
        }
    };

    var CHANGE_ACTIONS = {
        'submit': function (el) { if (el.form) el.form.submit(); },
        'per-page': function (el) { root.location.assign(perPageUrl(root.location.href, el.value)); },
        'select-all': function (el) {
            document.querySelectorAll('.row-select').forEach(function (box) { box.checked = el.checked; });
        }
    };

    document.addEventListener('click', function (e) {
        var el = closestWith(e.target, 'data-hunt-action');
        if (el) {
            var handler = CLICK_ACTIONS[el.getAttribute('data-hunt-action')];
            if (handler) handler(el, e);
        }
        // Close the profile menu on any click outside it.
        var btn = byId('profile-btn'), menu = byId('profile-menu');
        if (btn && menu && !btn.contains(e.target) && !menu.contains(e.target)) menu.classList.remove('open');
        // Close the global search dropdown on any click outside it.
        var inp = byId('global-search'), box = byId('search-results');
        if (inp && box && !inp.contains(e.target) && !box.contains(e.target)) box.style.display = 'none';
    });

    document.addEventListener('change', function (e) {
        var el = closestWith(e.target, 'data-hunt-change');
        if (el) {
            var handler = CHANGE_ACTIONS[el.getAttribute('data-hunt-change')];
            if (handler) handler(el, e);
        }
        if (e.target && e.target.id === '_hunt-media-upload') {
            if (e.target.files && e.target.files.length) uploadMedia(e.target.files);
            e.target.value = '';
        }
    });

    document.addEventListener('submit', function (e) {
        var form = e.target;
        if (form && form.hasAttribute && form.hasAttribute('data-hunt-bulk')) {
            var ids = form.querySelector('input[name="ids"]');
            if (ids) ids.value = getSelectedIds();
        }
        confirmSubmit(e, function (msg) { return root.confirm(msg); });
    }, true);

    document.addEventListener('keydown', function (e) {
        var modal = byId('_hunt-media-modal');
        if (e.key === 'Escape' && modal && modal.style.display !== 'none') closeMedia();
    });

    // --- global search --------------------------------------------------

    function initGlobalSearch() {
        var inp = byId('global-search'), box = byId('search-results'), timer;
        if (!inp || !box) return;
        function hide() { box.style.display = 'none'; box.innerHTML = ''; }
        inp.addEventListener('input', function () {
            root.clearTimeout(timer);
            var q = inp.value.trim();
            if (!q) { hide(); return; }
            timer = root.setTimeout(function () {
                root.fetch(globalSearchUrl(admin.prefix, q), { headers: { 'Accept': 'application/json' } })
                    .then(function (r) { return r.json(); })
                    .then(function (data) {
                        var groups = data.groups || [];
                        if (!groups.length) { hide(); return; }
                        box.innerHTML = renderSearchResults(groups);
                        box.style.display = 'block';
                    })
                    .catch(function () { box.style.display = 'none'; });
            }, 250);
        });
    }

    // --- relation pickers -----------------------------------------------

    /** Debounced relation search that fills `list` and calls `onPick(item)`. */
    function wireRelationSearch(input, list, field, onPick) {
        var timer;
        input.addEventListener('input', function () {
            root.clearTimeout(timer);
            timer = root.setTimeout(function () {
                root.fetch(relationSearchUrl(admin.prefix, admin.resourceKey, field, input.value.trim()))
                    .then(function (r) { return r.json(); })
                    .then(function (data) {
                        list.textContent = '';
                        if (!data.results || !data.results.length) { list.classList.add('hidden'); return; }
                        data.results.forEach(function (item) {
                            var li = document.createElement('li');
                            li.textContent = item.label;
                            li.className = 'px-3 py-2 cursor-pointer hover:bg-white/10';
                            li.addEventListener('mousedown', function (e) {
                                e.preventDefault();
                                onPick(item);
                                list.classList.add('hidden');
                            });
                            list.appendChild(li);
                        });
                        list.classList.remove('hidden');
                    })
                    .catch(function () { list.classList.add('hidden'); });
            }, 250);
        });
        input.addEventListener('blur', function () {
            root.setTimeout(function () { list.classList.add('hidden'); }, 200);
        });
    }

    function initAutocomplete(wrap) {
        var hidden = wrap.querySelector('[data-hunt-role="value"]');
        var display = wrap.querySelector('[data-hunt-role="search"]');
        var list = wrap.querySelector('[data-hunt-role="results"]');
        if (!hidden || !display || !list) return;
        wireRelationSearch(display, list, wrap.getAttribute('data-hunt-autocomplete'), function (item) {
            hidden.value = item.id;
            display.value = item.label;
        });
    }

    var BTM_CHIP_CLASS = 'inline-flex items-center gap-x-1 rounded-md bg-indigo-500/15 px-2 py-1 text-xs font-medium text-indigo-300 ring-1 ring-inset ring-indigo-500/30';
    var BTM_REMOVE_ICON = '<svg class="size-3" viewBox="0 0 12 12" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M2 2l8 8M10 2l-8 8"/></svg>';

    function initBelongsToMany(wrap) {
        var hidden = wrap.querySelector('[data-hunt-role="value"]');
        var chips = wrap.querySelector('[data-hunt-role="chips"]');
        var search = wrap.querySelector('[data-hunt-role="search"]');
        var list = wrap.querySelector('[data-hunt-role="results"]');
        if (!hidden || !chips || !search || !list) return;

        // One delegated listener covers server-rendered and newly added chips.
        chips.addEventListener('click', function (e) {
            var btn = e.target.closest ? e.target.closest('.btm-remove') : null;
            if (!btn) return;
            var chip = btn.closest('[data-id]');
            if (!chip) return;
            hidden.value = removeId(hidden.value, chip.dataset.id);
            chip.remove();
        });

        wireRelationSearch(search, list, wrap.getAttribute('data-hunt-btm'), function (item) {
            var updated = addId(hidden.value, item.id);
            search.value = '';
            if (updated === null) return;
            hidden.value = updated;
            var chip = document.createElement('span');
            chip.className = BTM_CHIP_CLASS;
            chip.dataset.id = String(item.id);
            chip.textContent = item.label;
            var btn = document.createElement('button');
            btn.type = 'button';
            btn.className = 'btm-remove ml-0.5 leading-none';
            btn.setAttribute('aria-label', 'Remove');
            btn.innerHTML = BTM_REMOVE_ICON;
            chip.appendChild(btn);
            chips.appendChild(chip);
        });
    }

    // --- Markdown editor (EasyMDE, loaded on demand) ---------------------

    var EASYMDE_DARK_CSS =
        '.EasyMDEContainer .CodeMirror{background:rgba(255,255,255,.05);color:#f9fafb;border-color:rgba(255,255,255,.1);border-radius:0 0 .375rem .375rem}' +
        '.EasyMDEContainer .editor-toolbar{background:rgba(255,255,255,.03);border-color:rgba(255,255,255,.1);border-radius:.375rem .375rem 0 0}' +
        '.EasyMDEContainer .editor-toolbar a,.EasyMDEContainer .editor-toolbar button{color:#9ca3af!important;border:none!important;background:none!important}' +
        '.EasyMDEContainer .editor-toolbar a:hover,.EasyMDEContainer .editor-toolbar button:hover,.EasyMDEContainer .editor-toolbar a.active,.EasyMDEContainer .editor-toolbar button.active{color:#fff!important;background:rgba(255,255,255,.08)!important;border-radius:.25rem}' +
        '.EasyMDEContainer .editor-toolbar i.separator{border-color:rgba(255,255,255,.15)}' +
        '.EasyMDEContainer .editor-toolbar.disabled-for-preview a:not(.no-disable){opacity:.35}' +
        '.EasyMDEContainer .editor-preview{background:#111827;color:#f9fafb;border-color:rgba(255,255,255,.1)}' +
        '.EasyMDEContainer .editor-preview table{border-collapse:collapse;width:100%}' +
        '.EasyMDEContainer .editor-preview table td,.EasyMDEContainer .editor-preview table th{border:1px solid rgba(255,255,255,.2);padding:.4rem .6rem}' +
        '.EasyMDEContainer .editor-statusbar{color:#6b7280;border-color:rgba(255,255,255,.1)}' +
        '.EasyMDEContainer .CodeMirror-cursor{border-left-color:#f9fafb}' +
        '.EasyMDEContainer .CodeMirror-selectedtext,.EasyMDEContainer .CodeMirror .CodeMirror-selected{background:rgba(99,102,241,.35)}' +
        '.EasyMDEContainer .cm-s-easymde .cm-header{color:#818cf8}' +
        '.EasyMDEContainer .cm-s-easymde .cm-link,.EasyMDEContainer .cm-s-easymde .cm-url{color:#6ee7b7}' +
        '.EasyMDEContainer .cm-s-easymde .cm-strong{color:#f9fafb;font-weight:700}' +
        '.EasyMDEContainer .cm-s-easymde .cm-em{color:#f9fafb;font-style:italic}';

    function addStylesheet(href) {
        var link = document.createElement('link');
        link.rel = 'stylesheet';
        link.href = href;
        document.head.appendChild(link);
    }

    function mountMarkdown(textarea) {
        if (!byId('_hunt-easymde-dark')) {
            var style = document.createElement('style');
            style.id = '_hunt-easymde-dark';
            style.textContent = EASYMDE_DARK_CSS;
            document.head.appendChild(style);
        }
        var options = {
            element: textarea,
            toolbar: [
                'bold', 'italic', 'heading', '|', 'table', 'link', '|',
                {
                    name: 'media',
                    action: function (editor) {
                        openMediaManager(function (url, name) {
                            editor.codemirror.replaceSelection(markdownImage(name, url));
                        });
                    },
                    className: 'fa fa-image',
                    title: 'Insert from Media Manager'
                },
                '|', 'guide'
            ],
            // The built-in preview renders the Markdown as raw HTML in this
            // page, so stored content could run script in the session of
            // whoever previews it. Preview and side-by-side are disabled
            // (buttons and keyboard shortcuts), and anything that does get
            // rendered is escaped to plain text. The detail page shows the
            // server-sanitised rendering instead.
            shortcuts: { togglePreview: null, toggleSideBySide: null },
            previewRender: function (plainText) { return '<pre>' + escapeForPreview(plainText) + '</pre>'; },
            renderingConfig: { sanitizerFunction: function (renderedHtml) { return escapeForPreview(renderedHtml); } },
            spellChecker: false,
            status: false,
            minHeight: '200px'
        };
        if (textarea.hasAttribute('data-hunt-readonly')) options.readOnly = true;
        return new root.EasyMDE(options);
    }

    function initMarkdown() {
        var areas = Array.from(document.querySelectorAll('textarea[data-hunt-markdown]'));
        if (!areas.length) return;
        function mountAll() { areas.forEach(mountMarkdown); }
        if (typeof root.EasyMDE !== 'undefined') { mountAll(); return; }
        addStylesheet(admin.prefix + '/assets/font-awesome.min.css');
        addStylesheet(admin.prefix + '/assets/easymde.min.css');
        var script = document.createElement('script');
        script.src = admin.prefix + '/assets/easymde.min.js';
        script.addEventListener('load', mountAll);
        document.head.appendChild(script);
    }

    // --- start-up ---------------------------------------------------------

    function init() {
        restoreNavGroups();
        initGlobalSearch();
        document.querySelectorAll('[data-hunt-autocomplete]').forEach(initAutocomplete);
        document.querySelectorAll('[data-hunt-btm]').forEach(initBelongsToMany);
        initMarkdown();
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})(typeof window !== 'undefined' ? window : null);
