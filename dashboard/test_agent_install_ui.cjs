const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const script = fs.readFileSync(path.join(__dirname, '..', 'static', 'js', 'agent_install_discovery.js'), 'utf8');
const tick = () => new Promise((resolve) => setImmediate(resolve));

function computer(number, overrides = {}) {
    return {
        hostname: `pc${number}`, fqdn: `pc${number}.control.local`, distinguished_name: `CN=pc${number},DC=control,DC=local`,
        ou_dn: 'OU=Lab,DC=control,DC=local', ou: 'Lab', operating_system: 'Windows',
        enabled: true, correlation_status: 'UNMANAGED', selectable: true, dns_status: 'RESOLVED',
        ...overrides,
    };
}

function setup(initial) {
    let nextStatus;
    const ids = new Map();
    const nodes = [];
    const filterButtons = ['enabled', 'unmanaged', 'managed', 'disabled', 'conflict', 'all'].map((name) => {
        const node = new Node('button');
        node.dataset.adFilter = name;
        return node;
    });
    let scans = 0;
    let preflights = 0;
    let installs = 0;
    let installBody;
    let nextScan;
    let nextInstall;
    function Node(tag) {
        this.tag = tag;
        this.children = [];
        this.listeners = {};
        this.dataset = {};
        this.value = '';
        this.hidden = false;
        nodes.push(this);
    }
    Object.defineProperty(Node.prototype, 'textContent', {
        get() { return this.text || this.children.map((child) => child.textContent).join(''); },
        set(value) { this.text = String(value); },
    });
    Object.defineProperty(Node.prototype, 'firstElementChild', { get() { return this.children[0]; } });
    Node.prototype.appendChild = function (child) { this.children.push(child); child.parent = this; return child; };
    Node.prototype.append = function (...children) { children.forEach((child) => this.appendChild(child)); };
    Node.prototype.replaceChildren = function (...children) { this.children = []; this.append(...children.filter(Boolean)); };
    Node.prototype.remove = function () { if (this.parent) this.parent.children = this.parent.children.filter((child) => child !== this); };
    Node.prototype.addEventListener = function (type, callback) { this.listeners[type] = callback; };
    Node.prototype.setAttribute = function (name, value) { this[name] = value; };
    Node.prototype.querySelector = function () { return { value: 'synthetic-csrf' }; };
    Node.prototype.querySelectorAll = function (selector) {
        return this.children.flatMap((child) => [
            ...(selector === 'input' && child.tag === 'input' ? [child] : []), ...child.querySelectorAll(selector),
        ]);
    };
    Node.prototype.focus = function () {};
    Node.prototype.click = function () { if (!this.disabled) this.listeners.click?.({ preventDefault() {} }); };
    const document = {
        getElementById(id) { if (!ids.has(id)) ids.set(id, new Node(id)); return ids.get(id); },
        createElement(tag) { return new Node(tag); },
        addEventListener(type, callback) { this[type] = callback; },
        querySelectorAll(query) { return query === '[data-ad-filter]' ? filterButtons : []; },
    };
    const root = document.getElementById('ad-discovery');
    root.dataset.scanUrl = '/scan/';
    root.dataset.preflightUrl = '/preflight/';
    root.dataset.installUrl = '/install/';
    ids.set('ad-ou-filter', new Node('select'));
    ids.get('ad-ou-filter').appendChild(new Node('option'));
    ids.set('ad-os-filter', new Node('select'));
    ids.get('ad-os-filter').appendChild(new Node('option'));
    ids.set('ad-page-size', new Node('select'));
    ids.get('ad-page-size').value = '10';
    vm.runInNewContext(script, {
        document, window: { lucide: { createIcons() {} }, setTimeout() { return 1; }, clearTimeout() {} },
        FormData: class { constructor() { this.fields = {}; } append(key, value) { this.fields[key] = value; } },
        fetch: async (url, options) => {
            if (url === '/preflight/') { preflights++; return { ok: true, json: async () => ({ status: 'READY', checks: {} }) }; }
            if (url === '/install/') {
                installs++; installBody = options.body.fields;
                if (nextInstall) return nextInstall();
                return { ok: true, json: async () => ({ status_url: '/agent-install/install-jobs/synthetic/' }) };
            }
            if (url === '/agent-install/install-jobs/synthetic/') {
                if (nextStatus) return nextStatus();
                return { ok: true, json: async () => ({ status: 'COMPLETED', first_heartbeat_at: '2026-10-03T12:00:00Z', endpoint_url: '/endpoints/synthetic/' }) };
            }
            scans++;
            if (nextScan) return nextScan();
            return { ok: true, json: async () => ({ computers: initial, summary: summary(initial) }) };
        },
    });
    document.DOMContentLoaded();
    return {
        ids, nodes, filterButtons, get scans() { return scans; }, get preflights() { return preflights; },
        get installs() { return installs; }, get installBody() { return installBody; },
        setNextScan(callback) { nextScan = callback; },
        setNextInstall(callback) { nextInstall = callback; },
        setNextStatus(callback) { nextStatus = callback; },
        refresh() { ids.get('ad-discovery-form').listeners.submit({ preventDefault() {} }); },
        rows() { return ids.get('ad-computer-rows').children; },
        clickFilter(name) { filterButtons.find((button) => button.dataset.adFilter === name).listeners.click(); },
    };
}

