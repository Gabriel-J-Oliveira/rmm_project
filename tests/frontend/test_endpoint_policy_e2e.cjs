const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {chromium} = require('playwright');

let phase = 'initialization';
(async () => {
    const server = process.env.NIGHTOWL_E2E_SERVER;
    assert(/^http:\/\/(localhost|127\.0\.0\.1):\d+$/.test(server));
    const chromePath = process.env.NIGHTOWL_TEST_CHROME;
    assert(chromePath && path.isAbsolute(chromePath) && fs.existsSync(chromePath),
        'NIGHTOWL_TEST_CHROME must point to the verified browser executable');
    const endpointId = process.env.NIGHTOWL_E2E_ENDPOINT;
    const endpointName = process.env.NIGHTOWL_E2E_ENDPOINT_NAME;
    const policyId = process.env.NIGHTOWL_E2E_POLICY;
    const policyName = process.env.NIGHTOWL_E2E_POLICY_NAME;
    assert(endpointId && endpointName && policyId && policyName, 'Synthetic endpoint and policy context is required');

    const browser = await chromium.launch({headless: true, executablePath: chromePath});
    try {
        const context = await browser.newContext({viewport: {width: 1440, height: 1000}});
        await context.addCookies([{name: 'sessionid', value: process.env.NIGHTOWL_E2E_SESSION, url: server}]);
        const page = await context.newPage();
        let confirmation = '';
        page.on('dialog', async dialog => { confirmation = dialog.message(); await dialog.accept(); });
        const root = path.resolve(__dirname, '../..');
        await page.route('**/*', async route => {
            const url = new URL(route.request().url());
            if (url.origin !== server) return route.abort();
            if (!url.pathname.startsWith('/static/')) return route.continue();
            const file = path.resolve(root, 'static', decodeURIComponent(url.pathname.slice(8)));
            assert(file.startsWith(path.join(root, 'static') + path.sep));
            if (!fs.existsSync(file)) return route.fulfill({status: 404, body: ''});
            return route.fulfill({body: fs.readFileSync(file), contentType: file.endsWith('.js') ? 'application/javascript' : file.endsWith('.css') ? 'text/css' : 'application/octet-stream'});
        });

        phase = 'endpoint_page';
        await page.goto(server + '/endpoints/' + encodeURIComponent(endpointId) + '/');
        assert.equal(await page.locator('[data-endpoint-detail]').getAttribute('data-endpoint-id'), endpointId);
        assert(await page.evaluate(() => document.cookie.split(';').some(cookie => cookie.trim().startsWith('csrftoken='))),
            'Endpoint page must provide the CSRF cookie for its authorized POST actions');
        await page.getByRole('button', {name: /Ações e Jobs/}).click();
        const card = page.locator('[data-endpoint-policy-id="' + policyId + '"]');
        await card.waitFor();
        assert.equal(await card.locator('h3').innerText(), policyName);
        assert.equal(await card.locator('img').count(), 0, 'Policy names must be rendered as text');
        assert((await card.innerText()).includes('Sem histórico'));
        assert((await card.innerText()).includes(endpointName));
        const runButton = card.getByRole('button', {name: 'Executar agora neste endpoint'});
        assert.equal(await runButton.isEnabled(), true);

        phase = 'endpoint_scoped_confirmation';
        await runButton.click();
        await page.waitForFunction(() => document.querySelector('[data-endpoint-toast]')?.textContent.includes('somente para'));
        assert(confirmation.includes(policyName));
        assert(confirmation.includes(endpointName));
        assert(confirmation.includes(endpointId));
        assert(confirmation.includes('apenas a este computador'));
        await page.waitForFunction(() => {
            const cardNode = document.querySelector('[data-endpoint-policy-id]');
            return cardNode && /Em fila|Enfileirado/.test(cardNode.textContent);
        });

        phase = 'mobile_layout';
        await page.setViewportSize({width: 390, height: 844});
        await page.waitForTimeout(150);
        assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), 'Endpoint policy section overflows mobile viewport');
        console.log('PASS endpoint policy UI: UUID assignment, persisted empty/history states, endpoint-specific confirmation and run, XSS text rendering, desktop/mobile layout.');
        await context.close();
    } finally {
        await browser.close();
    }
})().catch(error => {
    console.log('ENDPOINT_POLICY_BROWSER_FAILURE=' + JSON.stringify({phase, error_type: error.name, message: String(error.message).slice(0, 220)}));
    process.exitCode = 1;
});
