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
    const serviceNames = ['AcmeAgent', 'Mystery', ...Array.from({length: 292}, (_, index) => 'Service' + String(index + 1).padStart(3, '0')), 'Spooler'];
    const scanTimestamp = new Date().toISOString();

    const browser = await chromium.launch({headless: true, executablePath: chromePath});
    try {
        const context = await browser.newContext({viewport: {width: 1440, height: 1000}});
        await context.addCookies([{name: 'sessionid', value: process.env.NIGHTOWL_E2E_SESSION, url: server}]);
        const page = await context.newPage();
        const pageErrors = [];
    const api = {dispatches: 0, statusReads: 0, writeCalls: 0, actionDispatches: 0, actionStatusReads: 0, csrf: '', jobs: new Map()};
        let serviceState = 'Stopped';
        page.on('pageerror', error => pageErrors.push(error.name));
        await page.route('**/api/agent/service-manager/**', async route => {
            const request = route.request();
            const url = new URL(request.url());
            assert(url.pathname.startsWith('/api/agent/service-manager/' + endpointId + '/'));
            if (request.method() === 'GET' && url.pathname.endsWith('/availability/')) {
                return route.fulfill({status: 200, contentType: 'application/json', body: JSON.stringify({
                    available: true, reason_code: '', reason: '', schema_version: 3, page_size: 100,
                    max_page_bytes: 48 * 1024, max_services: 800,
                    service_actions: {
                        start_service: {enabled: true, services: ['AcmeAgent'], reason: ''},
                        stop_service: {enabled: true, services: ['Spooler'], reason: ''},
                        restart_service: {enabled: true, services: ['Spooler'], reason: ''}
                    }
                })});
            }
            if (url.pathname.endsWith('/actions/') && request.method() === 'POST') {
                api.actionDispatches++;
                api.csrf = request.headers()['x-csrftoken'] || api.csrf;
                const body = request.postDataJSON();
                assert.deepEqual(Object.keys(body).sort(), ['action_id', 'request_id', 'service_name']);
                assert.match(body.request_id, /^[0-9a-f-]{36}$/i);
                assert.equal(body.action_id, 'start_service');
                assert.equal(body.service_name, 'AcmeAgent');
                return route.fulfill({status: 202, contentType: 'application/json', body: JSON.stringify({
                    job_id: '9f49a67c-0cfc-4bd9-844b-001cbeab0001', status: 'queued', request_id: body.request_id
                })});
            }
            if (url.pathname.includes('/actions/') && request.method() === 'GET') {
                api.actionStatusReads++;
                serviceState = 'Running';
                return route.fulfill({status: 200, contentType: 'application/json', body: JSON.stringify({
                    job_id: '9f49a67c-0cfc-4bd9-844b-001cbeab0001', status: 'completed', action_id: 'start_service',
                    service_name: 'AcmeAgent', result_confirmed: true, result: {state: 'Running', changed: true}
                })});
            }
            if (request.method() === 'POST') {
                api.dispatches++;
                api.csrf = request.headers()['x-csrftoken'] || '';
                const body = request.postDataJSON();
                assert.deepEqual(Object.keys(body).sort(), ['cursor', 'request_id']);
                assert.match(body.request_id, /^[0-9a-f-]{36}$/i);
                const scan = Math.ceil(api.dispatches / 3);
                const pageIndex = ((api.dispatches - 1) % 3) + 1;
                assert.equal(body.cursor, pageIndex === 1 ? null : 'cursor-' + (pageIndex - 1));
                const jobId = 'service-job-' + api.dispatches;
                api.jobs.set(jobId, {scan, pageIndex, polls: 0});
                return route.fulfill({status: 202, contentType: 'application/json', body: JSON.stringify({
                    job_id: jobId, status: 'queued', request_id: body.request_id, schema_version: 3, page_size: 100
                })});
            }
            if (request.method() === 'GET' && url.pathname.includes('/jobs/')) {
                api.statusReads++;
                const jobId = url.pathname.split('/').filter(Boolean).at(-1);
                const job = api.jobs.get(jobId);
                assert(job, 'status request must match a dispatched synthetic job');
                job.polls++;
                if (job.scan === 2 && job.pageIndex === 1 && job.polls === 1) {
                    return route.fulfill({status: 200, contentType: 'application/json', body: JSON.stringify({status: 'queued', job_id: jobId})});
                }
                const start = (job.pageIndex - 1) * 100;
                const pageNames = serviceNames.slice(start, start + (job.pageIndex === 3 ? 95 : 100));
                const services = pageNames.map(name => ({
                    name,
                    display_name: name === 'AcmeAgent' ? '<img src=x onerror=alert(1)> Agent' : name + ' display',
                    status: name === 'AcmeAgent' ? serviceState : name === 'Mystery' ? 'unknown' : name === 'Spooler' ? 'Running' : 'Stopped',
                    startup_type: name === 'AcmeAgent' ? 'Manual' : name === 'Mystery' ? 'unknown' : 'Automatic'
                }));
                const collected = start + pageNames.length;
                return route.fulfill({status: 200, contentType: 'application/json', body: JSON.stringify({
                    status: 'completed', job_id: jobId, page_index: job.pageIndex,
                    page: {services, collected_at: scanTimestamp, snapshot_id: '0123456789abcdef0123456789abcdef',
                    total_services: 295, has_more: job.pageIndex < 3, cursor: job.pageIndex < 3 ? 'cursor-' + job.pageIndex : null,
                    schema_version: 3, page_size: 100, rows_collected: collected}
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
        assert.equal(api.dispatches, 3, '295 services should use three v3 jobs');
        assert(api.statusReads >= 1);
        assert(api.csrf.length > 0, 'Read-only job dispatch must retain session CSRF protection');
        assert.equal(await dialog.locator('img').count(), 0, 'Service labels must be inserted as text, not HTML');
        assert((await dialog.innerText()).includes('<img src=x onerror=alert(1)> Agent'));

        phase = 'service_control_confirmation';
        const startService = dialog.locator('[data-service-control="start_service"][data-service-name="AcmeAgent"]');
        assert.equal(await startService.isEnabled(), true);
        assert.equal(await dialog.locator('[data-service-control="stop_service"][data-service-name="AcmeAgent"]').isEnabled(), false);
        assert.equal(await dialog.locator('[data-service-control="restart_service"][data-service-name="AcmeAgent"]').isEnabled(), false);
        await startService.click();
        assert.equal(api.actionDispatches, 0, 'Opening the confirmation must not create a job.');
        assert((await dialog.locator('[data-service-action-description]').innerText()).includes(endpointName));
        assert((await dialog.locator('[data-service-action-description]').innerText()).includes('AcmeAgent'));
        await dialog.locator('[data-service-action-submit]').click();
        await dialog.locator('[data-service-action-status]').filter({hasText: 'Concluído e confirmado'}).waitFor();
        assert.equal(api.actionDispatches, 1);
        assert(api.actionStatusReads >= 1);
        assert.equal(api.writeCalls, 0, 'Only the dedicated, mocked service action path may be used by this browser fixture.');
        assert.equal(api.dispatches, 3, 'A confirmed action must update the displayed row without a full collection.');
        assert((await dialog.locator('[data-service-status]').innerText()).includes('Status atualizado na lista'));
        assert.equal(await dialog.locator('[data-service-rows] tr').count(), 295);

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
        const names = await dialog.locator('[data-service-selection]').evaluateAll(nodes => nodes.map(node => node.value));
        assert.deepEqual(names, serviceNames.slice().sort((a, b) => b.localeCompare(a)));
        const acmeRow = dialog.locator('[data-service-row="AcmeAgent"]');
        await acmeRow.locator('.endpoint-services-name').click();
        assert.equal(await acmeRow.locator('[data-service-selection]').isChecked(), true);
        assert.equal(await dialog.locator('[data-service-properties]').isVisible(), true);
        assert((await dialog.locator('[data-service-properties]').innerText()).includes('Detalhes do serviço'));
        assert((await dialog.locator('[data-service-properties]').innerText()).includes('AcmeAgent'));
        assert((await dialog.locator('[data-service-collected-at]').innerText()).includes('Dados recentes'));
        await dialog.locator('[data-service-properties-close]').click();
        assert.equal(await dialog.isVisible(), true, 'Closing service details must not close the manager dialog');
        assert.equal(await dialog.locator('[data-service-properties]').isHidden(), true);
        await dialog.locator('[data-service-row="Spooler"] .endpoint-services-name').click();
        assert.equal(await dialog.locator('[data-service-selection]:checked').inputValue(), 'Spooler');
        assert.equal(await dialog.locator('[data-service-properties]').isVisible(), true);
        await dialog.locator('[data-service-selection]:checked').focus();
        await dialog.locator('[data-service-selection]:checked').press('ArrowDown');
        assert.equal(await dialog.locator('[data-service-selection]:checked').inputValue(), 'Service292');
        assert.equal(await dialog.locator('[data-service-selection]:checked').count(), 1);
        assert((await dialog.locator('[data-service-properties]').innerText()).includes('Service292'));
        phase = 'no_hover_and_action_button_metrics';
        const staticRow = dialog.locator('[data-service-row="Service291"]');
        const rowBackground = await staticRow.evaluate(node => getComputedStyle(node).backgroundColor);
        await staticRow.hover();
        assert.equal(await staticRow.evaluate(node => getComputedStyle(node).backgroundColor), rowBackground,
            'Informational service rows must not change background on hover.');
        const stopButton = dialog.locator('[data-service-control="stop_service"][data-service-name="Spooler"]');
        assert.equal(await stopButton.isEnabled(), true);
        const buttonStyle = await stopButton.evaluate(node => ({background: getComputedStyle(node).backgroundColor,
            height: getComputedStyle(node).height, fontSize: getComputedStyle(node).fontSize}));
        await stopButton.hover();
        assert.equal(await stopButton.evaluate(node => getComputedStyle(node).backgroundColor), buttonStyle.background,
            'Service action buttons must not change background on hover.');
        assert.equal(buttonStyle.height, '26px');
        assert.equal(buttonStyle.fontSize, '12px');

        phase = 'desktop_screenshot';
        const artifacts = process.env.NIGHTOWL_E2E_ARTIFACT_DIR || os.tmpdir();
        const artifactDir = path.join(artifacts, 'nightowl-service-manager');
        fs.mkdirSync(artifactDir, {recursive: true});
        const desktopBounds = await dialog.boundingBox();
        assert(desktopBounds.width >= 1200 && desktopBounds.height >= 800,
            'Desktop service manager should use the available viewport: ' + JSON.stringify(desktopBounds));
        await page.screenshot({path: path.join(artifactDir, 'service-manager-desktop.png'), fullPage: true});

        phase = 'manual_refresh';
        await dialog.locator('[data-service-refresh]').click();
        await page.getByText(/Lista anterior preservada/).waitFor();
        assert.equal(await dialog.locator('[data-service-rows] tr').count(), 295,
            'The last complete list remains visible while refresh is queued.');
        assert.equal(await dialog.locator('[data-service-selection]:checked').inputValue(), 'Service292',
            'The selected service is preserved during refresh.');
        await page.waitForFunction(() => document.querySelector('[data-service-status]')?.textContent.includes('Coleta concluída'));
        assert.equal(api.dispatches, 6);
        assert.equal(api.writeCalls, 0);
        assert.match(await dialog.locator('[data-service-status]').innerText(), /3 página\(s\).*\d+\.\d s/);

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
        const mobileTable = await page.evaluate(() => {
            const scroller = document.querySelector('.endpoint-services-table-scroll');
            const table = document.querySelector('.endpoint-services-table');
            return {scrollerWidth: scroller.clientWidth, scrollerScrollWidth: scroller.scrollWidth,
                tableWidth: table.getBoundingClientRect().width};
        });
        assert(mobileTable.scrollerScrollWidth > mobileTable.scrollerWidth && mobileTable.tableWidth >= 760,
            'Mobile service columns should scroll inside the table panel: ' + JSON.stringify(mobileTable));
        await page.screenshot({path: path.join(artifactDir, 'service-manager-mobile.png'), fullPage: true});

        phase = 'theme_tokens';
        const themeColors = await page.evaluate(() => ({
            dialog: getComputedStyle(document.querySelector('.endpoint-services-frame')).backgroundColor,
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
        console.log('PASS service manager browser: synthetic 295-service v3 scan in three pages, previous list during refresh, local confirmed action update without requery, search, filters, sort, accessible selection, details, timed progress, XSS-safe text, desktop/mobile layout and theme tokens. Screenshots: ' + artifactDir);
        await context.close();
    } finally {
        await browser.close();
    }
})().catch(error => {
    console.log('SERVICE_MANAGER_BROWSER_FAILURE=' + JSON.stringify({phase, error_type: error.name,
        message: String(error.message).slice(0, 200)}));
    process.exitCode = 1;
});
