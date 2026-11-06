/* Sanitized, bounded snapshot of the test-owned Chrome tree. */
const {execFile} = require('node:child_process');
const http = require('node:http');
const path = require('node:path');

function httpProbe(listener) {
    return new Promise(resolve=>{
        const result={pid:listener.pid,port:listener.port,cdp_response:false};
        const request=http.get({host:'127.0.0.1',port:listener.port,path:'/json/version',timeout:700},response=>{
            let body=''; response.on('data',chunk=>{body+=chunk; if(body.length>16384) request.destroy();});
            response.on('end',()=>{try {const data=JSON.parse(body); result.cdp_response=typeof data.Browser==='string' &&
                /^Chrome\//.test(data.Browser) && typeof data.webSocketDebuggerUrl==='string';} catch {}
                result.status_code=response.statusCode; resolve(result);});
        });
        request.on('timeout',()=>request.destroy()); request.on('error',()=>resolve(result));
    });
}

async function snapshot(pid, profile, scenario) {
    if (!Number.isSafeInteger(pid) || pid<=0) return {scenario,category:'process_unavailable'};
    const script=path.join(__dirname,'Get-ChromeSnapshot.ps1');
    const result=await new Promise(resolve=>execFile('powershell.exe',['-NoProfile','-NonInteractive','-File',script],
        {timeout:20000,maxBuffer:262144,windowsHide:true,env:{...process.env,
            NIGHTOWL_SNAPSHOT_PID:String(pid),NIGHTOWL_SNAPSHOT_PROFILE:profile}},(error,stdout)=>{
            if(error) return resolve({scenario,category:error.killed?'snapshot_timeout':'snapshot_unavailable'});
            try {resolve({...JSON.parse(stdout),scenario});} catch {resolve({scenario,category:'snapshot_invalid'});}
        }));
    // netstat exposes the owning PID without administrator rights; no raw
    // addresses or responses are logged, and only this Chrome tree is probed.
    const pids=new Set((result.processes || []).map(p=>p.pid));
    pids.add(pid);
    const listeners=await new Promise(resolve=>execFile('netstat.exe',['-ano','-p','tcp'],
        {timeout:5000,maxBuffer:1048576,windowsHide:true},(error,stdout)=>{
            if(error) return resolve(null);
            const matches=[];
            for(const line of stdout.split(/\r?\n/)) {
                const m=line.trim().match(/^TCP\s+127\.0\.0\.1:(\d+)\s+\S+\s+LISTENING\s+(\d+)$/i);
                if(m && pids.has(Number(m[2]))) matches.push({pid:Number(m[2]),port:Number(m[1])});
            }
            resolve(matches);
        }));
    result.netstat_listeners=listeners;
    result.cdp_probes=Array.isArray(listeners)?await Promise.all(listeners.slice(0,8).map(httpProbe)):null;
    return result;
}
module.exports={snapshot};
