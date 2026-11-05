document.addEventListener('DOMContentLoaded', () => {
    const releases = JSON.parse(document.getElementById('deployment-release-options')?.textContent || '[]');
    const labels = {waiting:'Aguardando execução', installing:'Instalando', confirmed:'Confirmado por heartbeat',
        confirmation_pending:'Aguardando heartbeat', failed:'Falhou', expired:'Expirado'};
    document.querySelectorAll('[data-manual-install]').forEach((form) => {
        const channel = form.querySelector('[data-channel]');
        const release = form.querySelector('[data-release]');
        const hint = form.querySelector('[data-release-message]');
        const error = form.querySelector('.agent-token-error');
        const button = form.querySelector('[type=submit]');
        const panel = document.getElementById(form.dataset.manualInstall === 'ad' ? 'enrollment-token-result' : 'manual-token-result');
        let timer = null;
        const refreshOptions = () => {
            release.replaceChildren();
            releases.filter(item => item.channel === channel.value &&
                (channel.value !== 'stable' || (item.status === 'published' && !item.paused)))
                .forEach(item => {
                    const option = document.createElement('option');
                    option.value = item.id;
                    option.textContent = `${item.version}${item.paused ? ' (pausada)' : ''}`;
                    release.appendChild(option);
                });
            button.disabled = !release.options.length || form.dataset.trustReady !== 'true';
            hint.textContent = form.dataset.trustReady !== 'true' ? 'Trust bundle indisponível ou inválido.' :
                (!release.options.length ? 'Nenhuma release assinada elegível neste canal.' : '');
        };
        channel.addEventListener('change', refreshOptions);
        refreshOptions();
        const makeBlock = (label, value, buttonLabel) => {
            const block = document.createElement('div'); block.className = 'created-token-box';
            const content = document.createElement('div'); content.className = 'copy-value-content';
            const title = document.createElement('span'); title.textContent = label;
            const code = document.createElement('code'); code.textContent = value;
            const copy = document.createElement('button'); copy.type = 'button'; copy.className = 'copy-button';
            copy.textContent = buttonLabel; copy.addEventListener('click', () => navigator.clipboard.writeText(code.textContent));
            content.append(title, code); block.append(content, copy); return block;
        };
        form.addEventListener('submit', async (event) => {
            event.preventDefault(); button.disabled = true; error.hidden = true;
            if (timer !== null) { clearInterval(timer); timer = null; }
            try {
                const response = await fetch(form.dataset.createUrl, {method:'POST', credentials:'same-origin', cache:'no-store',
                    headers:{'X-CSRFToken':form.querySelector('[name=csrfmiddlewaretoken]').value, 'Accept':'application/json'}, body:new FormData(form)});
                const data = await response.json();
                if (!response.ok) throw new Error(data.detail || 'Não foi possível gerar a instalação.');
                const item = data.deployment || data.batch;
                panel.replaceChildren(); panel.hidden = false;
                let resultOpen = true;
                const toolbar = document.createElement('div'); toolbar.className = 'agent-token-result-toolbar';
                const heading = document.createElement('strong');
                heading.textContent = data.kind === 'ad' ? 'Comandos gerados' : 'Comando e token gerados';
                const close = document.createElement('button');
                close.type = 'button'; close.className = 'agent-token-result-close';
                close.textContent = 'Fechar';
                close.title = 'Ocultar resultado (não revoga a autorização)';
                close.addEventListener('click', () => {
                    resultOpen = false;
                    if (timer !== null) { clearInterval(timer); timer = null; }
                    panel.replaceChildren();
                    panel.hidden = true;
                });
                toolbar.append(heading, close);
                panel.append(toolbar);
                const commandArea = document.createElement('div'); commandArea.className = 'agent-token-command-area'; panel.append(commandArea);
                if (data.kind !== 'ad') commandArea.append(makeBlock('Comando PowerShell', item.command, 'Copiar comando'));
                if (data.manual_token) panel.append(makeBlock('Token manual', data.manual_token, 'Copiar token'));
                const expiry = document.createElement('span'); expiry.textContent = `Validade: ${new Date(item.expires_at).toLocaleString('pt-BR')}`;
                const status = document.createElement('span'); status.textContent = 'Instalação: aguardando execução.';
                const metadata = document.createElement('div'); metadata.className = 'agent-token-result-meta';
                metadata.append(expiry, status);
                if (data.kind === 'ad') {
                    const controls = document.createElement('div'); controls.className = 'agent-token-result-controls';
                    const next = document.createElement('button'); next.type = 'button'; next.textContent = 'Gerar próximo comando';
                    const message = document.createElement('p'); controls.append(next, message); panel.append(controls);
                    let pendingId = null;
                    const issue = async () => {
                        next.disabled = true;
                        pendingId ||= crypto.randomUUID();
                        try {
                            const r = await fetch(item.issue_url, {method:'POST', credentials:'same-origin', cache:'no-store',
                                headers:{'X-CSRFToken':form.querySelector('[name=csrfmiddlewaretoken]').value,
                                    'Content-Type':'application/x-www-form-urlencoded'},
                                body:new URLSearchParams({request_id:pendingId})});
                            const result = await r.json();
                            if (!r.ok) {
                                if (result.error === 'duplicate_request') pendingId = null;
                                throw new Error(result.detail || 'Não foi possível emitir o comando.');
                            }
                            pendingId = null;
                            if (!resultOpen) return;
                            commandArea.replaceChildren(makeBlock('Comando PowerShell', result.deployment.command, 'Copiar comando'));
                            message.textContent = `Usos restantes: ${result.remaining}`;
                            next.disabled = result.remaining === 0;
                        } catch (failure) {
                            if (!resultOpen) return;
                            message.textContent = failure.message || 'Não foi possível emitir o comando.';
                            next.disabled = false;
                        }
                    };
                    next.addEventListener('click', issue);
                    await issue();
                }
                if (!resultOpen) return;
                panel.append(metadata);
                const update = async () => {
                    try {
                        const url = data.kind === 'ad' ? `${form.dataset.historyUrl}?batch_id=${encodeURIComponent(item.id)}` : `${form.dataset.createUrl}${encodeURIComponent(item.id)}/`;
                        const r = await fetch(url, {credentials:'same-origin', cache:'no-store'});
                        if (!r.ok) throw new Error();
                        const body = await r.json();
                        if (!resultOpen) return;
                        const rows = data.kind === 'ad' ? body.deployments.filter(x => x.batch_id === item.id) : [body.deployment];
                        status.textContent = rows.length ? rows.map(x => `${x.endpoint?.hostname || 'Instalação'}: ${labels[x.ui_status] || 'Estado indisponível'}`).join(' · ') : 'Instalação: aguardando execução.';
                    } catch (_) { if (resultOpen) status.textContent = 'Acompanhamento temporariamente indisponível.'; }
                };
                update(); timer = setInterval(update, 5000);
            } catch (failure) { error.textContent = failure.message || 'Falha ao gerar instalação.'; error.hidden = false; }
            finally { refreshOptions(); }
        });
    });
});
