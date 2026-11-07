const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const source=fs.readFileSync('static/js/endpoint_detail.js','utf8');
const start=source.indexOf('    function renderTaskPanel(detail) {');
const end=source.indexOf('    function renderActivePanel()',start);
assert(start>=0&&end>start);
let coreWrites=0,maintenanceWrites=0,criticalWrites=0;
const core={dataset:{},set innerHTML(value){coreWrites++;this.html=value},get innerHTML(){return this.html}};
const maintenance={set innerHTML(value){maintenanceWrites++;this.html=value},get innerHTML(){return this.html}};
const critical={set innerHTML(value){criticalWrites++;this.html=value},get innerHTML(){return this.html}};
const scroll={scrollTop:73};
const jobs={innerHTML:''};
const taskTarget={querySelector(selector){return ({'[data-endpoint-actions-core]':core,'[data-endpoint-actions-maintenance]':maintenance,
 '[data-endpoint-actions-critical]':critical,'[data-endpoint-jobs-scroll]':scroll,'[data-endpoint-jobs-content]':jobs})[selector]||null}};
const context={panel:()=>taskTarget,renderTasks:()=>({core:'stable-actions',maintenance:'stable-maintenance',critical:'stable-critical'}),
 renderJobs:rows=>rows.map(row=>row.id).join(',')};
vm.runInNewContext(source.slice(start,end)+';this.render=renderTaskPanel;',context);
context.render({operationalActions:{restart_service:{enabled:true}},agent:{lifecycleStatus:'installed'},jobs:[{id:'1'}]});
assert.equal(coreWrites,1);assert.equal(maintenanceWrites,1);assert.equal(criticalWrites,1);
assert.equal(jobs.innerHTML,'1');
context.render({operationalActions:{restart_service:{enabled:true}},agent:{lifecycleStatus:'installed'},jobs:[{id:'2'},{id:'3'}]});
assert.equal(coreWrites,1,'routine job refresh does not remount action controls');
assert.equal(maintenanceWrites,1);assert.equal(criticalWrites,1);
assert.equal(jobs.innerHTML,'2,3');assert.equal(scroll.scrollTop,73,'job refresh preserves internal scroll');
console.log('endpoint actions/jobs: stable controls and preserved history scroll on refresh OK');
