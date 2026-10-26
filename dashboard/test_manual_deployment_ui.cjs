const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const scriptPath = path.join(__dirname, '..', 'static', 'js', 'manual_deployment.js');
const script = fs.readFileSync(scriptPath, 'utf8');

test('manual deployment presents distinct operational states', () => {
    const window = {};
    vm.runInNewContext(script, { window, document: { querySelector: () => null } });
    const labels = window.NightOwlManualDeployment;
    assert.equal(labels.statusLabel('waiting'), 'Aguardando execu\u00e7\u00e3o');
    assert.equal(labels.statusLabel('installing'), 'Instalando');
    assert.equal(labels.statusLabel('confirmed'), 'Instala\u00e7\u00e3o confirmada por heartbeat');
    assert.equal(labels.statusLabel('confirmation_pending'), 'Instalado; confirma\u00e7\u00e3o pendente de heartbeat');
    assert.equal(labels.statusLabel('failed'), 'Falha');
    assert.equal(labels.statusLabel('expired'), 'Token expirado');
    assert.equal(labels.statusLabel('unknown'), 'Status indispon\u00edvel');
});

test('manual deployment does not persist command or build DOM from untrusted HTML', () => {
    assert.doesNotMatch(script, /localStorage|sessionStorage/);
    assert.doesNotMatch(script, /\.innerHTML\s*=/);
    assert.match(script, /commandOutput\.textContent\s*=\s*deployment\.command/);
    assert.match(script, /cache:\s*"no-store"/);
    assert.match(script, /data-deployment-history/);
});

function createUI() {
    class Element {
        constructor() { this.dataset = {}; this.children = []; this.handlers = {}; this.value = ''; this.textContent = ''; }
        addEventListener(name, handler) { this.handlers[name] = handler; }
        querySelector(selector) { return elements[selector]; }
        querySelectorAll() { return this.children.filter((item) => item.dataset.deploymentId); }
        appendChild(child) { this.children.push(child); if (!this.value && child.value) this.value = child.value; }
        append(...items) { items.forEach((item) => this.appendChild(item)); }
        prepend(child) { this.children.unshift(child); }
        replaceChildren() { this.children = []; this.value = ''; }
        setAttribute() {}
    }
    const elements = Object.fromEntries([...script.matchAll(/(?:querySelector\(|querySelectorAll\()"(\[data-deployment-[^"\]]+\])"/g)]
        .map((match) => [match[1], new Element()]));
    elements["input[name='csrfmiddlewaretoken']"] = { value: 'csrf' };
    const form = elements['[data-deployment-form]'];
    form.dataset = { trustReady: 'true', createUrl: '/api/deployments/windows/', historyUrl: '/api/deployments/windows/history/' };
    elements['[data-deployment-channel]'].value = 'stable';
    const releases = [{ id: 'rc45', version: '0.1.1.0-rc45', channel: 'development', status: 'published', paused: true }];
    const requests = [], copied = [];
    const document = {
        querySelector: (selector) => elements[selector],
        querySelectorAll: (selector) => [elements[selector]].filter(Boolean),
        getElementById: () => ({ textContent: JSON.stringify(releases) }),
        createElement: () => new Element()
    };
    const window = { setInterval: () => 1, clearInterval() {}, setTimeout() {} };
    class FormData { set() {} delete() {} }
    const fetch = async (url, options = {}) => {
        requests.push({ url, options });
        return { ok: true, redirected: false, headers: { get: () => 'application/json' }, json: async () => options.method === 'POST'
            ? { status: 'ok', deployment: { id: 'attempt-1', release_version: '0.1.1.0-rc45', command: "$env:NIGHTOWL_DEPLOYMENT_TOKEN='deploy_SENTINEL'", expires_at: '2026-10-26T20:00:00Z' } }
            : { deployments: [{ id: 'unrelated-remote-job', ui_status: 'installing' }] } };
    };
    vm.runInNewContext(script, { window, document, FormData, fetch, Intl, Date, navigator: { clipboard: { writeText: async (text) => copied.push(text) } } });
    return { elements, form, requests, copied };
}

test('missing stable explains explicit development selection; generation and repeated copy use one token', async () => {
    const { elements, form, requests, copied } = createUI();
    assert.equal(elements['[data-deployment-submit]'].disabled, true);
    assert.match(elements['[data-deployment-error]'].textContent, /Selecione Development/);
    elements['[data-deployment-channel]'].value = 'development';
    elements['[data-deployment-channel]'].handlers.change();
    assert.equal(elements['[data-deployment-submit]'].disabled, false);
    await form.handlers.submit({ preventDefault() {} });
    await elements['[data-deployment-copy]'].handlers.click.call(elements['[data-deployment-copy]']);
    await elements['[data-deployment-copy]'].handlers.click.call(elements['[data-deployment-copy]']);
    assert.equal(requests.filter((item) => item.options.method === 'POST').length, 1);
    assert.equal(copied.length, 2);
    assert.equal(copied[0], copied[1]);
    assert.match(copied[0], /NIGHTOWL_DEPLOYMENT_TOKEN/);
    assert.doesNotMatch(copied[0], /downloads\/nightowl-agent\/Install-/);
});

test('loading history does not select an unrelated install or block a new manual command', async () => {
    const { elements } = createUI();
    elements['[data-deployment-channel]'].value = 'development';
    elements['[data-deployment-channel]'].handlers.change();
    elements['[data-deployment-open]'].handlers.click();
    await new Promise(setImmediate);
    assert.equal(elements['[data-deployment-submit]'].disabled, false);
    assert.notEqual(elements['[data-deployment-progress]'].hidden, false);
});
