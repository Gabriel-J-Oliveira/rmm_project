const fs = require('node:fs'), vm = require('node:vm'), assert = require('node:assert/strict');
const source = fs.readFileSync('static/js/endpoint_detail.js', 'utf8');
const start = source.indexOf('    function renderTaskCategory(title, iconName, buttons, options) {');
const end = source.indexOf('    function renderDiagnostics(detail)', start);
assert(start >= 0 && end > start);
const ctx = {agentLifecycleActionButtons: () => ['UNINSTALL'], purgeActionButtons: () => ['PURGE'],
    agentManagementActionButtons: () => ['UPDATE'], icon: () => '', actionButton: action => '[' + action + ']',
    operationalLabels: {flush_dns: 'DNS', query_service: 'Serviço'}, asObject: x => x || {},
    escapeHtml: x => String(x)};
vm.runInNewContext(source.slice(start, end) + ';this.render = renderTasks;', ctx);
const groups = ctx.render({operationalActions: {flush_dns: {enabled: true}, query_service: {enabled: false}}});
assert(groups.core.includes('Diagnósticos'));
assert(groups.core.includes('Operações'));
assert(groups.core.includes('data-action-grid="four"'));
assert(groups.core.includes('data-action-grid="three"'));
assert(groups.core.includes('[ping]'));
assert(groups.maintenance.includes('Manutenção do agente'));
assert(groups.maintenance.indexOf('UPDATE') > groups.maintenance.indexOf('Manutenção do agente'));
assert(groups.critical.includes('Ações críticas'));
assert(groups.critical.indexOf('UNINSTALL') > groups.critical.indexOf('Ações críticas'));
assert.match(groups.core, /data-endpoint-action="query_service" disabled/);
assert.doesNotMatch(groups.core, /data-endpoint-action="run_approved_script"/);
assert.match(fs.readFileSync('static/css/nightowl.css', 'utf8'), /grid-template-columns:\s*repeat\(auto-fit/);
