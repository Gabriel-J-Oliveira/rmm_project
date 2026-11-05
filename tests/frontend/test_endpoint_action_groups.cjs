const fs = require('node:fs'), vm = require('node:vm'), assert = require('node:assert/strict');
const source = fs.readFileSync('static/js/endpoint_detail.js', 'utf8');
const start = source.indexOf('    function renderTasks(detail) {');
const end = source.indexOf('    function renderDiagnostics(detail)', start);
assert(start >= 0 && end > start);
const ctx = {agentLifecycleActionButtons: () => ['UNINSTALL'], purgeActionButtons: () => ['PURGE'],
    agentManagementActionButtons: () => ['UPDATE'], icon: () => '', actionButton: action => '[' + action + ']',
    operationalLabels: {flush_dns: 'DNS', query_service: 'Serviço'}, asObject: x => x || {},
    escapeHtml: x => String(x), renderJobs: () => 'HISTORY'};
vm.runInNewContext(source.slice(start, end) + ';this.render = renderTasks;', ctx);
const html = ctx.render({operationalActions: {flush_dns: {enabled: true}, query_service: {enabled: false}}});
for (const group of ['Diagnósticos', 'Ações operacionais', 'Manutenção do agente', 'Ações destrutivas']) assert(html.includes(group));
assert(html.includes('data-approved-scripts-placement'));
assert(html.indexOf('UNINSTALL') > html.indexOf('Ações destrutivas'));
assert(html.indexOf('UPDATE') > html.indexOf('Manutenção do agente'));
assert.match(html, /data-endpoint-action="query_service" disabled/);
assert.match(fs.readFileSync('static/css/nightowl.css', 'utf8'), /grid-template-columns:\s*repeat\(auto-fit/);
