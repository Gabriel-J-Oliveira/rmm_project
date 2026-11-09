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

const context = { escapeHtml: value => String(value ?? '').replace(/[&<>"']/g, char => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[char])), icon: () => '<i aria-hidden="true"></i>' };
vm.runInNewContext(extract('    function actionButton(action, label, iconName) {', '    function isEndpointUninstalled(detail) {'), context);
vm.runInNewContext(extract('    function actionGroup(title, buttons, groupId) {', '    function jobResultLabel(job) {'), context);

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
        const markup = `<body class="page-endpoint-detail"><main class="endpoint-operational-row"><article class="panel endpoint-jobs-card">Fila de ações</article><article class="panel endpoint-actions-card"><header>Ações rápidas</header><div class="rmm-card__body">${context.renderQuickActions()}</div></article></main></body>`;
        await page.setContent(`<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width"><style>${css}
            body{margin:16px}.endpoint-operational-row{display:grid;grid-template-columns:minmax(0,7fr) minmax(260px,3fr);gap:16px}.endpoint-jobs-card{min-height:420px}
            @media(max-width:1320px){.endpoint-operational-row{grid-template-columns:minmax(0,1fr)}}
        </style>${markup}`);
        const inventory = page.locator('[data-action-group="inventory"]');
        assert.equal(await inventory.locator('button').count(), 3, 'all inventory actions remain present');
        const snapshots = [];
        for (const viewport of [{ width: 1920, height: 1080 }, { width: 1366, height: 768 }, { width: 900, height: 900 }, { width: 390, height: 844 }]) {
            await page.setViewportSize(viewport);
            const layout = await page.evaluate(() => {
                const buttons = [...document.querySelectorAll('[data-action-group="inventory"] button')].map(button => button.getBoundingClientRect());
                return {
                    mainWidth: document.querySelector('main').getBoundingClientRect().width,
                    actionsWidth: document.querySelector('.endpoint-actions-card').getBoundingClientRect().width,
                    documentWidth: document.documentElement.scrollWidth,
                    viewportWidth: innerWidth,
                    groupWidth: document.querySelector('[data-action-group="inventory"]').getBoundingClientRect().width,
                    groupParentWidth: document.querySelector('.endpoint-action-groups').getBoundingClientRect().width,
                    groupParentColumns: getComputedStyle(document.querySelector('.endpoint-action-groups')).gridTemplateColumns,
                    gridColumns: getComputedStyle(document.querySelector('[data-action-group="inventory"] > div')).gridTemplateColumns,
                    columns: new Set(buttons.map(rect => Math.round(rect.y))).size,
                    inside: buttons.every(rect => rect.left >= document.querySelector('[data-action-group="inventory"]').getBoundingClientRect().left - 1 && rect.right <= document.querySelector('[data-action-group="inventory"]').getBoundingClientRect().right + 1),
                    rowGaps: buttons.map(rect => Math.round(rect.y))
                };
            });
            assert.equal(layout.documentWidth <= layout.viewportWidth, true, `no horizontal overflow at ${viewport.width}px`);
            assert(layout.inside, `all action buttons stay within the card at ${viewport.width}px`);
            if (viewport.width === 1920) assert.equal(layout.columns, 1, `desktop places all inventory actions on one row: ${JSON.stringify(layout)}`);
            if (viewport.width === 1366) assert(layout.columns >= 1 && layout.columns <= 2, 'notebook adapts the inventory group to available width');
            if (viewport.width <= 900) assert(layout.columns >= 1, 'narrow layouts retain all actions without clipping');
            snapshots.push(`${viewport.width}px:${layout.columns}row(s)`);
            await page.screenshot({ path: path.join(output, `quick-actions-${viewport.width}.png`), fullPage: true });
        }
        console.log(`endpoint inventory quick actions: ${snapshots.join(', ')}; screenshots=${output}`);
    } finally {
        await browser.close();
    }
}

main().catch(error => { console.error(error); process.exitCode = 1; });
