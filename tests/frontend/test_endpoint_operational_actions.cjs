const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const source = fs.readFileSync('static/js/endpoint_detail.js','utf8');
const start = source.indexOf('    function runEndpointAction(action, origin) {');
const end = source.indexOf('    function buildEndpointSummary(',start);
assert(start >= 0 && end > start);
const calls=[], toasts=[];
let answer='AllowedSvc';
const labels={flush_dns:'Limpar DNS',query_service:'Consultar',gpupdate:'Política',start_service:'Iniciar',stop_service:'Parar',restart_service:'Reiniciar'};
const detail={source:'real',operationalActions:{start_service:{enabled:true,services:['AllowedSvc']},gpupdate:{enabled:true},stop_service:{enabled:false}}};
const ctx={endpointDetail:detail,operationalLabels:labels,asObject:x=>x&&typeof x==='object'?x:{},root:{dataset:{endpointId:'endpoint-1'}},
 window:{prompt:()=>answer,confirm:()=>true},showToast:x=>toasts.push(x),getCookie:()=> 'csrf',schedulePolling(){},reloadEndpoint(){},
 fetch:(url,opts)=>{calls.push({url,opts});return Promise.resolve({ok:true,json:async()=>({job_id:'id',status:'queued'})});},encodeURIComponent,JSON,Error};
vm.runInNewContext(source.slice(start,end)+';this.run=runEndpointAction;',ctx);
(async()=>{
 ctx.run('start_service'); await new Promise(setImmediate);
 assert.equal(calls[0].url,'/api/agent/operational-actions/endpoint-1/');
 assert.deepEqual(JSON.parse(calls[0].opts.body),{action_id:'start_service',action_version:1,parameters:{service_name:'AllowedSvc'}});
 answer='Unapproved';ctx.run('start_service');assert.equal(calls.length,1);
 ctx.run('stop_service');assert.equal(calls.length,1);
 ctx.run('gpupdate');await new Promise(setImmediate);
 assert.match(toasts.at(-1),/aplicação da política ainda não confirmada/);
 assert.deepEqual(JSON.parse(calls[1].opts.body),{action_id:'gpupdate',action_version:1,parameters:{}});
 console.log('operational actions: typed request, scope gating and gpupdate feedback OK');
})().catch(e=>{console.error(e);process.exitCode=1});
