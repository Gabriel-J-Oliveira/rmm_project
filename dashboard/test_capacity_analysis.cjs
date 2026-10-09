// Synthetic API fixtures only: real DOM, no AD, endpoints or production requests.
const assert = require('node:assert/strict');
const test = require('node:test');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require('playwright');
const read = file => fs.readFileSync(path.join(__dirname, '..', file), 'utf8');
const now = '2026-10-06T18:00:00Z';
let browser;
test.before(async () => { browser = await chromium.launch({headless: true, channel: process.env.PLAYWRIGHT_CHANNEL || undefined}); });
test.after(async () => { await browser?.close(); });
function rows(count = 80) {
  return Array.from({length: count}, (_, i) => ({
    id: String(i), hostname: `PC-${String(i).padStart(3, '0')}`, fqdn: `pc-${i}.example.test`,
    last_ip: `192.0.2.${i + 1}`, last_logged_user: `user-${i}`, agent_version: 'rc45',
    os_name: i % 2 ? 'Windows 11' : 'Windows 10', os_version: '10', os_build: '19045',
    manufacturer: 'Maker', model: 'Model', cpu_name: 'Synthetic CPU', cpu_cores: 4,
    memory_total_bytes: (i % 2 ? 16 : 8) * 1073741824,
    cpu_p95: 20, memory_p95: 40, coverage: 95, received_samples: 273,
    cpu_capacity: 'NO_PRESSURE_OBSERVED', memory_capacity: 'NO_PRESSURE_OBSERVED',
    evidence: 'SUFFICIENT', alerts_critical: i === 0 ? 2 : 0, alerts_total: i === 0 ? 2 : 0,
    status: 'online', classifications: {attention: i === 0, sustained: false, observe: false, offline: false, insufficient: false},
    alert_types: [], processes: [], last_seen: now, first_seen: new Date(Date.parse(now) - (i + 1) * 3600000).toISOString(),
    inventory_at: now, telemetry_last_at: now, system_disk_used_percent: i % 2 ? 50 : 90,
    max_disk_used_percent: i % 2 ? 50 : 90, min_disk_free_bytes: 100 * 1073741824,
    endpoint_url: `/endpoints/${i}/`, alerts_url: '/alerts/'
  }));
}
async function setup(t, values = rows(), width = 1440, clock = false) {
  const page = await browser.newPage({viewport: {width, height: 1000}});
  t.after(() => page.close());
  if (clock) await page.clock.install({ time: new Date(now) });
  const errors = []; page.on('pageerror', e => errors.push(e.message));
  let template = read('templates/dashboard/capacity.html').split('{% block content %}')[1].split('{% endblock %}')[0];
  template = template.replace(/{% if demo %}[\s\S]*?{% endif %}/g, '').replace(/{% url 'api-capacity-overview' %}/g, '/overview/').replace(/{%[^%]*%}/g, '');
  const html = `<!doctype html><html><head><meta charset="utf-8"><style>${read('static/css/nightowl.css')}${read('static/css/capacity.css')}body{padding:24px}</style></head><body>${template}<script>${read('static/js/capacity.js')}</script></body></html>`;
  await page.route('https://capacity.test/**', route => route.request().isNavigationRequest() ? route.fulfill({contentType: 'text/html', body: html}) : route.fulfill({json: {demo: false, generated_at: now, endpoints: values, counts: {}}}));
  await page.goto('https://capacity.test/');
  await page.waitForFunction(() => document.querySelector('[data-filter-count]').textContent.includes('endpoints'));
  t.after(() => assert.deepEqual(errors, []));
  return page;
}
async function add(page, field, operator, value, end) {
  await page.locator('[data-filter-field]').selectOption(field);
  await page.locator('[data-filter-operator]').selectOption({label: operator});
  await page.locator('[data-filter-value]').fill(String(value));
  if (end !== undefined) await page.locator('[data-filter-end]').fill(String(end));
  await page.locator('[data-filter-form] button[type=submit]').click();
}
test('new endpoints without telemetry remain visible and absence differs from partial evidence', async t => {
  const values = rows(3);
  values[0] = {...values[0], hostname: 'CS-CVEL-0253', received_samples: 0,
    telemetry_last_at: null, cpu_p95: null, memory_p95: null, coverage: 0,
    evidence: 'INSUFFICIENT', classifications: {...values[0].classifications, insufficient: true}};
  values[1] = {...values[1], hostname: 'CS-CVEL-0254', received_samples: 2, evidence: 'PARTIAL'};
  values[2] = {...values[2], received_samples: 0, evidence: 'INSUFFICIENT'};
  const page = await setup(t, values, 1920);
  const table = page.locator('[data-table-body]');
  assert.match(await table.textContent(), /CS-CVEL-0253/);
  assert.match(await table.textContent(), /Aguardando amostras de desempenho/);
  assert.match(await table.textContent(), /Parcial/);
  assert.match(await table.textContent(), /Sem amostras no período/);
  await page.locator('[data-search]').fill('CS-CVEL-0253');
  assert.equal(await table.locator('tr').count(), 1);
  const cells = await table.locator('tr td').allTextContents();
  assert.equal(cells[8], '—'); assert.equal(cells[9], '—');
  assert.match(cells[6], /GB/);
  assert.doesNotMatch(await table.textContent(), /desabilitada/i);
});
test('workload cards retain selection, show safe meters and handle absent RAM reference', async t => {
  const values = rows(2);
  values[0].processes = Array.from({length: 7}, (_, i) => ({name: i === 0 ? '<img src=x onerror=alert(1)>' : `Process ${i}`, category: '<b>Category</b>', cpu_percent: 90 - i * 10, working_set_bytes: 1073741824}));
  values[1].memory_total_bytes = null;
  values[1].processes = [{name: 'Unknown RAM', category: 'Other', cpu_percent: 95, working_set_bytes: 1234}];
  const page = await setup(t, values, 1920);
  const cards = page.locator('[data-workloads] .capacity-workload');
  assert.equal(await cards.count(), 6);
  assert.equal(await cards.first().locator('.capacity-workload-name').textContent(), 'Unknown RAM');
  assert.equal(await cards.first().locator('.capacity-workload-meter.cpu span').evaluate(n => n.style.width), '95%');
  assert.match(await cards.first().textContent(), /Proporção indisponível/);
  assert.equal(await cards.nth(1).locator('.capacity-workload-meter.ram span').evaluate(n => n.style.width), '12.5%');
  assert.match(await cards.nth(1).locator('.capacity-workload-meter.ram').getAttribute('aria-label'), /memória física/);
  assert.equal(await page.locator('[data-workloads] img, [data-workloads] b').count(), 0);
  await page.locator('[data-search]').fill('PC-001');
  assert.equal(await cards.count(), 1);
  await page.locator('[data-search]').fill('not-found');
  assert.equal(await cards.count(), 0);
});
test('global search covers identity, IP, user, OS and hardware', async t => {
  const page = await setup(t);
  for (const [query, count] of [['PC-013', 1], ['192.0.2.14', 1], ['user-13', 1], ['Windows 10', 40], ['Maker', 80], ['pc-13.example.test', 1]]) {
    await page.locator('[data-search]').fill(query);
    assert.equal(await page.locator('[data-filter-count]').textContent(), `${count} de 80 endpoints`);
    assert.equal(await page.locator('[data-kpis] [data-filter=monitored] strong').textContent(), String(count));
    assert.equal(await page.locator('.capacity-dot').count(), count);
    assert.equal(await page.locator('[data-table-body] tr').count(), Math.min(25, count));
    assert.equal(await page.locator('[data-ram-distribution] strong').evaluateAll(nodes => nodes.reduce((sum, n) => sum + Number(n.textContent), 0)), count);
    assert.equal(await page.locator('[data-distribution] strong').evaluateAll(nodes => nodes.reduce((sum, n) => sum + Number(n.textContent), 0)), count);
  }
});
test('numeric filters AND, ranges, chips, clearing and missing values', async t => {
  const values = rows(); values[0].memory_total_bytes = null; values[2].max_disk_used_percent = null;
  const page = await setup(t, values);
  await add(page, 'ram', '<', 12);
  assert.match(await page.locator('[data-filter-count]').textContent(), /^39 /);
  await add(page, 'disk', '>', 85);
  assert.match(await page.locator('[data-filter-count]').textContent(), /^38 /);
  await add(page, 'os', 'contém', 'Windows 11');
  assert.match(await page.locator('[data-filter-count]').textContent(), /^0 /);
  await page.locator('[data-remove-filter="2"]').click();
  assert.match(await page.locator('[data-filter-count]').textContent(), /^38 /);
  await page.locator('[data-clear-analysis]').click();
  await add(page, 'ram', '>=', 16);
  assert.match(await page.locator('[data-filter-count]').textContent(), /^40 /);
  await page.locator('[data-clear-analysis]').click();
  await add(page, 'ram', 'entre', 8, 16);
  assert.match(await page.locator('[data-filter-count]').textContent(), /^79 /);
});
test('invalid numeric range does not create a filter', async t => {
  const page = await setup(t);
  await add(page, 'ram', 'entre', 16, 8);
  assert.equal(await page.locator('[data-remove-filter]').count(), 0);
  assert.match(await page.locator('[data-filter-error]').textContent(), /válido/);
});
test('pagination bounds DOM and filters reset page', async t => {
  const page = await setup(t);
  assert.equal(await page.locator('[data-table-body] tr').count(), 25);
  await page.locator('[data-page="2"]').first().click();
  assert.equal(await page.locator('[aria-current=page]').textContent(), '2');
  await add(page, 'ram', '>=', 16);
  assert.equal(await page.locator('[aria-current=page]').textContent(), '1');
  await page.locator('[data-clear-analysis]').click();
  await page.locator('[data-page-size]').selectOption('50');
  assert.equal(await page.locator('[data-table-body] tr').count(), 50);
  await page.locator('[data-page-size]').selectOption('100');
  assert.equal(await page.locator('[data-table-body] tr').count(), 80);
});
test('category filters reach every widget, empty values remain absent', async t => {
  const values = rows(3);
  values[0].status = 'offline'; values[0].classifications.offline = true;
  values[0].cpu_p95 = null; values[0].memory_p95 = null;
  values[0].max_disk_used_percent = null; values[0].system_disk_used_percent = null;
  const page = await setup(t, values);
  await page.locator('[data-kpis] [data-filter=offline]').click();
  assert.match(await page.locator('[data-filter-count]').textContent(), /^1 /);
  assert.equal(await page.locator('[data-attention] article').count(), 1);
  assert.equal(await page.locator('[data-recent] article').count(), 1);
  assert.equal(await page.locator('.capacity-dot').count(), 0);
  assert.match(await page.locator('[data-table-body]').textContent(), /—/);
  await page.locator('[data-clear-filter]').click();
  await add(page, 'status', 'igual', 'online');
  assert.match(await page.locator('[data-filter-count]').textContent(), /^2 /);
  await page.locator('[data-clear-analysis]').click();
  await add(page, 'last_seen', '<=', 1);
  assert.match(await page.locator('[data-filter-count]').textContent(), /^3 /);
});
test('recent ordering, badges, inventory and telemetry states', async t => {
  const values = rows(4); values[0].telemetry_last_at = null; values[0].inventory_at = null;
  values[1].first_seen = '2026-10-04T18:00:00Z'; values[1].telemetry_last_at = '2026-10-04T18:00:00Z';
  values[3].first_seen = '2026-09-01T18:00:00Z';
  const page = await setup(t, values);
  assert.equal(await page.locator('[data-recent] article').count(), 3);
  assert.match(await page.locator('[data-recent] article').first().textContent(), /PC-000.*NOVO.*Aguardando.*Sem amostras/s);
  assert.match(await page.locator('[data-recent] article').last().textContent(), /PC-001.*RECENTE.*Desatualizada/s);
  await page.locator('[data-search]').fill('PC-003');
  assert.equal(await page.locator('[data-recent-section]').isVisible(), false);
});
test('XSS values remain text in cards, table, charts and chips', async t => {
  const values = rows(1); values[0].hostname = '<img src=x onerror=alert(1)>'; values[0].last_logged_user = '<script>alert(1)</script>'; values[0].os_name = '<b>Financeiro</b>';
  const page = await setup(t, values);
  assert.equal(await page.locator('[data-capacity-root] img, [data-capacity-root] script, [data-os-distribution] b').count(), 0);
  assert.match(await page.locator('[data-table-body]').textContent(), /<img src=x/);
  await add(page, 'user', 'contém', '<script>');
  assert.match(await page.locator('[data-filter-chips]').textContent(), /<script>/);
  assert.equal(await page.locator('[data-filter-chips] script').count(), 0);
});
test('desktop/mobile carousels are single-row and page fits viewport', async t => {
  for (const width of [1440, 390]) {
    const values = rows(); values.forEach(r => { r.alerts_critical = 1; });
    const page = await setup(t, values, width);
    assert.equal(await page.locator('[data-grid]').count(), 0);
    const tops = await page.locator('[data-attention] article').evaluateAll(nodes => new Set(nodes.map(n => Math.round(n.getBoundingClientRect().top))).size);
    assert.equal(tops, 1);
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
    await page.emulateMedia({reducedMotion: 'reduce'});
    await page.locator('[data-scroll=attention][data-direction="1"]').click();
    assert.ok(await page.locator('[data-attention]').evaluate(n => n.scrollLeft) > 0);
    await page.screenshot({path: path.join(process.env.TEMP || '/tmp', `capacity-analysis-${width}.png`), fullPage: true});
  }
});

