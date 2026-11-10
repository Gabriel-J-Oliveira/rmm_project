const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');

class Element {
    constructor() {
        this.hidden = false; this.value = ''; this.textContent = ''; this.children = []; this.listeners = {};
        this.dataset = {}; this.style = {}; this.disabled = false; this.offsetWidth = 190; this.offsetHeight = 120;
        this.classList = {toggle(){}};
    }
    addEventListener(name, fn) { this.listeners[name] = fn; }
    click() { this.listeners.click?.({preventDefault(){}}); }
    focus() {}
    append(...nodes) { this.children.push(...nodes); }
    replaceChildren(...nodes) { this.children = nodes; }
    setAttribute(name, value) { this[name] = value; }
    getBoundingClientRect() { return {left: 0, top: 0, width: 800, height: 450}; }
    dispatchEvent(event) { events.push(event); }
    querySelector(selector) { return this.nodes?.[selector]; }
}

const events = [], panel = new Element(), nodes = {};
for (const key of ['[data-auth-modal]','[data-terminal-modal]','[data-terminal-dialog]','[data-terminal-status]',
    '[data-connection-indicator]','[data-identity]','[data-screen]','[data-terminal-stage]','[data-terminal-notice]',
    '[data-terminal-context-menu]','[data-terminal-copy]','[data-terminal-paste]','[data-terminal-paste-execute]',
    '[data-cancel]','[data-terminal-close]','[data-auth-status]']) nodes[key] = new Element();
nodes['[data-auth-modal]'].hidden = true;
nodes['[data-terminal-modal]'].hidden = true;
nodes['[data-screen]'].hidden = true;
nodes['[data-terminal-context-menu]'].hidden = true;
panel.nodes = nodes; panel.dataset = {url: '/session', endpoint: 'CS-LAB-01'};
const form = new Element(), submit = new Element();
form.elements = {password: new Element(), account: new Element(), domain: new Element(), csrfmiddlewaretoken: {value: 'csrf'}};
form.nodes = {'[type=submit]': submit}; form.reportValidity = () => true; nodes.form = form;
const open = new Element(), storage = new Map([
    ['nightowl.terminal.accounts.operator-1.endpoint-1', JSON.stringify(['legacy-user'])],
    ['nightowl.preferences.theme', 'dark']
]);
const localStorage = {
    get length() { return storage.size; },
    key(index) { return [...storage.keys()][index] ?? null; },
    getItem(key) { return storage.get(key) ?? null; },
    setItem(key, value) { storage.set(key, value); },
    removeItem(key) { storage.delete(key); }
};
const document = {
    activeElement: null,
    querySelector: selector => selector === '[data-admin-auth]' ? panel : selector === '[data-terminal-open]' ? open : null,
    addEventListener() {}
};
let ws;
class Socket {
    constructor() { ws = this; this.readyState = 1; this.bufferedAmount = 0; this.sent = []; }
    send(value) { this.sent.push(value); }
    close() { this.closed = true; }
}
Socket.OPEN = 1;
let terminalInstance;
class Term {
    constructor(options) { this.options = options; this.parser = {registerOscHandler(){}}; this.cols = 80; this.rows = 24; terminalInstance = this; }
    loadAddon() {}
    open() {}
    onData(callback) { this.onDataCallback = callback; }
    resize() {}
    focus() {}
    dispose() { this.disposed = true; }
    write() {}
    getSelection() { return 'diagnostic output'; }
    paste(value) { this.lastPaste = value; }
}
const calls = [], clipboard = {written: '', read: 'whoami\r\nhostname', denied: false};
const context = {
    document, localStorage, location: {protocol: 'https:', hostname: 'lab', origin: 'https://lab'},
    fetch: async (url, opts) => {
        calls.push([url, opts]);
        return {ok: true, json: async () => ({session_id: '00000000-0000-0000-0000-000000000001', mode: 'admin_auth', execution: true,
            websocket_path: '/ws/terminal/00000000-0000-0000-0000-000000000001/operator/', credential: 'one-use-ticket-012345678901234567890123456789'})};
    },
    WebSocket: Socket, Terminal: Term, FitAddon: {FitAddon: class {fit(){}}},
    ResizeObserver: class {observe(){} disconnect(){}}, URL, TextEncoder, Uint8Array, DataView, JSON, BigInt,
    performance: {now: (() => { let n = 10; return () => ++n; })()},
    CustomEvent: class {constructor(type, init) { this.type = type; this.detail = init.detail; }},
    navigator: {clipboard: {
        writeText: async value => {if (clipboard.denied) throw new Error('denied'); clipboard.written = value;},
        readText: async () => {if (clipboard.denied) throw new Error('denied'); return clipboard.read;}
    }},
    setTimeout, clearTimeout, window: {addEventListener(){}, confirm: () => true}
};
vm.runInNewContext(fs.readFileSync('static/js/terminal_admin_auth.js', 'utf8'), context);
const source = fs.readFileSync('static/js/terminal_admin_auth.js', 'utf8');
assert(!/localStorage\.setItem|sessionStorage|indexedDB/.test(source), 'terminal UI has no credential persistence path');

