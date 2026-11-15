// Unit tests for src/hunt/admin/static/admin.js, run with `node --test`
// (no npm dependencies). tests/unit/test_fix_admin_csp.py runs this file.
//
// The first half tests the pure helpers the script exports. The second half
// loads the script into a small hand-written DOM stand-in to check the event
// wiring that replaced the inline onclick/onsubmit/onchange handlers.
'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const STATIC = path.resolve(__dirname, '..', '..', '..', 'src', 'hunt', 'admin', 'static');
const ADMIN_JS = path.join(STATIC, 'admin.js');
const util = require(ADMIN_JS);

// ---------------------------------------------------------------------------
// Pure helpers
// ---------------------------------------------------------------------------

test('escapeHtml escapes every HTML metacharacter', () => {
    assert.equal(util.escapeHtml(`<a href="x" onclick='y'>&</a>`), '&lt;a href=&quot;x&quot; onclick=&#39;y&#39;&gt;&amp;&lt;/a&gt;');
    assert.equal(util.escapeHtml(42), '42');
});

test('parseConfig tolerates missing and malformed JSON', () => {
    assert.deepEqual(util.parseConfig('{"prefix":"/hunt-admin","csrfToken":"t"}'), { prefix: '/hunt-admin', csrfToken: 't' });
    assert.deepEqual(util.parseConfig(''), {});
    assert.deepEqual(util.parseConfig('not json'), {});
    assert.deepEqual(util.parseConfig('[1,2]'), {});
    assert.deepEqual(util.parseConfig('null'), {});
});

test('id list helpers', () => {
    assert.equal(util.joinIds([1, '2', 3]), '1,2,3');
    assert.equal(util.joinIds([]), '');
    assert.deepEqual(util.splitIds(''), []);
    assert.equal(util.addId('', 5), '5');
    assert.equal(util.addId('1,2', 3), '1,2,3');
    assert.equal(util.addId('1,2', '2'), null);
    assert.equal(util.removeId('1,2,3', 2), '1,3');
    assert.equal(util.removeId('7', '7'), '');
});

test('perPageUrl keeps filters and resets the page', () => {
    const out = new URL(util.perPageUrl('http://x.test/hunt-admin/resources/posts?search=a%26b&page=4&per_page=25&filter_status=draft', '50'));
    assert.equal(out.searchParams.get('per_page'), '50');
    assert.equal(out.searchParams.get('page'), '1');
    assert.equal(out.searchParams.get('search'), 'a&b');
    assert.equal(out.searchParams.get('filter_status'), 'draft');
});

test('search URLs encode the query', () => {
    assert.equal(util.relationSearchUrl('/hunt-admin', 'posts', 'author_id', 'a&b c'), '/hunt-admin/resources/posts/search-relation?field=author_id&q=a%26b%20c');
    assert.equal(util.globalSearchUrl('/hunt-admin', '<x>&'), '/hunt-admin/search?q=%3Cx%3E%26');
});

test('renderSearchResults escapes server values', () => {
    const html = util.renderSearchResults([
        { resource: 'Posts<', resource_url: '/r?a=1&b="2"', items: [{ url: '/r/1', title: '<img src=x onerror=alert(1)>' }] },
    ]);
    assert.ok(!html.includes('<img'));
    assert.ok(html.includes('&lt;img src=x onerror=alert(1)&gt;'));
    assert.ok(html.includes('Posts&lt;'));
    assert.ok(html.includes('href="/r?a=1&amp;b=&quot;2&quot;"'));
    assert.equal(util.renderSearchResults([]), '');
    assert.equal(util.renderSearchResults(undefined), '');
});

test('markdown helpers', () => {
    assert.equal(util.markdownImage('cat.png', '/storage/media/cat.png'), '![cat.png](/storage/media/cat.png)');
    assert.equal(util.escapeForPreview('<img src=x onerror=alert(1)> & co'), '&lt;img src=x onerror=alert(1)&gt; &amp; co');
});

