(function () {
    'use strict';
    const root = document.querySelector('[data-automations-root]');
    if (!root) return;
    const form = root.querySelector('[data-automation-form]');
    const field = name => form.elements.namedItem(name);
    const message = root.querySelector('[data-automation-message]');
    const definitions = new Map();
    const targets = new Map();
    let selected = new Set(), busy = false, refreshing = false, searchTimer;
    const labels = {active:'Ativa',paused:'Pausada',finished:'Agenda encerrada',cancelled:'Cancelada',enqueued:'Em fila',
        queued:'Em fila',sent:'Entregue',running:'Em execucao',completed:'Concluida',failed:'Falhou',skipped:'Ignorada',
        expired:'Expirada',blocked:'Bloqueada',interrupted:'Interrompida',timed_out:'Tempo esgotado',
        once:'Unica',daily:'Diaria',weekly:'Semanal',monthly:'Mensal',interval:'Intervalo'};
    const errors = {forbidden:'Sem permissao para esta operacao.',revision_conflict:'A automacao mudou. Atualize antes de editar.',
        scheduler_disabled:'Scheduler desabilitado.',invalid_schedule_range:'Revise o inicio e o termino.',
        jobs_v2_capability_unconfirmed:'Agente sem capacidade v2 comprovada.',endpoint_identity_changed:'Identidade do endpoint mudou.',
        overlap_blocked:'Outro job pendente ou em execucao.',endpoint_offline:'Endpoint offline na janela prevista.',
        window_elapsed:'Janela de validade encerrada.',operator_cancelled:'Cancelada pelo operador.',definition_cancelled:'Automacao cancelada.',
        already_dispatched_or_final:'Execucao entregue ou finalizada; cancelamento nao aplicado.'};
    function el(tag,text) { const node=document.createElement(tag); if(text!=null)node.textContent=String(text); return node; }
    function date(value,zone) { return value ? new Date(value).toLocaleString('pt-BR',{timeZone:zone||'UTC',timeZoneName:'short'}) : '--'; }
    function say(value) { message.textContent=errors[value] || value || ''; }
    async function request(url, body) {
        const response=await fetch(url,{credentials:'same-origin',cache:'no-store',signal:AbortSignal.timeout(10000),
            ...(body===undefined ? {} : {method:'POST',headers:{'Content-Type':'application/json','X-CSRFToken':field('csrfmiddlewaretoken').value},body:JSON.stringify(body)})});
        const data=await response.json();
        if(!response.ok)throw new Error(data.error || 'Falha ao consultar agendamentos.');
        return data;
    }
    function button(label,icon,action) {
        const node=el('button');node.className='automation-icon-button';node.type='button';node.title=label;node.setAttribute('aria-label',label);
        const glyph=el('i');glyph.dataset.lucide=icon;node.append(glyph);
        node.addEventListener('click',()=>perform(action));return node;
    }
    async function perform(action) {
        if(busy)return;busy=true;
        try { await action();say('Operacao registrada.');await refresh(); }
        catch(error){say(error.message);}finally{busy=false;}
    }
    function targetOptions() {
        const query=field('target_query').value.trim().toLowerCase();
        field('targets').replaceChildren();
        targets.forEach((target,id)=>{
            if(!selected.has(id) && query && !target.hostname.toLowerCase().includes(query))return;
            const option=el('option',target.hostname+(target.domain?' / '+target.domain:''));option.value=id;option.selected=selected.has(id);field('targets').append(option);
        });
        root.querySelector('[data-automation-selected]').textContent=selected.size+' / 50';
    }
    function controls() {
        const kind=field('kind').value;
        root.querySelectorAll('[data-recurrence]').forEach(node=>node.hidden=node.dataset.recurrence!==kind);
        root.querySelectorAll('[data-schedule-time]').forEach(node=>node.hidden=kind==='immediate');
        field('starts_at').required=kind!=='immediate';
        root.querySelectorAll('[data-parameter]').forEach(node=>node.hidden=node.dataset.parameter!==field('job_type').value);
    }
    async function refresh() {
        if(refreshing)return;refreshing=true;
        try {
            const history=root.querySelector('[data-automation-history]');
            const filter=history.value;
            const data=await request(root.dataset.url+(filter?filter+'/':''));
            definitions.clear();data.definitions.forEach(item=>definitions.set(item.id,item));
            root.querySelector('[data-automation-health]').textContent=!data.enabled?'Scheduler: desabilitado':data.health.stale?'Scheduler: sem rodada recente':'Scheduler: '+date(data.health.last_success_at)+(data.health.failed?' / falhas: '+data.health.failed:'');
            history.replaceChildren(el('option','Todas'));history.firstChild.value='';
            const tbody=root.querySelector('[data-automation-definitions]');tbody.replaceChildren();
            data.definitions.forEach(item=>{
                const option=el('option',item.name);option.value=item.id;history.append(option);
                const row=el('tr');
                [item.name+' / r'+item.revision,(labels[item.state]||item.state)+(item.last_error?' / '+item.last_error+' / tentativa '+item.retry_count:''),item.job_type+' / '+item.targets.length,
                    (labels[item.schedule.kind]||item.schedule.kind)+' / '+item.timezone,date(item.next_due_at,item.timezone)].forEach(text=>row.append(el('td',text)));
                const actions=el('td');actions.className='automation-row-actions';
                const send=action=>request(root.dataset.url+item.id+'/',{action,revision:item.revision,request_id:crypto.randomUUID()});
                if(['active','paused'].includes(item.state)) {
                    actions.append(button('Editar','pencil',()=>edit(item)),button(item.state==='active'?'Pausar':'Reativar',item.state==='active'?'pause':'play',()=>send(item.state==='active'?'pause':'resume')),
                        button('Cancelar proxima ocorrencia','calendar-x',()=>send('cancel_next')),button('Cancelar automacao','ban',()=>{if(confirm('Cancelar futuras execucoes de '+item.name+'?'))return send('cancel');}));
                    if(item.state==='active')actions.append(button('Executar agora','zap',()=>{if(confirm('Executar agora em '+item.targets.length+' endpoint(s)?'))return send('run_now');}));
                }
                actions.append(button('Historico','history',()=>{history.value=item.id;return refresh();}));
                row.append(actions);tbody.append(row);
            });
            history.value=filter;
            const body=root.querySelector('[data-automation-occurrences]');body.replaceChildren();
            data.occurrences.forEach(item=>{
                const row=el('tr');
                [(item.snapshot.name||item.definition_id)+' / r'+item.revision,item.hostname,date(item.scheduled_for,item.snapshot.timezone),date(item.expires_at,item.snapshot.timezone),
                    (labels[item.status]||item.status)+(item.reason?' / '+(errors[item.reason]||item.reason):'')].forEach(text=>row.append(el('td',text)));
                const result=el('td');
                if(item.job_id){const link=el('a','Abrir job');link.href='/jobs/?tab=jobs&job='+encodeURIComponent(item.job_id);result.append(link);}else result.textContent='--';
                row.append(result);const actions=el('td');
                if(['queued','enqueued'].includes(item.status))actions.append(button('Cancelar antes do despacho','ban',()=>request('/jobs/occurrences/'+item.id+'/cancel/',{})));
                row.append(actions);body.append(row);
            });
            if(!tbody.children.length){const row=el('tr'),cell=el('td','Nenhum agendamento.');cell.colSpan=6;row.append(cell);tbody.append(row);}
            if(!body.children.length){const row=el('tr'),cell=el('td','Nenhuma ocorrencia.');cell.colSpan=7;row.append(cell);body.append(row);}
            if(window.lucide)window.lucide.createIcons();
        }catch(error){say(error.message);}finally{refreshing=false;}
    }
    function edit(item) {
        field('id').value=item.id;field('revision').value=item.revision;
        ['name','job_type','offline_policy'].forEach(key=>field(key).value=item[key]);
        field('timezone').value=item.timezone;field('kind').value=item.schedule.kind;field('validity').value=item.validity_seconds/60;
        const wall=value=>value ? new Intl.DateTimeFormat('sv-SE',{timeZone:item.timezone,year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hourCycle:'h23'}).format(new Date(value)).replace(' ','T') : '';
        field('starts_at').value=wall(item.starts_at);field('ends_at').value=wall(item.ends_at);
        field('minutes').value=item.schedule.minutes||60;field('monthday').value=item.schedule.monthday||1;
        form.querySelectorAll('[name="weekday"]').forEach(node=>node.checked=(item.schedule.weekdays||[]).includes(Number(node.value)));
        field('max_lines').value=item.payload.max_lines||120;field('count').value=item.payload.count||2;
        item.targets.forEach(target=>targets.set(target.id,target));selected=new Set(item.endpoint_ids);
        root.querySelector('[data-automation-submit]').textContent='Salvar revisao';targetOptions();controls();form.scrollIntoView({block:'start'});
    }
    form.addEventListener('submit',event=>{
        event.preventDefault();
        if(selected.size<1 || selected.size>50){say('Selecione entre 1 e 50 endpoints.');return;}
        const kind=field('kind').value,schedule={kind};
        if(kind==='weekly')schedule.weekdays=Array.from(form.querySelectorAll('[name="weekday"]:checked'),node=>Number(node.value));
        if(kind==='monthly')schedule.monthday=Number(field('monthday').value);
        if(kind==='interval')schedule.minutes=Number(field('minutes').value);
        const payload=field('job_type').value==='collect_logs'?{max_lines:Number(field('max_lines').value)}:field('job_type').value==='ping'?{count:Number(field('count').value)}:{};
        const data={name:field('name').value,job_type:field('job_type').value,payload,endpoint_ids:Array.from(selected),schedule,
            timezone:field('timezone').value,starts_at:kind==='immediate'?new Date().toISOString():field('starts_at').value,
            ends_at:kind==='immediate'?null:field('ends_at').value||null,validity_seconds:Number(field('validity').value)*60,offline_policy:field('offline_policy').value};
        if(field('id').value)data.revision=Number(field('revision').value);
        if(kind==='immediate'&&!confirm('Executar agora em '+selected.size+' endpoint(s)?'))return;
        perform(async()=>{await request(root.dataset.url+(field('id').value?field('id').value+'/':''),data);reset();});
    });
    function reset(){form.reset();field('id').value='';field('revision').value='';selected.clear();root.querySelector('[data-automation-submit]').textContent='Criar execucao';targetOptions();controls();}
    field('targets').addEventListener('change',()=>{selected=new Set(Array.from(field('targets').selectedOptions,node=>node.value));targetOptions();});
    field('kind').addEventListener('change',controls);field('job_type').addEventListener('change',controls);
    root.querySelector('[data-automation-reset]').addEventListener('click',reset);
    root.querySelector('[data-automation-refresh]').addEventListener('click',refresh);
    root.querySelector('[data-automation-history]').addEventListener('change',refresh);
    root.querySelector('[data-automation-search]').addEventListener('input',()=>{clearTimeout(searchTimer);targetOptions();searchTimer=setTimeout(async()=>{
        try{const data=await request(root.dataset.targetsUrl+'?q='+encodeURIComponent(field('target_query').value));data.endpoints.forEach(t=>targets.set(t.id,t));targetOptions();}catch(error){say(error.message);}
    },300);});
    const timer=setInterval(()=>{if(!document.hidden&&!root.hidden)refresh();},10000);
    window.addEventListener('pagehide',()=>clearInterval(timer));
    request(root.dataset.targetsUrl).then(data=>{data.endpoints.forEach(t=>targets.set(t.id,t));targetOptions();}).catch(error=>say(error.message));
    controls();refresh();
}());