function summary(items) {
    return { total: items.length, unmanaged: items.filter((item) => item.correlation_status === 'UNMANAGED').length,
        managed: items.filter((item) => item.correlation_status === 'MANAGED').length };
}

test('auto scan, refresh guard and previous data preservation', async () => {
    const app = setup([computer(1)]);
    await tick();
    assert.equal(app.scans, 1);
    assert.equal(app.rows().length, 1);
    assert.equal(app.ids.get('ad-kpi-total').textContent, '1');
    let finish;
    app.setNextScan(() => new Promise((resolve) => { finish = resolve; }));
    app.refresh();
    app.refresh();
    assert.equal(app.scans, 2);
    assert.equal(app.rows().length, 1);
    assert.equal(app.ids.get('ad-scan-button').disabled, true);
    finish({ ok: true, json: async () => ({ computers: [computer(2), computer(3)], summary: summary([computer(2), computer(3)]) }) });
    await tick();
    assert.equal(app.rows().length, 2);
    assert.equal(app.ids.get('ad-kpi-total').textContent, '2');
    app.setNextScan(() => Promise.resolve({ ok: false }));
    app.refresh();
    await tick();
    assert.equal(app.rows().length, 2);
    assert.match(app.ids.get('ad-scan-status').textContent, /dados anteriores/);
});

test('pagination follows whole-dataset filters and search', async () => {
    const items = Array.from({ length: 55 }, (_, index) => computer(index + 1));
    const app = setup(items);
    await tick();
    assert.equal(app.rows().length, 10);
    assert.match(app.ids.get('ad-page-summary').textContent, /1-10 de 55/);
    app.ids.get('ad-page-next').listeners.click();
    assert.match(app.ids.get('ad-page-summary').textContent, /11-20 de 55/);
    app.ids.get('ad-page-size').value = '20';
    app.ids.get('ad-page-size').listeners.change();
    assert.equal(app.rows().length, 20);
    assert.match(app.ids.get('ad-page-summary').textContent, /1-20 de 55/);
    app.ids.get('ad-page-size').value = '50';
    app.ids.get('ad-page-size').listeners.change();
    assert.equal(app.rows().length, 50);
    app.ids.get('ad-page-next').listeners.click();
    assert.equal(app.rows().length, 5);
    app.ids.get('ad-search').value = 'pc55';
    app.ids.get('ad-search').listeners.input();
    assert.equal(app.rows().length, 1);
    assert.match(app.ids.get('ad-page-summary').textContent, /1-1 de 1/);
    app.ids.get('ad-search').value = '';
    app.ids.get('ad-search').listeners.input();
    app.clickFilter('managed');
    assert.equal(app.ids.get('ad-visible-count').textContent, '0 de 55 computadores');
    app.clickFilter('all');
    assert.equal(app.rows().length, 50);
});

