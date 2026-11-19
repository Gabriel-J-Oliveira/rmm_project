const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {chromium} = require('playwright');

let phase = 'initialization';
(async () => {
    const server = process.env.NIGHTOWL_E2E_SERVER;
    assert(/^http:\/\/(localhost|127\.0\.0\.1):\d+$/.test(server));
    const chromePath = process.env.NIGHTOWL_TEST_CHROME;
    assert(chromePath && path.isAbsolute(chromePath) && fs.existsSync(chromePath));
    const endpointId = process.env.NIGHTOWL_E2E_ENDPOINT;
    assert(endpointId);
    const browser = await chromium.launch({headless: true, executablePath: chromePath});
    try {
        const context = await browser.newContext({viewport: {width: 1440, height: 1000}});
        await context.addCookies([{name: 'sessionid', value: process.env.NIGHTOWL_E2E_SESSION, url: server}]);
        const page = await context.newPage();
        const errors = [];
        page.on('pageerror', error => errors.push(error.name));
        const root = path.resolve(__dirname, '../..');
        await page.route('**/*', async route => {
            const url = new URL(route.request().url());
            if (url.origin !== server) return route.abort();
            if (!url.pathname.startsWith('/static/')) return route.fallback();
            const staticRoot = path.join(root, 'static');
            const file = path.resolve(staticRoot, decodeURIComponent(url.pathname.slice('/static/'.length)));
            assert(file.startsWith(staticRoot + path.sep), 'Static path escaped the source tree');
            if (!fs.existsSync(file)) return route.fulfill({status: 404, body: ''});
            const type = file.endsWith('.js') ? 'application/javascript' : file.endsWith('.css') ? 'text/css' : 'application/octet-stream';
            return route.fulfill({body: fs.readFileSync(file), contentType: type});
        });

        phase = 'endpoint_page';
        await page.goto(server + '/endpoints/' + encodeURIComponent(endpointId) + '/');
        const terminal = page.locator('[data-admin-auth]');
        assert.equal(await terminal.getAttribute('data-endpoint-uuid'), endpointId);
        await page.locator('[data-terminal-open]').click();
        const credentials = page.locator('[data-auth-modal]');
        await credentials.waitFor({state: 'visible'});
        await page.locator('[name="account"]').fill('gabriel.oliveira');
        assert.equal(await page.locator('[data-auth-principal]').innerText(), 'Será autenticado como: CONTROL\\gabriel.oliveira');

        phase = 'desktop_credentials';
        const artifacts = path.join(process.env.NIGHTOWL_E2E_ARTIFACT_DIR || require('node:os').tmpdir(), 'nightowl-terminal-admin');
        fs.mkdirSync(artifacts, {recursive: true});
        await page.screenshot({path: path.join(artifacts, 'terminal-admin-credentials-desktop.png'), fullPage: true});

        phase = 'mobile_credentials';
        await page.setViewportSize({width: 390, height: 844});
        await page.waitForTimeout(100);
        const credentialMobile = await credentials.evaluate(node => {
            const box = node.getBoundingClientRect();
            return {left: box.left, right: box.right, viewport: innerWidth, scrollWidth: node.scrollWidth, clientWidth: node.clientWidth};
        });
        assert(credentialMobile.left >= 0 && credentialMobile.right <= credentialMobile.viewport &&
            credentialMobile.scrollWidth <= credentialMobile.clientWidth + 1,
            'Mobile credential modal overflow: ' + JSON.stringify(credentialMobile));
        await page.screenshot({path: path.join(artifacts, 'terminal-admin-credentials-mobile.png'), fullPage: true});

        phase = 'desktop_workspace';
        await page.setViewportSize({width: 1440, height: 1000});
        await page.evaluate(() => {
            document.querySelector('[data-terminal-modal]').hidden = false;
            document.querySelector('[data-screen]').hidden = false;
        });
        const desktop = await page.locator('[data-terminal-dialog]').evaluate(node => {
            const box = node.getBoundingClientRect();
            return {left: box.left, right: box.right, top: box.top, bottom: box.bottom,
                width: box.width, height: box.height, viewportWidth: innerWidth, viewportHeight: innerHeight,
                outerBorder: getComputedStyle(node).borderTopColor,
                innerBorder: getComputedStyle(node.querySelector('[data-screen]')).borderTopColor};
        });
        assert(desktop.left >= 0 && desktop.right <= desktop.viewportWidth && desktop.top >= 0 && desktop.bottom <= desktop.viewportHeight,
            'Desktop terminal popup must remain inside viewport: ' + JSON.stringify(desktop));
        assert.equal(desktop.outerBorder, 'rgb(34, 197, 94)');
        assert.equal(desktop.innerBorder, 'rgb(139, 92, 246)');
        await page.screenshot({path: path.join(artifacts, 'terminal-admin-workspace-desktop.png'), fullPage: true});

        phase = 'mobile_workspace';
        await page.setViewportSize({width: 390, height: 844});
        await page.waitForTimeout(100);
        const mobile = await page.locator('[data-terminal-dialog]').evaluate(node => {
            const box = node.getBoundingClientRect();
            return {left: box.left, right: box.right, top: box.top, bottom: box.bottom,
                viewportWidth: innerWidth, viewportHeight: innerHeight, scrollWidth: node.scrollWidth, clientWidth: node.clientWidth};
        });
        assert(mobile.left >= 0 && mobile.right <= mobile.viewportWidth && mobile.top >= 0 && mobile.bottom <= mobile.viewportHeight &&
            mobile.scrollWidth <= mobile.clientWidth + 1, 'Mobile terminal popup overflow: ' + JSON.stringify(mobile));
        await page.screenshot({path: path.join(artifacts, 'terminal-admin-workspace-mobile.png'), fullPage: true});
        assert.deepEqual(errors, []);
        console.log('PASS terminal admin visual: synthetic authenticated endpoint, default domain preview, credential modal and responsive popup. Screenshots: ' + artifacts);
        await context.close();
    } finally { await browser.close(); }
})().catch(error => {
    console.log('TERMINAL_ADMIN_VISUAL_FAILURE=' + JSON.stringify({phase, error_type: error.name,
        message: String(error.message).slice(0, 240)}));
    process.exitCode = 1;
});