test('small formatters', () => {
    assert.equal(util.mediaStatusText(1), '1 file');
    assert.equal(util.mediaStatusText(3), '3 files');
    assert.ok(util.toggleBadgeClass(true).includes('text-green-400'));
    assert.ok(util.toggleBadgeClass(false).includes('text-gray-400'));
});

test('confirmSubmit blocks the submit only when the prompt is declined', () => {
    const makeEvent = (msg) => {
        const ev = { prevented: false, stopped: false, target: { getAttribute: () => msg } };
        ev.preventDefault = () => { ev.prevented = true; };
        ev.stopPropagation = () => { ev.stopped = true; };
        return ev;
    };
    const asked = [];
    let ev = makeEvent("Archive Bob's notes?");
    assert.equal(util.confirmSubmit(ev, (m) => { asked.push(m); return false; }), false);
    assert.deepEqual(asked, ["Archive Bob's notes?"]);
    assert.ok(ev.prevented && ev.stopped);

    ev = makeEvent('Sure?');
    assert.equal(util.confirmSubmit(ev, () => true), true);
    assert.ok(!ev.prevented);

    ev = makeEvent(null); // no data-confirm: never asks
    assert.equal(util.confirmSubmit(ev, () => { throw new Error('must not ask'); }), true);
    assert.ok(!ev.prevented);
});

test('toggleField sends the CSRF header and updates the badge only on 2xx', async () => {
    const makeBtn = () => ({ dataset: { toggleUrl: '/hunt-admin/resources/posts/3/field-toggle', attribute: 'published' }, textContent: 'No', className: 'old' });
    const calls = [];
    const alerts = [];
    const deps = (response) => ({
        csrfToken: 'tok-123',
        alert: (m) => alerts.push(m),
        fetch: (url, opts) => { calls.push([url, opts]); return Promise.resolve(response); },
    });

    let btn = makeBtn();
    await util.toggleField(btn, deps({ ok: true, json: () => Promise.resolve({ value: true }) }));
    assert.equal(calls[0][0], '/hunt-admin/resources/posts/3/field-toggle');
    assert.equal(calls[0][1].method, 'POST');
    assert.equal(calls[0][1].headers['X-CSRF-TOKEN'], 'tok-123');
    assert.deepEqual(JSON.parse(calls[0][1].body), { attribute: 'published' });
    assert.equal(btn.textContent, 'Yes');
    assert.ok(btn.className.includes('text-green-400'));
    assert.deepEqual(alerts, []);

    btn = makeBtn();
    await util.toggleField(btn, deps({ ok: false, status: 403, json: () => Promise.resolve({ value: true }) }));
    assert.equal(btn.textContent, 'No');
    assert.equal(btn.className, 'old');
    assert.deepEqual(alerts, ['The field could not be updated.']);

    btn = makeBtn(); // 2xx without a boolean value: leave the badge alone
    await util.toggleField(btn, deps({ ok: true, json: () => Promise.resolve({ value: 'yes' }) }));
    assert.equal(btn.textContent, 'No');
    assert.equal(alerts.length, 1);

    btn = makeBtn(); // network failure
    await util.toggleField(btn, { csrfToken: 't', alert: (m) => alerts.push(m), fetch: () => Promise.reject(new Error('offline')) });
    assert.equal(btn.textContent, 'No');
    assert.equal(alerts.length, 2);
});

// ---------------------------------------------------------------------------
// Event wiring, against a minimal DOM stand-in
// ---------------------------------------------------------------------------

function parseSelector(sel) {
    const m = /^([a-z]+)?((?:\.[\w-]+)*)((?:\[[^\]]+\])*)(:checked)?$/.exec(sel);
    if (!m) throw new Error('selector not supported by the test DOM: ' + sel);
    return {
        tag: m[1] || null,
        classes: m[2] ? m[2].split('.').filter(Boolean) : [],
        attrs: (m[3].match(/\[[^\]]+\]/g) || []).map((a) => {
            const [name, value] = a.slice(1, -1).split('=');
            return [name, value === undefined ? undefined : value.replace(/^"|"$/g, '')];
        }),
        checked: Boolean(m[4]),
    };
}

