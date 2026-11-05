const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {chart, escape} = require('../../static/js/endpoint_live.js');
const now = Date.now();
const points = Array.from({length:12}, (_, i) => ({sample_id:'synthetic-' + i,
    collected_at:new Date(now - (11-i)*10000).toISOString(),
    metrics:{'cpu.usage_percent':i < 6 ? 95 : 20, 'memory.used_percent':70, 'memory.available_percent':30,
        'memory.available_bytes':8*1073741824, 'memory.used_bytes':16*1073741824,
        'network.nic.received_bytes_per_sec':1000+i*100, 'network.nic.sent_bytes_per_sec':500,
        'disk.os.read_latency_ms':5+i, 'disk.os.write_latency_ms':4,
        'disk.os.read_bytes_per_sec':100000, 'disk.os.write_bytes_per_sec':20000,
        'disk.os.queue_length':.4},
    quality:{interfaces:{nic:{name:'<img src=x onerror=alert(1)>'}}, disk_instances:{os:{instance:'Synthetic Windows disk'}}}}));
assert(!escape('<script>').includes('<script>'));
assert(chart(points, ['cpu.usage_percent'], 100).includes('<path'));
assert(!chart([{metrics:{},collected_at:new Date().toISOString()}], ['cpu.usage_percent'], 100).includes('NaN'));
const legend = chart(points, ['cpu.usage_percent'], 100, ['<script>alert(1)</script>']);
assert(legend.includes('&lt;script&gt;') && !legend.includes('<script>'));
assert(legend.includes('Escala: 0 a 100.00'));
const missing = points.map(p => ({...p, metrics:{...p.metrics}}));
delete missing[5].metrics['cpu.usage_percent'];
assert(chart(missing, ['cpu.usage_percent'],100).split(' M').length >= 3, 'Missing evidence must break the chart line');
console.log('PASS live chart units, escaped labels, missing data gaps, numeric-only SVG paths.');

if (process.argv.includes('--visual')) (async () => {
    const {chromium} = require('playwright');
    const browser = await chromium.launch({headless:true, channel:'chrome'});
    try {
        const page = await browser.newPage();
        let active = false, expired = false;
        const css = fs.readFileSync(path.join(__dirname,'../../static/css/nightowl.css'),'utf8');
        await page.route('http://isolated.invalid/**', async route => {
            const request = route.request();
            if (request.url().includes('/api/')) {
                if (request.method() === 'POST') {
                    const body = request.postDataJSON();
                    active = body.action !== 'stop';
                    return route.fulfill({contentType:'application/json', body:JSON.stringify({state:active?'active':'expired', session_id:'synthetic-live', expires_at:new Date(Date.now()+45000).toISOString()})});
                }
                return route.fulfill({contentType:'application/json', body:JSON.stringify({state:expired?'expired':'active', points, last_sample_at:points.at(-1).collected_at, stale:false})});
            }
            return route.fulfill({contentType:'text/html', body:'<!doctype html><meta name="viewport" content="width=device-width"><style>'+css+'</style><body class="page-endpoint-detail"><main style="padding:16px"><h1>Fixture sintetica: monitoramento ao vivo</h1><section data-endpoint-detail data-live-allowed="yes" data-endpoint-id="synthetic"></section></main></body>'});
        });
        await page.goto('http://isolated.invalid/');
        await page.addScriptTag({path:path.join(__dirname,'../../static/js/endpoint_live.js')});
        await page.getByRole('button', {name:'Iniciar'}).click();
        await page.locator('[data-live-body]:not([hidden])').waitFor();
        assert(await page.locator('.endpoint-live svg path').count() >= 6);
        assert.equal(await page.locator('.endpoint-live img').count(), 0);
        for (const [width,height] of [[1920,1080],[390,844]]) {
            await page.setViewportSize({width,height});
            assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth+1));
            await page.screenshot({path:path.join(process.env.TEMP,`nightowl-live-${width}.png`), fullPage:true});
        }
        await page.getByRole('button',{name:'Encerrar',exact:true}).click();
        await page.locator('[data-live-body]').waitFor({state:'hidden'});
        expired = true;
        await page.getByRole('button',{name:'Iniciar',exact:true}).click();
        await page.getByText('Sessao expirada',{exact:true}).waitFor();
        console.log('PASS synthetic browser: authorized start, four charts, safe labels, selectors, stop/expiry, desktop/mobile screenshots.');
    } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
