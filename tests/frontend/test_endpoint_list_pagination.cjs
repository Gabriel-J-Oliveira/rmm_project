const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const rootPath = path.resolve(__dirname, '..', '..');
const source = fs.readFileSync(path.join(rootPath, 'static/js/endpoint_list.js'), 'utf8');
const css = fs.readFileSync(path.join(rootPath, 'static/css/nightowl.css'), 'utf8');

class Element {
    constructor(dataset = {}) {
        this.dataset = dataset;
        this.hidden = false;
        this.checked = false;
        this.disabled = false;
        this.indeterminate = false;
        this.value = '';
        this.textContent = '';
        this.listeners = {};
        this.classList = { toggle() {} };
    }
    addEventListener(name, callback) {
        this.listeners[name] = callback;
    }
    fire(name) {
        if (this.listeners[name]) this.listeners[name]({ target: this, stopPropagation() {}, preventDefault() {} });
    }
    querySelector(selector) {
        return selector === '[data-endpoint-select]' ? this.checkbox || null : null;
    }
}

function makePage(count = 64) {
    const root = new Element();
    const bulkBar = new Element();
    const selectedCount = new Element();
    const selectAll = new Element();
    const pagination = new Element();
    const pageSize = new Element();
    pageSize.value = '10';
    const pageSummary = new Element();
    const pageCurrent = new Element();
    const views = [new Element({ endpointView: 'table' }), new Element({ endpointView: 'cards' })];
    const panels = [new Element({ endpointViewPanel: 'table' }), new Element({ endpointViewPanel: 'cards' })];
    const controls = {
        '[data-endpoint-page-first]': new Element(),
        '[data-endpoint-page-previous]': new Element(),
        '[data-endpoint-page-next]': new Element(),
        '[data-endpoint-page-last]': new Element()
    };
    const rows = Array.from({ length: count }, (_, index) => {
        const row = new Element({ endpoint: `host-${index + 1}` });
        row.checkbox = new Element({ fleetEndpointId: `id-${index + 1}` });
        return row;
    });
    const rowChecks = rows.map(row => row.checkbox);
    const selectors = {
        '[data-endpoint-bulk-bar]': bulkBar,
        '[data-endpoint-selected-count]': selectedCount,
        '[data-endpoint-select-all]': selectAll,
        '[data-endpoint-pagination]': pagination,
        '[data-endpoint-page-size]': pageSize,
        '[data-endpoint-pagination-summary]': pageSummary,
        '[data-endpoint-page-current]': pageCurrent,
        '[data-endpoint-toast]': null
    };
    const rootLists = {
        '[data-endpoint-view]': views,
        '[data-endpoint-view-panel]': panels,
        '[data-endpoint-select]': rowChecks,
        '[data-endpoint-row]': rows,
        'tr[data-endpoint-row]': rows,
        '[data-endpoint-menu-toggle]': [],
        '.endpoint-action-popover': [],
        '[data-bulk-endpoint-action]': [],
        '[data-endpoint-action]': []
    };
    root.querySelector = selector => controls[selector] || selectors[selector] || null;
    root.querySelectorAll = selector => rootLists[selector] || [];
    const document = {
        querySelector: selector => selector === '[data-endpoint-ops]' ? root : selectors[selector] || null,
        querySelectorAll: () => [],
        addEventListener() {},
        dispatchEvent() {}
    };
    const window = {
        location: { href: 'http://localhost/endpoints/' },
        history: { pushState() {} },
        addEventListener() {},
        clearTimeout,
        setTimeout
    };
    vm.runInNewContext(source, { document, window, navigator: {}, console, URL, URLSearchParams, FormData, CustomEvent });
    return { rows, rowChecks, selectAll, selectedCount, bulkBar, pagination, pageSize,
        pageSummary, pageCurrent, controls, views };
}

test('page size stays within 50 and selection survives page navigation', () => {
    const page = makePage();
    assert.equal(page.rows.filter(row => !row.hidden).length, 10);
    assert.equal(page.pageSummary.textContent, 'Exibindo 1-10 de 64 endpoints');

    page.selectAll.checked = true;
    page.selectAll.fire('change');
    page.controls['[data-endpoint-page-next]'].fire('click');
    assert.equal(page.rows.filter(row => !row.hidden).length, 10);
    assert.equal(page.selectedCount.textContent, '10');
    assert.equal(page.selectAll.checked, false);

    page.pageSize.value = '50';
    page.pageSize.fire('change');
    assert.equal(page.rows.filter(row => !row.hidden).length, 50);
    assert.equal(page.selectedCount.textContent, '10');
    assert.equal(page.pageCurrent.textContent, '1 / 2');

    page.controls['[data-endpoint-page-last]'].fire('click');
    assert.equal(page.rows.filter(row => !row.hidden).length, 14);
    page.selectAll.checked = true;
    page.selectAll.fire('change');
    assert.equal(page.selectedCount.textContent, '24');
});

