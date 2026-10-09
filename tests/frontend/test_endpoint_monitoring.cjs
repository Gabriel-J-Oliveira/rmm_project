const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { render } = require('../../static/js/endpoint_monitoring.js');

const enabled = { telemetryEnabled: true, telemetrySampleSeconds: 300, telemetryFlushSeconds: 900 };
test('unknown is not disabled; incompatible agents cannot submit', () => {
    const html = render({ compatible: false, minimum_version: '0.1.1.0-rc46' });
    assert.match(html, /Nao reportado/);
    assert.match(html, /fieldset disabled/);
    assert.doesNotMatch(html, /Telemetria reportada<\/dt><dd>Desabilitada/);
});
test('requested and effective differ; success is not a sample receipt', () => {
    const html = render({ compatible: true, effective: { ...enabled, telemetryEnabled: false },
        requested: enabled, job_status: 'completed', samples_after_application: false });
    assert.match(html, /Desabilitada/);
    assert.match(html, /Habilitada \/ 300s \/ 900s/);
    assert.match(html, /Concluido/);
    assert.match(html, /Ainda nao confirmadas/);
});
test('pending jobs disable duplicate requests; safe output escapes all remote values', () => {
    const html = render({ compatible: true, effective: enabled, job_status: 'running',
        reported_at: '<img src=x onerror=alert(1)>', samples_after_application: true });
    assert.match(html, /fieldset disabled/);
    assert.match(html, /Aplicando/);
    assert.match(html, /&lt;img/);
    assert.doesNotMatch(html, /<img/);
    assert.match(html, /Recebidas/);
});
test('frontend submits only bounded telemetry fields over existing CSRF job path', () => {
    const source = fs.readFileSync(path.join(__dirname, '../../static/js/endpoint_detail.js'), 'utf8');
    assert.match(source, /createRealJob\("configure_telemetry", \{ configuration: configuration \}\)/);
    assert.match(source, /form\.reportValidity\(\)/);
    assert.match(source, /body\.set\("configuration", JSON\.stringify\(options\.configuration\)\)/);
    assert.match(source, /"X-CSRFToken": getCookie\("csrftoken"\)/);
});
test('monitoring renders at desktop/mobile without overflow or executable remote text', async () => {
    const { chromium } = require('playwright');
    const browser = await chromium.launch({ channel: process.env.PLAYWRIGHT_CHANNEL || 'chrome', headless: true });
    try {
        const page = await browser.newPage();
        for (const viewport of [{ width: 1920, height: 1080 }, { width: 390, height: 844 }]) {
            await page.setViewportSize(viewport);
            await page.setContent('<style>body{background:#12151b;color:white;font:16px Arial;margin:24px}input{box-sizing:border-box}dl{display:grid;gap:12px}dt{font-size:14px;color:#b8c2ce}dd{margin:0;overflow-wrap:anywhere}fieldset{min-width:0}button{padding:8px}</style>'
                + render({ compatible: true, effective: enabled, requested: enabled, job_status: 'completed',
                    reported_at: '2026-10-09T15:00:00Z', last_received_at: '<script>alert(1)</script>' }));
            assert.equal(await page.locator('[data-telemetry-form]').count(), 1);
            assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
            assert.equal(await page.locator('script').count(), 0);
            await page.screenshot({ path: path.join(process.env.TEMP || '/tmp', 'nightowl-monitoring-' + viewport.width + '.png'), fullPage: true });
        }
    } finally { await browser.close(); }
});
