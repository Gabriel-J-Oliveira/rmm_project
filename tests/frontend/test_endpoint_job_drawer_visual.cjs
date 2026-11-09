const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const vm = require('node:vm');

const root = path.resolve(__dirname, '..', '..');
const source = fs.readFileSync(path.join(root, 'static/js/endpoint_detail.js'), 'utf8');
const css = [
    fs.readFileSync(path.join(root, 'static/css/nightowl.css'), 'utf8'),
    fs.readFileSync(path.join(root, 'static/css/endpoint_actions_jobs.css'), 'utf8')
].join('\n');

function extract(from, to) {
    const start = source.indexOf(from);
    const end = source.indexOf(to, start + from.length);
    assert(start >= 0 && end > start, `source section exists: ${from}`);
    return source.slice(start, end);
}

const activeClasses = new Set(['is-open']);
const bodyClasses = new Set(['page-endpoint-detail', 'endpoint-drawer-open']);
const closeButton = { hidden: false, getAttribute: () => null, getClientRects: () => [{}], focus() { document.activeElement = this; } };
const copyButton = { hidden: false, getAttribute: () => null, getClientRects: () => [{}], focus() { document.activeElement = this; } };
const refreshButton = { hidden: false, getAttribute: () => null, getClientRects: () => [{}], focus() { document.activeElement = this; } };
const opener = { isConnected: true, focus() { document.activeElement = this; } };
const drawer = {
    classList: { add: value => activeClasses.add(value), remove: value => activeClasses.delete(value), contains: value => activeClasses.has(value) },
    setAttribute() {}, querySelector: () => closeButton, querySelectorAll: () => [closeButton, copyButton, refreshButton],
    contains: item => [closeButton, copyButton, refreshButton].includes(item), focus() {}
};
const drawerBody = { innerHTML: '' };
const drawerBackdrop = { hidden: false };
const job = {
    id: 'job-visual', jobId: 'job-visual', name: 'Atualização do agente', type: 'update_agent', status: 'completed',
    command: 'Atualização aprovada', createdAt: '2026-11-09T12:00:00Z', startedAt: '2026-11-09T12:00:02Z',
    finishedAt: '2026-11-09T12:00:04Z', durationMs: 2000, createdBy: 'operador', exitCode: 0,
    previousVersion: '0.1.1.0-rc46', targetVersion: '0.1.1.0-rc47', installedVersion: '0.1.1.0-rc47',
    correlationId: 'correlation-' + 'c'.repeat(72), endpoint: 'CS-CVEL-0281.control.local',
    payload: { localPath: 'C:\\ProgramData\\NightOwl\\' + 'deployment-'.repeat(22), records: Array.from({ length: 35 }, (_, index) => ({ index, details: 'Readable diagnostic value ' + 'long-string-'.repeat(9) })) },
    resultJson: { status: 'completed', installed_version: '0.1.1.0-rc47' }, stdout: 'Completed', stderr: ''
};
const document = {
    activeElement: opener,
    body: { classList: { add: value => bodyClasses.add(value), remove: value => bodyClasses.delete(value) } }
};
const context = {
    drawer, drawerBody, drawerBackdrop, document,
    drawerKicker: { textContent: '' }, drawerTitle: { textContent: '' }, drawerSubtitle: { textContent: '' },
    window: { scrollY: 420, pageYOffset: 420, scrollTo() {}, NightOwlJobDisplay: { status: item => item.status } },
    endpointDetail: { hostname: 'CS-CVEL-0281', activeJob: null, jobs: [job] }, labels: { update_agent: 'Atualizar agente' },
    jobProgress: () => null, jobResultLabel: item => item.status === 'completed' ? 'Job concluído.' : '', staleReasonLabel: () => '',
    formatDate: value => value || '-', formatDuration: value => `${value}ms`,
    escapeHtml: value => String(value ?? '').replace(/[&<>"']/g, char => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[char])),
    factList(items) { return '<dl class="endpoint-fact-list rmm-info-grid">' + items.map(item => `<div><dt>${item.label}</dt><dd>${this.escapeHtml(item.value == null ? '-' : item.value)}</dd></div>`).join('') + '</dl>'; }
};
context.factList = context.factList.bind(context);
vm.runInNewContext(extract('    function findJob(id) {', '    function openEventDrawer(id) {'), context);
vm.runInNewContext(extract('    function openJobDrawer(id) {', '    function copyText(value) {'), context);
context.openJobDrawer('job-visual');

async function main() {
    const { chromium } = require('playwright');
    const executablePath = process.env.NIGHTOWL_TEST_CHROME || [
        'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe',
        'C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe'
    ].find(candidate => fs.existsSync(candidate));
    const browser = await chromium.launch({ headless: true, ...(executablePath ? { executablePath } : {}) });
    const output = process.env.NIGHTOWL_SCREENSHOT_DIR || path.join(os.tmpdir(), 'nightowl-endpoint-final-ux');
    fs.mkdirSync(output, { recursive: true });
    try {
        const page = await browser.newPage({ viewport: { width: 1920, height: 1080 } });
        const markup = `<body class="page-endpoint-detail endpoint-drawer-open"><main style="height:2600px;padding:20px"><h1>Endpoint</h1><p>Page content behind the drawer</p></main><div class="event-drawer-backdrop endpoint-drawer-backdrop"></div><aside class="event-drawer endpoint-mini-drawer is-open" role="dialog" aria-modal="true"><header><div><span>Tarefa</span><h2>Atualização do agente</h2><p>Atualização aprovada</p></div><button>Fechar</button></header><div class="event-drawer-body">${drawerBody.innerHTML}</div></aside></body>`;
        const screenshots = [];
        for (const viewport of [{ width: 1920, height: 1080 }, { width: 1366, height: 768 }, { width: 390, height: 844 }]) {
            await page.setViewportSize(viewport);
            await page.setContent(`<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width"><style>${css}</style>${markup}`);
            await page.locator('.endpoint-job-drawer-section').filter({ hasText: 'Payload sanitizado' }).evaluate(node => node.open = true);
            const before = await page.evaluate(() => ({
                scrollY: scrollY,
                pageWidth: document.documentElement.scrollWidth,
                viewportWidth: innerWidth,
                drawer: document.querySelector('.endpoint-mini-drawer').getBoundingClientRect(),
                headerTop: document.querySelector('.endpoint-mini-drawer > header').getBoundingClientRect().top,
                body: { scrollWidth: document.querySelector('.endpoint-mini-drawer .event-drawer-body').scrollWidth, clientWidth: document.querySelector('.endpoint-mini-drawer .event-drawer-body').clientWidth, scrollHeight: document.querySelector('.endpoint-mini-drawer .event-drawer-body').scrollHeight, clientHeight: document.querySelector('.endpoint-mini-drawer .event-drawer-body').clientHeight },
                pres: [...document.querySelectorAll('.endpoint-mini-drawer pre')].map(pre => ({ scrollWidth: pre.scrollWidth, clientWidth: pre.clientWidth })),
                drawerWidth: document.querySelector('.endpoint-mini-drawer').getBoundingClientRect().width,
                overflowers: [...document.querySelectorAll('.endpoint-mini-drawer *')].filter(node => node.scrollWidth > node.clientWidth + 1).map(node => ({ tag: node.tagName, className: node.className, scroll: node.scrollWidth, client: node.clientWidth }))
            }));
            assert(before.pageWidth <= before.viewportWidth, `no page horizontal overflow at ${viewport.width}`);
            assert(before.body.scrollWidth <= before.body.clientWidth, `no drawer horizontal overflow at ${viewport.width}: ${JSON.stringify(before)}`);
            assert(before.pres.every(pre => pre.scrollWidth <= pre.clientWidth), `long output wraps at ${viewport.width}: ${JSON.stringify(before)}`);
            assert.equal(await page.locator('.endpoint-mini-drawer .event-drawer-body').evaluate(node => getComputedStyle(node).overflowY), 'auto', 'drawer body is the sole vertical scroll surface');
            assert.equal(await page.locator('.endpoint-mini-drawer pre').first().evaluate(node => getComputedStyle(node).overflowY), 'visible', 'JSON does not create nested scrolling');
            await page.locator('.endpoint-mini-drawer .event-drawer-body').hover({ position: { x: 24, y: 260 } });
            await page.mouse.wheel(0, 800);
            await page.waitForTimeout(80);
            const scrolled = await page.evaluate(() => ({
                scrollY,
                bodyTop: document.querySelector('.event-drawer-body').scrollTop,
                headerTop: document.querySelector('.endpoint-mini-drawer > header').getBoundingClientRect().top
            }));
            assert.equal(scrolled.scrollY, before.scrollY, 'background page stays locked');
            assert(scrolled.bodyTop > 0, `drawer content scrolls: ${JSON.stringify({ before, scrolled })}`);
            assert.equal(scrolled.headerTop, before.headerTop, 'drawer heading remains fixed');
            await page.screenshot({ path: path.join(output, `job-drawer-${viewport.width}.png`) });
            screenshots.push(`${viewport.width}x${viewport.height}`);
        }
        console.log(`endpoint job drawer visual: locked page, single scroll, wrapped JSON at ${screenshots.join(', ')}; screenshots=${output}`);
    } finally {
        await browser.close();
    }
}

main().catch(error => { console.error(error); process.exitCode = 1; });
