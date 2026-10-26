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
