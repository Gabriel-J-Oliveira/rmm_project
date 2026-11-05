const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '../../static/js/endpoint_detail.js'), 'utf8');
const escapeHtml = value => String(value).replace(/[&<>"']/g, ch => ({'&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;', "'":'&#39;'}[ch]));
function extract(name, next) { return source.slice(source.indexOf('    function ' + name + '('), source.indexOf('    function ' + next + '(')); }
const context = {escapeHtml, asObject: value => value && typeof value === 'object' ? value : {}, icon: () => '', formatDate: value => value || 'Desconhecido'};
vm.createContext(context);
vm.runInContext(extract('factList', 'actionButton') + extract('renderHardwareEvidence', 'renderInventory'), context);
const inv = {
    cpuDetails: {max_clock_mhz: 3600, socket: 'Synthetic socket'},
    memoryDetails: {total_bytes: 17179869184, modules: [{part_number:'<img src=x onerror=alert(1)>', serial_number:'<script>alert(1)</script>', memory_type:'ddr4', speed_mhz:3200, configured_speed_mhz:2666}]},
    baseboardDetails: {manufacturer:'<b>Untrusted OEM</b>'}, batteryDetails: {present:false},
    sectionStates: {memory: {status:'failed', last_attempt_at:'2026-10-29T12:00:00Z'}}
};
const html = context.renderHardwareEvidence(inv);
assert(html.includes('3.600 GHz / 3600 MHz'));
assert(html.includes('16.00 GiB'));
assert(html.includes('Nao presente'));
assert(html.includes('failed / 2026-10-29T12:00:00Z'));
for (const unsafe of ['<img ', '<script>', '<b>Untrusted']) assert(!html.includes(unsafe));
for (const safe of ['&lt;img', '&lt;script&gt;', '&lt;b&gt;Untrusted']) assert(html.includes(safe));
assert(context.renderHardwareEvidence({}).includes('Desconhecida'));
assert(source.includes('DNS configurado na interface'));
assert(source.includes('adapter.dns_servers || adapter.dns'));
assert(source.includes('adapter.ipv6_addresses'));
assert(source.includes('adapter.link_speed / 1000000'));
console.log('PASS: hardware renderer, original CPU units, RAM, absence/unknown, failure status, adapter DNS/IPv6/link, XSS');

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
            for (const [width,height] of [[1920,1080],[390,844]]) {
                await page.setViewportSize({width,height});
                await page.setContent('<!doctype html><meta charset="utf-8"><style>' + css + '</style><body class="page-endpoint-detail"><main class="endpoint-detail-shell" style="padding:16px"><h1>Inventario - fixture sintetica DEC-02</h1><div class="endpoint-inventory-grid">' + context.renderHardwareEvidence(safe) + '</div></main></body>');
                await page.screenshot({path:path.join(screenshotDir, 'hardware-' + width + '.png'),fullPage:true});
                const overflow = await page.evaluate(() => document.documentElement.scrollWidth > innerWidth + 1);
                assert(!overflow, 'Horizontal overflow at ' + width);
                assert(await page.locator('dd').count() >= 20);
                assert(await page.locator('.endpoint-hardware-evidence dd').evaluateAll(items => items.every(item =>
                    item.scrollWidth <= item.clientWidth + 1 && getComputedStyle(item).whiteSpace !== 'nowrap')),
                    'Hardware text truncated at ' + width);
                if (width <= 520) assert(await page.locator('.endpoint-hardware-evidence .rmm-info-grid').evaluateAll(items =>
                    items.every(item => getComputedStyle(item).gridTemplateColumns.trim().split(/\s+/).length === 1)),
                    'Hardware facts must use one column on narrow screens');
            }
            console.log('PASS: visual fixture 1920x1080/390x844; screenshots: ' + screenshotDir);
        } finally { await browser.close(); }
    })().catch(error => { console.error(error); process.exitCode=1; });
}
