const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
require('../../static/js/job_display.js');
const source=fs.readFileSync('static/js/endpoint_detail.js','utf8');
const start=source.indexOf('    function renderJobItem(job, isActive) {'),end=source.indexOf('    function ',start+15);
assert(start>=0&&end>start);
const ctx={window:{NightOwlJobDisplay:globalThis.NightOwlJobDisplay},jobProgress:globalThis.NightOwlJobDisplay.measuredProgress,
 jobResultLabel:j=>j.errorMessage||'',formatDate:v=>v||'-',formatDuration:()=>'-',staleReasonLabel:x=>x||'',
 escapeHtml:x=>String(x??''),jobBadge:(s,t)=>`<b>${t}</b>`,jobType:()=>'',cancelUninstallJobButton:()=>''};
vm.runInNewContext(source.slice(start,end)+';this.render=renderJobItem;',ctx);
for(const status of ['queued','running','completed','failed','expired','cancelled']){
 const html=ctx.render({status,progressPercentage:100,id:'id'},false);
 assert(!html.includes('100%'),`synthetic percentage rendered for ${status}`);
 if(status==='failed')assert(html.includes('Falhou'));
 assert(html.includes('endpoint-job-actions'));
 assert(html.indexOf('endpoint-job-card-content') < html.indexOf('endpoint-job-actions'));
}
assert(ctx.render({status:'running',isStale:true,id:'id'},false).includes('Resultado n&atilde;o confirmado'));
assert(!ctx.render({status:'completed',id:'id'},false).includes('data-copy-job='), 'copy is omitted without output');
assert(ctx.render({status:'completed',id:'id',stdout:'safe output'},false).includes('>Copiar</button>'));
const listStart=source.indexOf('    function renderJobs(jobs, limit) {'),listEnd=source.indexOf('    function renderJobItem(job, isActive) {',listStart);
const listContext={endpointDetail:{activeJob:null},renderJobItem:job=>`<article>${job.id}</article>`,emptyState:()=>'<p>EMPTY</p>'};
vm.runInNewContext(source.slice(listStart,listEnd)+';this.render=renderJobs;',listContext);
assert.match(listContext.render([],80),/EMPTY/,'empty history has an explicit empty state');
assert.equal((listContext.render([{id:'one'}],80).match(/<article>/g)||[]).length,1,'single job leaves remaining panel space empty');
assert.equal((listContext.render(Array.from({length:20},(_,i)=>({id:String(i)})),5).match(/<article>/g)||[]).length,5,'history count is bounded');
listContext.endpointDetail.activeJob={id:'active'};
const withActive=listContext.render([{id:'old'}],80);
assert(withActive.includes('Em andamento')&&withActive.includes('active')&&withActive.includes('old'));
console.log('endpoint job rendering: no synthetic percentages OK');