async function legend(page, chart) {
  return page.locator(`[data-executive-${chart}] .capacity-chart-legend li`).evaluateAll(nodes => Object.fromEntries(nodes.map(n => [n.querySelector('span:not(.swatch)').textContent, Number(n.querySelector('strong').textContent)])));
}
test('executive RAM/disk boundaries, C preference and OS grouping', async t => {
  const values = rows(6);
  [4, 8, 15.9, 16, 32, null].forEach((gb, i) => { values[i].memory_total_bytes = gb === null ? null : gb * 1073741824; });
  [69, 70, 85, 95, null, null].forEach((n, i) => { values[i].system_disk_used_percent = n; values[i].max_disk_used_percent = i === 4 ? 94 : i === 5 ? null : 99; });
  ['Microsoft Windows 10 Pro', 'Windows 11', 'Windows Server 2022', 'Linux', null, 'Windows 10'].forEach((name, i) => { values[i].os_name = name; });
  const page = await setup(t, values);
  assert.deepEqual(await legend(page, 'ram'), {'< 8 GB': 1, '8–15 GB': 2, '16–31 GB': 1, '32+ GB': 1, 'Não informado': 1});
  assert.deepEqual(await legend(page, 'disk'), {'< 70%': 1, '70–84%': 1, '85–94%': 2, '95%+': 1, 'Não informado': 1});
  assert.deepEqual(await legend(page, 'os'), {'Windows 10': 2, 'Windows 11': 1, 'Windows Server': 1, 'Outros / desconhecido': 2});
});
test('exclusive status, upgrade counts and all executive charts follow AND/search', async t => {
  const values = rows(4);
  values[0].status = 'offline'; values[0].classifications.offline = true;
  values[1].classifications.attention = true;
  values[2].classifications.insufficient = true;
  values[2].classifications.attention = true;
  values[2].inventory_at = null;
  const page = await setup(t, values);
  assert.deepEqual(await legend(page, 'status'), {'Online': 1, 'Offline': 1, 'Atenção (online)': 2, 'Desconhecido / outros': 0});
  const counts = await page.locator('[data-executive-upgrade] .capacity-executive-bar > strong').evaluateAll(nodes => nodes.map(n => Number(n.firstChild.textContent)));
  assert.deepEqual(counts, [2, 2, 2, 0, 1, 1, 1]);
  await add(page, 'ram', '<', 12); await add(page, 'os', 'contém', 'Windows 10');
  for (const chart of ['status', 'ram', 'disk', 'os']) assert.equal(await page.locator(`[data-executive-${chart}] .capacity-donut-center strong`).textContent(), '2');
  await page.locator('[data-search]').fill('PC-000');
  for (const chart of ['status', 'ram', 'disk', 'os']) assert.equal(await page.locator(`[data-executive-${chart}] .capacity-donut-center strong`).textContent(), '1');
  for (const chart of ['makers', 'models']) assert.equal(await page.locator(`[data-executive-${chart}] .capacity-executive-bar > strong`).first().evaluate(n => n.firstChild.textContent), '1');
});
test('missing inventory is not zero, empty filters clear every chart', async t => {
  const values = rows(2); values.forEach(r => { r.memory_total_bytes = null; r.system_disk_used_percent = null; r.max_disk_used_percent = null; r.manufacturer = null; r.model = null; });
  const page = await setup(t, values);
  for (const chart of ['ram', 'disk']) { assert.equal(await page.locator(`[data-executive-${chart}] .capacity-donut`).count(), 0); assert.match(await page.locator(`[data-executive-${chart}]`).textContent(), /Sem dados suficientes/); }
  assert.match(await page.locator('[data-executive-upgrade] .capacity-executive-bar').first().textContent(), /—.*Sem dados/);
  await page.locator('[data-search]').fill('no-match');
  for (const chart of ['status', 'ram', 'disk', 'os', 'makers', 'models', 'upgrade']) assert.match(await page.locator(`[data-executive-${chart}]`).textContent(), /Nenhum endpoint/);
});
test('top inventory is bounded, quantities preserved, untrusted labels escaped', async t => {
  const values = rows(12); values.forEach((r, i) => { r.manufacturer = `Maker ${i}`; r.model = `Model ${i}`; });
  values[0].manufacturer = '<img src=x onerror=alert(1)>'; values[0].model = '<script>alert(1)</script>';
  const page = await setup(t, values);
  for (const chart of ['makers', 'models']) {
    assert.equal(await page.locator(`[data-executive-${chart}] .capacity-executive-bar`).count(), 9);
    assert.match(await page.locator(`[data-executive-${chart}]`).textContent(), /Outros/);
    assert.equal(await page.locator(`[data-executive-${chart}] img, [data-executive-${chart}] script`).count(), 0);
    assert.equal(await page.locator(`[data-executive-${chart}] .capacity-executive-bar > strong`).evaluateAll(nodes => nodes.reduce((n, node) => n + Number(node.firstChild.textContent), 0)), 12);
  }
});
test('scrollbars hidden with native overflow, keyboard navigation remains available', async t => {
  const values = rows(30); values.forEach(r => { r.alerts_critical = 1; });
  const page = await setup(t, values);
  for (const key of ['attention', 'recent']) {
    const carousel = page.locator(`[data-${key}]`);
    assert.equal(await carousel.evaluate(n => getComputedStyle(n).scrollbarWidth), 'none');
    assert.equal(await carousel.evaluate(n => getComputedStyle(n, '::-webkit-scrollbar').display), 'none');
    await page.emulateMedia({reducedMotion: 'reduce'});
    const next = page.locator(`[data-scroll=${key}][data-direction="1"]`);
    await next.focus(); await page.keyboard.press('Enter');
    assert.ok(await carousel.evaluate(n => n.scrollLeft) > 0);
    await carousel.focus(); assert.equal(await carousel.evaluate(n => document.activeElement === n), true);
  }
});

