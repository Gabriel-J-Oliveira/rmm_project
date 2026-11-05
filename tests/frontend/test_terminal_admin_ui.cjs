const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
class Element {
    constructor() { this.hidden=false; this.value=''; this.textContent=''; this.children=[]; this.listeners={}; this.dataset={}; this.classList={toggle(){}}; }
    addEventListener(name, fn) { this.listeners[name]=fn; }
    click() { this.listeners.click?.({preventDefault(){}}); }
    focus() {} append(...nodes) { this.children.push(...nodes); } replaceChildren(...nodes) { this.children=nodes; }
    querySelector(selector) { return this.nodes?.[selector]; }
}
const panel=new Element(), nodes={};
for (const key of ['[data-status]','[data-screen]','[data-identity]','[data-drawer]','[data-drawer-shade]','[data-auth-modal]','[data-terminal-modal]','[data-accounts]','[data-drawer-close]','[data-new-account]','[data-terminal-close]','[data-maximize]','[data-terminal-dialog]','[data-cancel]']) nodes[key]=new Element();
panel.nodes=nodes; panel.dataset={url:'/session',user:'operator-1',endpoint:'endpoint-1'};
const form=new Element(), submit=new Element(); form.elements={password:new Element(),account:new Element(),domain:new Element(),csrfmiddlewaretoken:{value:'csrf'}}; form.nodes={'[type=submit]':submit}; nodes.form=form;
const open=new Element(), storage=new Map();
const document={querySelector:s=>s==='[data-admin-auth]'?panel:s==='[data-terminal-open]'?open:null,createElement:()=>new Element(),addEventListener(){}};
let ws;
class Socket { constructor() { ws=this; this.readyState=1; this.bufferedAmount=0; this.sent=[]; } send(x){this.sent.push(x)} close(){this.closed=true} }
Socket.OPEN=1;
class Term { constructor(){this.parser={registerOscHandler(){}}; this.cols=80; this.rows=24} loadAddon(){} open(){} onData(){} resize(){} focus(){} dispose(){this.disposed=true} write(){} }
const calls=[];
const context={document,location:{protocol:'https:',hostname:'lab',origin:'https://lab'},localStorage:{getItem:k=>storage.get(k),setItem:(k,v)=>storage.set(k,v)},
 fetch:async (url,opts)=>{calls.push([url,opts]); return {ok:true,json:async()=>({session_id:'00000000-0000-0000-0000-000000000001',mode:'admin_auth',execution:true,websocket_path:'/ws/terminal/00000000-0000-0000-0000-000000000001/operator/',credential:'ticket'})}},
 WebSocket:Socket,Terminal:Term,FitAddon:{FitAddon:class{fit(){}}},ResizeObserver:class{observe(){} disconnect(){}}, URL,TextEncoder,Uint8Array,DataView,JSON,BigInt,
 setTimeout,clearTimeout,window:{addEventListener(){}}};
vm.runInNewContext(fs.readFileSync('static/js/terminal_admin_auth.js','utf8'),context);
(async()=>{
 open.click(); nodes['[data-new-account]'].click();
 form.elements.account.value='LAB\\Admin'; form.elements.password.value='synthetic-secret';
 await form.listeners.submit({preventDefault(){}}); ws.onopen();
 ws.onmessage({data:JSON.stringify({type:'transport_ready',mode:'admin_auth'})});
 assert.equal(form.elements.password.value,'');
 assert.equal(storage.size,0,'username must not persist before ready');
 assert.equal(JSON.parse(ws.sent[1]).account,'Admin'); assert.equal(JSON.parse(ws.sent[1]).domain,'LAB');
 ws.onmessage({data:JSON.stringify({type:'ready',mode:'admin_auth',execution:true,identity:{sid:'S-1-5-21-1-2-3-1001',integrity:12288,elevated:true,windows_session_id:1}})});
 assert.equal(nodes['[data-terminal-modal]'].hidden,false);
 assert.deepEqual(JSON.parse(storage.get('nightowl.terminal.accounts.operator-1.endpoint-1')),['LAB\\Admin']);
 assert(!JSON.stringify([...storage]).includes('synthetic-secret'));
 nodes['[data-terminal-close]'].click(); assert(ws.closed); assert.equal(form.elements.password.value,'');
 assert(calls.some(([url])=>url.includes('/cancel/')));
 open.click(); nodes['[data-new-account]'].click(); form.elements.account.value='Rejected'; form.elements.password.value='bad-secret';
 await form.listeners.submit({preventDefault(){}}); ws.onopen();
 ws.onmessage({data:JSON.stringify({type:'transport_ready',mode:'admin_auth'})});
 ws.onmessage({data:JSON.stringify({type:'windows_auth_result',result:'rejected'})});
 assert.equal(form.elements.password.value,'');
 assert.deepEqual(JSON.parse(storage.get('nightowl.terminal.accounts.operator-1.endpoint-1')),['LAB\\Admin']);
 assert(!JSON.stringify([...storage]).includes('bad-secret'));
 console.log('terminal admin UI: credential cleanup, ready gating, scoped history and close OK');
})().catch(e=>{console.error(e);process.exitCode=1});
