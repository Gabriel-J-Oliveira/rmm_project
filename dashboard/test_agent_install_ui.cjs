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
        sid: `S-1-5-21-1-2-3-${number}`,
        ou_dn: 'OU=Lab,DC=control,DC=local', ou: 'Lab', operating_system: 'Windows',
        enabled: true, correlation_status: 'UNMANAGED', selectable: true, dns_status: 'RESOLVED',
        ...overrides,
    };
}

function setup(initial) {
    let modalOptions;
    const location = { href: '/agent-install/' };
    const ids = new Map();
    const nodes = [];
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
        querySelectorAll() { return []; },
        body: { classList: { add() {}, remove() {} } },
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
    document.getElementById('ad-state-filter').value = 'enabled';
    vm.runInNewContext(script, {
        document, window: { location, NightOwlInstallBatch: { BASE: '/batches/', modal(options) { modalOptions = options; }, request(url, data) { installs++; installBody = data; return Promise.resolve({ batch_id: 'synthetic' }); } }, lucide: { createIcons() {} }, setTimeout() { return 1; }, clearTimeout() {} },
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
        ids, nodes, location, get modal() { return modalOptions; }, get scans() { return scans; }, get preflights() { return preflights; },
        get installs() { return installs; }, get installBody() { return installBody; },
        setNextScan(callback) { nextScan = callback; },
        setNextInstall(callback) { nextInstall = callback; },
        setNextStatus(callback) { nextStatus = callback; },
        refresh() { ids.get('ad-discovery-form').listeners.submit({ preventDefault() {} }); },
        rows() { return ids.get('ad-computer-rows').children; },
        setFilter(name, value) { const input = ids.get('ad-' + name + '-filter'); input.value = value; input.listeners.input(); },
        selectPage(checked) { const box = ids.get('ad-select-page'); box.checked = checked; box.listeners.change(); },
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
    app.setFilter('agent', 'MANAGED');
    assert.equal(app.ids.get('ad-visible-count').textContent, '0 de 55 computadores');
    app.ids.get('ad-clear-filters').click();
    assert.equal(app.rows().length, 50);
});

test('selection is multiple, preserves pages and explains unavailable targets', async () => {
    const items = Array.from({ length: 12 }, (_, index) => computer(index + 1));
    items[1] = computer(2, { correlation_status: 'MANAGED', selectable: false });
    items[2] = computer(3, { enabled: false, selectable: false });
    items[3] = computer(4, { correlation_status: 'CONFLICT', selectable: false });
    const app = setup(items);
    await tick();
    app.ids.get('ad-clear-filters').click();
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
    assert.equal(app.ids.get('ad-selected-count').textContent, '2 selecionados');
    assert.equal(app.rows()[0].children[0].children[0].checked, true);
    assert.equal(app.rows()[4].children[0].children[0].checked, true);
    app.ids.get('ad-page-next').listeners.click();
    assert.equal(app.ids.get('ad-selected-count').textContent, '2 selecionados');
    app.rows()[0].children[10].children[0].click();
    assert.ok(app.modal);
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


test('51st selection rejected and modal posts pinned identities without IP then navigates', async () => {
    const app = setup(Array.from({length: 51}, (_,i) => computer(i+1)));
    await tick();
    app.ids.get('ad-page-size').value = '50'; app.ids.get('ad-page-size').listeners.change();
    for(let i=0;i<50;i++) { const box=app.rows()[i].children[0].children[0]; box.checked=true; box.listeners.change(); }
    assert.equal(app.ids.get('ad-selected-count').textContent,'50 selecionados');
    app.ids.get('ad-page-next').listeners.click();
    const box=app.rows()[0].children[0].children[0];box.checked=true;box.listeners.change();
    assert.equal(box.checked,false);
    assert.match(app.ids.get('ad-selection-note').textContent,/máximo 50/);
    app.ids.get('ad-prepare-button').listeners.click();
    assert.match(app.modal.description,/50/);
    await app.modal.perform({username:'synthetic',password:'synthetic-secret'});
    assert.deepEqual(Object.keys(app.installBody).sort(),['password','targets','username']);
    assert.equal(app.installBody.targets.length,50);
    assert.ok(app.installBody.targets.every(v=>v.fqdn.endsWith('.control.local') && v.sid && v.distinguished_name && !('ip' in v)));
    app.modal.accepted('abc');
    assert.equal(app.location.href,'/jobs/?tab=installations&batch=abc');
    assert.equal(app.preflights,0);
});
test('row install uses the same modal with one target', async () => {
    const app=setup([computer(1),computer(2)]);await tick();
    for(let i=0;i<2;i++){const b=app.rows()[i].children[0].children[0];b.checked=true;b.listeners.change();}
    app.ids.get('ad-prepare-button').listeners.click();assert.match(app.modal.description,/2/);
    app.rows()[0].children[10].children[0].click();
    assert.equal(app.ids.get('ad-selected-count').textContent,'1 selecionado');
    await app.modal.perform({username:'test',password:'test'});
    assert.equal(app.installBody.targets[0].fqdn,'pc1.control.local');
    assert.equal(app.installBody.targets[0].sid,computer(1).sid);
});

test('independent AD, agent and DNS filters combine with text, OU and OS', async () => {
    const app = setup([computer(1), computer(2, {enabled:false}), computer(3, {correlation_status:'MANAGED'}),
        computer(4, {dns_status:'UNRESOLVED'}), computer(5, {ou_dn:'OU=Other'}), computer(6, {operating_system:'Linux',dns_status:'ERROR'}),
        computer(7, {enabled:null, dns_status:null, correlation_status:null})]);
    await tick();
    app.setFilter('agent','UNMANAGED'); app.setFilter('dns','resolved');
    app.setFilter('ou','OU=Lab,DC=control,DC=local'); app.setFilter('os','Windows');
    assert.equal(app.ids.get('ad-visible-count').textContent,'1 de 7 computadores');
    app.ids.get('ad-search').value='missing'; app.ids.get('ad-search').listeners.input();
    assert.equal(app.ids.get('ad-visible-count').textContent,'0 de 7 computadores');
    assert.equal(app.ids.get('ad-select-page').disabled,true);
    app.ids.get('ad-clear-filters').click();
    assert.equal(app.ids.get('ad-visible-count').textContent,'7 de 7 computadores');
    app.setFilter('dns','missing');
    assert.equal(app.ids.get('ad-visible-count').textContent,'3 de 7 computadores');
    app.setFilter('dns','error');
    assert.equal(app.ids.get('ad-visible-count').textContent,'1 de 7 computadores');
    app.setFilter('state','unknown'); app.setFilter('dns','unknown'); app.setFilter('agent','unknown');
    assert.equal(app.ids.get('ad-visible-count').textContent,'1 de 7 computadores');
    assert.equal(app.rows()[0].children[0].children[0].disabled,true);
});

test('page selection excludes ineligible, persists and shows hidden selections', async () => {
    const app = setup(Array.from({length:25},(_,i)=>computer(i+1,{selectable:i!==2})));
    await tick(); app.selectPage(true);
    assert.equal(app.ids.get('ad-selected-count').textContent,'9 selecionados');
    assert.equal(app.ids.get('ad-select-page').checked,true);
    app.selectPage(true);
    assert.equal(app.ids.get('ad-selected-count').textContent,'9 selecionados');
    app.ids.get('ad-page-next').click();
    assert.match(app.ids.get('ad-hidden-selection').textContent,/9 fora desta página; 0 fora do filtro/);
    app.selectPage(true);
    assert.equal(app.ids.get('ad-selected-count').textContent,'19 selecionados');
    app.ids.get('ad-search').value='pc25'; app.ids.get('ad-search').listeners.input();
    assert.match(app.ids.get('ad-hidden-selection').textContent,/19 fora desta página; 19 fora do filtro/);
    app.ids.get('ad-clear-filters').click();
    assert.equal(app.ids.get('ad-select-page').checked,true);
    app.selectPage(false);
    assert.equal(app.ids.get('ad-selected-count').textContent,'10 selecionados');
    app.ids.get('ad-clear-selection').click();
    assert.equal(app.ids.get('ad-selected-count').textContent,'0 selecionados');
    assert.equal(app.ids.get('ad-clear-selection').disabled,true);
});

test('whole page over limit changes nothing and header has partial selection state', async () => {
    const app = setup(Array.from({length:61},(_,i)=>computer(i+1)));
    await tick();
    const first = app.rows()[0].children[0].children[0]; first.checked=true; first.listeners.change();
    assert.equal(app.ids.get('ad-select-page').indeterminate,true);
    app.ids.get('ad-page-size').value='50'; app.ids.get('ad-page-size').listeners.change();
    app.selectPage(true);
    app.ids.get('ad-page-next').click(); app.selectPage(true);
    assert.equal(app.ids.get('ad-selected-count').textContent,'50 selecionados');
    assert.equal(app.ids.get('ad-select-page').checked,false);
    assert.match(app.ids.get('ad-selection-note').textContent,/Nenhuma seleção foi alterada/);
    app.ids.get('ad-page-prev').click(); app.selectPage(false);
    assert.equal(app.ids.get('ad-selected-count').textContent,'0 selecionados');
});

test('missing or duplicate SID and duplicate FQDN cannot be arbitrarily selected', async () => {
    const app=setup([computer(1),computer(2,{sid:computer(1).sid}),computer(3,{sid:''}),
        computer(4),computer(5,{fqdn:computer(4).fqdn.toUpperCase()+'.'})]);
    await tick(); app.selectPage(true);
    assert.equal(app.ids.get('ad-selected-count').textContent,'0 selecionados');
    assert.equal(app.ids.get('ad-select-page').disabled,true);
});