class El {
    constructor(tag, attrs = {}, children = []) {
        this.tagName = tag;
        this.attrs = { ...attrs };
        this.children = [];
        this.parent = null;
        this.style = {};
        this.listeners = {};
        this.value = attrs.value || '';
        this.checked = false;
        this._text = '';
        this.dataset = {};
        for (const [k, v] of Object.entries(attrs)) {
            if (k.startsWith('data-')) this.dataset[k.slice(5).replace(/-(\w)/g, (_, c) => c.toUpperCase())] = v;
        }
        const el = this;
        const classes = () => new Set((el.attrs.class || '').split(/\s+/).filter(Boolean));
        const save = (set) => { el.attrs.class = [...set].join(' '); };
        this.classList = {
            contains: (c) => classes().has(c),
            add: (c) => { const s = classes(); s.add(c); save(s); },
            remove: (c) => { const s = classes(); s.delete(c); save(s); },
            toggle: (c) => { const s = classes(); const on = !s.has(c); if (on) s.add(c); else s.delete(c); save(s); return on; },
        };
        children.forEach((c) => this.appendChild(c));
    }
    get id() { return this.attrs.id || ''; }
    get textContent() { return this._text; }
    set textContent(v) { this._text = String(v); this.children = []; }
    set id(v) { this.attrs.id = String(v); }
    get form() { let n = this.parent; while (n && n.tagName !== 'form') n = n.parent; return n; }
    get nextElementSibling() { const s = this.parent.children; return s[s.indexOf(this) + 1] || null; }
    appendChild(c) { c.parent = this; this.children.push(c); return c; }
    getAttribute(n) { return n in this.attrs ? this.attrs[n] : null; }
    hasAttribute(n) { return n in this.attrs; }
    setAttribute(n, v) { this.attrs[n] = String(v); }
    addEventListener(type, fn) { (this.listeners[type] = this.listeners[type] || []).push(fn); }
    matches(sel) {
        const p = parseSelector(sel);
        if (p.tag && p.tag !== this.tagName) return false;
        if (!p.classes.every((c) => this.classList.contains(c))) return false;
        if (!p.attrs.every(([n, v]) => this.hasAttribute(n) && (v === undefined || this.attrs[n] === v))) return false;
        return !p.checked || this.checked;
    }
    closest(sel) { let n = this; while (n && !(n instanceof El && n.matches(sel))) n = n.parent; return n || null; }
    contains(other) { let n = other; while (n) { if (n === this) return true; n = n.parent; } return false; }
    querySelectorAll(sel) {
        const out = [];
        const walk = (n) => n.children.forEach((c) => { if (c.matches(sel)) out.push(c); walk(c); });
        walk(this);
        return out;
    }
    querySelector(sel) { return this.querySelectorAll(sel)[0] || null; }
}

function h(tag, attrs, ...children) { return new El(tag, attrs || {}, children); }

/** Load admin.js into a fresh fake window whose <body> holds `nodes`. */
function boot(nodes, { config = { prefix: '/hunt-admin', resourceKey: 'posts', csrfToken: 'tok-123' }, storage = {} } = {}) {
    const configEl = h('script', { type: 'application/json', id: 'hunt-admin-config' });
    configEl.textContent = JSON.stringify(config);
    const head = h('head', {}, configEl);
    const body = h('body', {}, ...nodes);
    const html = h('html', {}, head, body);
    const listeners = {};
    const document = {
        readyState: 'complete',
        head,
        body,
        getElementById: (id) => html.querySelectorAll('[id]').find((e) => e.id === id) || null,
        querySelectorAll: (sel) => html.querySelectorAll(sel),
        querySelector: (sel) => html.querySelector(sel),
        addEventListener: (type, fn) => { (listeners[type] = listeners[type] || []).push(fn); },
        createElement: (tag) => h(tag),
    };
    const win = {
        document,
        confirms: [],
        confirmAnswer: true,
        alerts: [],
        fetches: [],
        fetchResponse: { ok: true, json: () => Promise.resolve({}) },
        assigned: [],
        location: { href: 'http://x.test/hunt-admin/resources/posts?search=a&page=3', assign(url) { win.assigned.push(url); } },
        localStorage: { getItem: (k) => (k in storage ? storage[k] : null), setItem: (k, v) => { storage[k] = v; } },
        navigator: { clipboard: { written: [], writeText(v) { this.written.push(v); return Promise.resolve(); } } },
        confirm(msg) { win.confirms.push(msg); return win.confirmAnswer; },
        alert(msg) { win.alerts.push(msg); },
        fetch(url, opts) { win.fetches.push([url, opts]); return Promise.resolve(win.fetchResponse); },
        URL,
        setTimeout: (fn) => { fn(); return 0; },
        clearTimeout: () => {},
    };
    win.window = win;
    vm.runInNewContext(fs.readFileSync(ADMIN_JS, 'utf8'), win, { filename: ADMIN_JS });
    win.dispatch = (type, target, extra = {}) => {
        const ev = { type, target, defaultPrevented: false, ...extra };
        ev.preventDefault = () => { ev.defaultPrevented = true; };
        ev.stopPropagation = () => {};
        (listeners[type] || []).forEach((fn) => fn(ev));
        return ev;
    };
    win.storage = storage;
    return win;
}

