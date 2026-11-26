const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {chromium} = require('playwright');

(async () => {
    const server = process.env.NIGHTOWL_E2E_SERVER;
    assert.match(server, /^http:\/\/(localhost|127\.0\.0\.1):\d+$/);
    const executablePath = process.env.NIGHTOWL_TEST_CHROME;
    assert(executablePath && fs.existsSync(executablePath), 'Verified Chrome executable required');
    const out = path.join(process.env.NIGHTOWL_E2E_ARTIFACT_DIR, 'nightowl-noc');
    fs.mkdirSync(out, {recursive: true});
    const browser = await chromium.launch({headless: true, executablePath});
    try {
        const context = await browser.newContext({viewport: {width: 1680, height: 1080}});
        await context.addCookies([{name: 'sessionid', value: process.env.NIGHTOWL_E2E_SESSION, url: server}]);
        const page = await context.newPage();
        const errors = [];
        page.on('pageerror', (error) => errors.push(error.name));
        await page.goto(server + '/noc/');
        const list = page.locator('.noc-all-alerts');
        await list.getByRole('heading', {name: 'Todos os alertas'}).waitFor();
        assert.equal(await list.locator('.noc-occurrence').count(), 4);
        const desktopSection = await page.locator('.noc-thematic-grid').boundingBox();
        assert(desktopSection.y > (await list.boundingBox()).y);
        const cards = await page.locator('.noc-thematic-grid > .noc-panel').evaluateAll((nodes) => nodes.map((node) => node.getBoundingClientRect().top));
        assert(cards.every((top) => top === cards[0]), 'Desktop displays four cards on the same row');
        await page.screenshot({path: path.join(out, 'noc-desktop.png'), fullPage: true});
        await page.setViewportSize({width: 390, height: 844});
        const grid = await page.locator('.noc-thematic-grid').evaluate((node) => ({width: node.clientWidth, scroll: node.scrollWidth}));
        assert(grid.scroll <= grid.width + 1, 'Mobile cards fit without horizontal scrolling');
        assert(grid.width >= 320, 'The NOC uses the mobile viewport rather than keeping a desktop sidebar');
        assert.equal(await page.locator('[data-noc-drawer]').isVisible(), false);
        await page.evaluate(() => document.fonts.ready);
        await page.screenshot({path: path.join(out, 'noc-mobile.png'), fullPage: true});

        const occurrence = list.locator('.noc-occurrence').filter({hasText: 'Disco C: em 97%'});
        for (const [name, action, status] of [
            ['Reconhecer', 'acknowledge', 200], ['Criar chamado', 'ticket', 201], ['Verificar', 'verify', 201],
        ]) {
            const response = page.waitForResponse((res) => res.url().endsWith('/' + action + '/'));
            await occurrence.getByRole('button', {name, exact: true}).click();
            assert.equal((await response).status(), status);
            await page.locator('[data-noc-refresh]').focus();
            await page.mouse.move(3, 3);
            await page.waitForFunction(() => !document.querySelector('[data-noc-refresh]').disabled);
        }
        assert.deepEqual(errors, []);
    } finally {
        await browser.close();
    }
})().catch((error) => {
    console.error(error.stack);
    process.exitCode = 1;
});
