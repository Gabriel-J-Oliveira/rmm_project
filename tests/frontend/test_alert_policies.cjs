const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const fields = {};
for (const [name, value, label] of [
    ['metric', 'ram', 'Uso de RAM'], ['scope', 'desktops', 'Desktops'],
    ['duration_minutes', '15'], ['threshold', '90'], ['recovery_threshold', '80'],
    ['antivirus_condition', '', 'Proteção desativada'], ['job_window_minutes', ''],
    ['min_coverage_percent', '80'], ['max_age_minutes', '5'], ['endpoints', ''],
    ['severity', 'warning', 'Atenção'], ['enabled', 'on'], ['notify_visual', 'on'], ['notify_sound', ''],
]) {
    const wrapper = {hidden: false, hasAttribute: () => false};
    fields[name] = {
        value, wrapper, checked: value === 'on', closest: () => wrapper,
        selectedOptions: label ? [{textContent: label}] : [],
    };
}
const summary = {textContent: ''};
const unit = {textContent: ''};
const events = {};
const form = {
    elements: {namedItem: (name) => fields[name]},
    querySelector: () => summary,
    querySelectorAll: () => [unit],
    addEventListener: (event, handler) => { events[event] = handler; },
};
const document = {
    querySelector: () => form,
    getElementById: () => ({textContent: JSON.stringify({
        ram: {unit: '%', coverage: true},
        antivirus: {binary: true},
        job_failures: {unit: 'falhas', window: true},
    })}),
};
vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../../static/js/alert_policies.js'), 'utf8'), {document});

assert.match(summary.textContent, /Uso de RAM ≥ 90 % por pelo menos 15 min/);
assert.match(summary.textContent, /Recuperação com ≤ 80 %.*som desligado/);
assert.equal(unit.textContent, '%');
assert.equal(fields.endpoints.disabled, true);

fields.recovery_threshold.value = '95';
events.input();
assert.match(summary.textContent, /abaixo do limite/);

fields.metric.value = 'antivirus';
fields.antivirus_condition.value = 'disabled';
fields.notify_sound.checked = true;
events.change();
assert.equal(fields.threshold.disabled, true);
assert.equal(fields.antivirus_condition.wrapper.hidden, false);
assert.match(summary.textContent, /Proteção desativada.*proteção ativa confirmada.*som ligado/);

fields.metric.value = 'job_failures';
fields.metric.selectedOptions = [{textContent: 'Falhas de jobs'}];
fields.recovery_threshold.value = '0';
fields.threshold.value = '3';
fields.job_window_minutes.value = '30';
fields.scope.value = 'endpoints';
fields.endpoints.selectedOptions = [{}];
events.change();
assert.equal(fields.job_window_minutes.disabled, false);
assert.equal(fields.endpoints.disabled, false);
assert.match(summary.textContent, /1 endpoint.*3 falhas nos últimos 30 min/);
