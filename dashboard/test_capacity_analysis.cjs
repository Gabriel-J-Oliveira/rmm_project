// Synthetic browser fixtures only; no real endpoint or production request.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require('playwright');
const read = file => fs.readFileSync(path.join(__dirname, '..', file), 'utf8');

(async () => {
  const html = read('templates/dashboard/capacity.html')
    .replace(/\{%[^%]*%\}/g, '').replace(/\{\{[^}]*\}\}/g, '')
    .replace('data-overview-url=""', 'data-overview-url="/api/hardware-inventory/overview/"')
    .replace(/data-detail-template=""/, 'data-detail-template="/api/hardware-inventory/00000000-0000-4000-8000-000000000000/"');
  const browserHtml = `<!doctype html><html><head><meta charset="utf-8"><style>${read('static/css/nightowl.css')}${read('static/css/hardware_inventory.css')}</style></head><body>${html}<script>${read('static/js/hardware_inventory.js')}</script></body></html>`;
  const rows = Array.from({length: 120}, (_, index) => ({
    id: `id-${index}`, hostname: `LAB-PC-${String(index).padStart(2, '0')}`, fqdn: `lab-pc-${index}.example.test`,
    status: index % 3 ? 'online' : 'offline', last_logged_user: `operator-${index}`,
    manufacturer: 'Example Maker', model: index % 2 ? 'Model A' : 'Model B', os_name: 'Windows 11', os_version: '24H2',
    cpu: {name: 'CPU Example', physical_cores: 8, logical_processors: 16},
    memory: {total_bytes: 16 * 1073741824, slots_total: 2, slots_used: 1, slots_free: 1, modules: []},
    max_disk_used_percent: 75, disk_count: 1, inventory_at: index === 0 ? 'invalid-timestamp' : '2026-11-11T12:00:00Z', inventory_stale: false,
    inventory_provenance: {collected_at: '2026-11-11T12:00:00Z', sections: {hardware: '2026-11-11T12:00:00Z'}},
    inventory_received_at: '2026-11-11T12:01:00Z', physical_disks: [], volumes: [], alerts: [],
    endpoint_url: `/dashboard/endpoints/id-${index}/`, alerts_url: '/alerts/?q=LAB'
  }));
  const payload = {generated_at: 'invalid-timestamp',
    counts: {endpoints: rows.length, online: 80, offline: 40, inventory_known: 119, inventory_missing: 1, critical_alerts: 0}, endpoints: rows};
  const payloadText = JSON.stringify(payload);
  let detailRequests = 0;
  const browser = await chromium.launch({headless: true, channel: process.env.PLAYWRIGHT_CHANNEL || undefined});
  try {
    const page = await browser.newPage({viewport: {width: 1440, height: 1000}});
    page.on('pageerror', error => console.error(`BROWSER_ERROR name=${error.name} message=${error.message}`));
    await page.addInitScript(() => performance.mark('hardware-page-start'));
    await page.route('https://hardware.test/**', route => {
      if (route.request().isNavigationRequest()) return route.fulfill({contentType: 'text/html', body: browserHtml});
      const url = route.request().url();
      if (url.includes('/api/hardware-inventory/overview/')) return route.fulfill({status: 200, contentType: 'application/json', body: payloadText});
      if (/\/api\/hardware-inventory\/id-\d+\/$/.test(new URL(url).pathname)) {
        if (++detailRequests === 1) return route.fulfill({status: 503, body: 'synthetic detail failure'});
        const id = new URL(url).pathname.split('/').filter(Boolean).at(-1);
        const row = rows.find(item => item.id === id) || rows[0];
        return route.fulfill({json: {endpoint: row, applications: [], alerts: [], alert_counts: {}}});
      }
      return route.fulfill({status: 204, body: ''});
    });
    await page.goto('https://hardware.test/hardware-inventory/');
    await page.waitForFunction(() => document.querySelectorAll('[data-table] tr').length > 0);
    const metrics = await page.evaluate(() => ({
      firstRenderMs: performance.now() - performance.getEntriesByName('hardware-page-start')[0].startTime,
      apiMockRoundTripMs: performance.getEntriesByType('resource').find(item => item.name.includes('/api/hardware-inventory/overview/'))?.duration ?? null,
      updatedText: document.querySelector('[data-updated]').textContent,
      firstInventoryCell: document.querySelector('[data-table] tr:first-child td:last-child').textContent,
    }));
    console.log(`LOAD_TEST endpoints=120 json_bytes=${Buffer.byteLength(payloadText)} api_mock_roundtrip_ms=${metrics.apiMockRoundTripMs?.toFixed(2) ?? 'n/a'} first_render_ms=${metrics.firstRenderMs.toFixed(2)}`);
    assert.equal(await page.locator('[data-table] tr').count(), 25);
    assert.equal(await page.locator('[data-kpis] article').count(), 6);
    assert.match(metrics.updatedText, /Sem evidência/);
    assert.match(metrics.firstInventoryCell, /Sem evidência/);
    assert.doesNotMatch(await page.locator('[data-hardware-inventory]').innerText(), /CPU p95|RAM p95|Pressão sustentada|Maiores consumidores/);
    await page.locator('[data-filter="status"]').selectOption('offline');
    assert.equal(await page.locator('[data-result-count]').innerText(), '40 de 120 computadores');
    assert.equal(await page.locator('[data-table] tr').count(), 25);
    await page.locator('[data-filter="status"]').selectOption('');
    await page.locator('[data-search]').fill('lab-pc-30');
    assert.equal(await page.locator('[data-table] tr').count(), 1);
    assert.match(await page.locator('[data-table]').innerText(), /LAB-PC-30/);
    await page.locator('[data-search]').fill('');
    await page.locator('[data-size]').selectOption('10');
    assert.equal(await page.locator('[data-table] tr').count(), 10);
    await page.locator('[data-page="2"]').click();
    assert.match(await page.locator('[data-table]').innerText(), /LAB-PC-10/);
    await page.locator('[data-page="1"]').click();
    await page.locator('[data-table] tr').first().click();
    await page.waitForSelector('[data-detail-retry]');
    assert.match(await page.locator('[data-drawer-content]').innerText(), /Falha ao carregar os detalhes/);
    await page.locator('[data-detail-retry]').click();
    await page.waitForSelector('[data-drawer]:not([hidden])');
    assert.match(await page.locator('[data-drawer-content]').innerText(), /Processador|Memória física|Discos físicos|Origem e qualidade/);
    assert.match(await page.locator('[data-drawer-meta]').innerText(), /Sem evidência/);
    assert.doesNotMatch(await page.locator('[data-drawer-content]').innerText(), /99%|0 GB/);
    for (const width of [1280, 768, 390]) {
      await page.setViewportSize({width, height: 900});
      assert.ok(await page.locator('[data-hardware-inventory]').evaluate(el => el.scrollWidth <= window.innerWidth));
    }
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
