const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
class El {
 constructor(tag='div'){this.tag=tag;this.children=[];this.dataset={};this.events={};this.disabled=false;this.textContent='';}
 append(...nodes){this.children.push(...nodes);for(const n of nodes)n.parent=this;}
 remove(){if(this.parent)this.parent.children=this.parent.children.filter(x=>x!==this);}
 querySelector(selector){if(selector==='[data-approved-scripts-card]')return this.children.find(c=>Object.hasOwn(c.dataset,'approvedScriptsCard'))||null;return null;}
 addEventListener(name,fn){this.events[name]=fn;} setAttribute(){} focus(){}
}
const script={script_id:'environment_diagnostic',version:1,sha256:'a'.repeat(64),description:'Read only',approved:true,revoked:false,parameter_schema:{type:'object',properties:{},additionalProperties:false}};
const section=new El(),root=new El();root.dataset.endpointId='abc';
root.querySelector=s=>s==='[data-endpoint-tab-panel="tasks"] > section'?section:s==='[data-approved-scripts-card]'?section.querySelector(s):null;
root.dispatchEvent=event=>events.push(event.type);
const body=new El(),events=[],calls=[];
const doc={body,cookie:'csrftoken=token',activeElement:new El(),querySelector:s=>s==='[data-endpoint-detail]'?root:null,getElementById:()=>({textContent:JSON.stringify({[`environment_diagnostic:1:${script.sha256}`]:true})}),createElement:tag=>new El(tag),addEventListener(){},removeEventListener(){}};
let notifyMutation;
const ctx={document:doc,MutationObserver:class{constructor(fn){notifyMutation=fn} observe(){}},CustomEvent:class{constructor(type){this.type=type}},encodeURIComponent,JSON,Object,Array,
 fetch:async(url,options)=>{calls.push({url,options});return url.includes('approved-scripts')?{ok:true,json:async()=>({scripts:[script]})}:{ok:true,json:async()=>({job_id:'job'})};}};
vm.runInNewContext(fs.readFileSync('static/js/endpoint_approved_scripts.js','utf8'),ctx);
(async()=>{
 root.events.click({target:{closest:()=>true}}); await new Promise(setImmediate);
 const card=section.querySelector('[data-approved-scripts-card]'), row=card.children[1], button=row.children.at(-1);
 assert.equal(button.disabled,false);button.events.click();
 const dialog=body.children[0].children[0],footer=dialog.children.at(-1),submit=footer.children[1];
 assert(dialog.children.some(c=>c.textContent==='Este script não exige parâmetros.'));
 await submit.events.click();
 assert.deepEqual(JSON.parse(calls[1].options.body),{action_id:'run_approved_script',action_version:1,parameters:{script_id:script.script_id,script_version:1,sha256:script.sha256,parameters:{}}});
 assert(events.includes('nightowl:approved-script-queued'));assert.equal(body.children.length,0);
 script.revoked=true;section.children=[];notifyMutation();
 assert.equal(section.children[0].children[1].children.at(-1).disabled,true);
 console.log('approved scripts: catalog, confirmation and pinned execution OK');
})().catch(e=>{console.error(e);process.exitCode=1});
