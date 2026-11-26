const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {chromium} = require('playwright');

(async () => {
    const server = process.env.NIGHTOWL_E2E_SERVER;
    assert.match(server || '', /^http:\/\/(localhost|127\.0\.0\.1):\d+$/);
    const executablePath = process.env.NIGHTOWL_TEST_CHROME;
    assert(executablePath && path.isAbsolute(executablePath) && fs.existsSync(executablePath), 'Verified Chrome executable required');
    const artifactRoot = process.env.NIGHTOWL_E2E_ARTIFACT_DIR;
    assert(artifactRoot, 'Screenshot artifact directory is required');
    const out = path.join(artifactRoot, 'nightowl-software-center');
    fs.mkdirSync(out, {recursive: true});
    const browser = await chromium.launch({headless: true, executablePath});
    try {
        const context = await browser.newContext({viewport: {width: 1440, height: 1000}});
        await context.addCookies([{name: 'sessionid', value: process.env.NIGHTOWL_E2E_SESSION, url: server}]);
        const page = await context.newPage();
        const errors = [];
        const writes = [];
        page.on('pageerror', error => errors.push(error.name));
        page.on('request', request => {
            if (request.url().includes('/software/data/') && request.method() !== 'GET') writes.push(request.method());
        });
        await page.route('**/software/data/packages/', route => route.fulfill({
            status: 200,
            contentType: 'application/json',
            body: JSON.stringify({items: []})
        }));
        await page.goto(server + '/software/');
        assert.equal(await page.getByRole('heading', {name: 'Central de Softwares'}).count(), 1);
        assert.equal(await page.getByRole('tab').count(), 2);
        await page.getByText('Visual App', {exact: true}).waitFor();
        assert.equal(await page.locator('[data-software-inventory] .software-collected-card').count(), 2);
        assert.equal(await page.getByText('Visual Vendor', {exact: true}).count(), 1);
        assert.equal(await page.getByText('1 atual(is)', {exact: true}).count(), 1);
        assert.equal(await page.getByText('0 atual(is)', {exact: true}).count(), 1);
        assert.equal(await page.getByText('1 antigo(s)', {exact: true}).count(), 2);
        await page.locator('[data-software-freshness]').selectOption('stale');
        assert.equal(await page.locator('[data-software-inventory] .software-collected-card').count(), 2);
        await page.locator('[data-software-freshness]').selectOption('current');
        assert.equal(await page.locator('[data-software-inventory] .software-collected-card').count(), 1);
        await page.locator('[data-software-search]').fill('not-installed');
        await page.getByText('Nenhum software corresponde aos filtros.').waitFor();
        await page.locator('[data-software-search-clear]').click();
        await page.getByText('Visual App', {exact: true}).waitFor();
        await page.screenshot({path: path.join(out, 'software-inventory-desktop.png'), fullPage: true});

        await page.getByRole('tab', {name: 'Uploads'}).click();
        await page.getByText('Nenhum instalador cadastrado.', {exact: false}).waitFor();
        await page.unroute('**/software/data/packages/');
        await page.locator('[data-software-refresh]').click();
        await page.getByRole('heading', {name: 'Instaladores cadastrados'}).waitFor();
        await page.getByRole('tab', {name: 'Softwares coletados'}).click();

        const visualAppCard = page.locator('[data-software-inventory] .software-collected-card').filter({hasText: 'Visual App'});
        await visualAppCard.getByRole('button', {name: /Ver endpoints/}).click();
        await page.locator('[data-software-drawer]').getByRole('link', {name: 'LAB-SOFTWARE-BROWSER'}).waitFor();
        await page.locator('[data-software-drawer]').getByText(/1\.4, 1\.5/).waitFor();
        await page.locator('[data-software-drawer]').getByRole('link', {name: 'LAB-SOFTWARE-STALE'}).waitFor();
        await page.locator('[data-software-drawer-close]').click();
        await page.getByRole('tab', {name: 'Uploads'}).click();
        await page.getByRole('heading', {name: 'Instaladores cadastrados'}).waitFor();
        const uploadGrid = page.locator('[data-software-packages]');
        assert.equal(await uploadGrid.locator('.software-upload-card').count(), 1);
        assert.equal(await uploadGrid.locator('.software-upload-card h3 a').getAttribute('href'), '#');
        const columns = await uploadGrid.evaluate(node => getComputedStyle(node).gridTemplateColumns.split(' ').length);
        assert.equal(columns, 2, 'Uploads must use two columns on desktop');
        assert.equal(await page.getByRole('button', {name: /Adicionar arquivo de instalação/}).isDisabled(), true);
        await uploadGrid.locator('.software-upload-card').click({position: {x: 20, y: 15}});
        assert.equal(await page.locator('[data-software-drawer]').getAttribute('aria-hidden'), 'true', 'The whole upload card must not open the drawer');
        await uploadGrid.getByRole('link', {name: 'Visual Installer'}).click();
        const drawer = page.locator('[data-software-drawer]');
        await drawer.getByText('visual-installer.exe').waitFor();
        await drawer.getByText('SHA-256').waitFor();
        await drawer.getByText('Válida', {exact: true}).waitFor();
        assert.equal(await drawer.getByText('Visual Vendor', {exact: true}).count(), 1);
        await page.screenshot({path: path.join(out, 'software-uploads-desktop.png'), fullPage: true});
        await page.keyboard.press('Escape');

        await page.setViewportSize({width: 390, height: 844});
        const mobileGridColumns = await uploadGrid.evaluate(node => getComputedStyle(node).gridTemplateColumns.split(' ').length);
        assert.equal(mobileGridColumns, 1, 'Uploads must use one column on mobile');
        const mobileBounds = await page.locator('[data-software-root]').evaluate(node => ({width: node.clientWidth, scroll: node.scrollWidth}));
        assert(mobileBounds.scroll <= mobileBounds.width + 1, 'Software page must not overflow horizontally on mobile');
        await page.screenshot({path: path.join(out, 'software-uploads-mobile.png'), fullPage: true});
        await page.getByRole('tab', {name: 'Softwares coletados'}).click();
        await page.screenshot({path: path.join(out, 'software-inventory-mobile.png'), fullPage: true});
        assert.deepEqual(errors, []);
        assert.deepEqual(writes, [], 'This increment must not send write requests to software APIs');
    } finally {
        await browser.close();
    }
})().catch(error => {
    console.error(error.stack);
    process.exitCode = 1;
});
