const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

class Element {
    constructor() {
        this.hidden = false; this.disabled = false; this.value = ''; this.textContent = '';
        this.children = []; this.listeners = {}; this.dataset = {}; this.style = {};
        this.attributes = {}; this.offsetWidth = 190; this.offsetHeight = 120;
    }
    addEventListener(name, fn) { (this.listeners[name] ||= []).push(fn); }
    async emit(name, event = {}) {
        let result;
        for (const fn of this.listeners[name] || []) result = await fn(event);
        return result;
    }
    click() { return this.emit('click', {preventDefault() {}}); }
    focus() { this.focused = true; }
    append(...nodes) { this.children.push(...nodes); }
    replaceChildren(...nodes) { this.children = nodes; }
    setAttribute(name, value) { this.attributes[name] = value; }
    getBoundingClientRect() { return {left: 0, top: 0, width: 500, height: 380}; }
    contains(target) { return target === this; }
    dispatchEvent() { return true; }
    querySelector() { return null; }
}

const panel = new Element();
panel.dataset.requestUrl = '/terminal/request/endpoint/';
const nodes = {
    '[data-terminal-start]': new Element(), '[data-terminal-stop]': new Element(),
    '[data-terminal-state]': new Element(), '[data-terminal-identity]': new Element(),
    '[data-terminal-screen]': new Element(), '[data-terminal-notice]': new Element(),
    '[data-terminal-stage]': new Element(), '[data-terminal-context-menu]': new Element(),
    '[data-terminal-copy]': new Element(), '[data-terminal-paste]': new Element(),
    '[data-terminal-paste-execute]': new Element(), '[name=csrfmiddlewaretoken]': Object.assign(new Element(), {value: 'csrf-test'}),
};
for (const selector of ['[data-terminal-copy]', '[data-terminal-paste]', '[data-terminal-paste-execute]']) nodes[selector].disabled = true;
panel.querySelector = selector => nodes[selector] || null;
const documentListeners = {};
const document = {
    activeElement: null,
    querySelector: selector => selector === '[data-terminal-panel]' ? panel : null,
    addEventListener: (name, fn) => { (documentListeners[name] ||= []).push(fn); },
};
const storageCalls = [];
const storage = new Proxy({}, {get(_target, name) { storageCalls.push(String(name)); return () => storageCalls.push(`call:${String(name)}`); }});
const requests = [];
let socket;
class Socket {
    static OPEN = 1;
    constructor(url, protocol) { this.url = url; this.protocol = protocol; this.readyState = 1; this.bufferedAmount = 0; this.sent = []; socket = this; }
    send(value) { this.sent.push(value); }
    close() { this.readyState = 3; this.closed = true; }
}
class Terminal {
    constructor(options) {
        this.options = options; this.cols = 80; this.rows = 24; this.parserHandlers = {};
        this.parser = {registerOscHandler: (code, fn) => { this.parserHandlers[code] = fn; }};
        this.buffer = {active: {length: 0}}; this.selection = ''; this.pastes = [];
        globalThis.terminalInstance = this;
    }
    loadAddon() {}
    open() {}
    onData(fn) { this.data = fn; }
    paste(value) { this.pastes.push(value); this.data?.(value); }
    getSelection() { return this.selection; }
    resize(cols, rows) { this.cols = cols; this.rows = rows; this.resized = [cols, rows]; }
    focus() { this.focused = true; document.activeElement = this; }
    dispose() { this.disposed = true; }
    write(_bytes, callback) { callback?.(); }
}
let resizeObserver;
class FitAddon { fit() { this.fitted = true; } }
let clipboardValue = '';
let rejectRead = false, rejectWrite = false, delayedRead = null, expiredStatus = false, confirmResult = false, confirmCalls = 0;
const context = {
    document, location: {protocol: 'https:', origin: 'https://nightowl.test'},
    navigator: {
        clipboard: {
            async writeText(value) { if (rejectWrite) throw new Error('denied'); clipboardValue = value; },
            async readText() { if (rejectRead) throw new Error('denied'); if (delayedRead) return new Promise(resolve => { delayedRead.resolve = resolve; }); return clipboardValue; },
        },
        sendBeacon() { return true; },
    },
    window: {addEventListener() {}, confirm() { confirmCalls++; return confirmResult; }},
    fetch: async (url, options) => {
        requests.push({url, options});
        if (url.includes('/status/')) return {ok: true, json: async () => expiredStatus ? {status: 'expired'} : ({status: 'authorized', identity: {sid: 'S-1-5-21-1-2-3-1001', windows_session_id: 2, integrity: 8192, elevated: false}})};
        if (url.includes('/connect/')) return {ok: true, json: async () => ({mode: 'conpty', execution: true,
            websocket_path: '/ws/terminal/00000000-0000-0000-0000-000000000001/operator/', credential: 'one-use-ticket'})};
        return {ok: true, json: async () => ({session_id: '00000000-0000-0000-0000-000000000001', status: 'awaiting_consent'})};
    },
    WebSocket: Socket, Terminal, FitAddon: {FitAddon}, ResizeObserver: class {
        constructor(callback) { this.callback = callback; resizeObserver = this; }
        observe() {}
        disconnect() { this.disconnected = true; }
    },
    URL, TextEncoder, TextDecoder, Uint8Array, DataView, BigInt, JSON, CustomEvent: class { constructor(type, init) { this.type = type; this.detail = init.detail; } },
    performance: {now: () => 10}, requestAnimationFrame: fn => fn(), localStorage: storage, sessionStorage: storage,
    setTimeout, clearTimeout, console,
};

