const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const root = path.resolve(__dirname, '..', '..');
const source = fs.readFileSync(path.join(root, 'static/js/endpoint_detail.js'), 'utf8');

function extract(from, to) {
    const start = source.indexOf(from);
    const end = source.indexOf(to, start + from.length);
    assert(start >= 0 && end > start, `source section exists: ${from}`);
    return source.slice(start, end);
}

function element() {
    return {
        attributes: {},
        hidden: false,
        isConnected: true,
        focus() { context.document.activeElement = this; },
        getAttribute(name) { return this.attributes[name] || null; },
        getClientRects() { return [{}]; }
    };
}

const opener = element();
const closeButton = element();
const copyButton = element();
const refreshButton = element();
const classSet = new Set();
const bodyClasses = new Set();
const drawer = {
    attributes: {},
    classList: {
        add(value) { classSet.add(value); },
        remove(value) { classSet.delete(value); },
        contains(value) { return classSet.has(value); }
    },
    setAttribute(name, value) { this.attributes[name] = value; },
    querySelector(selector) { return selector === '[data-endpoint-drawer-close]' ? closeButton : null; },
    querySelectorAll() { return [closeButton, copyButton, refreshButton]; },
    contains(node) { return [this, closeButton, copyButton, refreshButton].includes(node); },
    focus() { context.document.activeElement = this; }
};
const drawerBody = { innerHTML: '' };
const drawerBackdrop = { hidden: true };
const job = {
    id: 'job-' + 'a'.repeat(72), jobId: 'job-' + 'a'.repeat(72), type: 'collect_software',
    status: 'completed', name: 'Coletar software', command: 'Leitura de inventário', createdAt: '2026-11-09T12:00:00Z',
    startedAt: '2026-11-09T12:00:01Z', durationMs: 1200, createdBy: 'operador', exitCode: 0,
    endpoint: 'CS-CVEL-0281.control.local', payload: {}, resultJson: {}, stdout: '', stderr: ''
};
const context = {
    drawer, drawerBody, drawerBackdrop,
    drawerKicker: { textContent: '' }, drawerTitle: { textContent: '' }, drawerSubtitle: { textContent: '' },
    document: {
        activeElement: opener,
        body: { classList: { add(value) { bodyClasses.add(value); }, remove(value) { bodyClasses.delete(value); } } }
    },
    window: { scrollY: 712, pageYOffset: 712, scrollTo(x, y) { this.restoredY = y; }, NightOwlJobDisplay: { status: item => item.status } },
    endpointDetail: { hostname: 'CS-CVEL-0281', activeJob: null, jobs: [job] },
    labels: { collect_software: 'Coletar software', update_agent: 'Atualizar agente' },
    jobProgress: () => null,
    jobResultLabel: item => item.status === 'failed' ? item.errorMessage || 'Falha' : item.status === 'completed' ? 'Job concluído.' : '',
    staleReasonLabel: () => 'sem atualização recente',
    formatDate: value => value || '-',
    formatDuration: value => value == null ? '-' : `${value}ms`,
    escapeHtml: value => String(value ?? '').replace(/[&<>"']/g, char => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[char])),
    factList(items) {
        return '<dl class="endpoint-fact-list rmm-info-grid">' + items.map(item => `<div><dt>${item.label}</dt><dd>${this.escapeHtml(item.value == null || item.value === '' ? '-' : item.value)}</dd></div>`).join('') + '</dl>';
    }
};
context.factList = context.factList.bind(context);
vm.runInNewContext(extract('    function findJob(id) {', '    function openEventDrawer(id) {'), context);
vm.runInNewContext(extract('    function openJobDrawer(id) {', '    function copyText(value) {'), context);
vm.runInNewContext(extract('    function trapDrawerFocus(event) {', '    function hasMeaningfulJobData(value) {'), context);

context.openJobDrawer(job.id);
assert.equal(drawer.attributes['aria-hidden'], 'false');
assert.equal(drawerBackdrop.hidden, false);
assert(bodyClasses.has('endpoint-drawer-open'));
assert.equal(context.document.activeElement, closeButton, 'focus enters the dialog');
assert.match(drawerBody.innerHTML, /Job ID/);
assert.match(drawerBody.innerHTML, /Job concluído\./);
assert.doesNotMatch(drawerBody.innerHTML, /Correlation ID|Payload sanitizado|Resultado sanitizado|Stdout|Stderr/, 'empty technical fields are omitted');
assert.match(JSON.stringify(job.__technicalDetails), /job_id/, 'copy data retains the full technical object');

function keyEvent(shiftKey) {
    return { key: 'Tab', shiftKey: Boolean(shiftKey), prevented: false, preventDefault() { this.prevented = true; } };
}
let event = keyEvent(true);
context.trapDrawerFocus(event);
assert(event.prevented);
assert.equal(context.document.activeElement, refreshButton, 'Shift+Tab wraps from first to last control');
event = keyEvent(false);
context.document.activeElement = refreshButton;
context.trapDrawerFocus(event);
assert(event.prevented);
assert.equal(context.document.activeElement, closeButton, 'Tab wraps from last to first control');

const errorJob = { ...job, id: 'failed-job', status: 'failed', errorCode: 'REMOTE_ACTION_FAILED', errorMessage: 'Service reported an error' };
context.endpointDetail.jobs = [errorJob];
context.openJobDrawer('failed-job');
assert.match(drawerBody.innerHTML, /Codigo de erro/);
assert.match(drawerBody.innerHTML, /REMOTE_ACTION_FAILED/);
assert.match(drawerBody.innerHTML, /Mensagem de erro/);

const longValue = 'C:\\ProgramData\\NightOwl\\' + 'deployment-identifier-'.repeat(30);
const updateJob = { ...job, id: 'update-job', type: 'update_agent', status: 'completed', previousVersion: '0.1.1.0-rc46', targetVersion: '0.1.1.0-rc47', installedVersion: '0.1.1.0-rc47', correlationId: 'corr-' + 'x'.repeat(96), payload: { path: longValue, steps: Array.from({ length: 24 }, (_, index) => ({ index, detail: 'diagnostic ' + 'long-value-'.repeat(10) })) }, resultJson: { installed_version: '0.1.1.0-rc47', rollback_confirmed: false } };
context.endpointDetail.jobs = [updateJob];
context.openJobDrawer('update-job');
assert.match(drawerBody.innerHTML, /Versao anterior/);
assert.match(drawerBody.innerHTML, /Versao alvo/);
assert.match(drawerBody.innerHTML, /Versao instalada/);
assert.match(drawerBody.innerHTML, /Identificacao tecnica e diagnostico/);
assert.match(drawerBody.innerHTML, /Payload sanitizado/);
assert.match(drawerBody.innerHTML, /Resultado sanitizado/);
assert(drawerBody.innerHTML.includes(longValue.replaceAll('\\', '\\\\')), 'long paths are not silently truncated');

context.closeDrawer();
assert.equal(drawer.attributes['aria-hidden'], 'true');
assert.equal(drawerBackdrop.hidden, true);
assert(!bodyClasses.has('endpoint-drawer-open'), 'background scroll lock is removed');
assert.equal(context.window.restoredY, 712, 'the original page position is restored');
assert.equal(context.document.activeElement, opener, 'focus returns to the opener');

console.log('endpoint job drawer: filtered details, full diagnostics, focus cycle, scroll lock and restoration passed');