const settle = () => new Promise((resolve) => setImmediate(resolve));

test('config block becomes window._huntAdmin', () => {
    const win = boot([]);
    assert.equal(win._huntAdmin.prefix, '/hunt-admin');
    assert.equal(win._huntAdmin.resourceKey, 'posts');
    assert.equal(win._huntAdmin.csrfToken, 'tok-123');
    assert.equal(typeof win._huntAdmin.openMediaManager, 'function');
    assert.equal(typeof win._huntAdmin._closeMedia, 'function');
});

test('data-confirm: declining cancels the submit, accepting lets it through', () => {
    const form = h('form', { 'data-confirm': "Delete Bob's record?" });
    const plain = h('form', {});
    const win = boot([form, plain]);

    win.confirmAnswer = false;
    assert.equal(win.dispatch('submit', form).defaultPrevented, true);
    assert.deepEqual(win.confirms, ["Delete Bob's record?"]);

    win.confirmAnswer = true;
    assert.equal(win.dispatch('submit', form).defaultPrevented, false);

    assert.equal(win.dispatch('submit', plain).defaultPrevented, false);
    assert.equal(win.confirms.length, 2); // the plain form never asked
});

test('bulk action forms receive the selected ids on submit', () => {
    const boxes = [1, 2, 3].map((id) => h('input', { type: 'checkbox', class: 'row-select', value: String(id) }));
    const ids = h('input', { type: 'hidden', name: 'ids' });
    const form = h('form', { 'data-hunt-bulk': '', 'data-confirm': 'Sure?' }, ids);
    const win = boot([...boxes, form]);
    boxes[0].checked = true;
    boxes[2].checked = true;
    win.confirmAnswer = false;
    const ev = win.dispatch('submit', form);
    assert.equal(ids.value, '1,3');
    assert.equal(ev.defaultPrevented, true); // destructive action still asks
});

test('select-all checks every row; the actions modal needs a selection', () => {
    const all = h('input', { type: 'checkbox', id: 'select-all', 'data-hunt-change': 'select-all' });
    const boxes = [1, 2].map((id) => h('input', { type: 'checkbox', class: 'row-select', value: String(id) }));
    const open = h('button', { 'data-hunt-action': 'actions-open' });
    const close = h('button', { 'data-hunt-action': 'actions-close' });
    const modal = h('div', { id: 'actions-modal', class: 'relative hidden' }, close);
    const win = boot([all, ...boxes, open, modal]);

    win.dispatch('click', open);
    assert.deepEqual(win.alerts, ['Please select at least one record.']);
    assert.ok(modal.classList.contains('hidden'));

    all.checked = true;
    win.dispatch('change', all);
    assert.ok(boxes.every((b) => b.checked));

    win.dispatch('click', open);
    assert.ok(!modal.classList.contains('hidden'));
    win.dispatch('click', close);
    assert.ok(modal.classList.contains('hidden'));
});

