const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const path=require('node:path');
class El {
 constructor(tag='div'){this.tag=tag;this.children=[];this.dataset={};this.events={};this.disabled=false;this.hidden=false;this.textContent='';this.parent=null;}
 append(...nodes){this.children.push(...nodes);for(const node of nodes)node.parent=this;}
 replaceChildren(...nodes){this.children=[];this.append(...nodes)}
 remove(){if(this.parent)this.parent.children=this.parent.children.filter(node=>node!==this)}
 querySelector(selector){return this.nodes?.[selector]||null}
 addEventListener(name,handler){this.events[name]=handler} setAttribute(){} focus(){}
}
const available={script_id:'environment_diagnostic',version:1,sha256:'a'.repeat(64),description:'Read only',approved:true,revoked:false,parameter_schema:{type:'object',properties:{},additionalProperties:false}};
const scriptsCss=fs.readFileSync(path.join(__dirname,'../../static/css/endpoint_actions_jobs.css'),'utf8');
assert.match(scriptsCss,/\.endpoint-approved-scripts-body\s*\{[^}]*grid-template-columns:\s*repeat\(2,\s*minmax\(0,\s*1fr\)\)/,'approved scripts use two desktop columns');
assert.match(scriptsCss,/\.endpoint-approved-script-row\s*\{[^}]*min-height:\s*132px/,'script cards have a consistent minimum height');
assert.match(scriptsCss,/@media\s*\(max-width:\s*720px\)[\s\S]*?\.endpoint-approved-scripts-body\s*\{[^}]*grid-template-columns:\s*minmax\(0,\s*1fr\)/,'approved scripts stack on small screens');
const revoked={...available,script_id:'revoked_diagnostic',sha256:'b'.repeat(64),revoked:true};
const placement=new El(),root=new El(),tasksButton=new El();root.dataset.endpointId='abc';
root.nodes={'[data-approved-scripts-placement]':placement};
root.addEventListener=(name,handler)=>root.events[name]=handler;
root.dispatchEvent=event=>events.push(event.type);
const body=new El(),events=[],calls=[];
const doc={body,cookie:'csrftoken=token',activeElement:new El(),querySelector:s=>s==='[data-endpoint-detail]'?root:null,
 getElementById:id=>({textContent:JSON.stringify(id==='approved-script-availability'?{
 [`${available.script_id}:1:${available.sha256}`]:true,[`${revoked.script_id}:1:${revoked.sha256}`]:true}: {})}),
 createElement:tag=>new El(tag),addEventListener(){},removeEventListener(){}};
const ctx={document:doc,CustomEvent:class{constructor(type){this.type=type}},encodeURIComponent,JSON,Object,Array,
 fetch:async(url,options)=>{calls.push({url,options});return url.includes('approved-scripts')?{ok:true,json:async()=>({scripts:[available,revoked]})}:{ok:true,json:async()=>({job_id:'job'})};}};
vm.runInNewContext(fs.readFileSync('static/js/endpoint_approved_scripts.js','utf8'),ctx);
(async()=>{
 assert.equal(placement.children[0].textContent,'Abra a categoria para consultar o catálogo aprovado.');
 root.events.click({target:{closest:selector=>selector==='[data-endpoint-tab="tasks"]'?tasksButton:null}});
 await new Promise(setImmediate);
 assert.equal(calls.filter(call=>call.url==='/api/agent/approved-scripts/').length,1);
 assert.equal(placement.children.length,2,'catalog is mounted in stable slot once');
 const row=placement.children[0],button=row.children[1];assert.equal(button.disabled,false);button.events.click();
 const dialog=body.children[0].children[0],footer=dialog.children.at(-1),submit=footer.children[1];
 assert(dialog.children.some(child=>child.textContent==='Este script não exige parâmetros.'));
 await submit.events.click();
 assert.deepEqual(JSON.parse(calls[1].options.body),{action_id:'run_approved_script',action_version:1,parameters:{script_id:available.script_id,script_version:1,sha256:available.sha256,parameters:{}}});
 assert(events.includes('nightowl:approved-script-queued'));assert.equal(body.children.length,0);
 assert.equal(placement.children[1].children[1].disabled,true,'revoked version remains blocked');
 assert(!ctx.MutationObserver,'approved scripts no longer depend on dynamic panel observation');
 console.log('approved scripts: stable slot, pinned execution and revocation guard OK');
})().catch(error=>{console.error(error);process.exitCode=1});
