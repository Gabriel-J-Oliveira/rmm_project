/* Synthetic administrative UI regression; all network requests are intercepted. */
const fs = require("node:fs");
const path = require("node:path");
const assert = require("node:assert/strict");
const {chromium} = require("playwright");

(async () => {
    const fixture = JSON.parse(fs.readFileSync(0, "utf8"));
    const browser = await chromium.launch({headless: true, ...(process.env.NIGHTOWL_BROWSER_CHANNEL ? {channel: process.env.NIGHTOWL_BROWSER_CHANNEL} : {})});
    try {
        const page = await browser.newPage({viewport: {width: 1440, height: 1000}});
        const requests = [], errors = [];
        page.on("pageerror", error => errors.push(error.message));
        let mismatch = false;
        await page.route("**/*", async route => {
            const request = route.request(), url = new URL(request.url());
            if (url.hostname === 'unpkg.com' && url.pathname.includes('lucide')) return route.fulfill({path: path.join(path.dirname(require.resolve('lucide/package.json')), 'dist/umd/lucide.min.js')});
            if (url.pathname === "/agent-releases/") return route.fulfill({contentType: "text/html", body: fixture.releases});
            if (url.pathname === "/endpoints/") return route.fulfill({contentType: "text/html", body: fixture.endpoints});
            if (request.method() === "POST") {
                const data = request.postDataJSON(); requests.push({path: url.pathname, data});
                if (url.pathname.endsWith("/bulk-policy/")) {
                    assert.deepEqual(data.endpoint_ids, [fixture.endpointId]);
                    assert.deepEqual(data.changes, {update_paused: true});
                    assert.equal(typeof data.reason, "string");
                    if (data.apply) { assert.equal(data.expected_bulk_change_hash, "b".repeat(64)); assert.equal(data.confirmed_count, 1); }
                    return route.fulfill({json: {selected_count: 1, changed_count: 1, rejected_count: 0, applied: data.apply,
                        bulk_change_hash: "b".repeat(64), targets: [{hostname: "SYNTHETIC", before: {update_paused: false}, after: {update_paused: true}, warnings: ["unknown_safe_warning"], rejection: ""}]}});
                }
                const preview = {release: {version: "0.1.1.0-rc39", channel: "development", status: "paused", signature_valid: true, rollout_percentage: 0, rollout_paused: true, allowed_groups: []},
                    total_candidates: 1, eligible_count: 0, eligible_percentage: 0, excluded_count: 1, cohort_hash: "a".repeat(64), cohort_schema: 1,
                    generated_at: "2026-09-17T15:00:00Z", reason_counts: {unknown_safe_reason: 1}, targets: [{hostname: "SYNTHETIC", current_version: "RC38", updater_version: "RC38", channel: "development", policy: "manual", groups: [], bucket: 5, eligible: false, reason_code: "unknown_safe_reason"}]};
                if (url.pathname.endsWith("/validate/")) return route.fulfill({status: mismatch ? 409 : 200, json: {matches: !mismatch, error: mismatch ? "preview_changed" : "", preview}});
                return route.fulfill({json: preview});
            }
            if (url.pathname.startsWith("/static/")) {
                const staticRoot = path.resolve(__dirname, "../static");
                const file = path.resolve(staticRoot, url.pathname.slice(8));
                if (file.startsWith(staticRoot + path.sep) && fs.existsSync(file) && fs.statSync(file).isFile()) return route.fulfill({path: file});
            }
            return route.fulfill({body: "", status: 200});
        });
        await page.goto("http://fleet.test/agent-releases/");
        await page.locator("[data-fleet-preview-form] button[type=submit]").click();
        await page.waitForFunction(() => document.querySelector("[data-fleet-summary]").textContent.includes("candidatos"));
        assert.match(await page.locator("[data-fleet-reasons]").innerText(), /unknown_safe_reason/);
        await page.locator("[data-fleet-validate]").click();
        await page.waitForFunction(() => document.querySelector("[data-fleet-status]").textContent.includes("corresponde"));
        mismatch = true;
        await page.locator("[data-fleet-validate]").click();
        await page.waitForFunction(() => document.querySelector("[data-fleet-status]").textContent.includes("Preview mudou"));
        assert.equal(await page.locator("[data-fleet-validate]").isDisabled(), true);
        fs.mkdirSync(path.resolve(__dirname, "../artifacts/fleet-ui"), {recursive: true});
        await page.screenshot({path: path.resolve(__dirname, "../artifacts/fleet-ui/preview-desktop.png"), fullPage: true});
        await page.goto("http://fleet.test/endpoints/");
        await page.locator(`[data-fleet-endpoint-id="${fixture.endpointId}"]`).check();
        await page.locator("[data-fleet-open]").click();
        await page.locator("[data-fleet-bulk-form] [name=update_paused]").selectOption("true");
        await page.locator("[data-fleet-bulk-form] [name=reason]").fill("Synthetic UI test");
        await page.locator("[data-fleet-bulk-form] button[type=submit]").click();
        await page.waitForFunction(() => document.querySelector("[data-fleet-bulk] [data-fleet-status]").textContent.includes("Impacto"));
        await page.locator("[data-fleet-confirm]").check();
        await page.locator("[data-fleet-apply]").dblclick();
        await page.waitForFunction(() => document.querySelector("[data-fleet-bulk] [data-fleet-status]").textContent.includes("Politicas aplicadas"));
        assert.equal(requests.filter(request => request.data.apply === true).length, 1);
        await page.screenshot({path: path.resolve(__dirname, "../artifacts/fleet-ui/bulk-desktop.png"), fullPage: true});
        await page.setViewportSize({width: 390, height: 844});
        await page.screenshot({path: path.resolve(__dirname, "../artifacts/fleet-ui/bulk-mobile.png")});
        const box = await page.locator("[data-fleet-bulk]").boundingBox();
        assert.ok(box.width <= 390 && box.x >= 0);
        assert.deepEqual(errors, []);
        console.log("Fleet UI PASS: preview, validation/409, fallback, bulk dry-run/apply, one submission, desktop/mobile.");
    } finally { await browser.close(); }
})().catch(error => { console.error(error.message); process.exitCode = 1; });
