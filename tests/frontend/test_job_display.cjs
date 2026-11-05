const assert=require('node:assert/strict');
require('../../static/js/job_display.js');
const display=globalThis.NightOwlJobDisplay;
for(const [job,label] of [
 [{status:'queued'},'Em fila'],[{status:'dispatched',rawStatus:'sent'},'Entregue ao agente'],
 [{status:'running',startedAt:'2026-01-01T00:00:00Z'},'Executando'],
 [{status:'completed'},'Concluído com sucesso'],[{status:'failed'},'Falhou'],
 [{status:'timed_out',rawStatus:'expired'},'Expirado'],[{status:'cancelled'},'Cancelado'],
 [{status:'outcome_unknown'},'Resultado não confirmado'],[{status:'completed',rawStatus:'duplicate'},'Resultado duplicado']
]) assert.equal(display.status(job),label);
assert.equal(display.unconfirmed({status:'running',isStale:true}),true);
assert.equal(display.unconfirmed({status:'failed',isStale:true}),false);
const milestones=display.milestones({createdAt:'a',dispatchedAt:'b',startedAt:'c',finishedAt:'d',status:'failed'});
assert.deepEqual(milestones.map(x=>x.label),['Criado','Entregue ao agente','Execução iniciada','Falhou']);
assert.equal(display.measuredProgress({status:'failed',progressPercentage:100}),null);
assert.equal(display.measuredProgress({status:'running',progressPercentage:60}),null);
console.log('job display: states, milestones, unknown and synthetic percentages OK');
