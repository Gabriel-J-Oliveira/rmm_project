/* Sanitized, bounded snapshot of the test-owned Chrome tree. */
const {execFile} = require('node:child_process');
const http = require('node:http');
const path = require('node:path');

function httpProbe(listener) {
    return new Promise(resolve=>{
        const result={pid:listener.pid,port:listener.port,cdp_response:false};
        let settled=false;
        const finish=()=>{if(!settled){settled=true;clearTimeout(watchdog);resolve(result);}};
        const watchdog=setTimeout(()=>{request.destroy();finish();},900);
        const request=http.get({host:'127.0.0.1',port:listener.port,path:'/json/version',timeout:700},response=>{
            let body=''; response.on('data',chunk=>{body+=chunk; if(body.length>16384) request.destroy();});
            response.on('end',()=>{try {const data=JSON.parse(body); result.cdp_response=typeof data.Browser==='string' &&
                /^Chrome\//.test(data.Browser) && typeof data.webSocketDebuggerUrl==='string';} catch {}
                result.status_code=response.statusCode; finish();});
            response.on('aborted',finish); response.on('error',finish); response.on('close',finish);
        });
        request.on('timeout',()=>{request.destroy();finish();}); request.on('error',finish);
    });
}

function ownedListeners(pids) {
    return new Promise(resolve=>execFile('netstat.exe',['-ano','-p','tcp'],
        {timeout:5000,maxBuffer:1048576,windowsHide:true},(error,stdout)=>{
            if(error) return resolve(null);
            const matches=[];
            for(const line of stdout.split(/\r?\n/)) {
                const m=line.trim().match(/^TCP\s+127\.0\.0\.1:(\d+)\s+\S+\s+(?:LISTENING|ESTABLISHED)\s+(\d+)$/i);
                if(m && pids.has(Number(m[2]))) matches.push({pid:Number(m[2]),port:Number(m[1])});
            }
            resolve(matches);
        }));
}

async function snapshot(pid, profile, scenario) {
    if (!Number.isSafeInteger(pid) || pid<=0) return {scenario,category:'process_unavailable'};
    // Probe root-owned ports immediately, before the slower Windows snapshot.
    const firstCheckedUtc=new Date().toISOString();
    const initialListeners=await ownedListeners(new Set([pid]));
    const initialProbes=Array.isArray(initialListeners)?await Promise.all(initialListeners.slice(0,8).map(httpProbe)):null;
    const script=path.join(__dirname,'Get-ChromeSnapshot.ps1');
    const result=await new Promise(resolve=>execFile('powershell.exe',['-NoProfile','-NonInteractive','-File',script],
        {timeout:20000,maxBuffer:262144,windowsHide:true,env:{...process.env,
            NIGHTOWL_SNAPSHOT_PID:String(pid),NIGHTOWL_SNAPSHOT_PROFILE:profile}},(error,stdout)=>{
            const lines=String(stdout || '').split(/\r?\n/).filter(Boolean);
            const earlyLine=lines.find(line=>line.startsWith('CHROME_SNAPSHOT_EARLY='));
            let early=null; try {if(earlyLine) early=JSON.parse(earlyLine.slice('CHROME_SNAPSHOT_EARLY='.length));} catch {}
            if(error) return resolve({scenario,category:error.killed?'snapshot_timeout':'snapshot_unavailable',early});
            try {resolve({...JSON.parse(lines.at(-1)),scenario,early});} catch {resolve({scenario,category:'snapshot_invalid',early});}
        }));
    // No raw addresses or HTTP responses are logged, and only this tree is probed.
    const pids=new Set((result.processes || []).map(p=>p.pid)); pids.add(pid);
    const listeners=await ownedListeners(pids);
    result.initial_checked_utc=firstCheckedUtc;
    result.initial_listeners=initialListeners;
    result.initial_cdp_probes=initialProbes;
    result.netstat_listeners=listeners;
    result.cdp_probes=Array.isArray(listeners)?await Promise.all(listeners.slice(0,8).map(httpProbe)):null;
    return result;
}
module.exports={snapshot};
