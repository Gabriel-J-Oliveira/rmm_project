/* Existing Chrome + Node built-ins only. Real browser/VT/network, no browser or tool installation. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const {spawn} = require('node:child_process');
const sleep = ms => new Promise(resolve=>setTimeout(resolve,ms));
let phase='launch';
class CDP {
    constructor(ws) {
        this.ws=ws; this.id=0; this.pending=new Map();
        ws.addEventListener('message',event=>{
            const message=JSON.parse(event.data);
            if(message.id && this.pending.has(message.id)) {
                const entry=this.pending.get(message.id);this.pending.delete(message.id);clearTimeout(entry.timer);
                message.error ? entry.reject(new Error('cdp_command_failed')) : entry.resolve(message.result);
            }
        });
    }
    call(method,params={},sessionId) {
        const id=++this.id;
        return new Promise((resolve,reject)=>{
            const timer=setTimeout(()=>{this.pending.delete(id);reject(new Error('cdp_timeout'));},10000);
            this.pending.set(id,{resolve,reject,timer});this.ws.send(JSON.stringify({id,method,params,...(sessionId?{sessionId}:{})}));
        });
    }
}
(async()=>{
    const server=process.env.NIGHTOWL_TEST_SERVER;
    assert(/^http:\/\/127\.0\.0\.1:\d+$/.test(server));
    const profile=fs.mkdtempSync(path.join(os.tmpdir(),'nightowl-terminal-browser-'));
    const chrome=spawn('C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe',[
        '--headless=new','--remote-debugging-port=0',`--user-data-dir=${profile}`,'--no-first-run','--no-default-browser-check',
        '--disable-sync','--disable-background-networking','--disable-background-timer-throttling','--disable-renderer-backgrounding',
        '--disable-backgrounding-occluded-windows','--disable-features=BackForwardCache','--enable-logging=stderr','about:blank'
    ],{windowsHide:true,stdio:['ignore','ignore','pipe']});
    let chromeExitCode=null, chromeSignal=null, chromeSpawnError=null;
    const chromeDiagnostics = new Set();
    chrome.stderr.on('data',data=>{
        const value=data.toString();
        if (/access is denied|access_denied/i.test(value)) chromeDiagnostics.add('access_denied');
        if (/sandbox/i.test(value)) chromeDiagnostics.add('sandbox');
        if (/profile|user.data/i.test(value)) chromeDiagnostics.add('profile');
        if (/devtools listening/i.test(value)) chromeDiagnostics.add('devtools_listening');
        if (/failed|error/i.test(value)) chromeDiagnostics.add('other_error');
    });
    chrome.on('exit',(code,signal)=>{chromeExitCode=code;chromeSignal=signal;});
    chrome.on('error',error=>{chromeSpawnError=error.code || 'spawn_failed';});
    let cdp;
    try {
        const portFile=path.join(profile,'DevToolsActivePort');
        for(let i=0;i<100 && !fs.existsSync(portFile);i++) await sleep(100);
        if (!fs.existsSync(portFile)) console.log('TERMINAL_BROWSER_LAUNCH='+JSON.stringify({
            stage:'launch',category:chromeSpawnError?'spawn_failed':'port_unavailable',exit_code:chromeExitCode,signal:chromeSignal,error_code:chromeSpawnError,diagnostics:[...chromeDiagnostics]
        }));
        assert(fs.existsSync(portFile),'Existing Chrome did not start');
        const [port,endpoint]=fs.readFileSync(portFile,'utf8').trim().split(/\r?\n/);
        const ws=new WebSocket(`ws://127.0.0.1:${port}${endpoint}`);
        await new Promise((resolve,reject)=>{ws.addEventListener('open',resolve,{once:true});ws.addEventListener('error',reject,{once:true});});
        cdp=new CDP(ws);
        const {targetId}=await cdp.call('Target.createTarget',{url:'about:blank'});
        const {sessionId}=await cdp.call('Target.attachToTarget',{targetId,flatten:true});
        const call=(method,params)=>cdp.call(method,params,sessionId);
        async function evaluate(expression) {
            const result=await call('Runtime.evaluate',{expression,returnByValue:true,awaitPromise:true});
            if(result.exceptionDetails) {
                console.log('TERMINAL_BROWSER_EVAL_ERROR='+JSON.stringify({class:result.exceptionDetails.exception?.className,line:result.exceptionDetails.lineNumber,column:result.exceptionDetails.columnNumber}));
                throw new Error('browser_evaluation_failed');
            }
            return result.result.value;
        }
        async function until(expression,timeout=30000) {
            const deadline=Date.now()+timeout;
            while(Date.now()<deadline) {if(await evaluate(expression)) return;await sleep(10);}
            throw new Error('browser_condition_timeout');
        }
        await call('Page.enable');await call('Network.enable');await call('Runtime.enable');
        await cdp.call('Target.activateTarget',{targetId});await call('Page.bringToFront');
        await call('Network.setCacheDisabled',{cacheDisabled:true});
        await call('Network.setBlockedURLs',{urls:['https://*']}); // No external scripts/styles during smoke.
        await call('Network.setCookie',{name:'sessionid',value:process.env.NIGHTOWL_TEST_COOKIE,url:server,httpOnly:true});
        await call('Emulation.setDeviceMetricsOverride',{width:1400,height:1000,deviceScaleFactor:1,mobile:false});
        await call('Page.addScriptToEvaluateOnNewDocument',{source:`
            window.__terminalMetrics=[];
            document.addEventListener('nightowl:terminal-metric',event=>{
                if(window.__terminalMetrics.length<2000) window.__terminalMetrics.push(event.detail);
            });`});
        phase='endpoint_page';
        await call('Page.navigate',{url:server+process.env.NIGHTOWL_TEST_ENDPOINT_PATH});
        await until(`document.readyState==='complete' && !!document.querySelector('[data-terminal-start]:not(:disabled)')`);
        assert(await evaluate(`!Array.from(document.scripts).some(s=>s.src && new URL(s.src).origin!==location.origin)`));
        phase='request_and_consent';
        await evaluate(`document.querySelector('[data-terminal-start]').click()`);
        try {await until(`document.querySelector('[data-terminal-state]').textContent==='Conectado'`,45000);}
        catch(error) {
            console.log('TERMINAL_BROWSER_STATE='+JSON.stringify({state:await evaluate(`document.querySelector('[data-terminal-state]').textContent`),
                csrf_cookie_present:await evaluate(`document.cookie.includes('csrftoken=')`)}));
            throw error;
        }
        assert((await evaluate(`document.querySelector('[data-terminal-identity]').textContent`)).includes('não elevado'));
        const text=`Array.from(document.querySelectorAll('[data-terminal-screen] .xterm-rows > div')).map(e=>e.textContent).join(String.fromCharCode(10))`;
        async function focus() {await evaluate(`document.querySelector('[data-terminal-screen] .xterm-helper-textarea').focus()`);}
        async function enter() {
            await call('Input.dispatchKeyEvent',{type:'keyDown',key:'Enter',code:'Enter',windowsVirtualKeyCode:13,text:'\r'});
            await call('Input.dispatchKeyEvent',{type:'keyUp',key:'Enter',code:'Enter',windowsVirtualKeyCode:13});
        }
        async function clearLine() {
            await call('Input.dispatchKeyEvent',{type:'keyDown',key:'c',code:'KeyC',windowsVirtualKeyCode:67,modifiers:2,text:'\x03'});
            await call('Input.dispatchKeyEvent',{type:'keyUp',key:'c',code:'KeyC',windowsVirtualKeyCode:67,modifiers:2});
            await sleep(50);
        }
        await focus();
        phase='ansi_input';
        await call('Input.insertText',{text:`[Console]::WriteLine(([string][char]27)+'[32m'+'VT_'+'ação漢字'+([string][char]27)+'[0m')`});
        await enter();
        phase='ansi_render';
        await until(`(${text}).includes('VT_ação漢字')`);
        assert(await evaluate(`Array.from(document.querySelectorAll('.xterm-rows span')).some(e=>e.textContent.includes('VT_ação') && getComputedStyle(e).color!=='rgb(226, 232, 240)')`));
        phase='resize';
        await call('Emulation.setDeviceMetricsOverride',{width:980,height:800,deviceScaleFactor:1,mobile:false});
        await sleep(300);
        await focus();
        const echoes=[],commands=[];
        phase='echo_samples';
        for(let i=0;i<100;i++) {
            const marker=`ECHO_BROWSER_${String(i).padStart(3,'0')}`;
            const at=await evaluate('performance.now()');
            await call('Input.insertText',{text:marker});
            await until(`(${text}).includes(${JSON.stringify(marker)})`,5000);
            await evaluate('new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r)))');
            echoes.push((await evaluate('performance.now()'))-at);
            await clearLine();
        }
        phase='command_samples';
        for(let i=0;i<100;i++) {
            const number=String(i).padStart(3,'0'),marker=`BROWSER_${number}_DONE`;
            await call('Input.insertText',{text:`[Console]::WriteLine('BROWSER_'+'${number}_DONE')`});
            const at=await evaluate('performance.now()');
            await enter();
            await until(`(${text}).includes(${JSON.stringify(marker)})`,5000);
            await evaluate('new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r)))');
            commands.push((await evaluate('performance.now()'))-at);
        }
        phase='incremental_output';
        await call('Input.insertText',{text:`[Console]::WriteLine('EARLY_'+'BROWSER');Import-Module Microsoft.PowerShell.Utility -ErrorAction Stop;Start-Sleep -Milliseconds 500;[Console]::WriteLine('LATE_'+'BROWSER')`});
        await enter();
        await until(`(${text}).includes('EARLY_BROWSER')`);
        assert(!(await evaluate(`(${text}).includes('LATE_BROWSER')`)));
        await until(`(${text}).includes('LATE_BROWSER')`);
        const metrics=await evaluate('window.__terminalMetrics');
        const render=metrics.filter(m=>m.stage==='receive_to_render').map(m=>m.duration_ms).sort((a,b)=>a-b);
        phase='manual_close';
        const closedAt=await evaluate('performance.now()');
        await evaluate(`document.querySelector('[data-terminal-stop]').click()`);
        await until(`document.querySelector('[data-terminal-state]').textContent==='Encerrado'`);
        assert(await evaluate(`document.querySelector('[data-terminal-screen]').childElementCount===0`));
        assert(await evaluate(`!Object.keys(localStorage).some(k=>/terminal/i.test(k)) && !Object.keys(sessionStorage).some(k=>/terminal/i.test(k))`));
        const screenClearMs=(await evaluate('performance.now()'))-closedAt;
        const firstId=metrics.find(m=>m.stage==='request_to_authorized')?.session_id;
        phase='fresh_session';
        await sleep(1000);
        await evaluate(`document.querySelector('[data-terminal-start]').click()`);
        await until(`document.querySelector('[data-terminal-state]').textContent==='Conectado'`,45000);
        const ids=await evaluate(`window.__terminalMetrics.filter(m=>m.stage==='request_to_authorized').map(m=>m.session_id)`);
        assert(ids.length===2 && ids[1]!==firstId,'Reconnect must request a fresh session and consent');
        await focus();
        await call('Input.insertText',{text:`[Console]::WriteLine('CLOSE_'+'TAB')`});await enter();
        await until(`(${text}).includes('CLOSE_TAB')`);
        phase='pagehide_close';
        await call('Page.navigate',{url:'about:blank'});
        const pct=values=>{const s=values.slice().sort((a,b)=>a-b);return{n:s.length,p50_ms:+((s[49]+s[50])/2).toFixed(3),p95_ms:+s[94].toFixed(3)}};
        console.log('BROWSER_TERMINAL_MEASURED='+JSON.stringify({echo:pct(echoes),enter_to_marker_render:pct(commands),
            request_to_authorized_ms:metrics.find(m=>m.stage==='request_to_authorized')?.duration_ms,
            local_screen_clear_ms:screenClearMs,
            render_samples:metrics.filter(m=>m.stage==='receive_to_render').length,
            receive_to_render_p50_ms:render[Math.floor(render.length*.5)],receive_to_render_p95_ms:render[Math.floor(render.length*.95)],
            kind:'real_chrome_vt_real_powershell_loopback_no_tls_consent_click_simulated_in_test',automation_overhead_included:true}));
        console.log('PASS browser VT ANSI Unicode resize real input/output incremental manual close fresh session pagehide ephemeral buffers');
    } finally {
        if(cdp) {try{await cdp.call('Browser.close');}catch{} cdp.ws.close();}
        if(chrome.exitCode===null) await Promise.race([new Promise(resolve=>chrome.once('exit',resolve)),sleep(5000)]);
        if(chrome.exitCode===null) chrome.kill();
        const resolved=fs.realpathSync(profile), temp=fs.realpathSync(os.tmpdir());
        assert(path.dirname(resolved)===temp && path.basename(resolved).startsWith('nightowl-terminal-browser-'));
        try{fs.rmSync(resolved,{recursive:true,force:true,maxRetries:3,retryDelay:100});}catch{console.log('BROWSER_PROFILE_CLEANUP=deferred');}
    }
})().catch(error=>{console.log('TERMINAL_BROWSER_FAILURE='+JSON.stringify({phase,error_type:error.name,
    code:['browser_evaluation_failed','cdp_command_failed','browser_condition_timeout','cdp_timeout'].includes(error.message)?error.message:'assertion'}));process.exitCode=1;});
