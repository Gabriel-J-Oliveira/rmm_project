/* Local-only Chrome/CDP probe. Never emits paths, browser contents or credentials. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const {spawn, execFileSync} = require('node:child_process');
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));

function access(dir) {
    if (!dir) return {configured:false};
    const state = {configured:true, exists:fs.existsSync(dir)};
    for (const [name, mode] of [['readable',fs.constants.R_OK],['writable',fs.constants.W_OK]]) {
        try {fs.accessSync(dir, mode); state[name]=true;} catch {state[name]=false;}
    }
    return state;
}
function psJson(script, extra={}) {
    try {
        const output=execFileSync('powershell.exe',['-NoProfile','-NonInteractive','-Command',script],
            {encoding:'utf8',timeout:8000,stdio:['ignore','pipe','ignore'],env:{...process.env,...extra}});
        return JSON.parse(output.trim());
    } catch {return null;}
}
function windowsProfile() {
    return psJson("$sid=[Security.Principal.WindowsIdentity]::GetCurrent().User.Value; " +
        "$p=Get-CimInstance Win32_UserProfile -Filter \"SID='$sid'\" -ErrorAction Stop; " +
        "@{record_present=($null -ne $p); loaded=([bool]$p.Loaded); " +
        "hive_loaded=(Test-Path -LiteralPath ('Registry::HKEY_USERS\\'+$sid)); " +
        "profile_exists=($null -ne $p -and (Test-Path -LiteralPath $p.LocalPath)); " +
        "matches_environment=($null -ne $p -and $p.LocalPath -ieq $env:USERPROFILE)} | ConvertTo-Json -Compress");
}
function matchingChromeProcesses(profile) {
    return psJson("@(Get-CimInstance Win32_Process -Filter \"Name='chrome.exe'\" -ErrorAction Stop | " +
        "Where-Object { $_.CommandLine -and $_.CommandLine.Contains($env:NIGHTOWL_DIAG_PROFILE) }).Count | ConvertTo-Json -Compress",
        {NIGHTOWL_DIAG_PROFILE:profile});
}
async function main() {
    const profile=fs.mkdtempSync(path.join(os.tmpdir(),'nightowl-cdp-probe-'));
    const resolved=fs.realpathSync(profile), temp=fs.realpathSync(os.tmpdir());
    assert(path.dirname(resolved)===temp && path.basename(resolved).startsWith('nightowl-cdp-probe-'));
    const chromePath=process.argv.includes('--missing-executable')
        ? path.join(profile,'missing-chrome.exe') : 'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe';
    const result={phase:'launch',profile:access(profile),windows_profile:windowsProfile(),
        environment:Object.fromEntries(['USERPROFILE','APPDATA','LOCALAPPDATA','TEMP','TMP'].map(name=>[name,access(process.env[name])]))};
    let chrome, ws, exitCode=null, spawnError=null, closeRequested=false;
    const started=performance.now();
    try {
        chrome=spawn(chromePath,['--headless=new','--remote-debugging-port=0',`--user-data-dir=${profile}`,
            '--no-first-run','--no-default-browser-check','--disable-sync','--disable-background-networking',
            '--enable-logging=stderr','about:blank'],{windowsHide:true,stdio:['ignore','ignore','pipe']});
        const categories=new Set();
        chrome.stderr.on('data',data=>{const s=data.toString();
            if (/access is denied|access_denied/i.test(s)) categories.add('access_denied');
            if (/sandbox/i.test(s)) categories.add('sandbox');
            if (/profile|user.data/i.test(s)) categories.add('profile');
            if (/devtools listening/i.test(s)) categories.add('devtools_listening');
            if (/failed|error/i.test(s)) categories.add('other_error');});
        chrome.on('exit',code=>{exitCode=code;});
        chrome.on('error',error=>{spawnError=error.code || 'spawn_failed';});
        const portFile=path.join(profile,'DevToolsActivePort');
        for(let i=0;i<100 && !fs.existsSync(portFile) && !spawnError && exitCode===null;i++) await sleep(100);
        result.elapsed_ms=Math.round(performance.now()-started);
        result.port_file_present=fs.existsSync(portFile);
        result.process_running=!!chrome.pid && exitCode===null;
        result.exit_code=exitCode; result.spawn_error=spawnError; result.categories=[...categories];
        if (!result.port_file_present) throw new Error(spawnError?'spawn_failed':'port_unavailable');
        const [port,endpoint]=fs.readFileSync(portFile,'utf8').trim().split(/\r?\n/);
        assert(/^\d+$/.test(port) && /^\/devtools\/browser\/[a-z0-9-]+$/i.test(endpoint));
        result.phase='cdp_connect';
        ws=new WebSocket(`ws://127.0.0.1:${port}${endpoint}`);
        await new Promise((resolve,reject)=>{ws.addEventListener('open',resolve,{once:true});ws.addEventListener('error',reject,{once:true});});
        const request=(id,method)=>new Promise((resolve,reject)=>{
            const timer=setTimeout(()=>reject(new Error('cdp_timeout')),10000);
            const receive=event=>{const msg=JSON.parse(event.data);if(msg.id!==id)return;
                clearTimeout(timer);ws.removeEventListener('message',receive);msg.error?reject(new Error('cdp_error')):resolve(msg.result);};
            ws.addEventListener('message',receive);ws.send(JSON.stringify({id,method}));
        });
        result.phase='cdp_version';
        const version=await request(1,'Browser.getVersion');
        assert(/^Chrome\//.test(version.product));
        result.chrome_version=version.product;
        result.phase='close';
        await request(2,'Browser.close'); closeRequested=true;
    } catch(error) {
        result.error=['spawn_failed','port_unavailable','cdp_timeout','cdp_error'].includes(error.message)?error.message:'probe_error';
    } finally {
        if(ws) ws.close();
        if(chrome && chrome.pid && chrome.exitCode===null) {
            await Promise.race([new Promise(resolve=>chrome.once('exit',resolve)),sleep(5000)]);
            if(chrome.exitCode===null) {
                try {execFileSync('taskkill.exe',['/PID',String(chrome.pid),'/T','/F'],{timeout:5000,stdio:'ignore'});} catch {}
                await Promise.race([new Promise(resolve=>chrome.once('exit',resolve)),sleep(3000)]);
            }
        }
        result.close_requested=closeRequested;
        result.process_exited=!chrome || !chrome.pid || chrome.exitCode!==null;
        result.remaining_profile_processes=matchingChromeProcesses(profile);
        try {fs.rmSync(resolved,{recursive:true,force:true});result.profile_removed=!fs.existsSync(resolved);}
        catch {result.profile_removed=false;}
        if (!result.process_exited || result.remaining_profile_processes!==0 || !result.profile_removed)
            result.error=result.error || 'cleanup_failed';
        console.log('CHROME_CDP_ISOLATED='+JSON.stringify(result));
    }
    if(result.error) process.exitCode=1;
}
main().catch(()=>{console.log('CHROME_CDP_ISOLATED='+JSON.stringify({phase:'setup',error:'probe_error'}));process.exitCode=1;});
