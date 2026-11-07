const fs=require('node:fs'),os=require('node:os'),path=require('node:path');
const {spawn,execFileSync}=require('node:child_process');
const sleep=ms=>new Promise(r=>setTimeout(r,ms));
class CDP {
    constructor(ws) { this.ws=ws;this.id=0;this.pending=new Map();ws.addEventListener('message',event=>{const m=JSON.parse(event.data);if(m.id && this.pending.has(m.id)){const p=this.pending.get(m.id);this.pending.delete(m.id);clearTimeout(p.timer);m.error?p.reject(new Error('cdp_command_failed')):p.resolve(m.result);}}); }
    call(method,params={},sessionId) {const id=++this.id;return new Promise((resolve,reject)=>{const timer=setTimeout(()=>{this.pending.delete(id);reject(new Error('cdp_timeout'));},10000);this.pending.set(id,{resolve,reject,timer});this.ws.send(JSON.stringify({id,method,params,...(sessionId?{sessionId}:{})}));});}
}
function countProfileProcesses(profile) {
    const script="@(Get-CimInstance Win32_Process -Filter \"Name='chrome.exe'\" -ErrorAction Stop | Where-Object { $_.CommandLine -and $_.CommandLine.Contains($env:NIGHTOWL_DIAG_PROFILE) }).Count | ConvertTo-Json -Compress";
    try{return JSON.parse(execFileSync('powershell.exe',['-NoProfile','-NonInteractive','-Command',script],{encoding:'utf8',timeout:8000,stdio:['ignore','pipe','ignore'],env:{...process.env,NIGHTOWL_DIAG_PROFILE:profile}}).trim());}catch{return null;}
}
async function launchChrome({missing=false}={}) {
    const profile=fs.mkdtempSync(path.join(os.tmpdir(),'nightowl-chrome-'));
    const resolved=fs.realpathSync(profile),temp=fs.realpathSync(os.tmpdir());
    if(path.dirname(resolved)!==temp || !path.basename(resolved).startsWith('nightowl-chrome-')) throw new Error('profile_invalid');
    let chrome,cdp,exitCode=null,spawnError=null;const started=performance.now(),categories=new Set();
    const result={phase:'launch'};
    async function cleanup() {
        if(cdp){try{await cdp.call('Browser.close');}catch{} cdp.ws.close();}
        if(chrome && chrome.pid && chrome.exitCode===null){await Promise.race([new Promise(r=>chrome.once('exit',r)),sleep(5000)]);if(chrome.exitCode===null){try{execFileSync('taskkill.exe',['/PID',String(chrome.pid),'/T','/F'],{timeout:5000,stdio:'ignore'});}catch{} await Promise.race([new Promise(r=>chrome.once('exit',r)),sleep(3000)]);}}
        result.process_exited=!chrome || !chrome.pid || chrome.exitCode!==null;
        result.remaining_profile_processes=countProfileProcesses(profile);
        try{fs.rmSync(resolved,{recursive:true,force:true});result.profile_removed=!fs.existsSync(resolved);}catch{result.profile_removed=false;}
        console.log('CHROME_CDP_CLEANUP='+JSON.stringify({process_exited:result.process_exited,remaining_profile_processes:result.remaining_profile_processes,profile_removed:result.profile_removed}));
        if(!result.process_exited || result.remaining_profile_processes!==0 || !result.profile_removed) throw new Error('cleanup_failed');
    }
    try {
        const executable=missing?path.join(profile,'missing.exe'):process.env.NIGHTOWL_TEST_CHROME;
        if(!executable || !path.isAbsolute(executable)) throw new Error('chrome_not_configured');
        if(!fs.existsSync(executable) || !fs.statSync(executable).isFile()) throw new Error('chrome_not_found');
        chrome=spawn(executable,['--headless=new','--remote-debugging-port=0',`--user-data-dir=${profile}`,'--no-first-run','--no-default-browser-check','--disable-sync','--disable-background-networking','--disable-background-timer-throttling','--disable-renderer-backgrounding','--disable-backgrounding-occluded-windows','--disable-features=BackForwardCache','--enable-logging=stderr','about:blank'],{windowsHide:true,stdio:['ignore','ignore','pipe']});
        chrome.stderr.on('data',data=>{const s=data.toString();if(/access is denied|access_denied/i.test(s))categories.add('access_denied');if(/sandbox/i.test(s))categories.add('sandbox');if(/profile|user.data/i.test(s))categories.add('profile');if(/failed|error/i.test(s))categories.add('other_error');});
        chrome.on('exit',code=>{exitCode=code;});chrome.on('error',error=>{spawnError=error.code||'spawn_failed';});
        const portFile=path.join(profile,'DevToolsActivePort');
        for(let i=0;i<100 && !fs.existsSync(portFile) && !spawnError && exitCode===null;i++) await sleep(100);
        result.elapsed_ms=Math.round(performance.now()-started);result.process_running=!!chrome.pid && exitCode===null;result.exit_code=exitCode;result.categories=[...categories];
        if(!fs.existsSync(portFile)) throw new Error(spawnError?'spawn_failed':'port_unavailable');
        const [port,endpoint]=fs.readFileSync(portFile,'utf8').trim().split(/\r?\n/);
        if(!/^\d+$/.test(port)||!/^\/devtools\/browser\/[a-z0-9-]+$/i.test(endpoint)) throw new Error('cdp_endpoint_invalid');
        result.phase='cdp_connect';const ws=new WebSocket(`ws://127.0.0.1:${port}${endpoint}`);
        await new Promise((resolve,reject)=>{ws.addEventListener('open',resolve,{once:true});ws.addEventListener('error',()=>reject(new Error('cdp_connect_failed')),{once:true});});
        cdp=new CDP(ws);
        const rows=execFileSync('netstat.exe',['-ano','-p','tcp'],{encoding:'utf8',timeout:5000,stdio:['ignore','pipe','ignore']});
        const owned=rows.split(/\r?\n/).some(row=>{const m=row.trim().match(/^TCP\s+127\.0\.0\.1:(\d+)\s+\S+\s+(?:LISTENING|ESTABLISHED)\s+(\d+)$/i);return m && m[1]===port && Number(m[2])===chrome.pid;});
        if(!owned) throw new Error('cdp_process_mismatch');
        const version=await cdp.call('Browser.getVersion');if(!/^Chrome\//.test(version.product)) throw new Error('cdp_version_invalid');
        result.phase='ready';result.chrome_version=version.product;console.log('CHROME_CDP_LAUNCH='+JSON.stringify(result));
        return {cdp,cleanup};
    } catch(error) {
        const allowed=['chrome_not_configured','chrome_not_found','spawn_failed','port_unavailable','cdp_endpoint_invalid','cdp_connect_failed','cdp_process_mismatch','cdp_version_invalid','cdp_timeout','cdp_command_failed'];
        result.error=allowed.includes(error.message)?error.message:'launch_failed';
        console.log('CHROME_CDP_LAUNCH='+JSON.stringify(result));
        await cleanup();throw new Error(result.error);
    }
}
module.exports={launchChrome,sleep};
