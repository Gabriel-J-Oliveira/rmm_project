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
            if (request.url().includes('/software/') && !request.url().includes('/software/data/') && request.method() !== 'GET') {
                writes.push({method: request.method(), path: new URL(request.url()).pathname});
            }
        });
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
        await page.getByRole('heading', {name: 'Instaladores cadastrados'}).waitFor();
        await page.getByRole('link', {name: 'Visual Installer'}).waitFor();
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
        assert.equal(await page.getByRole('button', {name: /Adicionar arquivo de instalação/}).isDisabled(), false);
        await page.getByRole('button', {name: /Adicionar arquivo de instalação/}).click();
        const drawer = page.locator('[data-software-drawer]');
        await drawer.getByRole('heading', {name: 'Adicionar arquivo de instalação'}).waitFor();
        const msi = Buffer.alloc(512);
        Buffer.from('d0cf11e0a1b11ae1', 'hex').copy(msi, 0);
        msi.writeUInt16LE(3, 26);
        msi.writeUInt16LE(0xfffe, 28);
        msi.writeUInt16LE(9, 30);
        msi.writeUInt16LE(6, 32);
        let msiOffset = 64;
        for (const entry of ['_Tables', '_Columns', '_StringPool', '_StringData']) {
            Buffer.from(entry, 'utf16le').copy(msi, msiOffset);
            msiOffset += Buffer.byteLength(entry, 'utf16le') + 1;
        }
        await drawer.locator('input[name="friendly_name"]').fill('E2E Package MSI');
        await drawer.locator('input[type="file"]').setInputFiles({name: 'e2e-package.msi', mimeType: 'application/octet-stream', buffer: msi});
        const createResponse = page.waitForResponse(response => response.url().includes('/software/packages/create/') && response.request().method() === 'POST');
        await drawer.getByRole('button', {name: 'Enviar e salvar como rascunho'}).click();
        assert.equal((await createResponse).status(), 201);
        await page.getByRole('link', {name: 'E2E Package MSI'}).waitFor();
        await drawer.locator('[data-software-drawer-kicker]').getByText('Cadastro de instalador', {exact: true}).waitFor();
        assert.equal(await uploadGrid.locator('.software-upload-card').count(), 2);
        const firstHash = await drawer.locator('.software-mono').textContent();
        await drawer.locator('[data-package-reason]').fill('Origem revisada pelo operador');
        await drawer.locator('[data-package-action="approve"]').click();
        await drawer.locator('.software-detail-grid').getByText('Aprovado', {exact: true}).waitFor();
        await drawer.locator('input[name="file"]').setInputFiles({name: 'e2e-package.exe', mimeType: 'application/octet-stream', buffer: (() => {
            const exe = Buffer.alloc(512);
            exe.write('MZ', 0, 'ascii');
            exe.writeUInt32LE(128, 0x3c);
            exe.write('PE\0\0', 128, 'binary');
            exe.writeUInt16LE(1, 134);
            exe.writeUInt16LE(2, 150);
            return exe;
        })()});
        await drawer.locator('input[name="reason"]').fill('Substituição funcional de teste');
        const replaceResponse = page.waitForResponse(response => response.url().includes('/software/packages/') && response.url().endsWith('/replace/') && response.request().method() === 'POST');
        await drawer.getByRole('button', {name: 'Substituir arquivo'}).click();
        assert.equal((await replaceResponse).status(), 200);
        await drawer.locator('dd').filter({hasText: 'e2e-package.exe'}).waitFor();
        await drawer.locator('.software-detail-grid').getByText('Rascunho', {exact: true}).waitFor();
        const replacementHash = await drawer.locator('.software-mono').textContent();
        assert.notEqual(replacementHash, firstHash, 'Replacement must receive a new server-computed hash');
        await drawer.locator('[data-package-reason]').fill('Teste de revogação');
        await drawer.locator('[data-package-action="revoke"]').click();
        await drawer.locator('.software-detail-grid').getByText('Revogado', {exact: true}).waitFor();
        await drawer.locator('[data-software-drawer-close]').click();
        await uploadGrid.locator('.software-upload-card').filter({hasText: 'Visual Installer'}).click({position: {x: 20, y: 15}});
        assert.equal(await page.locator('[data-software-drawer]').getAttribute('aria-hidden'), 'true', 'The whole upload card must not open the drawer');
        await uploadGrid.getByRole('link', {name: 'E2E Package MSI'}).click();
        await drawer.locator('dd').filter({hasText: 'e2e-package.exe'}).waitFor();
        await drawer.getByText('SHA-256').waitFor();
        await drawer.getByText('Não verificada', {exact: true}).waitFor();
        await drawer.getByText('Instalador aprovado', {exact: true}).waitFor();
        await page.screenshot({path: path.join(out, 'software-uploads-desktop.png'), fullPage: true});
        await page.keyboard.press('Escape');

        await page.setViewportSize({width: 390, height: 844});
        const mobileGridColumns = await uploadGrid.evaluate(node => getComputedStyle(node).gridTemplateColumns.split(' ').length);
        assert.equal(mobileGridColumns, 1, 'Uploads must use one column on mobile');
        const mobileBounds = await page.locator('[data-software-root]').evaluate(node => ({width: node.clientWidth, scroll: node.scrollWidth}));
        await page.screenshot({path: path.join(out, 'software-uploads-mobile.png'), fullPage: true});
        if (mobileBounds.scroll > mobileBounds.width + 1) {
            const overflow = await page.locator('[data-software-root]').evaluate(root => {
                const bounds = root.getBoundingClientRect();
                return Array.from(root.querySelectorAll('*')).filter(node => !node.closest('[hidden]')).map(node => {
                    const rect = node.getBoundingClientRect();
                    const style = getComputedStyle(node);
                    return {
                        tag: node.tagName.toLowerCase(), className: typeof node.className === 'string' ? node.className : '',
                        text: (node.textContent || '').trim().slice(0, 48), position: style.position,
                        left: Math.round(rect.left), right: Math.round(rect.right),
                        clientWidth: node.clientWidth, scrollWidth: node.scrollWidth
                    };
                }).filter(item => item.position !== 'fixed' && (item.right > bounds.right + 1 || item.left < bounds.left - 1 || item.scrollWidth > item.clientWidth + 1)).slice(0, 20);
            });
            assert.fail('Software page must not overflow horizontally on mobile: ' + JSON.stringify({mobileBounds, overflow}));
        }
        await page.getByRole('tab', {name: 'Softwares coletados'}).click();
        await page.screenshot({path: path.join(out, 'software-inventory-mobile.png'), fullPage: true});
        assert.deepEqual(errors, []);
        assert.ok(writes.length >= 4, 'The UI must persist package operations through authenticated APIs');
        assert.ok(writes.every(item => item.path.startsWith('/software/packages/')), 'Writes must use the package API only: ' + JSON.stringify(writes));
    } finally {
        await browser.close();
    }
})().catch(error => {
    console.error(error.stack);
    process.exitCode = 1;
});