test('selection stays unitary and explains unavailable targets', async () => {
    const items = Array.from({ length: 12 }, (_, index) => computer(index + 1));
    items[1] = computer(2, { correlation_status: 'MANAGED', selectable: false });
    items[2] = computer(3, { enabled: false, selectable: false });
    items[3] = computer(4, { correlation_status: 'CONFLICT', selectable: false });
    const app = setup(items);
    await tick();
    app.clickFilter('all');
    assert.deepEqual(app.rows().slice(1, 4).map((row) => row.children[0].children[0].disabled), [true, true, true]);
    assert.match(app.rows()[1].children[0].textContent, /Já gerenciado/);
    assert.match(app.rows()[2].children[0].textContent, /Desabilitado no AD/);
    assert.match(app.rows()[3].children[0].textContent, /Correlação ambígua/);
    app.rows()[0].children[0].children[0].checked = true;
    app.rows()[0].children[0].children[0].listeners.change();
    assert.equal(app.ids.get('ad-selected-count').textContent, '1 selecionado');
    assert.equal(app.ids.get('ad-prepare-button').disabled, false);
    app.rows()[4].children[0].children[0].checked = true;
    app.rows()[4].children[0].children[0].listeners.change();
    assert.equal(app.ids.get('ad-selected-count').textContent, '1 selecionado');
    assert.equal(app.rows()[0].children[0].children[0].checked, false);
    assert.equal(app.rows()[4].children[0].children[0].checked, true);
    app.ids.get('ad-page-next').listeners.click();
    assert.equal(app.ids.get('ad-selected-count').textContent, '1 selecionado');
    app.rows()[0].children[10].children[0].click();
    assert.ok(app.nodes.some((node) => node.className === 'ad-preflight-form'));
    assert.equal(app.preflights, 0);
    app.setNextScan(() => Promise.resolve({ ok: true, json: async () => ({
        computers: [computer(12, { correlation_status: 'MANAGED', selectable: false })],
        summary: summary([computer(12, { correlation_status: 'MANAGED', selectable: false })]),
    }) }));
    app.refresh();
    await tick();
    assert.equal(app.ids.get('ad-selected-count').textContent, '0 selecionados');
    app.setNextScan(() => Promise.resolve({ ok: true, json: async () => ({ computers: [computer(1)], summary: summary([computer(1)]) }) }));
    app.refresh();
    await tick();
    assert.equal(app.ids.get('ad-selected-count').textContent, '0 selecionados');
});

test('install requires READY, a second credential, and follows sanitized job status', async () => {
    const app = setup([computer(1)]);
    await tick();
    app.rows()[0].children[0].children[0].checked = true;
    app.rows()[0].children[0].children[0].listeners.change();
    app.ids.get('ad-prepare-button').listeners.click();
    const drawer = app.ids.get('ad-detail-content');
    assert.equal(app.nodes.some((node) => node.text === 'Instalar NightOwl'), false);
    const preflight = drawer.children.find((node) => node.className === 'ad-preflight-form');
    const preflightPassword = preflight.children[1].children[0];
    preflightPassword.value = 'synthetic-preflight-secret';
    await preflight.listeners.submit({ preventDefault() {} });
    assert.equal(preflightPassword.value, '');
    const installButton = drawer.children.find((node) => node.text === 'Instalar NightOwl');
    assert.ok(installButton);
    installButton.listeners.click();
    const installForm = drawer.children.find((node) => node.className === 'ad-preflight-form');
    const installUser = installForm.children[0].children[0];
    const installPassword = installForm.children[1].children[0];
    installUser.value = 'synthetic-admin';
    installPassword.value = 'synthetic-second-secret';
    await installForm.listeners.submit({ preventDefault() {} });
    await tick();
    assert.equal(app.installs, 1);
    assert.equal(app.installBody.password, 'synthetic-second-secret');
    assert.equal(installPassword.value, '');
    assert.match(drawer.textContent, /NightOwl instalado com sucesso/);
    assert.equal(app.nodes.some((node) => node.tag === 'a' && node.href === '/endpoints/synthetic/'), true);
});

