const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');

async function main() {
    const { chromium } = require('playwright');
    const root = path.resolve(__dirname, '..', '..');
    const stylesheet = [
        'static/css/nightowl.css',
        'static/css/endpoint_actions_jobs.css',
        'static/css/endpoint_detail_refinement.css'
    ].map(file => fs.readFileSync(path.join(root, file), 'utf8')).join('\n');
    const script = fs.readFileSync(path.join(root, 'static/js/endpoint_detail.js'), 'utf8');
    const battery = script.slice(script.indexOf('battery: \'<article'), script.indexOf("\n        };", script.indexOf('battery: \'<article')));
    assert.equal((battery.match(/\{ label:/g) || []).length, 6, 'production battery renderer keeps all six facts');
    assert(!battery.includes('className:'), 'battery facts do not assign positional surface classes');

    const executablePath = process.env.NIGHTOWL_TEST_CHROME || [
        'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe',
        'C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe'
    ].find(candidate => fs.existsSync(candidate));
    const browser = await chromium.launch({ headless: true, ...(executablePath ? { executablePath } : {}) });
    const output = path.join(os.tmpdir(), 'nightowl-endpoint-detail-theme');
    fs.mkdirSync(output, { recursive: true });

    try {
        const page = await browser.newPage({ viewport: { width: 1440, height: 1000 }, deviceScaleFactor: 1 });
        const facts = [
            ['Presença', 'Presente'], ['Fabricante / modelo', 'Dell / Latitude'],
            ['Part Number / serial', 'DEMO-PN / DEMO-SERIAL'],
            ['Capacidade original / carga completa', '54.000 / 49.500 mWh'],
            ['Saúde / ciclos', '91% / 84'], ['Última coleta / tentativa', '10/11/2026 09:10 / coletado']
        ];
        const factMarkup = facts.map(([label, value], index) => `<div class="rmm-info-tile"><dt>${label}</dt><dd${index === 2 ? ' class="mono"' : ''}>${value}</dd></div>`).join('');
        const extraFacts = count => Array.from({ length: count }, (_, index) => `<div class="rmm-info-tile${count === 7 && index === 5 ? ' endpoint-fact-wide' : ''}"><dt>Campo ${index + 1}</dt><dd>${index === count - 1 ? 'Valor longo '.repeat(8) : 'Valor conhecido'}</dd></div>`).join('');
        await page.setContent(`<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width"><style>${stylesheet}</style><body class="page-endpoint-detail"><main class="endpoint-detail-shell"><nav class="endpoint-tabs"><button>Visão geral</button><button>Inventário</button><button>Monitoramento</button></nav><section class="endpoint-tab-panel"><article class="panel endpoint-dense-card endpoint-hardware-evidence"><header><h2>Bateria</h2></header><dl class="endpoint-fact-list rmm-info-grid">${factMarkup}</dl></article><article class="panel endpoint-dense-card"><header><h2>Five</h2></header><dl class="endpoint-fact-list rmm-info-grid five-facts">${extraFacts(5)}</dl></article><article class="panel endpoint-dense-card"><header><h2>Seven</h2></header><dl class="endpoint-fact-list rmm-info-grid seven-facts">${extraFacts(7)}</dl></article><button class="endpoint-quick-action-button">Coletar inventário</button></section></main></body>`);

        const tiles = page.locator('.endpoint-hardware-evidence .endpoint-fact-list > div');
        assert.equal(await tiles.count(), 6, 'battery card renders six tiles');
        const computed = await tiles.evaluateAll(elements => elements.map(element => {
            const style = getComputedStyle(element);
            return {
                background: style.backgroundColor,
                border: style.border,
                radius: style.borderRadius,
                padding: style.padding,
                minHeight: style.minHeight,
                gap: style.gap,
                display: style.display,
                shadow: style.boxShadow,
                transform: style.transform
            };
        }));
        assert(computed.every(style => JSON.stringify(style) === JSON.stringify(computed[0])), `all six battery tiles share computed surface and spacing: ${JSON.stringify(computed)}`);
        assert.notEqual(computed[0].background, 'rgba(0, 0, 0, 0)', 'tiles have a distinct inner surface');
        assert.equal(computed[0].minHeight, '54px', 'tiles use one shared minimum height');
        const otherFactStyles = await page.locator('.five-facts > div, .seven-facts > div').evaluateAll(elements => elements.map(element => {
            const style = getComputedStyle(element);
            return { background: style.backgroundColor, border: style.border, radius: style.borderRadius, padding: style.padding, minHeight: style.minHeight };
        }));
        assert(otherFactStyles.every(style => JSON.stringify(style) === JSON.stringify({
            background: computed[0].background,
            border: computed[0].border,
            radius: computed[0].radius,
            padding: computed[0].padding,
            minHeight: computed[0].minHeight
        })), 'five/seven fact groups share the same surface regardless of position');
        assert.equal(await page.locator('.seven-facts .endpoint-fact-wide').evaluate(element => getComputedStyle(element).gridColumn), '1 / -1', 'semantic wide class changes layout only');
        assert(await page.locator('.seven-facts dd').last().evaluate(element => element.scrollWidth <= element.clientWidth), 'long values wrap instead of clipping');

        const card = page.locator('.endpoint-hardware-evidence');
        const cardBefore = await card.evaluate(element => {
            const style = getComputedStyle(element);
            return { background: style.backgroundColor, border: style.borderColor, shadow: style.boxShadow, transform: style.transform };
        });
        await tiles.nth(0).hover();
        const tileAfterHover = await tiles.nth(0).evaluate(element => {
            const style = getComputedStyle(element);
            return { background: style.backgroundColor, border: style.border, radius: style.borderRadius, padding: style.padding, shadow: style.boxShadow, transform: style.transform };
        });
        assert.equal(tileAfterHover.background, computed[0].background, 'fact tile has no decorative hover');
        assert.equal(tileAfterHover.border, computed[0].border, 'fact tile border is stable on hover');
        assert.equal(tileAfterHover.radius, computed[0].radius, 'fact tile radius is stable on hover');
        assert.equal(tileAfterHover.padding, computed[0].padding, 'fact tile spacing is stable on hover');
        assert.deepEqual(await card.evaluate(element => {
            const style = getComputedStyle(element);
            return { background: style.backgroundColor, border: style.borderColor, shadow: style.boxShadow, transform: style.transform };
        }), cardBefore, 'main card is unchanged while hovering a fact');

        const action = page.locator('.endpoint-quick-action-button');
        const actionBefore = await action.evaluate(element => getComputedStyle(element).backgroundColor);
        await action.hover();
        const actionHovered = await action.evaluate(element => getComputedStyle(element).backgroundColor);
        assert.notEqual(actionHovered, actionBefore, 'interactive action button retains hover feedback');
        await page.screenshot({ path: path.join(output, 'inventory-battery-desktop.png'), fullPage: true });

        await page.setViewportSize({ width: 900, height: 900 });
        await page.screenshot({ path: path.join(output, 'inventory-battery-notebook.png'), fullPage: true });
        await page.setViewportSize({ width: 390, height: 844 });
        const narrow = await page.evaluate(() => ({
            documentWidth: document.documentElement.scrollWidth,
            viewportWidth: window.innerWidth,
            tileRects: [...document.querySelectorAll('.endpoint-hardware-evidence .endpoint-fact-list > div')].map(element => element.getBoundingClientRect().toJSON())
        }));
        assert(narrow.documentWidth <= narrow.viewportWidth, 'narrow endpoint layout has no horizontal overflow');
        assert(narrow.tileRects.every(rect => rect.width > 0 && rect.right <= narrow.viewportWidth + 1), 'all battery tiles fit the narrow viewport');
        await page.screenshot({ path: path.join(output, 'inventory-battery-mobile.png'), fullPage: true });

        await page.evaluate(() => document.body.classList.remove('page-endpoint-detail'));
        const outsideScope = await tiles.first().evaluate(element => ({
            background: getComputedStyle(element).backgroundColor,
            minHeight: getComputedStyle(element).minHeight
        }));
        assert.notEqual(outsideScope.background, computed[0].background, 'page-scoped theme does not style a non-endpoint page');
        console.log(`endpoint fact tile visual checks passed; screenshots=${output}`);
    } finally {
        await browser.close();
    }
}

main().catch(error => { console.error(error); process.exitCode = 1; });
