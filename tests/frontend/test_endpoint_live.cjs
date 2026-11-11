const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {chart, escape, errorMessage} = require('../../static/js/endpoint_live.js');
const {render: renderMonitoring} = require('../../static/js/endpoint_monitoring.js');
assert.match(errorMessage('live_cache_unavailable'), /Cache do monitoramento indisponível/);
assert.match(errorMessage('session_owned_by_another_operator'), /Outro operador/);
assert.doesNotMatch(errorMessage('<script>secret</script>'), /<script>|secret/);
const now = Date.now();
const points = Array.from({length:12}, (_, i) => ({sample_id:'synthetic-' + i,
    collected_at:new Date(now - (11-i)*10000).toISOString(),
        metrics:{'cpu.usage_percent':i < 6 ? 95 : 20, 'memory.used_percent':70, 'memory.available_percent':30,
        'memory.available_bytes':8*1073741824, 'memory.used_bytes':16*1073741824,
        'network.nic.received_bytes_per_sec':1000+i*100, 'network.nic.sent_bytes_per_sec':500,
        'network.eth1.received_bytes_per_sec':500+i*25, 'network.eth1.sent_bytes_per_sec':250,
        'disk.os.read_latency_ms':5+i, 'disk.os.write_latency_ms':4,
        'disk.data.read_latency_ms':2+i, 'disk.data.write_latency_ms':1,
        'disk.os.read_bytes_per_sec':100000, 'disk.os.write_bytes_per_sec':20000,
        'disk.os.queue_length':.4},
    quality:{interfaces:{nic:{name:'<img src=x onerror=alert(1)>'},eth1:{name:'Ethernet · Adaptador virtual com nome excepcionalmente longo para validar o corte responsivo'}}, disk_instances:{os:{instance:'Synthetic Windows disk'},data:{instance:'Disco secundário · volume de dados com identificação extensa'}}}}));
assert(!escape('<script>').includes('<script>'));
assert(chart(points, ['cpu.usage_percent'], 100).includes('<path'));
assert(!chart([{metrics:{},collected_at:new Date().toISOString()}], ['cpu.usage_percent'], 100).includes('NaN'));
const legend = chart(points, ['cpu.usage_percent'], 100, ['<script>alert(1)</script>']);
assert(legend.includes('&lt;script&gt;') && !legend.includes('<script>'));
assert(legend.includes('100') && legend.includes('mín') && legend.includes('máx'));
assert(legend.includes('data-live-tip') && legend.includes('tabindex="0"'));
assert(chart(points, ['network.nic.received_bytes_per_sec'], null).includes('KiB/s'));
assert(chart([{collected_at:new Date().toISOString(),metrics:{'network.nic.received_bytes_per_sec':1048576}}], ['network.nic.received_bytes_per_sec'], null).includes('MiB/s'));
assert(chart(points, ['disk.os.read_latency_ms'], null).includes('ms'));
assert(chart([{collected_at:new Date().toISOString(),metrics:{}}], ['cpu.usage_percent'], 100).includes('Sem medição'));
const missing = points.map(p => ({...p, metrics:{...p.metrics}}));
delete missing[5].metrics['cpu.usage_percent'];
assert(chart(missing, ['cpu.usage_percent'],100).split(' M').length >= 3, 'Missing evidence must break the chart line');
console.log('PASS live chart units, escaped labels, missing data gaps, numeric-only SVG paths.');

