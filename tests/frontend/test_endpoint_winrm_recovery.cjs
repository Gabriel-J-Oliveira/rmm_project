const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
class El {
 constructor(){this.children=[];this.dataset={};this.listeners={};this.hidden=false;this.disabled=false;this.value='';this.textContent='';this.title='';this.elements={};}
 append(...items){this.children.push(...items)} prepend(...items){this.children.unshift(...items)}
 replaceChildren(...items){this.children=items} setAttribute(){} focus(){}
 addEventListener(name,handler){this.listeners[name]=handler}
 querySelector(selector){return this.nodes?.[selector]||null}
}
const root=new El(),context=new El(),indicator=new El(),summary=new El(),reason=new El(),openButton=new El(),followButton=new El(),form=new El(),modal=new El(),known=new El(),cancel=new El(),feedback=new El(),submit=new El(),body=new El();
context.dataset={endpoint:'e1',user:'u1',agentStatus:'Offline',statusUrl:'/status/'};
root.nodes={'[data-winrm-indicator]':indicator,'[data-winrm-recovery-summary]':summary,'[data-winrm-recovery-reason]':reason,
 '[data-winrm-recovery-open]':openButton,'[data-winrm-recovery-follow]':followButton,'[data-winrm-recovery]':form,'[data-recovery-modal]':modal};
form.nodes={'[data-recovery-known]':known,'[data-recovery-cancel]':cancel,'[data-winrm-recovery-status]':feedback,'button[type="submit"]':submit};
form.elements={username:new El(),password:new El(),restart_running:{checked:false},csrfmiddlewaretoken:{value:'csrf'}};form.action='/recover/';
const documentEvents={};
const doc={body,querySelector:s=>s==='[data-endpoint-detail]'?root:s==='[data-winrm-context]'?context:null,
 getElementById:()=>({textContent:'{}'}),createElement:()=>new El(),addEventListener:(name,fn)=>{documentEvents[name]=fn},removeEventListener(){}};
let complete,calls=[];const job={id:'j1',status:'FAILED',stage:'WAITING_HEARTBEAT',error_code:'HEARTBEAT_TIMEOUT',started_at:'2026-01-01T00:00:00Z',finished_at:'2026-01-01T00:00:01Z'};
const store=new Map(),ctx={document:doc,window:{addEventListener(){}},location:{protocol:'https:'},AbortController,Date,JSON,Number,Math,setTimeout,clearTimeout,
 localStorage:{getItem:k=>store.get(k),setItem:(k,v)=>store.set(k,v)},
 fetch:(url,opts)=>{calls.push({url,opts});if(url==='/status/')return Promise.resolve({ok:true,json:async()=>({can_recover:true,latest_recovery:job})});return new Promise(r=>complete=r);}};
vm.runInNewContext(fs.readFileSync('static/js/endpoint_winrm_recovery.js','utf8'),ctx);
(async()=>{
 await new Promise(setImmediate);
 assert.equal(openButton.disabled,false,'backend eligibility enables recovery only after status response');
 assert.match(summary.textContent,/Agente: Offline/);
 assert.equal(reason.hidden,true);
 documentEvents['nightowl:winrm-checking']();assert.match(indicator.textContent,/Verificando/);
 documentEvents['nightowl:winrm-checked']({detail:{tcp:'open',wsman:'reachable',authentication:'not_tested',checked_at:'2026-11-06T12:00:00Z'}});
 assert.match(indicator.title,/TCP: open/);
 openButton.listeners.click();assert.equal(modal.hidden,false);
 form.elements.username.value='LAB\\admin';form.elements.password.value='fixture-secret';
 const inflight=form.listeners.submit({preventDefault(){}});assert.equal(form.elements.password.value,'');
 assert.equal(JSON.parse(calls.at(-1).opts.body).restart_running,false);
 complete({ok:true,json:async()=>({status:'HEARTBEAT_TIMEOUT',job_id:'j1'})});await inflight;await new Promise(setImmediate);
 const drawer=body.children[1],content=drawer.children[1];assert.equal(drawer.hidden,false);
 assert(content.children.some(x=>x.textContent.includes('Resultado sem confirmação')));
 assert(!JSON.stringify([...store]).includes('fixture-secret'));
 assert.equal(calls.filter(x=>x.url==='/recover/').length,1);
 console.log('WinRM recovery: stable mount, backend eligibility, status summary and credential cleanup OK');
})().catch(e=>{console.error(e);process.exitCode=1});
