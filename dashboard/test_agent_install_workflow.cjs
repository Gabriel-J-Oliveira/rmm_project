const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const script = fs.readFileSync(path.join(__dirname, '..', 'static', 'js', 'agent_install_workflow.js'), 'utf8');
const tick = () => new Promise((resolve) => setImmediate(resolve));

function setup(rowCount = 12) {
    const nodes = [];
    class Node {
        constructor(tag) {
            this.tag = tag;
            this.children = [];
            this.listeners = {};
            this.attributes = {};
            this.hidden = false;
            this.value = '';
            nodes.push(this);
        }
        set textContent(value) { this.text = String(value); this.children = []; }
        get textContent() { return this.text || this.children.map((item) => item.textContent).join(''); }
        set innerHTML(_value) { throw new Error('HTML injection'); }
        appendChild(item) { this.children.push(item); item.parent = this; return item; }
        append(...items) { items.forEach((item) => this.appendChild(item)); }
        prepend(item) { this.children.unshift(item); item.parent = this; }
        replaceChildren(...items) { this.children = []; this.append(...items); }
        addEventListener(type, callback) { this.listeners[type] = callback; }
        setAttribute(name, value) { this.attributes[name] = String(value); }
        getAttribute(name) { return this.attributes[name]; }
        contains(item) { return this === item || this.children.some((child) => child.contains(item)); }
        remove() { if (this.parent) this.parent.children = this.parent.children.filter((child) => child !== this); }
        focus() { this.focused = true; }
        querySelectorAll(selector) {
            const match = (item) => {
                if (selector === '[data-token-row]') return Object.hasOwn(item.attributes, 'data-token-row');
                if (selector === '[data-token-empty]') return Object.hasOwn(item.attributes, 'data-token-empty');
                if (selector === '[type="submit"]') return item.type === 'submit';
                if (selector === '.agent-token-error') return item.className === 'agent-token-error';
                if (selector === '[name="csrfmiddlewaretoken"]') return item.name === 'csrfmiddlewaretoken';
                return false;
            };
            return this.children.flatMap((child) => [match(child) ? child : null, ...child.querySelectorAll(selector)]).filter(Boolean);
        }
        querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
    }
    const ids = new Map();
    function id(name, tag = 'div') { const item = new Node(tag); ids.set(name, item); return item; }
    const helpWrap = new Node('div');
    const helpButton = id('agent-help-button', 'button');
    const popover = id('agent-help-popover');
    popover.hidden = true;
    helpWrap.append(helpButton, popover);
    const history = id('agent-token-history-rows', 'tbody');
    for (let number = 0; number < rowCount; number++) {
        const row = new Node('tr');
        row.setAttribute('data-token-row', '');
        history.appendChild(row);
    }
    id('agent-token-page-summary');
    id('agent-token-page-numbers');
    id('agent-token-page-prev', 'button');
    id('agent-token-page-next', 'button');
    id('agent-token-page-size', 'select').value = '5';
    const active = new Node('strong');
    active.textContent = '3';
    function form(name) {
        const item = id(name, 'form');
        item.action = '/agent-install/';
        item.elements = { expires_hours: { value: '2' } };
        const submit = new Node('button');
        submit.type = 'submit';
        const error = new Node('p');
        error.className = 'agent-token-error';
        error.hidden = true;
        const csrf = new Node('input');
        csrf.name = 'csrfmiddlewaretoken';
        csrf.value = 'synthetic-csrf';
        item.append(submit, error, csrf);
        return item;
    }
    form('enrollment-token-form');
    form('manual-token-form');
    id('enrollment-token-result').hidden = true;
    id('manual-token-result').hidden = true;
    const adTable = id('ad-computer-rows');
    adTable.appendChild(new Node('tr'));
    const adSelection = id('ad-selected-count');
    adSelection.textContent = '1 selecionado';
    const adPage = id('ad-page-summary');
    adPage.textContent = 'Mostrando 11-20 de 31';
    let requests = 0;
    let nextResponse;
    const document = {
        getElementById(name) { return ids.get(name); },
        createElement(tag) { return new Node(tag); },
        querySelector(selector) {
            if (selector === '.agent-help-wrap') return helpWrap;
            if (selector === '.agent-install-metrics .metric-card:nth-child(4) .metric-value') return active;
            return null;
        },
        addEventListener(type, callback) { this[type] = callback; },
        body: new Node('body'),
    };
    vm.runInNewContext(script, {
        document,
        window: { matchMedia: () => ({ matches: true }), location: { href: '/agent-install/' }, lucide: { createIcons() {} } },
        navigator: { clipboard: { writeText: async () => {} } },
        FormData: class { constructor(item) { this.form = item; } },
        fetch: async () => { requests++; return nextResponse(); },
    });
    document.DOMContentLoaded();
    return { ids, nodes, document, helpWrap, active, get requests() { return requests; },
        setResponse(callback) { nextResponse = callback; },
        submit(name) { return ids.get(name).listeners.submit({ preventDefault() {} }); },
        visibleRows() { return history.querySelectorAll('[data-token-row]').filter((row) => !row.hidden); } };
}

