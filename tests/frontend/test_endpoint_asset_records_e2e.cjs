const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {chromium} = require('playwright');

(async () => {
    const server = process.env.NIGHTOWL_E2E_SERVER;
    const endpointId = process.env.NIGHTOWL_E2E_ENDPOINT;
    const endpointName = process.env.NIGHTOWL_E2E_ENDPOINT_NAME;
    const chromePath = process.env.NIGHTOWL_TEST_CHROME;
    assert(/^http:\/\/(localhost|127\.0\.0\.1):\d+$/.test(server || ''), 'isolated Django live server is required');
    assert(endpointId && endpointName && process.env.NIGHTOWL_E2E_SESSION, 'synthetic endpoint session is required');
    assert(chromePath && path.isAbsolute(chromePath) && fs.existsSync(chromePath), 'verified NIGHTOWL_TEST_CHROME is required');

    const browser = await chromium.launch({headless: true, executablePath: chromePath});
    try {
        const context = await browser.newContext({viewport: {width: 1440, height: 960}});
        await context.addCookies([{name: 'sessionid', value: process.env.NIGHTOWL_E2E_SESSION, url: server}]);
        const page = await context.newPage();
        const pageErrors = [];
        page.on('pageerror', error => pageErrors.push(error.name));

        await page.goto(`${server}/endpoints/${endpointId}/`, {waitUntil: 'domcontentloaded'});
        await page.getByRole('button', {name: /Registros/}).click();
        await page.getByRole('heading', {name: 'Sem associação patrimonial'}).waitFor();
        await page.locator('[data-asset-records-content]').getByText('BROWSER-ASSET-01').first().waitFor();
        await page.getByText('Example Devices').waitFor();

        await page.locator('[data-asset-create-form] input[name="identity_confirmed"]').check();
        await page.getByRole('button', {name: 'Criar e associar patrimônio'}).click();
        await page.getByRole('heading', {name: 'Registro patrimonial'}).waitFor();
        await page.getByText(/PROV-[0-9A-F]{32}/).waitFor();
        await page.getByText(endpointName).waitFor();

        const form = page.locator('[data-asset-record-form]');
        await form.locator('[name="record_type"]').selectOption('observation');
        await form.locator('[name="occurred_at"]').fill(new Date(Date.now() - new Date().getTimezoneOffset() * 60000).toISOString().slice(0, 16));
        await form.locator('[name="description"]').fill('Registro manual validado no navegador de CI');
        await form.getByRole('button', {name: 'Salvar registro'}).click();
        await page.getByText('Registro manual validado no navegador de CI').waitFor();
        await page.getByText('asset-records-browser').waitFor();

        await page.setViewportSize({width: 390, height: 844});
        const mobile = await page.evaluate(() => ({
            panel: document.querySelector('[data-asset-records]'),
            content: document.querySelector('[data-asset-records-content]')
        }));
        assert(mobile.panel && mobile.panel.getBoundingClientRect().width > 0, 'records panel should remain rendered on mobile');
        assert(mobile.content.scrollWidth <= mobile.content.clientWidth + 1,
            `records panel should not overflow at mobile width: ${mobile.content.scrollWidth}/${mobile.content.clientWidth}`);
        assert.deepEqual(pageErrors, [], 'page should not raise JavaScript errors');
        console.log(JSON.stringify({result: 'passed', endpoint: endpointName, recordType: 'observation', mobile}));
        await context.close();
    } finally {
        await browser.close();
    }
})().catch(error => {
    console.error(error && error.stack ? error.stack : 'Asset records browser E2E failed');
    process.exitCode = 1;
});