test('automatic and manual refresh preserve filters, ordering and pagination', async t => {
  const values = rows();
  const page = await setup(t, values, 1920, true);
  await page.locator('[data-sort]').selectOption('hostname');
  await page.locator('[data-page="2"]').first().click();
  let requests = 0;
  page.on('request', request => { if (request.url().includes('/overview/')) requests++; });
  values[25].cpu_p95 = 66;
  await page.clock.fastForward(300001);
  await page.waitForFunction(() => document.querySelector('[data-table-body]').textContent.includes('66%'));
  assert.equal(requests, 1);
  assert.equal(await page.locator('[data-pagination] [aria-current=page]').textContent(), '2');
  assert.equal(await page.locator('[data-sort]').inputValue(), 'hostname');
  await page.locator('[data-search]').fill('PC-025');
  await page.locator('[data-refresh]').first().click();
  await page.waitForFunction(() => document.querySelector('[data-updated]').textContent.startsWith('Atualizado'));
  assert.equal(await page.locator('[data-search]').inputValue(), 'PC-025');
  assert.equal(await page.locator('[data-table-body] tr').count(), 1);
});

test('hidden tabs pause refresh and visibility return updates stale data', async t => {
  const page = await setup(t, rows(), 1440, true);
  let requests = 0;
  page.on('request', request => { if (request.url().includes('/overview/')) requests++; });
  await page.evaluate(() => Object.defineProperty(document, 'hidden', {configurable: true, value: true}));
  await page.clock.fastForward(600001);
  assert.equal(requests, 0);
  await page.evaluate(() => { Object.defineProperty(document, 'hidden', {configurable: true, value: false}); document.dispatchEvent(new Event('visibilitychange')); });
  await page.waitForFunction(() => document.querySelector('[data-updated]').textContent.startsWith('Atualizado'));
  assert.equal(requests, 1);
});

