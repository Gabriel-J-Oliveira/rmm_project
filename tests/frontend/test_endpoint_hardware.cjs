const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '../../static/js/endpoint_detail.js'), 'utf8');
const escapeHtml = value => String(value).replace(/[&<>"']/g, ch => ({'&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;', "'":'&#39;'}[ch]));
function extract(name, next) { return source.slice(source.indexOf('    function ' + name + '('), source.indexOf('    function ' + next + '(')); }
const context = {
    escapeHtml,
    asObject: value => value && typeof value === 'object' ? value : {},
    asArray: value => Array.isArray(value) ? value : [],
    icon: () => '',
    formatDate: value => value || 'Desconhecido',
    emptyState: () => '<div class="empty-state"></div>',
    renderDiskList: () => '<div class="endpoint-disk-table"><article class="disk-row">Disco de teste</article></div>'
};
vm.createContext(context);
vm.runInContext(extract('factList', 'actionButton') + extract('renderHardwareEvidence', 'softwareCounters'), context);
const inv = {
    cpuDetails: {max_clock_mhz: 3600, socket: 'Synthetic socket'},
    memoryDetails: {total_bytes: 17179869184, modules: [{part_number:'<img src=x onerror=alert(1)>', serial_number:'<script>alert(1)</script>', memory_type:'ddr4', speed_mhz:3200, configured_speed_mhz:2666}]},
    baseboardDetails: {manufacturer:'<b>Untrusted OEM</b>'}, batteryDetails: {present:false},
    sectionStates: {memory: {status:'failed', last_attempt_at:'2026-10-29T12:00:00Z'}}
};
const evidence = context.renderHardwareEvidence(inv);
const html = Object.values(evidence).join('');
assert(html.includes('3.600 GHz / 3600 MHz'));
assert(html.includes('16.00 GiB'));
assert(html.includes('Nao presente'));
assert(html.includes('failed / 2026-10-29T12:00:00Z'));
for (const unsafe of ['<img ', '<script>', '<b>Untrusted']) assert(!html.includes(unsafe));
for (const safe of ['&lt;img', '&lt;script&gt;', '&lt;b&gt;Untrusted']) assert(html.includes(safe));
assert(context.renderHardwareEvidence({}).battery.includes('Desconhecida'));
assert(source.includes('DNS configurado na interface'));
assert(source.includes('adapter.dns_servers || adapter.dns'));
assert(source.includes('adapter.ipv6_addresses'));
assert(source.includes('adapter.link_speed / 1000000'));

const inventoryHtml = context.renderInventory({
    os: 'Windows 11 Pro', domain: 'control.local', ip: '192.0.2.10', user: 'synthetic.user',
    agent: {version: '0.1.1.0-test'}, disks: [{name: 'C:', totalGb: 256}], inventory: {
        ...inv, osVersion: 'synthetic', build: 'test-build', architecture: 'x64', manufacturer: 'Synthetic OEM',
        model: 'Synthetic model', serial: 'SYNTHETIC-01', cpu: 'Synthetic CPU', memoryGb: 16,
        primaryIp: '192.0.2.10', primaryMac: '00-11-22-33-44-55', defaultGateway: '192.0.2.1',
        dnsServers: ['192.0.2.2'], macs: ['00-11-22-33-44-55'], adapters: [{name: 'Synthetic NIC', ipv4_addresses: ['192.0.2.10']}]
    }
});
assert(inventoryHtml.includes('endpoint-inventory-column'));
assert(inventoryHtml.includes('endpoint-inventory-secondary-grid'));
assert(!inventoryHtml.includes('endpoint-span-2'));
assert(inventoryHtml.indexOf('Bateria') < inventoryHtml.indexOf('Memoria fisica'));
assert(inventoryHtml.indexOf('Rede e coleta') < inventoryHtml.indexOf('Discos'));
console.log('PASS: hardware/network/disk renderers, evidence order, adapters, original CPU units, unknowns and XSS');

if (process.argv.includes('--visual')) {
    (async () => {
        const {chromium} = require('playwright');
        const browser = await chromium.launch({headless:true, channel:'chrome'});
        try {
            const page = await browser.newPage();
            const css = fs.readFileSync(path.join(__dirname, '../../static/css/nightowl.css'), 'utf8');
            const screenshotDir = path.join(process.env.TEMP, 'nightowl-dec02-visual');
            fs.mkdirSync(screenshotDir, {recursive:true});
            const safe = JSON.parse(JSON.stringify(inv));
            safe.memoryDetails.modules[0].part_number = 'SYNTHETIC-PART-3200';
            safe.memoryDetails.modules[0].serial_number = 'SYNTHETIC-01';
            safe.baseboardDetails.manufacturer = 'Synthetic OEM';
            for (const [width,height] of [[1920,1080],[1366,900],[390,844]]) {
                await page.setViewportSize({width,height});
                const detail = {
                    os: 'Windows 11 Pro', domain: 'control.local', ip: '192.0.2.10', user: 'synthetic.user',
                    agent: {version: '0.1.1.0-test'}, disks: [{name: 'C:', totalGb: 256}], inventory: {
                        ...safe, osVersion: 'synthetic', build: 'test-build', architecture: 'x64', manufacturer: 'Synthetic OEM',
                        model: 'Synthetic model', serial: 'SYNTHETIC-01', cpu: 'Synthetic CPU', memoryGb: 16,
                        primaryIp: '192.0.2.10', primaryMac: '00-11-22-33-44-55', defaultGateway: '192.0.2.1',
                        dnsServers: ['192.0.2.2'], macs: ['00-11-22-33-44-55'], adapters: [{name: 'Synthetic NIC', ipv4_addresses: ['192.0.2.10']}]
                    }
                };
                await page.setContent('<!doctype html><meta charset="utf-8"><style>' + css + '</style><body class="page-endpoint-detail"><main class="endpoint-detail-shell" style="padding:16px"><h1>Inventario - fixture sintetica DEC-02</h1><section data-endpoint-tab-panel="inventory"><div class="endpoint-inventory-rendered">' + context.renderInventory(detail) + '</div></section></main></body>');
                await page.screenshot({path:path.join(screenshotDir, 'hardware-' + width + '.png'),fullPage:true});
                const overflow = await page.evaluate(() => document.documentElement.scrollWidth > innerWidth + 1);
                assert(!overflow, 'Horizontal overflow at ' + width);
                assert(await page.locator('dd').count() >= 35);
                assert(await page.locator('.endpoint-hardware-evidence dd').evaluateAll(items => items.every(item =>
                    item.scrollWidth <= item.clientWidth + 1 && getComputedStyle(item).whiteSpace !== 'nowrap')),
                    'Hardware text truncated at ' + width);
                const layout = await page.evaluate(() => {
                    const network = document.querySelector('.endpoint-inventory-network-card').getBoundingClientRect();
                    const disks = document.querySelector('.endpoint-disk-panel').getBoundingClientRect();
                    const batteryCard = Array.from(document.querySelectorAll('.endpoint-hardware-evidence')).find(card => card.textContent.includes('Bateria')).getBoundingClientRect();
                    const memoryCard = Array.from(document.querySelectorAll('.endpoint-hardware-evidence')).find(card => card.textContent.includes('Memoria fisica')).getBoundingClientRect();
                    const columns = Array.from(document.querySelectorAll('.endpoint-inventory-column'));
                    return {
                        columns: getComputedStyle(document.querySelector('.endpoint-inventory-grid')).gridTemplateColumns.trim().split(/\s+/).length,
                        secondaryColumns: getComputedStyle(document.querySelector('.endpoint-inventory-secondary-grid')).gridTemplateColumns.trim().split(/\s+/).length,
                        inventoryCardsFillColumns: columns.every(column => Array.from(column.children).every(card => Math.abs(card.getBoundingClientRect().width - column.getBoundingClientRect().width) < 1)),
                        network: {x: network.x, y: network.y, width: network.width},
                        disks: {x: disks.x, y: disks.y, width: disks.width},
                        batteryY: batteryCard.y,
                        memoryY: memoryCard.y
                    };
                });
                if (width > 720) {
                    assert.equal(layout.columns, 2, 'Inventory columns at ' + width);
                    assert.equal(layout.secondaryColumns, 2, 'Network/disks columns at ' + width);
                    assert(layout.inventoryCardsFillColumns, 'Inventory cards should fill their columns at ' + width);
                    assert.equal(layout.network.y, layout.disks.y, 'Network and disks must share a row at ' + width);
                    assert(layout.network.x < layout.disks.x, 'Network must be left of disks at ' + width);
                    assert(layout.network.width < 0.7 * width, 'Network card should not stretch across the page');
                    assert(layout.disks.height < layout.network.height, 'Disks should retain a content-based height at ' + width);
                    assert(layout.batteryY < layout.memoryY, 'Battery card should move above the memory evidence card');
                    const card = page.locator('.endpoint-inventory-network-card');
                    const before = await card.evaluate(el => {
                        const s=getComputedStyle(el); return [s.backgroundColor,s.borderColor,s.boxShadow,s.transform].join('|');
                    });
                    await card.hover();
                    const after = await card.evaluate(el => {
                        const s=getComputedStyle(el); return [s.backgroundColor,s.borderColor,s.boxShadow,s.transform].join('|');
                    });
                    assert.equal(after, before, 'Inventory cards must not change appearance on hover');
                } else {
                    assert.equal(layout.columns, 1, 'Inventory should stack on narrow screens');
                    assert.equal(layout.secondaryColumns, 1, 'Network/disks should stack on narrow screens');
                    assert(layout.disks.y > layout.network.y, 'Disks should follow network on narrow screens');
                }
            }
            console.log('PASS: visual fixture 1920x1080/1366x900/390x844; screenshots: ' + screenshotDir);
        } finally { await browser.close(); }
    })().catch(error => { console.error(error); process.exitCode=1; });
}
