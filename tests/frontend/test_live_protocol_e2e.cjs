// Actual Django HTTP/cache, synthetic operator/endpoint, no production origins.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {chromium} = require('playwright');
(async () => {
    const base = process.env.NIGHTOWL_E2E_SERVER;
    assert(/^http:\/\/(localhost|127\.0\.0\.1):\d+$/.test(base), 'Loopback-only E2E');
    const browser = await chromium.launch({headless:true,channel:'chrome'});
    try {
        const context = await browser.newContext();
        await context.addCookies([
            {name:'sessionid',value:process.env.NIGHTOWL_E2E_SESSION,url:base,httpOnly:true},
            {name:'csrftoken',value:process.env.NIGHTOWL_E2E_CSRF,url:base}
        ]);
        const page = await context.newPage();
        const css = fs.readFileSync(path.join(__dirname,'../../static/css/nightowl.css'),'utf8');
        await page.route(base + '/isolated-fixture', route => route.fulfill({contentType:'text/html',body:
            '<!doctype html><meta name="viewport" content="width=device-width"><style>'+css+'</style><body class="page-endpoint-detail"><main style="padding:16px"><h1>E2E isolado: pontos sinteticos via .NET e Django reais</h1><section data-endpoint-detail data-live-allowed="yes" data-endpoint-id="'+process.env.NIGHTOWL_E2E_ENDPOINT+'"></section></main></body>'}));
        await page.goto(base+'/isolated-fixture');
        await page.addScriptTag({path:path.join(__dirname,'../../static/js/endpoint_live.js')});
        await page.getByRole('button',{name:'Iniciar',exact:true}).click();
        await page.locator('[data-live-state]').filter({hasText:'Recebendo amostras'}).waitFor();
        assert(await page.locator('.endpoint-live svg path').count() >= 2);
        assert(!await page.locator('[data-live-body]').isHidden());
        await page.screenshot({path:path.join(process.env.TEMP,'nightowl-live-real-loopback.png'),fullPage:true});
        await page.getByRole('button',{name:'Encerrar',exact:true}).click();
        await page.locator('[data-live-body]').waitFor({state:'hidden'});
        console.log('PASS real loopback browser/live cache/CSRF/stop; all measurements synthetic.');
    } finally { await browser.close(); }
})().catch(() => { console.error('FAIL isolated browser gate (details withheld).'); process.exitCode=1; });