test('boolean toggle button posts with the CSRF token from the config block', async () => {
    const inner = h('span', {});
    const btn = h('button', { 'data-hunt-action': 'toggle-field', 'data-toggle-url': '/hunt-admin/resources/posts/3/field-toggle', 'data-attribute': 'published' }, inner);
    const win = boot([btn]);
    win.fetchResponse = { ok: true, json: () => Promise.resolve({ value: true }) };
    win.dispatch('click', inner); // a click on a child still finds the button
    await settle();
    assert.equal(win.fetches.length, 1);
    assert.equal(win.fetches[0][1].headers['X-CSRF-TOKEN'], 'tok-123');
    assert.equal(btn.textContent, 'Yes');

    win.fetchResponse = { ok: false, json: () => Promise.resolve({ value: false }) };
    win.dispatch('click', btn);
    await settle();
    assert.equal(btn.textContent, 'Yes'); // unchanged on a non-2xx answer
    assert.deepEqual(win.alerts, ['The field could not be updated.']);
});

test('per-page selector and filter selects', () => {
    const perPage = h('select', { 'data-hunt-change': 'per-page' });
    perPage.value = '100';
    const filter = h('select', { 'data-hunt-change': 'submit' });
    let submitted = 0;
    const form = h('form', {}, filter);
    form.submit = () => { submitted += 1; };
    const win = boot([perPage, form]);
    win.dispatch('change', perPage);
    const url = new URL(win.assigned[0]);
    assert.equal(url.searchParams.get('per_page'), '100');
    assert.equal(url.searchParams.get('page'), '1');
    assert.equal(url.searchParams.get('search'), 'a');
    win.dispatch('change', filter);
    assert.equal(submitted, 1);
});

test('sidebar, profile menu and navigation groups', () => {
    const sidebar = h('div', { id: 'mobile-sidebar' });
    const open = h('button', { 'data-hunt-action': 'sidebar-open' });
    const close = h('button', { 'data-hunt-action': 'sidebar-close' });
    const profileBtn = h('button', { id: 'profile-btn', 'data-hunt-action': 'profile-toggle' });
    const menu = h('div', { id: 'profile-menu' });
    const chevron = h('svg', { class: 'nav-chevron' });
    const group = h('button', { 'data-group': 'system', 'data-hunt-action': 'nav-group-toggle' }, chevron);
    const items = h('ul', { class: 'nav-group-items' });
    const elsewhere = h('div', {});
    const win = boot([sidebar, open, close, profileBtn, menu, h('li', {}, group, items), elsewhere]);

    win.dispatch('click', open);
    assert.ok(sidebar.classList.contains('open'));
    assert.equal(win.document.body.style.overflow, 'hidden');
    win.dispatch('click', close);
    assert.ok(!sidebar.classList.contains('open'));

    win.dispatch('click', profileBtn);
    assert.ok(menu.classList.contains('open'));
    win.dispatch('click', elsewhere); // outside click closes it
    assert.ok(!menu.classList.contains('open'));

    win.dispatch('click', chevron);
    assert.ok(items.classList.contains('collapsed'));
    assert.equal(chevron.style.transform, 'rotate(-90deg)');
    assert.deepEqual(JSON.parse(win.storage._hunt_nav), { system: true });
    win.dispatch('click', group);
    assert.ok(!items.classList.contains('collapsed'));
});

test('collapsed navigation groups are restored from localStorage', () => {
    const chevron = h('svg', { class: 'nav-chevron' });
    const group = h('button', { 'data-group': 'system', 'data-hunt-action': 'nav-group-toggle' }, chevron);
    const items = h('ul', { class: 'nav-group-items' });
    boot([h('li', {}, group, items)], { storage: { _hunt_nav: '{"system":true}' } });
    assert.ok(items.classList.contains('collapsed'));
});