test('help opens on hover and click, closes on Escape, outside click and second click', () => {
    const app = setup();
    const popover = app.ids.get('agent-help-popover');
    const button = app.ids.get('agent-help-button');
    app.helpWrap.listeners.mouseenter();
    assert.equal(popover.hidden, false);
    app.helpWrap.listeners.mouseleave();
    assert.equal(popover.hidden, true);
    button.listeners.click();
    assert.equal(button.getAttribute('aria-expanded'), 'true');
    app.document.keydown({ key: 'Escape' });
    assert.equal(popover.hidden, true);
    assert.equal(button.focused, true);
    app.helpWrap.listeners.mouseleave();
    button.listeners.click();
    button.listeners.click();
    assert.equal(popover.hidden, true);
    button.listeners.click();
    app.document.click({ target: {} });
    assert.equal(popover.hidden, true);
});

test('token history paginates 5, 10 and 20 without reloading', () => {
    const app = setup(12);
    const size = app.ids.get('agent-token-page-size');
    assert.equal(app.visibleRows().length, 5);
    assert.match(app.ids.get('agent-token-page-summary').textContent, /1-5 de 12/);
    app.ids.get('agent-token-page-next').listeners.click();
    assert.match(app.ids.get('agent-token-page-summary').textContent, /6-10 de 12/);
    size.value = '10';
    size.listeners.change();
    assert.equal(app.visibleRows().length, 10);
    size.value = '20';
    size.listeners.change();
    assert.equal(app.visibleRows().length, 12);
    assert.equal(app.requests, 0);
});

test('enrollment AJAX is single request, preserves AD state and renders safe inline result', async () => {
    const app = setup(2);
    let finish;
    app.setResponse(() => new Promise((resolve) => { finish = resolve; }));
    const first = app.submit('enrollment-token-form');
    const second = app.submit('enrollment-token-form');
    assert.equal(app.requests, 1);
    assert.equal(app.ids.get('enrollment-token-form').querySelector('[type="submit"]').disabled, true);
    const token = '<img src=x onerror=alert(1)>';
    finish({ ok: true, json: async () => ({ ok: true, kind: 'enrollment', token, name: 'Synthetic',
        prefix: 'synthetic-prefix', id: 'synthetic-id', revoke_url: '/revoke/',
        allowed_domain: 'example.test', expires_at: '2026-10-03T12:00:00Z', created_at: '2026-10-03T10:00:00Z',
        command: 'powershell.exe -File install.ps1', max_uses: 1 }) });
    await Promise.all([first, second]);
    await tick();
    const result = app.ids.get('enrollment-token-result');
    assert.equal(result.hidden, false);
    assert.ok(app.nodes.some((item) => item.tag === 'code' && item.textContent === token));
    assert.equal(app.nodes.some((item) => item.tag === 'img'), false);
    assert.equal(app.ids.get('manual-token-result').hidden, true);
    assert.equal(app.ids.get('ad-computer-rows').children.length, 1);
    assert.equal(app.ids.get('ad-selected-count').textContent, '1 selecionado');
    assert.equal(app.ids.get('ad-page-summary').textContent, 'Mostrando 11-20 de 31');
    assert.equal(app.active.textContent, '4');
    assert.equal(app.ids.get('agent-token-history-rows').querySelectorAll('[data-token-row]').length, 3);
    assert.equal(app.ids.get('enrollment-token-form').querySelector('[type="submit"]').disabled, false);
});

test('manual AJAX renders beneath its own card and backend error remains contextual', async () => {
    const app = setup(0);
    app.setResponse(() => Promise.resolve({ ok: true, json: async () => ({ ok: true, kind: 'manual_validation',
        token: 'synthetic-manual', expires_at: '2026-10-03T12:00:00Z' }) }));
    await app.submit('manual-token-form');
    assert.equal(app.ids.get('manual-token-result').hidden, false);
    assert.equal(app.ids.get('enrollment-token-result').hidden, true);
    assert.equal(app.active.textContent, '3');
    app.setResponse(() => Promise.resolve({ ok: false, json: async () => ({ ok: false,
        error: 'A validade do token manual deve ser maior que zero.' }) }));
    await app.submit('manual-token-form');
    assert.match(app.ids.get('manual-token-form').querySelector('.agent-token-error').textContent, /maior que zero/);
    assert.equal(app.ids.get('manual-token-result').hidden, false);
    assert.equal(app.ids.get('ad-selected-count').textContent, '1 selecionado');
});

test('client rejects invalid enrollment expiry without request', async () => {
    const app = setup(0);
    app.ids.get('enrollment-token-form').elements.expires_hours.value = '73';
    await app.submit('enrollment-token-form');
    assert.equal(app.requests, 0);
    assert.match(app.ids.get('enrollment-token-form').querySelector('.agent-token-error').textContent, /1 e 72/);
});
