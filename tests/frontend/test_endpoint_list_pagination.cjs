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
        row.checkbox = new Element();
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
        addEventListener() {}
    };
    const window = {};
    vm.runInNewContext(source, { document, window, navigator: {}, console });
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

test('pagination is hidden for other views; only summary cards keep hover', () => {
    const page = makePage();
    page.views[1].fire('click');
    assert.equal(page.pagination.hidden, true);
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
