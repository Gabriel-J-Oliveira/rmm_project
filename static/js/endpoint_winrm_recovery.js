(() => {
    'use strict';
    const root = document.querySelector('[data-endpoint-detail]');
    const context = document.querySelector('[data-winrm-context]');
    if (!root || !context) return;

    const indicator = root.querySelector('[data-winrm-indicator]');
    const compactAgent = root.querySelector('[data-winrm-compact-agent]');
    const compactLastTest = root.querySelector('[data-winrm-compact-last-test]');
    const summary = root.querySelector('[data-winrm-recovery-summary]');
    const historyList = root.querySelector('[data-winrm-history]');
    const reason = root.querySelector('[data-winrm-recovery-reason]');
    const openButton = root.querySelector('[data-winrm-recovery-open]');
    const followButton = root.querySelector('[data-winrm-recovery-follow]');
    const form = root.querySelector('[data-winrm-recovery]');
    const modal = root.querySelector('[data-recovery-modal]');
    const key = `nightowl.terminal.accounts.${context.dataset.user}.${context.dataset.endpoint}`;
    let remote = {}, latest = null, trackedId = null, allowed = false;
    let statusReceived = false, pending = false, checkingWinrm = false, timer = null, deadline = 0;
    try { remote = JSON.parse(document.getElementById('winrm-last-check').textContent); } catch (_) {}

    const make = (tag, text, className) => {
        const element = document.createElement(tag);
        if (text !== undefined) element.textContent = text;
        if (className) element.className = className;
        return element;
    };

    function winrmLabel() {
        if (checkingWinrm) return 'Verificando';
        if (remote.wsman === 'reachable') return 'WS-Man acessível';
        if (remote.tcp === 'unavailable' || remote.wsman === 'unavailable') return 'Indisponível';
        return 'Não verificado';
    }

    function resultLabel(job) {
        if (!job) return 'Nenhuma tentativa registrada';
        if (job.status === 'COMPLETED') return 'Recuperação concluída; heartbeat novo confirmado';
        if (job.status === 'OUTCOME_UNKNOWN' || job.error_code === 'HEARTBEAT_TIMEOUT') return 'Resultado sem confirmação';
        if (job.status === 'FAILED' || job.status === 'INTERRUPTED') return 'Falhou';
        return 'Em andamento';
    }

    function historyStatus(item) {
        if (item.operation === 'winrm_connectivity') {
            const tcp = item.tcp === 'reachable' ? 'alcançável' : item.tcp === 'unavailable' ? 'indisponível' : 'não verificado';
            const wsman = item.wsman === 'reachable' ? 'alcançável' : item.wsman === 'unavailable' ? 'indisponível' : item.wsman === 'not_verified' ? 'desafiou autenticação' : 'não verificado';
            const auth = item.authentication === 'validated' ? 'validada' : 'não verificada';
            return `TCP ${tcp}; WS-Man ${wsman}; autenticação ${auth}${item.error_code ? `; código ${item.error_code}` : ''}`;
        }
        if (item.status === 'COMPLETED') return 'Recuperado; heartbeat novo confirmado';
        if (item.status === 'OUTCOME_UNKNOWN' || item.error_code === 'HEARTBEAT_TIMEOUT') return 'Sem confirmação';
        if (item.status === 'FAILED' || item.status === 'INTERRUPTED') return `Falhou${item.error_code ? `: ${item.error_code}` : ''}`;
        return 'Em andamento';
    }

    function renderHistory(items) {
        if (!historyList) return;
        historyList.replaceChildren();
        if (items === null) {
            historyList.append(make('li', 'Histórico indisponível no momento.'));
            return;
        }
        if (!Array.isArray(items) || items.length === 0) {
            historyList.append(make('li', 'Nenhuma operação WinRM registrada para este endpoint.'));
            return;
        }
        items.forEach(item => {
            const operation = item.operation === 'winrm_connectivity' ? 'Verificação de conectividade' : 'Recuperação do agente';
            const timestamp = item.created_at ? new Date(item.created_at).toLocaleString() : 'horário não informado';
            const actor = item.requested_by ? ` · operador ${item.requested_by}` : '';
            const stage = item.stage && item.stage !== 'UNKNOWN' ? ` · etapa ${item.stage}` : '';
            historyList.append(make('li', `${timestamp} · ${operation}: ${historyStatus(item)}${stage}${actor}`));
        });
    }

    function disabledReason() {
        if (pending) return 'Solicitação de recuperação em andamento.';
        if (!statusReceived) return 'Aguardando validação de elegibilidade pelo backend.';
        if (allowed) return '';
        if (String(context.dataset.agentStatus || '').toLowerCase() === 'online') return 'O agente está online; recuperação via WinRM só é permitida para endpoint offline elegível.';
        return 'Não elegível: identidade única, credencial válida, heartbeat antigo e ausência de operação conflitante são obrigatórios.';
    }

    function updateSummary() {
        if (compactAgent) compactAgent.textContent = `Agente: ${context.dataset.agentStatus || 'Não verificado'}`;
        if (compactLastTest) compactLastTest.textContent = remote.checked_at ? `Último teste: ${remote.checked_at}` : 'Último teste: não realizado';
        if (indicator) {
            indicator.textContent = `WinRM: ${winrmLabel()}`;
            indicator.title = `Última verificação: ${remote.checked_at || 'não informada'}; transporte HTTPS/${remote.port || 5986}; TCP: ${remote.tcp || 'não verificado'}; WS-Man: ${remote.wsman || 'não verificado'}; autenticação: ${remote.authentication || 'não verificada'}; ${remote.error || ''}`;
        }
        if (summary) {
            const checked = remote.checked_at ? `Último teste: ${remote.checked_at}.` : 'Teste remoto ainda não executado.';
            summary.textContent = `Agente: ${context.dataset.agentStatus}. WinRM: ${winrmLabel()}. ${checked} Recuperação: ${resultLabel(latest)}${latest && latest.created_at ? ` (${latest.created_at})` : ''}.`;
        }
        const why = disabledReason();
        if (openButton) {
            openButton.disabled = !form || !allowed || pending;
            openButton.title = why;
        }
        if (reason) {
            reason.textContent = why;
            reason.hidden = !why;
        }
        if (followButton) {
            followButton.disabled = !latest;
            followButton.textContent = latest && ['RUNNING', 'QUEUED'].includes(latest.status) ? 'Acompanhar recuperação' : 'Ver última tentativa';
        }
    }

    function accounts() {
        try {
            const stored = JSON.parse(localStorage.getItem(key) || '[]');
            return Array.isArray(stored) ? stored.filter(value => typeof value === 'string' && value.length <= 256).slice(0, 10) : [];
        } catch (_) { return []; }
    }

    function remember(value) {
        if (!value) return;
        try { localStorage.setItem(key, JSON.stringify([value, ...accounts().filter(item => item.toLowerCase() !== value.toLowerCase())].slice(0, 10))); } catch (_) {}
    }

    const shade = make('div', undefined, 'winrm-drawer-shade');
    const drawer = make('aside', undefined, 'winrm-recovery-drawer');
    shade.hidden = drawer.hidden = true;
    drawer.setAttribute('aria-label', 'Acompanhamento da recuperação');
    const head = make('header'), heading = make('h2', 'Recuperação do agente'), close = make('button', 'Fechar');
    close.type = 'button';
    const content = make('div');
    head.append(heading, close);
    drawer.append(head, content);
    document.body.append(shade, drawer);

    function hideDrawer() {
        drawer.hidden = shade.hidden = true;
        if (timer) { clearTimeout(timer); timer = null; }
    }

    function renderDrawer(timedOut = false) {
        content.replaceChildren();
        content.append(make('p', timedOut ? 'Prazo de acompanhamento encerrado; o resultado permanece sem confirmação.' : resultLabel(latest)));
        if (!latest || (trackedId && latest.id !== trackedId)) {
            content.append(make('p', 'Registro desta operação não está disponível na consulta mais recente.'));
            return;
        }
        const finished = latest.finished_at || new Date().toISOString();
        const seconds = latest.started_at ? Math.max(0, Math.floor((Date.parse(finished) - Date.parse(latest.started_at)) / 1000)) : 0;
        content.append(make('p', `Etapa registrada: ${latest.stage || 'não informada'} · tempo decorrido: ${Number.isFinite(seconds) ? seconds : 0}s`));
        const stages = make('ol');
        const labels = ['Identificação do computador', 'Conexão e autenticação WinRM', 'Verificação do serviço NightOwl', 'Recuperação do serviço', 'Confirmação de novo heartbeat'];
        const hasRecoveryEvidence = ['WAITING_HEARTBEAT', 'COMPLETED'].includes(latest.stage);
        labels.forEach((label, index) => {
            let state = 'não informada';
            if (hasRecoveryEvidence && index < 4) state = 'confirmada';
            if (index === 4 && latest.stage === 'WAITING_HEARTBEAT') state = 'aguardando ou sem confirmação';
            if (index === 4 && latest.status === 'COMPLETED') state = 'confirmada';
            stages.append(make('li', `${label}: ${state}`));
        });
        content.append(stages);
        if (latest.error_code) content.append(make('p', `Código: ${latest.error_code}`));
    }

    async function refresh() {
        const response = await fetch(context.dataset.statusUrl, {
            credentials: 'same-origin', cache: 'no-store', headers: {Accept: 'application/json'}
        });
        if (!response.ok) throw new Error('status_unavailable');
        const data = await response.json();
        latest = data.latest_recovery;
        renderHistory(Array.isArray(data.history) ? data.history : null);
        allowed = data.can_recover === true;
        statusReceived = true;
        if (data.agent_status) context.dataset.agentStatus = ({online: 'Online', offline: 'Offline', unknown: 'Não verificado'})[data.agent_status] || data.agent_status;
        updateSummary();
        return data;
    }

    function follow() {
        if (drawer.hidden) return;
        refresh().then(() => {
            renderDrawer();
            if (latest && (!trackedId || latest.id === trackedId) && ['RUNNING', 'QUEUED'].includes(latest.status)) {
                if (Date.now() >= deadline) { renderDrawer(true); return; }
                timer = setTimeout(follow, 3000);
            }
        }).catch(() => renderDrawer(true));
    }

    function openDrawer(id = null) {
        trackedId = id;
        drawer.hidden = shade.hidden = false;
        deadline = Date.now() + 120000;
        renderDrawer();
        follow();
    }

    close.addEventListener('click', hideDrawer);
    shade.addEventListener('click', hideDrawer);
    followButton?.addEventListener('click', () => openDrawer(latest && latest.id));
    openButton?.addEventListener('click', () => {
        if (!form || !allowed || !modal) return;
        const known = form.querySelector('[data-recovery-known]');
        known.replaceChildren();
        const other = make('option', 'Usar outra conta');
        other.value = '';
        known.append(other);
        accounts().forEach(name => { const option = make('option', name); option.value = name; known.append(option); });
        form.elements.username.value = '';
        form.elements.password.value = '';
        form.querySelector('[data-winrm-recovery-status]').textContent = '';
        modal.hidden = false;
        form.elements.username.focus();
    });

    if (form) {
        const username = form.elements.username, password = form.elements.password;
        const submit = form.querySelector('button[type="submit"]');
        const feedback = form.querySelector('[data-winrm-recovery-status]');
        form.querySelector('[data-recovery-known]').addEventListener('change', event => { username.value = event.target.value; });
        form.querySelector('[data-recovery-cancel]').addEventListener('click', () => { password.value = ''; modal.hidden = true; });
        window.addEventListener('pagehide', () => { password.value = ''; });
        form.addEventListener('submit', async event => {
            event.preventDefault();
            if (pending || !allowed || location.protocol !== 'https:') { password.value = ''; return; }
            pending = true;
            submit.disabled = true;
            feedback.textContent = 'Solicitação em andamento; aguarde o backend.';
            updateSummary();
            const used = username.value.trim();
            const controller = new AbortController();
            const timeout = setTimeout(() => controller.abort(), 120000);
            let body = JSON.stringify({username: used, password: password.value, restart_running: form.elements.restart_running.checked});
            password.value = '';
            try {
                const request = fetch(form.action, {
                    method: 'POST', credentials: 'same-origin', cache: 'no-store', signal: controller.signal,
                    headers: {'Content-Type': 'application/json', 'X-CSRFToken': form.elements.csrfmiddlewaretoken.value}, body
                });
                body = null;
                const response = await request, data = await response.json();
                if (data.status === 'RECOVERED') remember(used);
                modal.hidden = true;
                await refresh();
                if (response.ok && data.job_id) openDrawer(data.job_id);
                else openDrawer();
            } catch (_) {
                body = null;
                modal.hidden = true;
                try { await refresh(); } catch (_) {}
                openDrawer();
                content.prepend(make('p', 'Sem confirmação da solicitação; consulte o registro antes de tentar novamente.'));
            } finally {
                clearTimeout(timeout);
                password.value = '';
                pending = false;
                submit.disabled = false;
                updateSummary();
            }
        });
    }

    document.addEventListener('nightowl:winrm-checking', () => { checkingWinrm = true; updateSummary(); });
    document.addEventListener('nightowl:winrm-checked', event => {
        checkingWinrm = false;
        if (event.detail) remote = event.detail;
        updateSummary();
        refresh().catch(() => renderHistory(null));
    });
    updateSummary();
    refresh().catch(() => { statusReceived = false; updateSummary(); });
})();