test('log rows, queue dialogs and the media picker buttons', async () => {
    const exception = h('div', { class: 'exception-body hidden' });
    const cell = h('td', {}, exception);
    const row = h('tr', { 'data-hunt-action': 'toggle-exception' }, cell);
    const dialog = h('dialog', { id: 'job-modal-7' });
    let shown = 0;
    dialog.showModal = () => { shown += 1; };
    const view = h('button', { 'data-hunt-action': 'dialog-open', 'data-hunt-target': 'job-modal-7' });
    const urlInput = h('input', { id: '_media_url_cover' });
    const pick = h('button', { 'data-hunt-action': 'media-pick', 'data-hunt-target': '_media_url_cover' });
    const copy = h('button', { 'data-hunt-action': 'copy-value', 'data-hunt-target': '_media_url_cover' });
    const closeBtn = h('button', { 'data-hunt-action': 'media-close' });
    const grid = h('div', { id: '_hunt-media-grid' });
    const modal = h('div', { id: '_hunt-media-modal' }, closeBtn, grid, h('div', { id: '_hunt-media-status' }));
    modal.style.display = 'none';
    const win = boot([h('table', {}, row), dialog, view, urlInput, pick, copy, modal]);

    win.dispatch('click', cell);
    assert.ok(!exception.classList.contains('hidden'));

    win.dispatch('click', view);
    assert.equal(shown, 1);

    win.fetchResponse = { ok: true, json: () => Promise.resolve({ files: [{ url: '/storage/media/a.png', name: 'a.png' }], total: 1, last_page: 1 }) };
    win.dispatch('click', pick);
    assert.equal(modal.style.display, 'block');
    assert.equal(win.fetches[0][0], '/hunt-admin/media/api?page=1');
    await settle();
    const card = grid.children[0];
    card.listeners.click[0](); // choose the file
    assert.equal(urlInput.value, '/storage/media/a.png');
    assert.equal(modal.style.display, 'none');

    win.dispatch('click', copy);
    assert.deepEqual(win.navigator.clipboard.written, ['/storage/media/a.png']);

    win.dispatch('click', pick);
    win.dispatch('keydown', win.document.body, { key: 'Escape' });
    assert.equal(modal.style.display, 'none');
    win.dispatch('click', pick);
    win.dispatch('click', closeBtn);
    assert.equal(modal.style.display, 'none');
});

test('global search renders escaped results and hides on an empty query', async () => {
    const input = h('input', { id: 'global-search' });
    const box = h('div', { id: 'search-results' });
    const win = boot([input, box]);
    win.fetchResponse = { ok: true, json: () => Promise.resolve({ groups: [{ resource: 'Posts', resource_url: '/hunt-admin/resources/posts', items: [{ url: '/hunt-admin/resources/posts/1', title: '<b>x</b>' }] }] }) };
    input.value = ' needle ';
    input.listeners.input[0]();
    await settle();
    assert.equal(win.fetches[0][0], '/hunt-admin/search?q=needle');
    assert.equal(box.style.display, 'block');
    assert.ok(box.innerHTML.includes('&lt;b&gt;x&lt;/b&gt;'));
    input.value = '';
    input.listeners.input[0]();
    assert.equal(box.style.display, 'none');
});

test('searchable BelongsTo picker fills the hidden input', async () => {
    const value = h('input', { type: 'hidden', 'data-hunt-role': 'value' });
    const search = h('input', { type: 'text', 'data-hunt-role': 'search' });
    const results = h('ul', { class: 'hidden', 'data-hunt-role': 'results' });
    const wrap = h('div', { 'data-hunt-autocomplete': 'author_id' }, value, search, results);
    const win = boot([wrap]);
    win.fetchResponse = { ok: true, json: () => Promise.resolve({ results: [{ id: 9, label: 'Ada <script>' }] }) };
    search.value = 'ad';
    search.listeners.input[0]();
    await settle();
    assert.equal(win.fetches[0][0], '/hunt-admin/resources/posts/search-relation?field=author_id&q=ad');
    assert.ok(!results.classList.contains('hidden'));
    const option = results.children[0];
    assert.equal(option.textContent, 'Ada <script>'); // text, never markup
    option.listeners.mousedown[0]({ preventDefault() {} });
    assert.equal(value.value, 9);
    assert.equal(search.value, 'Ada <script>');
    assert.ok(results.classList.contains('hidden'));
});

