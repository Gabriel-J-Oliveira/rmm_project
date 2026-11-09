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
test('summary distinguishes real zero values from missing measurements', () => {
    const html = render({last_metrics: {cpu_percent: 0, memory_used_percent: 55, disk_read_bytes_per_sec: 0}, last_sample_at: '2026-11-05T12:00:00Z'});
    assert.match(html, /0\.0 %/);
    assert.match(html, /55\.0 %/);
    assert.match(html, /0\.0 B\/s/);
    assert.match(html, /Nao informado/);
    assert.match(html, /2026-11-05T12:00:00Z/);
});
test('reported legacy mode explains why live samples are unavailable', () => {
    const html = render({reported_mode: 'legacy', mode_reported_at: '2026-11-05T19:00:00Z'});
    assert.match(html, /modo legacy reportado não envia amostras/);
    assert.match(html, /2026-11-05T19:00:00Z/);
    assert.doesNotMatch(render({reported_mode: 'continuous'}), /modo legacy reportado/);
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
test('monitoring retains the existing live panel during refresh and keeps it inside its tab', () => {
    const detail = fs.readFileSync(path.join(__dirname, '../../static/js/endpoint_detail.js'), 'utf8');
    const live = fs.readFileSync(path.join(__dirname, '../../static/js/endpoint_live.js'), 'utf8');
    const html = render({ compatible: true, effective: enabled });
    assert.match(html, /data-live-placement/);
    assert.doesNotMatch(html, /max-width:800px|style=/);
    assert.match(detail, /if \(live\) live\.remove\(\)/);
    assert.match(detail, /querySelector\('\[data-live-placement\]'\)\.appendChild\(live\)/);
    assert.match(live, /data-endpoint-tab-panel="monitoring"/);
    assert.match(live, /new MutationObserver/);
    assert.match(live, /observer\.disconnect\(\)/);
    assert.doesNotMatch(live, /root\.appendChild\(panel\)/);
    assert.match(live, /Aguardando dados do agente/);
});
test('monitoring renders at desktop/mobile without overflow or executable remote text', async () => {
    const { chromium } = require('playwright');
    const browser = await chromium.launch({ channel: process.env.PLAYWRIGHT_CHANNEL || 'chrome', headless: true });
    try {
        const page = await browser.newPage();
        const css = fs.readFileSync(path.join(__dirname, '../../static/css/nightowl.css'), 'utf8');
        const states = [
            { name: 'inactive', text: 'Sessao inativa', hidden: true },
            { name: 'active', text: 'Recebendo amostras · Última amostra: agora', hidden: false },
            { name: 'ended', text: 'Analise encerrada · agora', hidden: false }
        ];
        for (const viewport of [{ width: 1920, height: 1080 }, { width: 1366, height: 900 }, { width: 1024, height: 768 }, { width: 390, height: 844 }]) {
            await page.setViewportSize(viewport);
            for (const state of states) {
                await page.setContent('<style>' + css + 'body{margin:24px;background:var(--night-black);color:var(--text-primary)}</style><body class="page-endpoint-detail"><main>'
                    + render({ compatible: true, effective: enabled, requested: enabled, job_status: 'completed',
                        reported_at: '2026-10-09T15:00:00Z', last_received_at: '<script>alert(1)</script>' })
                    + '</main></body>');
                const live = page.locator('[data-live-placement]');
                await live.evaluate((node, current) => {
                    node.innerHTML = '<section class="endpoint-live panel endpoint-dense-card"><header><h2>Monitoramento ao vivo</h2><div class="live-actions"><button>Encerrar</button></div></header><p data-live-state></p><div class="endpoint-live-grid"><div><h3>CPU (%)</h3><p>Sem medição disponível</p></div><div><h3>RAM (%)</h3><p>Sem medição disponível</p></div><div><h3>Rede (bytes/s)</h3><p>Sem medição disponível</p></div><div><h3>Disco (ms)</h3><p>Sem medição disponível</p></div></div></section>';
                    const panel = node.firstElementChild;
                    panel.querySelector('[data-live-state]').textContent = current.text;
                    panel.querySelector('.live-actions button').hidden = current.hidden;
                    if (current.hidden) panel.querySelector('.endpoint-live-grid').hidden = true;
                }, state);
                assert.equal(await page.locator('[data-telemetry-form]').count(), 1);
                assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
                assert.equal(await page.locator('script').count(), 0);
                const layout = await page.evaluate(() => {
                    const cards = [...document.querySelectorAll('.endpoint-monitoring-top-grid > .panel')].map(card => card.getBoundingClientRect());
                    return { columns: getComputedStyle(document.querySelector('.endpoint-monitoring-top-grid')).gridTemplateColumns.trim().split(/\s+/).length,
                        summaryAlign: getComputedStyle(document.querySelector('.endpoint-monitoring-summary')).alignSelf,
                        configAlign: getComputedStyle(document.querySelector('.endpoint-telemetry-config')).alignSelf,
                        summary: cards[0], config: cards[1], live: document.querySelector('.endpoint-live').getBoundingClientRect(),
                        fieldsetColumns: getComputedStyle(document.querySelector('[data-telemetry-form] fieldset')).gridTemplateColumns,
                        inputs: [...document.querySelectorAll('[data-telemetry-form] input[type="number"]')].map(input => input.getBoundingClientRect()),
                        applyButton: document.querySelector('[data-telemetry-form] button').getBoundingClientRect(),
                        width: document.documentElement.scrollWidth };
                });
                if (viewport.width > 1100) {
                    assert.equal(layout.columns, 2, 'summary/config use two columns at desktop');
                    assert(layout.summary.x < layout.config.x, 'summary stays left of config');
                    assert(Math.abs(layout.summary.height - layout.config.height) <= 1, `telemetry configuration fills the summary card height (${layout.summary.height} vs ${layout.config.height}; align ${layout.summaryAlign}/${layout.configAlign})`);
                    assert.equal(layout.inputs.length, 2, 'both legacy interval controls remain available');
                    assert(Math.abs(layout.inputs[0].x - layout.inputs[1].x) <= 1, 'legacy interval inputs are vertically aligned in one column');
                    assert(layout.inputs[0].width > 280, 'inputs use the available card width');
                    assert(layout.applyButton.y > layout.inputs[1].bottom, 'apply action follows both inputs');
                } else {
                    assert.equal(layout.columns, 1, 'summary/config stack on notebook/mobile');
                    assert(layout.config.y > layout.summary.y, 'configuration follows summary');
                }
                assert(layout.live.width > 0, 'live panel remains mounted for ' + state.name);
                await page.screenshot({ path: path.join(process.env.TEMP || '/tmp', 'nightowl-monitoring-' + viewport.width + '-' + state.name + '.png'), fullPage: true });
            }
        }
    } finally { await browser.close(); }
});
