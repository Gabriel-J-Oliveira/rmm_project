const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

test('NOC polls once, preserves focus, and keeps the panel on errors', async () => {
    const fields = new Map();
    function field(selector) {
        if (!fields.has(selector)) {
            fields.set(selector, {
                handlers: {},
                textContent: '',
                innerHTML: '',
                classList: {contains: () => false},
                addEventListener(name, handler) { this.handlers[name] = handler; },
                contains(node) { return Boolean(node); },
                querySelector() { return null; },
            });
        }
        return fields.get(selector);
    }

    let tick;
    let requests = 0;
    let finish;
    const root = {querySelector: field, querySelectorAll: () => []};
    const document = {
        querySelector: (selector) => selector === '[data-noc-root]' ? root : field(selector),
        querySelectorAll: () => [],
        activeElement: null,
        addEventListener() {},
    };
    const window = {
        location: {href: 'http://localhost/noc/?scope=active'},
        setInterval(callback) { tick = callback; },
    };
    const fetch = (url) => {
        requests += 1;
        assert.equal(url.searchParams.get('fragment'), '1');
        assert.equal(url.searchParams.get('scope'), 'active');
        return new Promise((resolve) => { finish = resolve; });
    };
    vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../../static/js/noc.js'), 'utf8'),
                       {document, window, URL, fetch});
    const content = field('[data-noc-content]');
    const status = field('[data-noc-refresh-status]');
    const refresh = field('[data-noc-refresh]').handlers.click;
    document.activeElement = {};
    const pending = refresh();
    await refresh();
    assert.equal(requests, 1);
    finish({
        ok: true,
        json: async () => ({html: '<p>Atualizado</p>', read_at: new Date().toISOString()}),
    });
    await pending;
    assert.equal(content.innerHTML, '');
    document.activeElement = null;
    tick();
    assert.equal(content.innerHTML, '<p>Atualizado</p>');
    const failed = refresh();
    finish({ok: false});
    await failed;
    assert.equal(content.innerHTML, '<p>Atualizado</p>');
    assert.match(status.textContent, /Falha na atualização/);
});