function flush() { return new Promise(resolve => setImmediate(resolve)); }
function keyEvent(key, ctrlKey, shiftKey) {
    return {key, ctrlKey, shiftKey, prevented: false, stopped: false,
        preventDefault() { this.prevented = true; }, stopPropagation() { this.stopped = true; }};
}
function sentInput() {
    return socket.sent.filter(frame => frame instanceof Uint8Array)
        .map(frame => new TextDecoder().decode(frame.subarray(9))).join('');
}

(async () => {
    const template = fs.readFileSync('templates/dashboard/endpoint_terminal.html', 'utf8');
    assert.match(template, /data-terminal-copy/);
    assert.match(template, /data-terminal-paste/);
    assert.match(template, /data-terminal-paste-execute/);
    assert.match(template, /Ctrl\+C interrompe o processo remoto/);
    vm.runInNewContext(fs.readFileSync('static/js/endpoint_terminal.js', 'utf8'), context);
    assert.equal(nodes['[data-terminal-copy]'].disabled, true, 'clipboard actions stay disabled before authorization');
    await nodes['[data-terminal-start]'].click();
    await flush(); await flush();
    assert(socket, 'authorized ConPTY flow should create the existing WebSocket');
    socket.onopen();
    socket.onmessage({data: JSON.stringify({type: 'ready', mode: 'conpty', execution: true,
        identity: {sid: 'S-1-5-21-1-2-3-1001', windows_session_id: 2, integrity: 8192, elevated: false}})});
    const terminal = globalThis.terminalInstance;
    assert.equal(terminal.options.scrollback, 2000);
    assert.equal(terminal.parserHandlers[52]('ignored'), true, 'OSC 52 remains blocked');
    assert.equal(nodes['[data-terminal-copy]'].disabled, false);

    terminal.selection = 'selected output';
    const copyKey = keyEvent('C', true, true);
    await nodes['[data-terminal-screen]'].emit('keydown', copyKey);
    assert(copyKey.prevented && copyKey.stopped);
    await flush();
    assert.equal(clipboardValue, 'selected output');
    rejectWrite = true;
    await nodes['[data-terminal-screen]'].emit('keydown', keyEvent('c', true, true));
    await flush();
    assert.match(nodes['[data-terminal-notice]'].textContent, /permissão de cópia negada/);
    rejectWrite = false;

    clipboardValue = 'first line\r\nsecond line';
    await nodes['[data-terminal-screen]'].emit('keydown', keyEvent('v', true, true));
    await flush();
    assert.equal(terminal.pastes.at(-1), 'first line second line');
    assert(!terminal.pastes.at(-1).includes('\r'), 'plain paste cannot submit an Enter');

    rejectRead = true;
    const pasteCount = terminal.pastes.length;
    await nodes['[data-terminal-screen]'].emit('keydown', keyEvent('v', true, true));
    await flush();
    assert.equal(terminal.pastes.length, pasteCount, 'denied clipboard read must not paste');
    assert.match(nodes['[data-terminal-notice]'].textContent, /permissão de leitura negada/);
    rejectRead = false;

    let contextPrevented = false;
    await nodes['[data-terminal-screen]'].emit('contextmenu', {clientX: 480, clientY: 370, preventDefault() { contextPrevented = true; }});
    assert(contextPrevented);
    assert.equal(nodes['[data-terminal-context-menu]'].hidden, false);
    const menuDown = {key: 'ArrowDown', preventDefault() { this.prevented = true; }};
    await nodes['[data-terminal-context-menu]'].emit('keydown', menuDown);
    assert(menuDown.prevented && nodes['[data-terminal-paste]'].focused, 'context menu supports keyboard navigation');
    clipboardValue = 'single command';
    await nodes['[data-terminal-paste-execute]'].click();
    await flush();
    assert.equal(terminal.pastes.at(-1), 'single command\r', 'explicit execute adds one Enter');

    clipboardValue = 'line one\nline two'; confirmResult = false;
    const beforeMultiline = terminal.pastes.length;
    await nodes['[data-terminal-paste-execute]'].click(); await flush();
    assert.equal(terminal.pastes.length, beforeMultiline, 'multiline execution must wait for confirmation');
    assert.equal(confirmCalls, 1);
    confirmResult = true;
    await nodes['[data-terminal-paste-execute]'].click(); await flush();
    assert.equal(terminal.pastes.at(-1), 'line one\rline two\r');

    clipboardValue = `large${'x'.repeat(65536)}`;
    const beforeOversize = terminal.pastes.length;
    await nodes['[data-terminal-paste]'].click(); await flush();
    assert.equal(terminal.pastes.length, beforeOversize);
    assert.match(nodes['[data-terminal-notice]'].textContent, /limite de 64 KiB/);

    const plainCtrlC = keyEvent('c', true, false);
    await nodes['[data-terminal-screen]'].emit('keydown', plainCtrlC);
    assert.equal(plainCtrlC.prevented, false, 'Ctrl+C remains available to xterm as remote interrupt');
    terminal.data('\x03');
    assert.equal(sentInput().at(-1), '\x03');

    resizeObserver.callback();
    await new Promise(resolve => setTimeout(resolve, 180));
    assert.deepEqual(terminal.resized, [80, 24]);
    assert(socket.sent.some(value => typeof value === 'string' && JSON.parse(value).type === 'resize'));
    assert.equal(storageCalls.length, 0, 'terminal and clipboard content are never persisted');
    assert(!JSON.stringify(requests).includes('selected output'), 'clipboard data is never sent in HTTP requests');

    const beforeDelayedPaste = terminal.pastes.length;
    delayedRead = {};
    await nodes['[data-terminal-screen]'].emit('keydown', keyEvent('v', true, true));
    await flush();
    await nodes['[data-terminal-stop]'].click();
    delayedRead.resolve('must not cross sessions'); delayedRead = null;
    await flush();
    assert.equal(terminal.pastes.length, beforeDelayedPaste, 'clipboard completion after close cannot write into a terminal');
    assert.equal(terminal.disposed, true);
    assert.equal(nodes['[data-terminal-screen]'].hidden, true);
    assert.equal(nodes['[data-terminal-screen]'].children.length, 0);
    assert.equal(nodes['[data-terminal-copy]'].disabled, true);
    expiredStatus = true;
    await nodes['[data-terminal-start]'].click();
    await flush(); await flush();
    assert.equal(nodes['[data-terminal-state]'].textContent, 'Encerrado', 'expired session closes without opening a socket');
    assert.equal(nodes['[data-terminal-start]'].disabled, false);
    console.log('terminal clipboard/scrollback contracts: PASS');
})().catch(error => { console.error(error); process.exitCode = 1; });
