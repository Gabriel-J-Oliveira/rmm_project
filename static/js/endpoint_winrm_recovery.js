(() => {
    'use strict';
    const root = document.querySelector('[data-endpoint-detail]');
    const context = document.querySelector('[data-winrm-context]');
    if (!root || !context) return;
    const indicator = document.querySelector('[data-winrm-indicator]');
    const form = document.querySelector('[data-winrm-recovery]');
    const modal = document.querySelector('[data-recovery-modal]');
    const key = `nightowl.terminal.accounts.${context.dataset.user}.${context.dataset.endpoint}`;
    let remote = {}, latest = null, trackedId = null, allowed = false, statusReceived = false, pending = false, timer = null, deadline = 0;
    try { remote = JSON.parse(document.getElementById('winrm-last-check').textContent); } catch (_) {}
    const make = (tag,text,cls) => { const e=document.createElement(tag); if(text!==undefined)e.textContent=text; if(cls)e.className=cls; return e; };
    function showIndicator(value, checking=false) {
        remote = value || {};
        const state = checking ? 'Verificando' : remote.wsman === 'reachable' ? 'Acessível' :
            remote.tcp === 'unavailable' || remote.wsman === 'unavailable' ? 'Indisponível' : 'Não verificado';
        indicator.textContent = `WinRM: ${state}`;
        indicator.title = `Última verificação: ${remote.checked_at || 'não informada'}; transporte HTTPS/${remote.port || 5986}; TCP: ${remote.tcp || 'não verificado'}; WS-Man: ${remote.wsman || 'não verificado'}; autenticação: ${remote.authentication || 'não verificada'}; ${remote.error || ''}`;
        updateCard();
    }
    document.addEventListener('nightowl:winrm-checking',()=>showIndicator(remote,true));
    document.addEventListener('nightowl:winrm-checked',event=>showIndicator(event.detail || remote));
    function accounts() { try { const a=JSON.parse(localStorage.getItem(key)||'[]'); return Array.isArray(a)?a.filter(x=>typeof x==='string' && x.length<=256).slice(0,10):[]; } catch (_) { return []; } }
    function remember(value) { if (!value) return; try { localStorage.setItem(key,JSON.stringify([value,...accounts().filter(x=>x.toLowerCase()!==value.toLowerCase())].slice(0,10))); } catch (_) {} }
    function resultLabel(job) {
        if (!job) return 'Nenhuma tentativa registrada';
        if (job.status === 'COMPLETED') return 'Recuperado: heartbeat novo confirmado';
        if (job.status === 'OUTCOME_UNKNOWN' || job.error_code === 'HEARTBEAT_TIMEOUT') return 'Sem confirmação';
        if (job.status === 'FAILED' || job.status === 'INTERRUPTED') return 'Falhou';
        return 'Em andamento';
    }
    const shade=make('div',undefined,'winrm-drawer-shade'), drawer=make('aside',undefined,'winrm-recovery-drawer');
    shade.hidden=drawer.hidden=true; drawer.setAttribute('aria-label','Acompanhamento da recuperação');
    const head=make('header'), heading=make('h2','Recuperação do agente'), close=make('button','Fechar'); close.type='button';
    const content=make('div'); head.append(heading,close); drawer.append(head,content); document.body.append(shade,drawer);
    function hideDrawer(){drawer.hidden=shade.hidden=true; if(timer){clearTimeout(timer);timer=null;}}
    close.addEventListener('click',hideDrawer); shade.addEventListener('click',hideDrawer);
    function renderDrawer(timedOut=false) {
        content.replaceChildren(); content.append(make('p',timedOut?'Sem confirmação: prazo de acompanhamento encerrado.':resultLabel(latest)));
        if (!latest || (trackedId && latest.id !== trackedId)) {content.append(make('p','Registro desta operação não disponível na consulta mais recente.')); return;}
        const timestamp=latest.finished_at || new Date().toISOString();
        const seconds=latest.started_at ? Math.max(0,Math.floor((Date.parse(timestamp)-Date.parse(latest.started_at))/1000)) : 0;
        content.append(make('p',`Etapa registrada: ${latest.stage || 'não informada'} · tempo decorrido: ${Number.isFinite(seconds)?seconds:0}s`));
        const list=make('ol');
        const names=['Identificação do computador','Conexão e autenticação WinRM','Verificação do serviço NightOwl','Recuperação do serviço','Confirmação de novo heartbeat'];
        const evidence=['WAITING_HEARTBEAT','COMPLETED'].includes(latest.stage);
        names.forEach((name,index)=>{
            let label='não informada';
            if(evidence && index<4) label='confirmada';
            if(index===4 && latest.stage==='WAITING_HEARTBEAT') label='aguardando ou sem confirmação';
            if(index===4 && latest.status==='COMPLETED') label='confirmada';
            list.append(make('li',`${name}: ${label}`));
        });
        content.append(list);
        if(latest.error_code) content.append(make('p',`Código: ${latest.error_code}`));
    }
    async function refresh() {
        const response=await fetch(context.dataset.statusUrl,{credentials:'same-origin',cache:'no-store',headers:{Accept:'application/json'}});
        if(!response.ok) throw Error();
        const data=await response.json(); latest=data.latest_recovery; allowed=!!data.can_recover; statusReceived=true;
        if (data.agent_status) context.dataset.agentStatus = ({online:'Online', offline:'Offline', unknown:'Não verificado'})[data.agent_status] || data.agent_status;
        updateCard(); return data;
    }
    function follow() {
        if(drawer.hidden)return;
        refresh().then(()=>{
            renderDrawer();
            if(latest && (!trackedId || latest.id===trackedId) && ['RUNNING','QUEUED'].includes(latest.status)) {
                if(Date.now() >= deadline){renderDrawer(true);return;}
                timer=setTimeout(follow,3000);
            }
        }).catch(()=>renderDrawer(true));
    }
    function openDrawer(id=null){trackedId=id;drawer.hidden=shade.hidden=false;deadline=Date.now()+120000;renderDrawer();follow();}
    function updateCard() {
        const section=root.querySelector('[data-endpoint-tab-panel="tasks"] > section');
        if(!section)return;
        let card=section.querySelector('[data-winrm-recovery-card]');
        if(!card){card=make('section',undefined,'endpoint-dense-card');card.dataset.winrmRecoveryCard='';section.append(card);}
        const winrm = remote.wsman === 'reachable' ? 'Acessível' : remote.tcp === 'unavailable' || remote.wsman === 'unavailable' ? 'Indisponível' : 'Não verificado';
        card.replaceChildren(make('h3','Recuperação do agente'),make('p',`Agente: ${context.dataset.agentStatus}`),make('p',`WinRM: ${winrm}`),make('p',`Última tentativa: ${resultLabel(latest)}`));
        const action=make('button','Recuperar agente','endpoint-quick-action-button'); action.type='button'; action.disabled=!form || !statusReceived || !allowed || pending;
        action.addEventListener('click',()=>{
            if(!form || !allowed)return;
            const known=form.querySelector('[data-recovery-known]'); known.replaceChildren();
            const other=make('option','Usar outra conta'); other.value=''; known.append(other);
            accounts().forEach(name=>{const o=make('option',name);o.value=name;known.append(o);});
            form.elements.username.value='';form.elements.password.value='';form.querySelector('[data-winrm-recovery-status]').textContent='';modal.hidden=false;form.elements.username.focus();
        });
        const history=make('button','Acompanhar','endpoint-quick-action-button');history.type='button';history.disabled=!latest;history.addEventListener('click',()=>openDrawer());
        card.append(action,history);
    }
    const observer=new MutationObserver(()=>{const section=root.querySelector('[data-endpoint-tab-panel="tasks"] > section');if(section && !section.querySelector('[data-winrm-recovery-card]'))updateCard();});
    observer.observe(root,{childList:true,subtree:true});
    if(form){
        const username=form.elements.username,password=form.elements.password,button=form.querySelector('button[type="submit"]'),feedback=form.querySelector('[data-winrm-recovery-status]');
        form.querySelector('[data-recovery-known]').addEventListener('change',event=>{username.value=event.target.value;});
        form.querySelector('[data-recovery-cancel]').addEventListener('click',()=>{password.value='';modal.hidden=true;});
        window.addEventListener('pagehide',()=>{password.value='';});
        form.addEventListener('submit',async event=>{
            event.preventDefault(); if(pending || !allowed || location.protocol!=='https:'){password.value='';return;}
            pending=true;button.disabled=true;feedback.textContent='Solicitação em andamento; aguarde o backend.'; updateCard();
            const used=username.value.trim(), controller=new AbortController(), timeout=setTimeout(()=>controller.abort(),120000);
            let body=JSON.stringify({username:used,password:password.value,restart_running:form.elements.restart_running.checked}); password.value='';
            try {
                const request=fetch(form.action,{method:'POST',credentials:'same-origin',cache:'no-store',signal:controller.signal,headers:{'Content-Type':'application/json','X-CSRFToken':form.elements.csrfmiddlewaretoken.value},body});body=null;
                const response=await request, data=await response.json();
                if(data.status==='RECOVERED')remember(used);
                modal.hidden=true;
                if(response.ok && data.job_id){await refresh(); openDrawer(data.job_id);}
                else { await refresh(); openDrawer(); content.prepend(make('p',data.error_code==='RECOVERY_OUTCOME_UNKNOWN'?'Sem confirmação da operação solicitada.':'Solicitação não confirmada; consulte o registro mais recente.')); }
            } catch (_) { body=null;modal.hidden=true;try{await refresh();}catch(_){} openDrawer();content.prepend(make('p','Sem confirmação da solicitação; consulte o registro antes de tentar novamente.')); }
            finally {clearTimeout(timeout);password.value='';pending=false;button.disabled=false;updateCard();}
        });
    }
    showIndicator(remote); refresh().catch(()=>{statusReceived=false;updateCard();});
})();
