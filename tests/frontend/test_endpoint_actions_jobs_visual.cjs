const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');

async function main() {
    const { chromium } = require('playwright');
    const root = path.resolve(__dirname, '..', '..');
    const css = [
        fs.readFileSync(path.join(root, 'static/css/nightowl.css'), 'utf8'),
        fs.readFileSync(path.join(root, 'static/css/endpoint_actions_jobs.css'), 'utf8')
    ].join('\n');
    const output = process.env.NIGHTOWL_SCREENSHOT_DIR || path.join(os.tmpdir(), 'nightowl-endpoint-actions-jobs');
    fs.mkdirSync(output, { recursive: true });
    const executablePath = process.env.NIGHTOWL_TEST_CHROME || [
        'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe',
        'C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe'
    ].find(candidate => fs.existsSync(candidate));
    const browser = await chromium.launch({ headless: true, ...(executablePath ? { executablePath } : {}) });
    try {
        const page = await browser.newPage({ viewport: { width: 1600, height: 1000 }, deviceScaleFactor: 1 });
        const categories = ['Diagnósticos', 'Operações', 'Manutenção do agente', 'Scripts aprovados', 'Ações críticas'];
        const markup = categories.map((title, i) => `<section class="endpoint-action-category${i === 4 ? ' endpoint-actions-danger' : ''}"><h3 class="endpoint-category-heading">${title}</h3><div class="endpoint-action-grid"><button class="endpoint-quick-action-button">Ação de exemplo</button><button class="endpoint-quick-action-button">Outra ação</button></div></section>`).join('');
        await page.setContent(`<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width"><style>${css}</style><body class="page-endpoint-detail"><main style="padding:24px"><header class="endpoint-ops-header"><div class="endpoint-ops-title"><div class="endpoint-title-line"><h1>CS-CVEL-0281</h1><span class="status-badge">Online</span></div><div class="endpoint-critical-strip">Windows · control.local · 192.168.1.10</div></div><div class="endpoint-ops-actions"><button class="copy-btn">Hostname</button></div></header><section class="endpoint-actions-tab" style="margin-top:16px"><div class="endpoint-actions-jobs-layout"><div class="endpoint-actions-column"><section class="panel endpoint-quick-actions-card"><div class="endpoint-quick-actions-intro"><h2>Ações rápidas</h2><p>Acesso, conectividade e recuperação.</p></div><div class="endpoint-quick-action-list"><button class="endpoint-quick-action-button">Acessar terminal</button><button class="endpoint-quick-action-button">Testar WinRM</button><button class="endpoint-quick-action-button">Reativar agente</button></div></section>${markup}</div><aside class="panel endpoint-jobs-panel"><header class="endpoint-jobs-header"><h2>Últimos jobs</h2><button aria-label="Atualizar">↻</button></header><div class="endpoint-jobs-scroll"><div class="endpoint-jobs-content"></div></div></aside></div></section></main></body>`);
        const setJobs = count => page.locator('.endpoint-jobs-content').evaluate((node, n) => {
            node.innerHTML = n ? Array.from({ length: n }, (_, i) => `<article class="endpoint-job-item"><div class="endpoint-job-card-content"><div class="endpoint-job-card-heading"><span>Concluído</span><span>Coleta de inventário</span></div><div class="endpoint-job-card-meta"><time>09/11/2026 10:00</time><span>2s</span></div><p class="endpoint-job-result">${i % 2 ? 'Resumo curto.' : 'Resultado de leitura longo para validar a quebra de texto sem sobrepor os controles laterais. '.repeat(3)}</p></div><div class="endpoint-job-actions"><button class="endpoint-job-action-button">Detalhes</button><button class="endpoint-job-action-button">Copiar</button></div></article>`).join('') : '<p class="endpoint-empty-state">Nenhum job registrado neste endpoint</p>';
        }, count);

        await setJobs(0);
        assert.match(await page.locator('.endpoint-jobs-content').innerText(), /Nenhum job/);
        assert.equal(await page.locator('.endpoint-jobs-panel').evaluate(e => getComputedStyle(e).height), await page.locator('.endpoint-actions-column').evaluate(e => getComputedStyle(e).height), 'empty history stretches with actions');
        await setJobs(1);
        assert.equal(await page.locator('.endpoint-job-item').count(), 1);
        await setJobs(18);
        const desktop = await page.evaluate(() => {
            const actions = document.querySelector('.endpoint-actions-column').getBoundingClientRect();
            const jobs = document.querySelector('.endpoint-jobs-panel').getBoundingClientRect();
            const scroll = document.querySelector('.endpoint-jobs-scroll');
            return { actionsBottom: actions.bottom, jobsBottom: jobs.bottom, scrollHeight: scroll.scrollHeight, clientHeight: scroll.clientHeight,
                actionsWidth: actions.width, jobsWidth: jobs.width, cols: getComputedStyle(document.querySelector('.endpoint-actions-jobs-layout')).gridTemplateColumns };
        });
        assert(Math.abs(desktop.actionsBottom - desktop.jobsBottom) <= 1, 'desktop jobs panel shares the actions column bottom');
        assert(desktop.scrollHeight > desktop.clientHeight, 'long history scrolls inside its list');
        assert(desktop.jobsWidth > 0 && desktop.actionsWidth > desktop.jobsWidth * 3, 'desktop layout keeps the approximate 80/20 split');
        const quickButton = page.locator('.endpoint-quick-action-button').first();
        const quickCard = page.locator('.endpoint-quick-actions-card');
        const cardBefore = await quickCard.evaluate(e => ({ border: getComputedStyle(e).borderColor, background: getComputedStyle(e).backgroundColor, shadow: getComputedStyle(e).boxShadow, transform: getComputedStyle(e).transform }));
        await quickButton.hover();
        const hoverBackground = await quickButton.evaluate(e => getComputedStyle(e).backgroundColor);
        assert.equal(hoverBackground, 'rgba(124, 58, 237, 0.18)', 'quick-action hover uses purple');
        await page.mouse.down();
        assert.equal(await quickButton.evaluate(e => getComputedStyle(e).backgroundColor), 'rgba(124, 58, 237, 0.28)', 'pressed action has a distinct purple state');
        await page.mouse.up();
        const cardAfter = await quickCard.evaluate(e => ({ border: getComputedStyle(e).borderColor, background: getComputedStyle(e).backgroundColor, shadow: getComputedStyle(e).boxShadow, transform: getComputedStyle(e).transform }));
        assert.deepEqual(cardAfter, cardBefore, 'card appearance does not change when hovered');
        const disabledButton = page.locator('.endpoint-quick-action-button').nth(2);
        await disabledButton.evaluate(e => e.disabled = true);
        const disabledBefore = await disabledButton.evaluate(e => ({ border: getComputedStyle(e).borderColor, background: getComputedStyle(e).backgroundColor }));
        await disabledButton.hover();
        const disabledAfter = await disabledButton.evaluate(e => ({ border: getComputedStyle(e).borderColor, background: getComputedStyle(e).backgroundColor }));
        assert.deepEqual(disabledAfter, disabledBefore, 'disabled action does not receive hover styling');
        await disabledButton.evaluate(e => e.disabled = false);
        await page.mouse.move(0, 0);
        await page.screenshot({ path: path.join(output, 'desktop-many-jobs.png'), fullPage: true });

        await page.setViewportSize({ width: 900, height: 900 });
        await page.evaluate(() => window.scrollTo(0, 0));
        const mobile = await page.evaluate(() => {
            const actions = document.querySelector('.endpoint-actions-column').getBoundingClientRect();
            const jobs = document.querySelector('.endpoint-jobs-panel').getBoundingClientRect();
            const scroll = document.querySelector('.endpoint-jobs-scroll');
            const cards = [...document.querySelectorAll('.endpoint-job-item')];
            const actionOverflow = cards.some(card => [...card.querySelectorAll('.endpoint-job-actions')].some(buttons => buttons.getBoundingClientRect().right > card.getBoundingClientRect().right + 1));
            return { actionsBottom: actions.bottom, jobsTop: jobs.top, scrollHeight: scroll.scrollHeight, clientHeight: scroll.clientHeight, actionOverflow };
        });
        assert(mobile.jobsTop >= mobile.actionsBottom, 'narrow layout stacks the jobs panel below actions');
        assert(mobile.scrollHeight > mobile.clientHeight, 'narrow layout retains internal history scrolling');
        assert.equal(mobile.actionOverflow, false, 'job actions stay within each card at notebook width');
        await page.screenshot({ path: path.join(output, 'narrow-many-jobs.png'), fullPage: true });

        await page.setViewportSize({ width: 390, height: 844 });
        await page.evaluate(() => window.scrollTo(0, 0));
        const phone = await page.evaluate(() => ({
            documentWidth: document.documentElement.scrollWidth,
            viewportWidth: window.innerWidth,
            actionOverflow: [...document.querySelectorAll('.endpoint-job-item')].some(card => [...card.querySelectorAll('.endpoint-job-actions')].some(actions => actions.getBoundingClientRect().right > card.getBoundingClientRect().right + 1))
        }));
        assert(phone.documentWidth <= phone.viewportWidth, 'phone layout has no horizontal page overflow');
        assert.equal(phone.actionOverflow, false, 'job actions stay within each card at phone width');
        await page.screenshot({ path: path.join(output, 'phone-many-jobs.png'), fullPage: true });
        console.log(`endpoint actions/jobs visual: empty, one and long history; desktop and narrow layouts OK; screenshots=${output}`);
    } finally {
        await browser.close();
    }
}

main().catch(error => { console.error(error); process.exitCode = 1; });
