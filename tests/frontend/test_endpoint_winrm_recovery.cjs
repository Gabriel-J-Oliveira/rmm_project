const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
class El{constructor(){this.children=[];this.dataset={};this.listeners={};this.hidden=false;this.value='';this.textContent='';}append(...x){this.children.push(...x)}prepend(...x){this.children.unshift(...x)}replaceChildren(...x){this.children=x}setAttribute(){}focus(){}addEventListener(k,f){this.listeners[k]=f}querySelector(s){return this.nodes?.[s]||this.children.find(x=>s==='[data-winrm-recovery-card]'&&Object.hasOwn(x.dataset,'winrmRecoveryCard'))||null}}
const root=new El(),section=new El(),context=new El(),indicator=new El(),modal=new El(),form=new El(),body=new El();
context.dataset={endpoint:'e1',user:'u1',agentStatus:'Offline',statusUrl:'/status/'};
root.querySelector=s=>s.includes('tasks')?section:null;section.nodes={};
const known=new El(),cancel=new El(),feedback=new El(),submit=new El();
form.nodes={'[data-recovery-known]':known,'[data-recovery-cancel]':cancel,'[data-winrm-recovery-status]':feedback,'button[type="submit"]':submit};
form.elements={username:new El(),password:new El(),restart_running:{checked:false},csrfmiddlewaretoken:{value:'csrf'}};form.action='/recover/';
const doc={body,querySelector:s=>({'[data-endpoint-detail]':root,'[data-winrm-context]':context,'[data-winrm-indicator]':indicator,'[data-winrm-recovery]':form,'[data-recovery-modal]':modal})[s],getElementById:()=>({textContent:'{}'}),createElement:()=>new El(),addEventListener(){}};
let complete, calls=[]; const job={id:'j1',status:'FAILED',stage:'WAITING_HEARTBEAT',error_code:'HEARTBEAT_TIMEOUT',started_at:'2026-01-01T00:00:00Z',finished_at:'2026-01-01T00:00:01Z'};
const store=new Map(),ctx={document:doc,window:{addEventListener(){}},location:{protocol:'https:'},MutationObserver:class{observe(){}},AbortController,Date,JSON,Number,Math,setTimeout,clearTimeout,
 localStorage:{getItem:k=>store.get(k),setItem:(k,v)=>store.set(k,v)},
 fetch:(url,opts)=>{calls.push({url,opts});if(url==='/status/')return Promise.resolve({ok:true,json:async()=>({can_recover:true,latest_recovery:job})});return new Promise(r=>complete=r);}};
vm.runInNewContext(fs.readFileSync('static/js/endpoint_winrm_recovery.js','utf8'),ctx);
(async()=>{await new Promise(setImmediate);const card=section.children[0];assert.equal(card.children.at(-2).disabled,false);card.children.at(-2).listeners.click();
 form.elements.username.value='LAB\\admin';form.elements.password.value='fixture-secret';
 const inflight=form.listeners.submit({preventDefault(){}});assert.equal(form.elements.password.value,'');
 assert.equal(JSON.parse(calls.at(-1).opts.body).restart_running,false);
 complete({ok:true,json:async()=>({status:'HEARTBEAT_TIMEOUT',job_id:'j1'})});await inflight;await new Promise(setImmediate);
 const drawer=body.children[1],content=drawer.children[1];assert.equal(drawer.hidden,false);
 assert(content.children.some(x=>x.textContent==='Sem confirmação'));
 assert(!JSON.stringify([...store]).includes('fixture-secret'));
 assert.equal(calls.filter(x=>x.url==='/recover/').length,1);
 console.log('WinRM recovery: credential cleanup, timeout state and no retry OK');
})().catch(e=>{console.error(e);process.exitCode=1});
