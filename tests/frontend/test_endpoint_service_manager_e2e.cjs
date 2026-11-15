const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
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
    assert(endpointId && endpointName, 'Synthetic endpoint context is required');

    const browser = await chromium.launch({headless: true, executablePath: chromePath});
    try {
        const context = await browser.newContext({viewport: {width: 1440, height: 1000}});
        await context.addCookies([{name: 'sessionid', value: process.env.NIGHTOWL_E2E_SESSION, url: server}]);
        const page = await context.newPage();
        const pageErrors = [];
        const api = {dispatches: 0, statusReads: 0, writeCalls: 0, csrf: ''};
        page.on('pageerror', error => pageErrors.push(error.name));
        await page.route('**/api/agent/service-manager/**', async route => {
            const request = route.request();
            const url = new URL(request.url());
            assert.equal(url.pathname, '/api/agent/service-manager/' + endpointId + '/' +
                (url.pathname.endsWith('/availability/') ? 'availability/' :
                    url.pathname.includes('/jobs/') ? url.pathname.split('/').slice(-3).join('/') : ''));
            if (request.method() === 'GET' && url.pathname.endsWith('/availability/')) {
                return route.fulfill({status: 200, contentType: 'application/json', body: JSON.stringify({
                    available: true, reason_code: '', reason: '', page_size: 20, max_services: 800
                })});
            }
            if (request.method() === 'POST') {
                api.dispatches++;
                api.csrf = request.headers()['x-csrftoken'] || '';
                const body = request.postDataJSON();
                assert.deepEqual(Object.keys(body).sort(), ['cursor', 'request_id']);
                assert.match(body.request_id, /^[0-9a-f-]{36}$/i);
                assert.equal(body.cursor, null);
                return route.fulfill({status: 202, contentType: 'application/json', body: JSON.stringify({
                    job_id: 'b5e59c66-5bf6-4f61-9b6d-10fa361e847e', status: 'queued', request_id: body.request_id
                })});
            }
            if (request.method() === 'GET' && url.pathname.includes('/jobs/')) {
                api.statusReads++;
                return route.fulfill({status: 200, contentType: 'application/json', body: JSON.stringify({
                    status: 'completed', job_id: 'b5e59c66-5bf6-4f61-9b6d-10fa361e847e', page_index: 1,
                    page: {services: [
                        {name: 'Spooler', display_name: 'Print Spooler', status: 'Running', startup_type: 'Automatic'},
                        {name: 'AcmeAgent', display_name: '<img src=x onerror=alert(1)> Agent', status: 'Stopped', startup_type: 'Manual'},
                        {name: 'Mystery', display_name: 'Mystery Service', status: 'unknown', startup_type: 'unknown'}
                    ], collected_at: new Date().toISOString(), snapshot_id: '0123456789abcdef0123456789abcdef',
                    total_services: 3, has_more: false, cursor: null}
                })});
            }
            api.writeCalls++;
            return route.fulfill({status: 405, contentType: 'application/json', body: '{"error":"method_not_allowed"}'});
        });

        const repoRoot = path.resolve(__dirname, '../..');
        await page.route('**/*', async route => {
            const url = new URL(route.request().url());
            if (url.origin !== server) return route.abort();
            if (!url.pathname.startsWith('/static/')) return route.fallback();
            const staticRoot = path.join(repoRoot, 'static');
            const file = path.resolve(staticRoot, decodeURIComponent(url.pathname.slice('/static/'.length)));
            assert(file.startsWith(staticRoot + path.sep), 'Static path escaped the source tree');
            if (!fs.existsSync(file)) return route.fulfill({status: 404, body: ''});
            const contentType = file.endsWith('.js') ? 'application/javascript' :
                file.endsWith('.css') ? 'text/css' : 'application/octet-stream';
            return route.fulfill({body: fs.readFileSync(file), contentType});
        });

        phase = 'endpoint_page';
        await page.goto(server + '/endpoints/' + encodeURIComponent(endpointId) + '/');
        assert.equal(await page.locator('[data-endpoint-detail]').getAttribute('data-endpoint-id'), endpointId);
        await page.getByRole('button', {name: /Ações e Jobs/}).click();
        const openButton = page.locator('[data-endpoint-action="query_service"]');
        await openButton.waitFor();
        await page.waitForFunction(() => {
            const button = document.querySelector('[data-endpoint-action="query_service"]');
            return button && !button.disabled;
        });

        phase = 'service_read';
        await openButton.click();
        const dialog = page.locator('[data-service-manager-dialog]');
        await dialog.waitFor({state: 'visible'});
        await page.getByText('Spooler', {exact: true}).waitFor();
        assert.equal(api.dispatches, 1);
        assert(api.statusReads >= 1);
        assert(api.csrf.length > 0, 'Read-only job dispatch must retain session CSRF protection');
        assert.equal(api.writeCalls, 0, 'Phase A must not expose remote write operations');
        assert.equal(await dialog.locator('img').count(), 0, 'Service labels must be inserted as text, not HTML');
        assert((await dialog.innerText()).includes('<img src=x onerror=alert(1)> Agent'));

        phase = 'search_filters_properties';
        await dialog.locator('[data-service-search]').fill('acme');
        assert.equal(await dialog.locator('[data-service-rows] tr').count(), 1);
        await dialog.locator('[data-service-search]').fill('');
        await dialog.locator('[data-service-state-filter]').selectOption('unknown');
        assert.equal(await dialog.locator('[data-service-rows] tr').count(), 1);
        await dialog.locator('[data-service-state-filter]').selectOption('all');
        await dialog.locator('[data-service-startup-filter]').selectOption('Manual');
        assert.equal(await dialog.locator('[data-service-rows] tr').count(), 1);
        await dialog.locator('[data-service-startup-filter]').selectOption('all');
        await dialog.locator('[data-service-sort]').selectOption('name-desc');
        const names = await dialog.locator('[data-service-rows] tr [data-service-name]').allTextContents();
        assert.deepEqual(names, ['Spooler', 'Mystery', 'AcmeAgent'].sort((a, b) => b.localeCompare(a)));
        await dialog.locator('[data-service-name="AcmeAgent"]').click();
        assert((await dialog.locator('[data-service-properties]').innerText()).includes('Nome interno'));
        assert((await dialog.locator('[data-service-properties]').innerText()).includes('AcmeAgent'));
        assert((await dialog.locator('[data-service-collected-at]').innerText()).includes('Dados recentes'));

        phase = 'desktop_screenshot';
        const artifacts = process.env.NIGHTOWL_E2E_ARTIFACT_DIR || os.tmpdir();
        const artifactDir = path.join(artifacts, 'nightowl-service-manager');
        fs.mkdirSync(artifactDir, {recursive: true});
        await page.screenshot({path: path.join(artifactDir, 'service-manager-desktop.png'), fullPage: true});

        phase = 'manual_refresh';
        await dialog.locator('[data-service-refresh]').click();
        await page.waitForFunction(() => document.querySelector('[data-service-status]')?.textContent.includes('Coleta concluída'));
        assert.equal(api.dispatches, 2);
        assert.equal(api.writeCalls, 0);

        phase = 'mobile_layout';
        await page.setViewportSize({width: 390, height: 844});
        await page.waitForTimeout(100);
        const mobile = await page.evaluate(() => {
            const node = document.querySelector('[data-service-manager-dialog]');
            const box = node.getBoundingClientRect();
            return {viewport: innerWidth, left: box.left, right: box.right, width: box.width,
                contentWidth: node.clientWidth, scrollWidth: node.scrollWidth, open: node.open};
        });
        assert(mobile.open && mobile.left >= -1 && mobile.right <= mobile.viewport + 1 &&
            mobile.scrollWidth <= mobile.contentWidth + 1,
            'Service manager overflows the mobile viewport: ' + JSON.stringify(mobile));
        await page.screenshot({path: path.join(artifactDir, 'service-manager-mobile.png'), fullPage: true});

        phase = 'theme_tokens';
        const themeColors = await page.evaluate(() => ({
            dialog: getComputedStyle(document.querySelector('[data-service-manager-dialog]')).backgroundColor,
            token: (() => {
                const probe = document.createElement('div');
                probe.style.backgroundColor = 'var(--card)';
                document.body.appendChild(probe);
                const color = getComputedStyle(probe).backgroundColor;
                probe.remove();
                return color;
            })()
        }));
        assert(themeColors.token && themeColors.dialog === themeColors.token,
            'The service dialog must use NightOwl theme surface tokens: ' + JSON.stringify(themeColors));
        assert.deepEqual(pageErrors, [], 'The endpoint page must not produce JavaScript errors');
        console.log('PASS service manager browser: real endpoint page, authorized read-only list, search, filters, sort, properties, manual refresh, XSS-safe text, desktop/mobile layout and theme tokens. Screenshots: ' + artifactDir);
        await context.close();
    } finally {
        await browser.close();
    }
})().catch(error => {
    console.log('SERVICE_MANAGER_BROWSER_FAILURE=' + JSON.stringify({phase, error_type: error.name,
        message: String(error.message).slice(0, 200)}));
    process.exitCode = 1;
});