test('install handles non-JSON and malformed responses without exposing HTML or credentials', async () => {
    for (const response of [
        { ok: false, json: async () => { throw new SyntaxError('<html>PRIVATE_TRACE</html>'); } },
        { ok: false, json: async () => null },
        { ok: true, json: async () => ['unexpected'] },
        { ok: true, json: async () => ({ status_url: 123 }) },
    ]) {
        const app = setup([computer(1)]);
        await tick();
        const checkbox = app.rows()[0].children[0].children[0];
        checkbox.checked = true; checkbox.listeners.change();
        app.ids.get('ad-prepare-button').listeners.click();
        const drawer = app.ids.get('ad-detail-content');
        const preflight = drawer.children.find((node) => node.className === 'ad-preflight-form');
        await preflight.listeners.submit({ preventDefault() {} });
        drawer.children.find((node) => node.text === 'Instalar NightOwl').listeners.click();
        const form = drawer.children.find((node) => node.className === 'ad-preflight-form');
        form.children[0].children[0].value = 'synthetic-admin';
        const password = form.children[1].children[0];
        password.value = 'SUPER_SECRET_REMOTE_INSTALL_91827';
        app.setNextInstall(() => response);
        await form.listeners.submit({ preventDefault() {} });
        assert.equal(app.installs, 1);
        assert.equal(password.value, '');
        assert.match(drawer.textContent, /instalação/i);
        assert.equal(drawer.textContent.includes('PRIVATE_TRACE'), false);
        assert.equal(drawer.textContent.includes('SUPER_SECRET_REMOTE_INSTALL_91827'), false);
        assert.equal(drawer.textContent.includes('NightOwl instalado com sucesso'), false);
    }
});

test('terminal diagnostics are plain text, preserve retry warning and never render credential', async () => {
    const app = setup([computer(1)]);
    await tick();
    const checkbox = app.rows()[0].children[0].children[0];
    checkbox.checked = true; checkbox.listeners.change();
    app.ids.get('ad-prepare-button').listeners.click();
    const drawer = app.ids.get('ad-detail-content');
    const preflight = drawer.children.find((node) => node.className === 'ad-preflight-form');
    await preflight.listeners.submit({ preventDefault() {} });
    drawer.children.find((node) => node.text === 'Instalar NightOwl').listeners.click();
    const form = drawer.children.find((node) => node.className === 'ad-preflight-form');
    form.children[0].children[0].value = 'synthetic-admin';
    form.children[1].children[0].value = 'SUPER_SECRET_REMOTE_INSTALL_91827';
    app.setNextStatus(() => ({ ok: true, json: async () => ({ status: 'OUTCOME_UNKNOWN',
        stage: 'INSTALLER_FINISHED', diagnostics: { installer_exit_code: 73, safe_to_retry: 'NO',
            safe_error_code: '<img src=x onerror=alert(1)>' } }) }));
    await form.listeners.submit({ preventDefault() {} });
    await tick();
    assert.match(drawer.textContent, /Exit code: 73/);
    assert.match(drawer.textContent, /Não repita sem revisão/);
    assert.ok(drawer.textContent.includes('<img src=x onerror=alert(1)>'));
    assert.equal(app.nodes.some((node) => node.tag === 'img'), false);
    assert.equal(form.children[1].children[0].value, '');
    assert.equal(drawer.textContent.includes('SUPER_SECRET_REMOTE_INSTALL_91827'), false);
    assert.equal(drawer.textContent.includes('SUPER_SECRET_ENROLLMENT_TOKEN_73192'), false);
    assert.equal(app.installs, 1);
});