test('BelongsToMany picker adds and removes chips', async () => {
    const value = h('input', { type: 'hidden', 'data-hunt-role': 'value', value: '1' });
    const removeExisting = h('button', { class: 'btm-remove' });
    const existing = h('span', { 'data-id': '1' }, removeExisting);
    const chips = h('div', { 'data-hunt-role': 'chips' }, existing);
    existing.remove = () => { chips.children.splice(chips.children.indexOf(existing), 1); };
    const search = h('input', { type: 'text', 'data-hunt-role': 'search' });
    const results = h('ul', { class: 'hidden', 'data-hunt-role': 'results' });
    const wrap = h('div', { 'data-hunt-btm': 'tags' }, value, chips, search, results);
    const win = boot([wrap]);
    win.fetchResponse = { ok: true, json: () => Promise.resolve({ results: [{ id: 1, label: 'one' }, { id: 4, label: 'four' }] }) };
    search.value = 'o';
    search.listeners.input[0]();
    await settle();
    assert.equal(win.fetches[0][0], '/hunt-admin/resources/posts/search-relation?field=tags&q=o');
    results.children[0].listeners.mousedown[0]({ preventDefault() {} }); // already selected: no duplicate
    assert.equal(value.value, '1');
    assert.equal(chips.children.length, 1);
    results.children[1].listeners.mousedown[0]({ preventDefault() {} });
    assert.equal(value.value, '1,4');
    assert.equal(chips.children.length, 2);
    assert.equal(chips.children[1].dataset.id, '4');

    chips.listeners.click[0]({ target: removeExisting });
    assert.equal(value.value, '4');
    assert.equal(chips.children.length, 1);
});

test('Markdown textareas load EasyMDE from the asset route with preview disabled', () => {
    const area = h('textarea', { id: 'field_body', 'data-hunt-markdown': '' });
    const readonly = h('textarea', { id: 'field_notes', 'data-hunt-markdown': '', 'data-hunt-readonly': '' });
    const win = boot([area, readonly]);
    const head = win.document.head;
    const script = head.children.find((c) => c.tagName === 'script' && c.src);
    assert.equal(script.src, '/hunt-admin/assets/easymde.min.js');
    assert.deepEqual(head.children.filter((c) => c.tagName === 'link').map((c) => c.href), ['/hunt-admin/assets/font-awesome.min.css', '/hunt-admin/assets/easymde.min.css']);

    const created = [];
    win.EasyMDE = function (options) { created.push(options); };
    script.listeners.load[0]();
    assert.equal(created.length, 2);
    const options = created[0];
    assert.equal(options.element, area);
    assert.ok(!options.toolbar.includes('preview'));
    assert.ok(!options.toolbar.includes('side-by-side'));
    assert.equal(options.shortcuts.togglePreview, null);
    assert.equal(options.shortcuts.toggleSideBySide, null);
    assert.equal(options.previewRender('<img src=x onerror=alert(1)>'), '<pre>&lt;img src=x onerror=alert(1)&gt;</pre>');
    assert.equal(options.renderingConfig.sanitizerFunction('<script>x</script>'), '&lt;script&gt;x&lt;/script&gt;');
    assert.equal(options.readOnly, undefined);
    assert.equal(created[1].readOnly, true);
});

test('first-party scripts use no eval and build no inline handlers', () => {
    for (const name of ['admin.js', 'admin-media.js']) {
        const source = fs.readFileSync(path.join(STATIC, name), 'utf8');
        assert.ok(!/\beval\s*\(/.test(source), name + ' uses eval');
        assert.ok(!/new\s+Function\s*\(/.test(source), name + ' uses new Function');
        assert.ok(!/set(Timeout|Interval)\s*\(\s*['"`]/.test(source), name + ' passes a string to a timer');
        assert.ok(!/\.on[a-z]+\s*=/.test(source), name + ' assigns an on* handler property');
        assert.ok(!/setAttribute\(\s*['"]on/.test(source), name + ' sets an on* attribute');
        assert.ok(!/javascript:/.test(source), name + ' uses a javascript: URL');
    }
});