test('overlapping refresh clicks are serialized and stale periods discarded', async t => {
  const page = await setup(t);
  let release;
  const gate = new Promise(resolve => { release = resolve; });
  let inflight = 0, peak = 0, calls = 0;
  await page.route('**/overview/**', async route => {
    calls++; inflight++; peak = Math.max(peak, inflight);
    if (calls === 1) await gate;
    inflight--;
    await route.fulfill({json: {generated_at: now, endpoints: rows(calls === 1 ? 1 : 3), counts: {}}});
  });
  await page.locator('[data-refresh]').first().click();
  await page.locator('[data-refresh]').first().click();
  await page.locator('[data-capacity-period]').selectOption('48h');
  release();
  await page.waitForFunction(() => document.querySelector('[data-filter-count]').textContent === '3 de 3 endpoints');
  assert.equal(peak, 1);
  assert.equal(calls, 2);
});

test('refresh keeps the selected drawer endpoint and tab, close ignores late detail', async t => {
  const page = await setup(t, rows(2), 1440, true);
  await page.evaluate(() => { document.querySelector('[data-capacity-root]').dataset.detailTemplate = '/detail/00000000-0000-4000-8000-000000000000/'; });
  let detailCalls = 0;
  await page.route('**/detail/**', route => {
    detailCalls++;
    return route.fulfill({json: {endpoint: rows(2)[0], primary: null}});
  });
  await page.locator('[data-table-body] tr').first().click();
  await page.waitForFunction(() => document.querySelector('[data-drawer-title]').textContent === 'PC-000');
  await page.locator('[data-tab=hardware]').click();
  await page.clock.fastForward(300001);
  await page.waitForFunction(() => document.querySelector('[data-updated]').textContent.startsWith('Atualizado'));
  assert.equal(detailCalls, 2);
  assert.equal(await page.locator('[data-drawer]').isVisible(), true);
  assert.equal(await page.locator('[data-tab=hardware]').getAttribute('aria-selected'), 'true');
  assert.equal(await page.locator('[data-drawer-title]').textContent(), 'PC-000');
  await page.locator('[data-close-drawer]').click();
  await page.clock.fastForward(300001);
  assert.equal(detailCalls, 2);
  assert.equal(await page.locator('[data-drawer]').isVisible(), false);
});