(async () => {
    assert.equal(storage.has('nightowl.terminal.accounts.operator-1.endpoint-1'), false, 'legacy terminal account key is removed');
    assert.equal(storage.get('nightowl.preferences.theme'), 'dark', 'unrelated localStorage key is preserved');
    open.click();
    assert.equal(nodes['[data-auth-modal]'].hidden, false, 'credentials open directly');
    assert.equal(form.elements.account.value, '');
    assert.equal(form.elements.password.value, '');
    form.elements.account.value = 'LAB\\Admin';
    form.elements.password.value = 'synthetic-secret';
    await form.listeners.submit({preventDefault(){}});
    assert.equal(nodes['[data-terminal-modal]'].hidden, false, 'connection modal is immediate');
    assert.equal(nodes['[data-terminal-status]'].textContent, 'Solicitando conexão');
    assert.equal(form.elements.account.value, '', 'account field cleared on submit');
    assert.equal(form.elements.password.value, '', 'password field cleared on submit');
    ws.onopen();
    ws.onmessage({data: JSON.stringify({type: 'transport_ready', mode: 'admin_auth', execution: true})});
    assert.equal(nodes['[data-terminal-status]'].textContent, 'Autenticando no Windows');
    const authMessage = JSON.parse(ws.sent[1]);
    assert.deepEqual({account: authMessage.account, domain: authMessage.domain, password: authMessage.password},
        {account: 'Admin', domain: 'LAB', password: 'synthetic-secret'});
    assert.equal(storage.size, 1, 'credentials are not written to browser storage');
    ws.onmessage({data: JSON.stringify({type: 'ready', mode: 'admin_auth', execution: true,
        identity: {sid: 'S-1-5-21-1-2-3-1001', integrity: 12288, elevated: true, windows_session_id: 1}})});
    assert.equal(nodes['[data-terminal-status]'].textContent, 'Conectado');
    assert.equal(nodes['[data-identity]'].textContent, 'S-1-5-21-1-2-3-1001 · sessão Windows 1 · administrador elevado confirmado');
    assert.equal(terminalInstance.options.scrollback, 2000);
    assert.equal(nodes['[data-terminal-modal]'].hidden, false);
    assert(!JSON.stringify(events.map(event => event.detail)).includes('synthetic-secret'));
    assert(!JSON.stringify(events.map(event => event.detail)).includes('LAB'));
    assert(!JSON.stringify(events.map(event => event.detail)).includes('Admin'));
    assert(!JSON.stringify(calls).includes('synthetic-secret'));

    let prevented = false;
    nodes['[data-screen]'].listeners.keydown({ctrlKey: true, shiftKey: true, key: 'c', preventDefault(){prevented = true;}, stopPropagation(){}});
    await new Promise(resolve => setImmediate(resolve));
    assert(prevented, 'Ctrl+Shift+C is handled by the UI');
    assert.equal(clipboard.written, 'diagnostic output');
    clipboard.denied = true;
    nodes['[data-screen]'].listeners.keydown({ctrlKey: true, shiftKey: true, key: 'c', preventDefault(){}, stopPropagation(){}});
    await new Promise(resolve => setImmediate(resolve));
    assert.match(nodes['[data-terminal-notice]'].textContent, /Clipboard indisponível/);
    clipboard.denied = false;
    nodes['[data-screen]'].listeners.contextmenu({preventDefault(){}, clientX: 80, clientY: 80});
    nodes['[data-terminal-paste]'].click();
    await new Promise(resolve => setImmediate(resolve));
    assert.equal(terminalInstance.lastPaste, 'whoami hostname', 'paste does not submit multiline Enter');
    context.window.confirm = () => false;
    nodes['[data-terminal-paste-execute]'].click();
    await new Promise(resolve => setImmediate(resolve));
    assert.equal(terminalInstance.lastPaste, 'whoami hostname', 'multiline execution cancellation sends nothing');
    context.window.confirm = () => true;
    nodes['[data-terminal-paste-execute]'].click();
    await new Promise(resolve => setImmediate(resolve));
    assert.equal(terminalInstance.lastPaste, 'whoami\rhostname\r', 'execute requires explicit action');
    let ctrlCPrevented = false;
    nodes['[data-screen]'].listeners.keydown({ctrlKey: true, shiftKey: false, key: 'c', preventDefault(){ctrlCPrevented = true;}, stopPropagation(){}});
    assert.equal(ctrlCPrevented, false, 'Ctrl+C remains available to xterm as process interrupt');

    nodes['[data-terminal-close]'].click();
    assert.equal(ws.closed, true);
    assert.equal(terminalInstance.disposed, true);
    assert.equal(nodes['[data-terminal-modal]'].hidden, true);
    assert(calls.some(([url]) => url.includes('/cancel/')));

    open.click();
    form.elements.account.value = 'Rejected'; form.elements.password.value = 'bad-secret';
    await form.listeners.submit({preventDefault(){}}); ws.onopen();
    ws.onmessage({data: JSON.stringify({type: 'transport_ready', mode: 'admin_auth', execution: true})});
    ws.onmessage({data: JSON.stringify({type: 'windows_auth_result', result: 'rejected'})});
    assert.equal(nodes['[data-terminal-status]'].textContent, 'Falha de autenticação');
    assert.equal(form.elements.password.value, '');
    assert.equal(form.elements.account.value, '');
    assert.equal(storage.size, 1);
    assert(!JSON.stringify(calls).includes('bad-secret'));
    assert(!JSON.stringify(events.map(event => event.detail)).includes('bad-secret'));
    nodes['[data-terminal-close]'].click();

    open.click(); form.elements.account.value = 'LAB\\Admin'; form.elements.password.value = 'system-token-secret';
    await form.listeners.submit({preventDefault(){}}); ws.onopen();
    ws.onmessage({data: JSON.stringify({type: 'transport_ready', mode: 'admin_auth', execution: true})});
    ws.onmessage({data: JSON.stringify({type: 'ready', mode: 'admin_auth', execution: true,
        identity: {sid: 'S-1-5-18', integrity: 16384, elevated: true, windows_session_id: 0}})});
    assert.equal(nodes['[data-terminal-status]'].textContent, 'Falha na sessão', 'SYSTEM identity is rejected by the UI');
    assert.equal(nodes['[data-identity]'].textContent, 'Identidade Windows não confirmada');
    assert.equal(form.elements.password.value, '');
    nodes['[data-terminal-close]'].click();

    open.click(); form.elements.account.value = 'cancel-me'; form.elements.password.value = 'cancel-secret';
    nodes['[data-cancel]'].click();
    assert.equal(form.elements.account.value, ''); assert.equal(form.elements.password.value, '');
    assert.equal(nodes['[data-auth-modal]'].hidden, true);
    console.log('terminal admin UI: direct credential modal, scoped storage cleanup, staged connection, authenticated identity, clipboard and secret cleanup OK');
})().catch(error => { console.error(error); process.exitCode = 1; });