test('pagination follows the current result set in every lazy view; only summary cards keep hover', () => {
    const page = makePage();
    page.views[1].fire('click');
    assert.equal(page.pagination.hidden, false);
    assert.doesNotMatch(css, /\.endpoint-card-item:hover\s*\{/);
    assert.match(css, /\.panel:not\(\.endpoint-ops-filter-panel\):not\(\.endpoint-ops-panel\):hover/);
    assert.match(css, /\.endpoint-metric-card:hover/);
    assert.match(css, /\.endpoint-ops-table-wrap\s*\{[^}]*overflow-y:\s*visible/s);
    assert.match(css, /\.endpoint-table tbody tr:hover td\s*\{\s*background:\s*transparent;/);
});

test('large endpoint panels remain stationary while summary cards and controls retain hover', async () => {
    const { chromium } = require('playwright');
    const browser = await chromium.launch({ channel: process.env.PLAYWRIGHT_CHANNEL || 'chrome', headless: true });
    try {
        const page = await browser.newPage();
        await page.setContent(`<style>${css}</style><body class="page-endpoints">
            <a class="metric-card endpoint-metric-card" href="#" style="width:180px">Online</a>
            <section class="panel endpoint-ops-filter-panel" style="padding:30px">Filtros
                <div class="endpoint-quick-filters"><a href="#">Todos</a></div>
            </section>
            <section class="panel endpoint-ops-panel" style="padding:30px">Parque operacional
                <div class="endpoint-row-actions"><button>Abrir</button></div>
                <article class="endpoint-card-item attention-critical">Endpoint</article>
            </section></body>`);
        const appearance = selector => page.locator(selector).evaluate(element => {
            const style = getComputedStyle(element);
            return [style.backgroundColor, style.borderColor, style.boxShadow, style.transform];
        });
        for (const viewport of [{ width: 1920, height: 1080 }, { width: 390, height: 844 }]) {
            await page.setViewportSize(viewport);
            for (const selector of ['.endpoint-ops-filter-panel', '.endpoint-ops-panel', '.endpoint-card-item']) {
                await page.mouse.move(0, 0);
                await page.waitForTimeout(220);
                const before = await appearance(selector);
                await page.locator(selector).hover({ position: { x: 10, y: 10 } });
                await page.waitForTimeout(220);
                assert.deepEqual(await appearance(selector), before, selector);
            }
            for (const selector of ['.endpoint-metric-card', '.endpoint-quick-filters a', '.endpoint-row-actions button']) {
                await page.mouse.move(0, 0);
                await page.waitForTimeout(220);
                const before = await appearance(selector);
                await page.locator(selector).hover();
                await page.waitForTimeout(220);
                assert.notDeepEqual(await appearance(selector), before, selector);
            }
        }
    } finally { await browser.close(); }
});

test('25 rows, navigation boundaries and page-only deselection', () => {
    const page = makePage();
    page.pageSize.value = '25';
    page.pageSize.fire('change');
    assert.equal(page.rows.filter(row => !row.hidden).length, 25);
    assert.equal(page.controls['[data-endpoint-page-previous]'].disabled, true);
    page.selectAll.checked = true;
    page.selectAll.fire('change');
    page.controls['[data-endpoint-page-next]'].fire('click');
    page.selectAll.checked = true;
    page.selectAll.fire('change');
    assert.equal(page.selectedCount.textContent, '50');
    page.selectAll.checked = false;
    page.selectAll.fire('change');
    assert.equal(page.selectedCount.textContent, '25');
    page.controls['[data-endpoint-page-first]'].fire('click');
    assert.equal(page.selectAll.checked, true);
    page.controls['[data-endpoint-page-last]'].fire('click');
    assert.equal(page.pageSummary.textContent, 'Exibindo 51-64 de 64 endpoints');
    assert.equal(page.controls['[data-endpoint-page-next]'].disabled, true);
    page.views[1].fire('click');
    page.views[0].fire('click');
    assert.equal(page.pagination.hidden, false);
    assert.equal(page.pageCurrent.textContent, '3 / 3');
});

test('empty and single-page tables do not expose invalid navigation', () => {
    const empty = makePage(0);
    assert.equal(empty.pageSummary.textContent, 'Nenhum endpoint para exibir');
    assert.equal(empty.pageCurrent.textContent, '0 / 0');
    assert.equal(empty.pagination.hidden, true);
    assert.ok(Object.values(empty.controls).every(button => button.disabled));
    const single = makePage(8);
    assert.equal(single.rows.filter(row => !row.hidden).length, 8);
    assert.equal(single.pagination.hidden, true);
    single.pageSize.value = '100';
    single.pageSize.fire('change');
    assert.equal(single.rows.filter(row => !row.hidden).length, 8);
    assert.equal(single.pageCurrent.textContent, '1 / 1');
});

test('server paging keeps selected endpoint IDs and requests non-table views only when opened', async () => {
    const { chromium } = require('playwright');
    const browser = await chromium.launch({ channel: process.env.PLAYWRIGHT_CHANNEL || 'chrome', headless: true });
    try {
        const page = await browser.newPage();
        await page.route('http://localhost/**', route => route.fulfill({ status: 200, body: '<html><body></body></html>' }));
        await page.goto('http://localhost/endpoints/');
        await page.setContent(`<form data-endpoint-filter-form><input name="q" value=""><input type="hidden" name="quick" value="all"></form>
            <strong data-endpoint-visible-count>21</strong><strong data-endpoint-count="total_endpoints">21</strong>
            <section data-endpoint-ops data-endpoint-server-paginated="true" data-endpoint-url="/endpoints/" data-endpoint-result-count="21" data-endpoint-page="1" data-endpoint-page-count="3" data-endpoint-page-size-value="10">
                <div data-endpoint-bulk-bar hidden><span data-endpoint-selected-count>0</span><span data-endpoint-selection-hidden hidden></span><button data-endpoint-selection-clear></button></div>
                <input type="checkbox" data-endpoint-select-all>
                <button data-endpoint-view="table" class="is-active"></button><button data-endpoint-view="cards"></button><button data-endpoint-view="health"></button>
                <div data-endpoint-view-panel="table" data-endpoint-loaded="true"><table><tbody data-endpoint-table-body><tr data-endpoint-row data-endpoint="host-1"><td><input type="checkbox" data-endpoint-select data-fleet-endpoint-id="id-1"></td></tr></tbody></table></div>
                <div data-endpoint-view-panel="cards" data-endpoint-loaded="false" hidden></div><div data-endpoint-view-panel="health" data-endpoint-loaded="false" hidden></div>
                <nav data-endpoint-pagination><span data-endpoint-pagination-summary></span><select data-endpoint-page-size><option value="10" selected>10</option><option value="25">25</option><option value="50">50</option></select>
                    <button data-endpoint-page-first></button><button data-endpoint-page-previous></button><span data-endpoint-page-current>1 / 3</span><button data-endpoint-page-next></button><button data-endpoint-page-last></button></nav>
            </section><div data-endpoint-toast hidden></div>`);
        await page.evaluate(() => {
            window.requests = [];
            window.fetch = async (input) => {
                const url = new URL(input, location.href);
                window.requests.push(url.pathname + url.search);
                const pageNumber = Number(url.searchParams.get('page') || 1);
                const view = url.searchParams.get('view') || 'table';
                const html = view === 'cards'
                    ? '<article class="endpoint-card-item">host-card</article>'
                    : `<tr data-endpoint-row data-endpoint="host-${pageNumber * 10 + 1}"><td><input type="checkbox" data-endpoint-select data-fleet-endpoint-id="id-${pageNumber * 10 + 1}"></td></tr>`;
                return new Response(JSON.stringify({html, view, page: pageNumber, page_count: 3, page_size: 10, result_count: 21,
                    endpoint_ids: [`id-${pageNumber * 10 + 1}`], total_endpoints: 21, online_count: 20, offline_critical_count: 1,
                    agent_outdated_count: 0, security_attention_count: 0, disk_critical_count: 0}), {headers: {'Content-Type': 'application/json'}});
            };
        });
        await page.addScriptTag({ content: source });
        await page.locator('[data-endpoint-select]').check();
        await page.locator('[data-endpoint-select]').dispatchEvent('change', { bubbles: true });
        await page.locator('[data-endpoint-page-next]').click();
        await page.waitForFunction(() => document.querySelector('[data-endpoint-page-current]').textContent === '2 / 3');
        assert.equal(await page.locator('[data-endpoint-selected-count]').textContent(), '1');
        assert.equal(await page.locator('[data-endpoint-selection-hidden]').textContent(), '1 fora da página atual');
        assert.match(await page.evaluate(() => window.requests[0]), /page=2/);

        await page.locator('[data-endpoint-view="cards"]').click();
        await page.waitForSelector('.endpoint-card-item');
        assert.equal(await page.locator('[data-endpoint-view-panel="cards"]').isVisible(), true);
        assert.equal(await page.locator('[data-endpoint-view-panel="table"]').isVisible(), false);
        assert.match(await page.evaluate(() => window.requests.at(-1)), /view=cards/);
        assert.deepEqual(await page.evaluate(() => window.NightOwlEndpointSelection.getIds()), ['id-1']);
    } finally { await browser.close(); }
});
