const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const root = path.join(__dirname, '..');
const script = fs.readFileSync(path.join(root, 'static/js/agent_install_advanced.js'), 'utf8');
const template = fs.readFileSync(path.join(root, 'templates/dashboard/agent_install.html'), 'utf8');

function setup() {
    const button = {
        attributes: { 'aria-expanded': 'false' }, listeners: {},
        getAttribute(name) { return this.attributes[name]; },
        setAttribute(name, value) { this.attributes[name] = value; },
        addEventListener(name, callback) { this.listeners[name] = callback; },
    };
    const content = { hidden: true };
    const document = {
        getElementById(id) {
            return { 'agent-advanced-toggle': button, 'agent-advanced-content': content }[id];
        },
        addEventListener(name, callback) { this[name] = callback; },
    };
    const storage = { getItem() { throw new Error('storage used'); }, setItem() { throw new Error('storage used'); } };
    vm.runInNewContext(script, { document, localStorage: storage, sessionStorage: storage });
    document.DOMContentLoaded();
    return { button, content };
}

test('advanced installation is a native button and is closed on each page load', () => {
    assert.match(template, /<button[^>]*type="button"[^>]*id="agent-advanced-toggle"[^>]*aria-expanded="false"[^>]*aria-controls="agent-advanced-content"/);
    assert.match(template, /<div[^>]*id="agent-advanced-content" hidden>/);
    assert.match(template, /data-lucide="chevron-down"/);
    const first = setup();
    assert.equal(first.button.getAttribute('aria-expanded'), 'false');
    assert.equal(first.content.hidden, true);
    first.button.listeners.click();
    const second = setup();
    assert.equal(second.content.hidden, true);
});

test('click and native Enter/Space activation expand and collapse without browser storage', () => {
    const { button, content } = setup();
    for (const activation of ['mouse', 'Enter', 'Space']) {
        button.listeners.click();
        assert.equal(button.getAttribute('aria-expanded'), 'true', activation);
        assert.equal(content.hidden, false, activation);
        button.listeners.click();
        assert.equal(button.getAttribute('aria-expanded'), 'false', activation);
        assert.equal(content.hidden, true, activation);
    }
    assert.doesNotMatch(script, /localStorage|sessionStorage/);
});

test('advanced content retains generator, copy control and package facts', () => {
    for (const id of ['agent-server-url', 'agent-endpoint-mode', 'agent-package-origin',
        'agent-install-mode', 'agent-package-path', 'agent-run-check', 'agent-keep-fallback',
        'agent-force-config', 'agent-debug-logs', 'generated-install-command']) {
        assert.match(template, new RegExp(`id="${id}"`));
    }
    assert.match(template, /data-copy-target="\.generated-command-block"/);
    for (const value of ['NightOwl.Agent.Windows.zip', 'Install-NightOwlAgentDotNet.ps1',
        'Uninstall-NightOwlAgentDotNet.ps1', 'checksums.json', 'version.json',
        'URL do pacote', 'Packages\/jobs', 'Fallback legado']) {
        assert.ok(template.includes(value), value);
    }
    assert.match(template, /id="ad-discovery"/);
    assert.match(template, /id="enrollment-token-form"/);
    assert.match(template, /id="manual-token-form"/);
    assert.match(template, /id="agent-token-history-rows"/);
});
