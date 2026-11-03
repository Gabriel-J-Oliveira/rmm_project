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
}
assert(ctx.render({status:'running',isStale:true,id:'id'},false).includes('Resultado não confirmado'));
console.log('endpoint job rendering: no synthetic percentages OK');