if (process.argv.includes('--visual')) (async () => {
    const {chromium} = require('playwright');
    const browser = await chromium.launch({
        headless:true,
        ...(process.env.NIGHTOWL_TEST_CHROME
            ? {executablePath:process.env.NIGHTOWL_TEST_CHROME}
            : {channel:'chrome'})
    });
    try {
        const page = await browser.newPage();
        let active = false, expired = false;
        const css = [
            'static/css/nightowl.css',
            'static/css/endpoint_actions_jobs.css',
            'static/css/endpoint_detail_refinement.css'
        ].map(file => fs.readFileSync(path.join(__dirname, '../../', file), 'utf8')).join('\n');
        const monitoring = renderMonitoring({compatible:true,last_metrics:{},last_sample_at:'2026-11-11T12:00:00Z'});
        await page.route('http://isolated.invalid/**', async route => {
            const request = route.request();
            if (request.url().includes('/api/')) {
                if (request.method() === 'POST') {
                    const body = request.postDataJSON();
                    active = body.action !== 'stop';
                    return route.fulfill({contentType:'application/json', body:JSON.stringify({state:active?'active':'expired', session_id:'synthetic-live', expires_at:new Date(Date.now()+45000).toISOString()})});
                }
                return route.fulfill({contentType:'application/json', body:JSON.stringify({state:expired?'expired':'active', session_id:'synthetic-live', points, last_sample_at:points.at(-1).collected_at, stale:false})});
            }
            return route.fulfill({contentType:'text/html', body:'<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width"><style>'+css+'</style><body class="page-endpoint-detail"><main class="endpoint-detail-shell" style="padding:16px"><h1>Fixture sintetica: monitoramento ao vivo</h1><section data-endpoint-detail data-live-allowed="yes" data-endpoint-id="synthetic"><section class="endpoint-tab-panel" data-endpoint-tab-panel="monitoring">'+monitoring+'</section><section class="endpoint-tab-panel" data-endpoint-tab-panel="inventory"><article class="panel endpoint-dense-card">Card informativo sintético</article><button class="copy-btn">Controle interativo</button></section></section></main></body>'});
        });
        await page.goto('http://isolated.invalid/');
        await page.addScriptTag({path:path.join(__dirname,'../../static/js/endpoint_live.js')});
        await page.getByRole('button', {name:'Iniciar'}).click();
        await page.locator('[data-live-body]:not([hidden])').waitFor();
        try {
            await page.waitForFunction(() => document.querySelectorAll('.endpoint-live svg path').length >= 6, null, {timeout:5000});
        } catch (error) {
            const diagnostic = await page.evaluate(() => ({
                status:document.querySelector('[data-live-state]')?.textContent,
                visible:!document.querySelector('[data-live-body]')?.hidden,
                paths:document.querySelectorAll('.endpoint-live svg path').length,
                selectors:document.querySelectorAll('.endpoint-live select').length,
                livePanel:!!document.querySelector('.endpoint-live')
            }));
            throw new Error(`Synthetic live chart did not render: ${JSON.stringify(diagnostic)}; ${error.message}`);
        }
        assert.equal(await page.locator('.endpoint-live img').count(), 0);
        assert.equal(await page.locator('[data-live-interface] option').count(), 2);
        assert.equal(await page.locator('[data-live-disk] option').count(), 2);
        const selectStyles = await page.locator('[data-live-interface]').evaluate(select => {
            const style = getComputedStyle(select);
            return {width:style.width,minHeight:style.minHeight,borderColor:style.borderTopColor,
                background:style.backgroundColor,color:style.color,fontSize:style.fontSize};
        });
        assert.equal(selectStyles.width, await page.locator('[data-live-interface]').evaluate(select => select.parentElement.getBoundingClientRect().width + 'px'));
        assert.equal(selectStyles.minHeight, '40px');
        assert.notEqual(selectStyles.background, 'rgba(0, 0, 0, 0)');
        assert.notEqual(selectStyles.borderColor, 'rgba(0, 0, 0, 0)');
        await page.keyboard.press('Tab');
        await page.keyboard.press('Tab');
        const focusStyle = await page.locator('[data-live-interface]').evaluate(select => ({
            focused:document.activeElement === select,
            outline:getComputedStyle(select).outlineStyle,
            outlineWidth:getComputedStyle(select).outlineWidth
        }));
        assert.equal(focusStyle.focused, true, 'interface selector is reachable by keyboard');
        assert.equal(focusStyle.outline, 'solid');
        await page.emulateMedia({colorScheme:'light'});
        assert.equal(await page.locator('[data-live-interface]').evaluate(select => getComputedStyle(select).backgroundColor), selectStyles.background,
            'browser light preference does not invert the application dark theme controls');
        await page.emulateMedia({colorScheme:'dark'});
        await page.locator('[data-live-interface]').selectOption('eth1');
        await page.locator('[data-live-disk]').selectOption('data');
        await page.waitForTimeout(80);
        assert.equal(await page.locator('[data-live-interface]').inputValue(), 'eth1');
        assert.equal(await page.locator('[data-live-disk]').inputValue(), 'data');
        const monitoringCard = page.locator('.endpoint-monitoring-summary');
        const cardBeforeHover = await monitoringCard.evaluate(card => {
            const style=getComputedStyle(card); return {background:style.backgroundColor,border:style.borderColor,
                shadow:style.boxShadow,transform:style.transform};
        });
        await monitoringCard.hover();
        assert.deepEqual(await monitoringCard.evaluate(card => {
            const style=getComputedStyle(card); return {background:style.backgroundColor,border:style.borderColor,
                shadow:style.boxShadow,transform:style.transform};
        }), cardBeforeHover, 'informational monitoring cards do not react to hover');
        const inventoryCard = page.locator('[data-endpoint-tab-panel="inventory"] .panel');
        const inventoryBeforeHover = await inventoryCard.evaluate(card => {
            const style=getComputedStyle(card); return {background:style.backgroundColor,border:style.borderColor,
                shadow:style.boxShadow,transform:style.transform};
        });
        await inventoryCard.hover();
        assert.deepEqual(await inventoryCard.evaluate(card => {
            const style=getComputedStyle(card); return {background:style.backgroundColor,border:style.borderColor,
                shadow:style.boxShadow,transform:style.transform};
        }), inventoryBeforeHover, 'informational inventory cards do not react to hover');
        const control = page.locator('[data-endpoint-tab-panel="inventory"] button');
        const controlBackground = await control.evaluate(button => getComputedStyle(button).backgroundColor);
        await control.hover();
        assert.notEqual(await control.evaluate(button => getComputedStyle(button).backgroundColor), controlBackground,
            'interactive controls retain their own hover feedback');
        await page.locator('[data-live-interface]').evaluate(select => { select.disabled = true; });
        assert.equal(await page.locator('[data-live-interface]').evaluate(select => getComputedStyle(select).cursor), 'not-allowed');
        await page.locator('[data-live-interface]').evaluate(select => { select.disabled = false; });
        for (const [width,height] of [[1920,1080],[1366,900],[1024,768],[390,844]]) {
            await page.setViewportSize({width,height});
            assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth+1));
            assert(await page.locator('.endpoint-live-selectors').evaluate(el => el.getBoundingClientRect().width > 0));
            await page.screenshot({path:path.join(process.env.TEMP,`nightowl-live-${width}.png`), fullPage:true});
        }
        await page.getByRole('button',{name:'Encerrar',exact:true}).click();
        await page.getByText(/Análise encerrada/).waitFor();
        assert(await page.locator('.endpoint-live svg path').count() >= 6);
        await page.getByRole('button',{name:'Limpar',exact:true}).click();
        await page.locator('[data-live-body]').waitFor({state:'hidden'});
        expired = true;
        await page.getByRole('button',{name:'Iniciar',exact:true}).click();
        await page.getByText(/Sessão expirada/).waitFor();
        console.log('PASS synthetic browser: authorized start, four charts, safe labels, selectors, stop/expiry, desktop/mobile screenshots.');
    } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
