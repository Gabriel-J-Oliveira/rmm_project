// Real DOM with synthetic APIs only. Run with playwright installed; no AD/WinRM.
const assert = require('node:assert/strict');
const test = require('node:test');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require('playwright');
const root = path.join(__dirname, '..');
const read = file => fs.readFileSync(path.join(root, file), 'utf8');
const ID = '11111111-1111-4111-8111-111111111111';
const NEXT = '22222222-2222-4222-8222-222222222222';
const ITEM = '33333333-3333-4333-8333-333333333333';
let browser;
test.before(async () => { browser = await chromium.launch({ headless: true, ...(process.env.PLAYWRIGHT_CHANNEL ? {channel: process.env.PLAYWRIGHT_CHANNEL} : {}) }); });
test.after(async () => { await browser?.close(); });
function batch(overrides={}) {
    return {id:ID,status:'RUNNING',total_count:3,success_count:1,failure_count:0,review_count:1,
        waiting_count:1,current_index:3,progress_percentage:72,started_at:'2026-10-06T01:00:00Z',
        manual_stall_action_available:false,items:[{id:ITEM,hostname:'PC-001',fqdn:'pc01.control.local',
            status:'INSTALLING',stage:'INSTALLER_STARTED',progress_percentage:68,
            progress_message:'Instalador NightOwl iniciado no computador',is_stalled:false}],...overrides};
}
function content(file) {
    return read(file).split('{% block content %}')[1].split('{% endblock %}')[0]
        .replace("{% include 'dashboard/_install_batches.html' %}",read('templates/dashboard/_install_batches.html'))
        .replace(/{% csrf_token %}/g,'<input name="csrfmiddlewaretoken" value="synthetic-csrf" type="hidden">')
        .replace(/{% url 'agent-install-ad-scan' %}/g,'/scan/')
        .replace(/{%[^%]*%}/g,'').replace(/{{[^}]*}}/g,'');
}
async function setup(t, {agent=false, initial=batch(), query='?tab=installations&batch='+ID, items=[]}={}) {
    const page = await browser.newPage({viewport:{width:1440,height:950}}); t.after(()=>page.close());
    const errors=[];page.on('pageerror',e=>errors.push(e.message));
    const state={value:initial,requests:[],listGets:0,detailGets:0,fail:false,hold:null};
    const scripts=agent ? ['install_batch_client','agent_install_discovery'] : ['jobs','install_batch_client','install_batches'];
    const lucide=process.env.NIGHTOWL_LUCIDE_FILE ? '<script>'+fs.readFileSync(process.env.NIGHTOWL_LUCIDE_FILE,'utf8')+'</script>' : '';
    const html='<!doctype html><html><head><meta charset="utf-8"><style>'+read('static/css/nightowl.css')+'</style>'+lucide+'</head><body class="'+(agent?'page-agent-install':'page-jobs')+'">'+content('templates/dashboard/'+(agent?'agent_install':'jobs')+'.html')+scripts.map(s=>'<script>'+read('static/js/'+s+'.js')+'</script>').join('')+'</body></html>';
    await page.route('https://nightowl.test/**',async route=>{
        const req=route.request(),url=new URL(req.url());
        if(req.isNavigationRequest())return route.fulfill({contentType:'text/html',body:html});
        state.requests.push({url:req.url(),method:req.method(),body:req.postData(),headers:req.headers()});
        if(url.pathname==='/scan/')return route.fulfill({json:{computers:items,summary:{total:items.length,unmanaged:items.length,managed:0}}});
        if(req.method()==='POST') {
            if(state.holdPost)await state.holdPost;
            if(state.postFail)return route.fulfill({status:409,json:{error_code:'ACTION_NOT_AVAILABLE',raw:'SUPER_SECRET_TEST_PASSWORD_91827'}});
            return route.fulfill({status:url.pathname.endsWith('mark-stalled/')?200:202,json:url.pathname.endsWith('mark-stalled/')?{item_id:ITEM,status:'REVIEW_REQUIRED'}:{batch_id:NEXT}});
        }
        if(url.pathname==='/agent-install/install-batches/') {
            state.listGets++;
            if(state.hold) await state.hold;
            if(state.fail)return route.fulfill({status:503,json:{}});
            return route.fulfill({json:{results:[state.value],page:1,pages:1}});
        }
        state.detailGets++;
        return route.fulfill({json:{...state.value,id:url.pathname.includes(NEXT)?NEXT:state.value.id}});
    });
    await page.clock.install();
    await page.goto('https://nightowl.test/'+(agent?'agent-install/':'jobs/')+query);
    await page.waitForFunction(agent ? ()=>document.querySelector('#ad-computer-rows').children.length>0 : ()=>document.querySelector('#install-item-rows').children.length>0);
    return {page,state,errors};
}
test('tab query, counts, backend progress, detail and terminal distinctions are real DOM text',async t=>{
    const {page,state,errors}=await setup(t);
    assert.equal(await page.locator('[data-task-tab=installations]').evaluate(n=>n.classList.contains('is-active')),true);
    assert.equal(await page.locator('#install-batch-drawer').isVisible(),true);
    assert.match(await page.locator('#install-batch-rows').innerText(),/Instalando 3 de 3/);
    assert.match(await page.locator('#install-batch-rows').innerText(),/1 requer revisão/);
    assert.equal(await page.locator('#install-batch-rows [role=progressbar]').getAttribute('aria-valuenow'),'72');
    assert.equal(await page.locator('#install-item-rows [role=progressbar]').getAttribute('aria-valuenow'),'68');
    assert.equal(await page.locator('#install-detail-footer').isVisible(),false);
    state.value=batch({status:'COMPLETED',review_count:0,success_count:3,waiting_count:0,progress_percentage:100,items:[{...batch().items[0],status:'COMPLETED',retry_eligible:false}]});
    await page.locator('#install-detail-refresh').click();
    await page.waitForFunction(()=>document.querySelector('#install-detail-summary').textContent.includes('3 de 3 computadores gerenciados'));
    assert.equal(await page.locator('#install-detail-footer').isVisible(),false);assert.deepEqual(errors,[]);
});
test('independent polling, no overlap, preserved rows, failed fetch recovery and reopen',async t=>{
    const {page,state}=await setup(t);
    await page.locator('#install-detail-close').click();
    await page.evaluate(()=>window.originalRow=document.querySelector('#install-batch-rows tr'));
    let release;state.hold=new Promise(r=>release=r);
    const before=state.listGets;
    await page.locator('#install-list-refresh').click();await page.locator('#install-list-refresh').click();
    await page.clock.runFor(5000);assert.equal(state.listGets,before+1);
    release();state.hold=null;
    await page.waitForFunction(()=>document.querySelector('#install-list-refresh').getAttribute('aria-busy')==='false');
    assert.equal(await page.evaluate(()=>window.originalRow===document.querySelector('#install-batch-rows tr')),true);
    state.fail=true;await page.locator('#install-list-refresh').click();
    await page.waitForFunction(()=>document.querySelector('#install-list-feedback').textContent.includes('indisponível'));
    assert.match(await page.locator('#install-batch-rows').innerText(),/Instalando/);
    state.fail=false;state.value=batch({current_index:2,progress_percentage:55});
    await page.clock.runFor(5000);
    await page.waitForFunction(()=>document.querySelector('#install-batch-rows').textContent.includes('Instalando 2 de 3'));
    await page.locator('#install-batch-rows button').click();
    await page.waitForFunction(()=>document.querySelector('#install-detail-summary').textContent.includes('Instalando 2 de 3'));
    assert.equal(await page.locator('#install-batch-drawer').evaluate(n=>getComputedStyle(n).height),'950px');
    assert.equal(await page.locator('body').evaluate(n=>getComputedStyle(n).overflow),'hidden');
});
test('XSS-safe item tooltip, authorized stall confirmation, retry opens new batch',async t=>{
    const malicious='<img src=x onerror=alert(1)><script>alert(1)</script>';
    const item={...batch().items[0],hostname:malicious,error_message:malicious,error_code:'<b>bad</b>',status:'REVIEW_REQUIRED',reconciliation_required:true};
    const {page,state}=await setup(t,{initial:batch({status:'COMPLETED_WITH_ERRORS',items:[item]})});
    assert.equal(await page.locator('#install-item-rows img,#install-item-rows script').count(),0);
    await page.locator('#install-item-rows button').focus();
    assert.match(await page.locator('.install-batch-tooltip').innerText(),/<img/);
    assert.equal(await page.locator('.install-batch-tooltip img').count(),0);
    await page.locator('#install-batch-retry').click();
    const pass=page.locator('.install-credential-modal input[type=password]');
    await page.locator('.install-credential-modal input[type=text]').fill('synthetic-admin');await pass.fill('SUPER_SECRET_TEST_PASSWORD_91827');
    await page.locator('.install-credential-modal button[type=submit]').click();
    await page.waitForFunction(()=>!document.querySelector('.install-credential-modal'));
    const retry=state.requests.find(r=>r.method==='POST');assert.ok(retry.url.endsWith(ID+'/retry/'));
    assert.deepEqual(JSON.parse(retry.body),{username:'synthetic-admin',password:'SUPER_SECRET_TEST_PASSWORD_91827'});
    assert.equal(retry.url.includes('SUPER_SECRET'),false);
    assert.equal(await page.evaluate(()=>Object.keys(localStorage).length+Object.keys(sessionStorage).length),0);
    assert.equal((await page.locator('body').innerText()).includes('SUPER_SECRET'),false);
    await page.waitForFunction(()=>document.querySelector('#install-action-feedback').textContent==='Retry iniciado');
    state.value=batch({id:NEXT,manual_stall_action_available:true,items:[{...batch().items[0],is_stalled:true,stale_seconds:83}]});
    await page.locator('#install-detail-refresh').click();
    await page.waitForFunction(()=>document.querySelector('#install-item-rows').textContent.includes('83s'));
    await page.getByRole('button',{name:'Marcar como travada e seguir',exact:true}).click();
    assert.equal(await page.evaluate(()=>document.activeElement.textContent),'Confirmar e seguir');
    await page.getByRole('button',{name:'Confirmar e seguir',exact:true}).click();
    await page.waitForFunction(()=>document.querySelector('#install-action-feedback').textContent.includes('Resultado registrado'));
    const stall=state.requests.find(r=>r.url.endsWith('mark-stalled/'));assert.deepEqual(JSON.parse(stall.body),{item_id:ITEM});
});
test('credential modal clears password during pending dispatch, secure FQDN POST and redirect',async t=>{
    const items=[1,2].map(i=>({hostname:'PC'+i,fqdn:'pc'+i+'.control.local',distinguished_name:'CN=pc'+i,enabled:true,selectable:true,correlation_status:'UNMANAGED'}));
    const {page,state}=await setup(t,{agent:true,items,query:''});
    await page.locator('#ad-computer-rows input[type=checkbox]').nth(0).check();
    await page.locator('#ad-computer-rows input[type=checkbox]').nth(1).check();
    await page.locator('#ad-prepare-button').click();
    await page.locator('.install-credential-modal input[type=text]').fill('synthetic-admin');
    await page.locator('.install-credential-modal input[type=password]').fill('SUPER_SECRET_TEST_PASSWORD_91827');
    let release;state.holdPost=new Promise(r=>release=r);
    await page.locator('.install-credential-modal button[type=submit]').click();
    await page.waitForFunction(()=>document.querySelector('.install-credential-modal input[type=password]').value==='');
    assert.equal(await page.locator('.install-credential-modal button[type=submit]').isDisabled(),true);
    release();state.holdPost=null;
    await page.waitForURL('**/jobs/?tab=installations&batch='+NEXT);
    const post=state.requests.find(r=>r.method==='POST'&&r.url.includes('install-batches'));
    assert.deepEqual(JSON.parse(post.body),{targets:['pc1.control.local','pc2.control.local'],username:'synthetic-admin',password:'SUPER_SECRET_TEST_PASSWORD_91827'});
    assert.equal(state.requests.some(r=>r.url.includes('/preflight/')),false);
    assert.equal(state.requests.some(r=>r.url.includes('SUPER_SECRET')),false);
    assert.equal(await page.evaluate(()=>Object.keys(localStorage).length+Object.keys(sessionStorage).length),0);
});
test('visual desktop/mobile fixtures with no remote side effects',async t=>{
    const {page,state}=await setup(t,{initial:batch({status:'COMPLETED_WITH_ERRORS',total_count:20,success_count:13,review_count:7,waiting_count:0,progress_percentage:100,finished_at:'2026-10-06T01:12:00Z',items:Array.from({length:20},(_,i)=>({...batch().items[0],id:`${String(i+4).padStart(8,'0')}-1111-4111-8111-111111111111`,hostname:'PC-'+i,status:i%3?'COMPLETED':'REVIEW_REQUIRED',reconciliation_required:i%3===0,error_message:i%3?'':'Resultado não comprovado',progress_message:i%3?'NightOwl instalado e gerenciado':'Resultado não comprovado',progress_percentage:i%3?100:68}))})});
    if(process.env.NIGHTOWL_SCREENSHOT_DIR){fs.mkdirSync(process.env.NIGHTOWL_SCREENSHOT_DIR,{recursive:true});await page.screenshot({path:path.join(process.env.NIGHTOWL_SCREENSHOT_DIR,'batch-desktop.png')});}
    assert.ok(await page.locator('.install-batch-drawer-body').evaluate(n=>n.scrollHeight>n.clientHeight));
    await page.setViewportSize({width:390,height:844});
    assert.equal(await page.locator('#install-batch-drawer').evaluate(n=>n.getBoundingClientRect().width),390);
    if(process.env.NIGHTOWL_SCREENSHOT_DIR)await page.screenshot({path:path.join(process.env.NIGHTOWL_SCREENSHOT_DIR,'batch-mobile.png')});
    assert.equal(state.requests.some(r=>r.method==='POST'),false);
});

