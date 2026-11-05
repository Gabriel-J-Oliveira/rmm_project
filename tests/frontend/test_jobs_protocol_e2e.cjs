const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {chromium} = require('playwright');

let phase = 'initialization';
(async () => {
    const server = process.env.NIGHTOWL_E2E_SERVER;
    assert(/^http:\/\/(localhost|127\.0\.0\.1):\d+$/.test(server), 'Only isolated loopback is permitted');
    const browser = await chromium.launch({headless:true,channel:'chrome'});
    phase = 'browser_launched';
    try {
        const context = await browser.newContext({viewport:{width:1920,height:1080}});
        await context.addCookies([{name:'sessionid',value:process.env.NIGHTOWL_E2E_SESSION,url:server}]);
        const page = await context.newPage();
        const root = path.resolve(__dirname,'../..');
        await page.route('**/*',async route => {
            const url = new URL(route.request().url());
            if (url.origin !== server) return route.abort();
            if (!url.pathname.startsWith('/static/')) return route.continue();
            const file = path.resolve(root,'static',decodeURIComponent(url.pathname.slice(8)));
            assert(file.startsWith(path.join(root,'static')+path.sep));
            if (!fs.existsSync(file)) return route.fulfill({status:404,body:''});
            return route.fulfill({body:fs.readFileSync(file),contentType:file.endsWith('.js')?'application/javascript':file.endsWith('.css')?'text/css':'application/octet-stream'});
        });
        await page.goto(server+'/jobs/?tab=jobs');
        phase = 'page_loaded';
        await page.waitForFunction(() => document.querySelectorAll('[data-jobs-table-body] [data-job-id]').length === 10);
        phase = 'rows_received';
        assert((await page.locator('[data-jobs-table-body]').innerText()).includes('SYNTHETIC-JOBS-E2E'));
        assert(!(await page.locator('[data-jobs-table-body]').innerText()).includes('mock'));
        assert(await page.locator('[data-task-drawer]').isHidden(), 'Closed preview drawer must stay hidden');
        await page.locator('[data-job-action="details"]').first().click();
        await page.screenshot({path:path.join(process.env.TEMP,'nightowl-jobs-real-diagnostic.png'),fullPage:true});
        phase = 'assert_operator';
        assert((await page.locator('[data-job-field="createdBy"]').innerText()).includes('synthetic-operator'));
        phase = 'assert_history';
        assert((await page.locator('[data-job-timeline]').innerText()).includes('job.started'));
        phase = 'assert_status';
        assert((await page.locator('[data-job-field="status"]').textContent()).includes('Concluida'));
        await page.screenshot({path:path.join(process.env.TEMP,'nightowl-jobs-real-1920.png'),fullPage:true});
        await page.setViewportSize({width:390,height:844});
        await page.waitForTimeout(250);
        assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), 'Jobs page must not overflow the mobile viewport');
        const drawer = await page.locator('[data-job-drawer]').boundingBox();
        assert(drawer && drawer.x >= 0 && drawer.x + drawer.width <= 391, 'Open drawer must fit the viewport');
        assert(await page.locator('[data-task-drawer]').isHidden());
        await page.screenshot({path:path.join(process.env.TEMP,'nightowl-jobs-real-390.png'),fullPage:true});
        console.log('PASS persisted jobs DOM, actual Django API, audit timeline and responsive screenshots; no synthetic API interception.');
    } finally {await browser.close();}
})().catch(error => {console.log('JOB_BROWSER_FAILURE='+JSON.stringify({phase,error_type:error.name}));process.exitCode=1;});
