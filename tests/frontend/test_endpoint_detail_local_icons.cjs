const assert = require('node:assert/strict');
const { spawnSync } = require('node:child_process');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { pathToFileURL } = require('node:url');

function main() {
    const root = path.resolve(__dirname, '..', '..');
    const template = fs.readFileSync(path.join(root, 'templates/dashboard/endpoint_detail.html'), 'utf8');
    const library = path.join(root, 'static/vendor/lucide/1.71.0/lucide.min.js');
    const endpointJs = fs.readFileSync(path.join(root, 'static/js/endpoint_detail.js'), 'utf8');
    const names = new Set([...template.matchAll(/data-lucide="([a-z0-9-]+)"/g)].map(match => match[1]));
    const operationIcons = endpointJs.match(/\{flush_dns:'([^']+)',query_service:'([^']+)',gpupdate:'([^']+)',start_service:'([^']+)',stop_service:'([^']+)',restart_service:'([^']+)'\}/);
    assert(operationIcons, 'dynamic endpoint operations expose their fixed icon map');
    operationIcons.slice(1).forEach(name => names.add(name));
    const iconNames = [...names];
    const browserPath = process.env.NIGHTOWL_TEST_CHROME || [
        'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe',
        'C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe'
    ].find(candidate => fs.existsSync(candidate));
    assert(browserPath, 'Chrome or Edge is required for the real-browser icon smoke test');
    assert(fs.existsSync(library), 'pinned local Lucide bundle exists');
    const tempDir = fs.mkdtempSync(path.join(os.tmpdir(), 'nightowl-local-lucide-'));
    const htmlPath = path.join(tempDir, 'icons.html');
    const markup = iconNames.map(name => `<i data-lucide="${name}"></i>`).join('');
    fs.writeFileSync(htmlPath, `<!doctype html><meta charset="utf-8"><main>${markup}</main><script src="${pathToFileURL(library).href}"></script><script>lucide.createIcons();document.documentElement.dataset.iconSmoke=document.querySelectorAll('svg.lucide').length+'/${iconNames.length}';</script>`);

    const run = spawnSync(browserPath, ['--headless=new', '--disable-gpu', '--no-first-run', '--allow-file-access-from-files', '--dump-dom', pathToFileURL(htmlPath).href], { encoding: 'utf8', timeout: 30000 });
    fs.rmSync(tempDir, { recursive: true, force: true });
    assert.equal(run.error, undefined, `browser starts successfully: ${run.error || ''}`);
    assert.equal(run.status, 0, `browser smoke exits successfully: ${run.stderr}`);
    assert(run.stdout.includes(`data-icon-smoke="${iconNames.length}/${iconNames.length}"`), `all endpoint icons render (${iconNames.length}); browser output: ${run.stdout.slice(-700)}`);
    console.log(`PASS real browser with local Lucide 1.71.0: ${iconNames.length} endpoint icons rendered`);
}

try {
    main();
} catch (error) {
    console.error(error);
    process.exitCode = 1;
}