test('stall never appears without backend authorization and modal Escape restores focus',async t=>{
    const {page,state}=await setup(t,{initial:batch({status:'COMPLETED_WITH_ERRORS',items:[{...batch().items[0],is_stalled:true,stale_seconds:200,status:'FAILED',retry_eligible:true}]})});
    assert.equal(await page.getByRole('button',{name:'Marcar como travada e seguir',exact:true}).count(),0);
    await page.locator('#install-batch-retry').click();
    if(process.env.NIGHTOWL_SCREENSHOT_DIR)await page.screenshot({path:path.join(process.env.NIGHTOWL_SCREENSHOT_DIR,'credential-modal.png')});
    await page.keyboard.press('Escape');
    assert.equal(await page.locator('.install-credential-modal').count(),0);
    assert.equal(await page.locator('#install-batch-drawer').isVisible(),true);
    assert.equal(await page.evaluate(()=>document.activeElement.id),'install-batch-retry');
    assert.equal(state.requests.some(r=>r.method==='POST'),false);
    await page.keyboard.press('Escape');assert.equal(await page.locator('#install-batch-drawer').isVisible(),false);
});
test('mutation HTTP error is sanitized and password cleared without auto retry',async t=>{
    const {page,state}=await setup(t,{initial:batch({status:'COMPLETED_WITH_ERRORS',items:[{...batch().items[0],status:'FAILED',retry_eligible:true}]})});
    state.postFail=true;
    await page.locator('#install-batch-retry').click();
    await page.locator('.install-credential-modal input[type=text]').fill('synthetic-admin');
    await page.locator('.install-credential-modal input[type=password]').fill('SUPER_SECRET_TEST_PASSWORD_91827');
    await page.locator('.install-credential-modal button[type=submit]').click();
    await page.waitForFunction(()=>document.querySelector('.install-credential-modal .install-feedback').textContent.includes('não está mais disponível'));
    assert.equal(await page.locator('.install-credential-modal input[type=password]').inputValue(),'');
    assert.equal((await page.locator('body').innerText()).includes('SUPER_SECRET'),false);
    await page.clock.runFor(6000);assert.equal(state.requests.filter(r=>r.method==='POST').length,1);
});
