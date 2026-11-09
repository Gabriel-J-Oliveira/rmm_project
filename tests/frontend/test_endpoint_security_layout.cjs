const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const root = path.resolve(__dirname, '..', '..');
const source = fs.readFileSync(path.join(root, 'static/js/endpoint_detail.js'), 'utf8');
const css = fs.readFileSync(path.join(root, 'static/css/nightowl.css'), 'utf8');
const start = source.indexOf('    function renderSecurity(detail) {');
const end = source.indexOf('    function renderPatches(detail) {', start);
assert(start >= 0 && end > start, 'Security renderer must exist');

const context = {
    asObject: value => value && typeof value === 'object' ? value : {},
    icon: () => '',
    severityBadge: () => '',
    formatDate: () => 'Synthetic date',
    factList: () => '',
    actionButton: () => '<button></button>',
    emptyState: label => `<div>${label}</div>`,
    escapeHtml: value => String(value),
    renderEvents: () => '<div class="security-events-fixture"></div>'
};
vm.createContext(context);
vm.runInContext(source.slice(start, end), context);

const html = context.renderSecurity({
    security: {antivirusProducts: [{name: 'Synthetic AV'}]},
    localAdmins: [],
    policyViolations: [{severity: 'warning', policy: 'Synthetic policy', item: 'Synthetic item'}],
    events: [{category: 'security', eventType: 'security.synthetic'}]
});
const order = [
    'Protecao local',
    'Ultimos eventos de seguranca',
    'Acesso remoto',
    'Administradores locais',
    'Politicas violadas',
    'Produtos AV detectados'
].map(label => html.indexOf(label));

assert(order.every(index => index >= 0), 'All security cards must remain rendered');
assert.deepEqual(order, [...order].sort((a, b) => a - b), 'Security cards should follow the requested two-column order');
assert(!html.includes('endpoint-span-2'), 'Security cards should occupy the regular two-column grid');
assert.match(html, /class="endpoint-compact-grid security-grid endpoint-security-layout"/);
assert.match(css, /\.endpoint-security-layout\s*>\s*\.panel\s*\{\s*align-self:\s*stretch;/);
console.log('PASS: security card order, two-column layout and equal-height row stretching');
