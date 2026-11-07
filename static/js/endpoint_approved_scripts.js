(() => {
    'use strict';
    const root = document.querySelector('[data-endpoint-detail]');
    const hint = document.getElementById('approved-script-availability');
    if (!root || !hint) return;
    let availability = {};
    try { availability = JSON.parse(hint.textContent); } catch (_) { return; }
    let reasons = {};
    try { reasons = JSON.parse(document.getElementById('approved-script-reasons')?.textContent || '{}') || {}; } catch (_) {}
    let catalog = null, loading = false, failure = false;
    const element = (tag, text, className) => {
        const node = document.createElement(tag);
        if (text !== undefined) node.textContent = text;
        if (className) node.className = className;
        return node;
    };
    const key = script => `${script.script_id}:${script.version}:${script.sha256}`;
    const schemaSupported = script => {
        const schema = script.parameter_schema;
        return !!schema && schema.type === 'object' && schema.additionalProperties === false &&
            schema.properties && typeof schema.properties === 'object' && !Array.isArray(schema.properties) &&
            Object.keys(schema.properties).length === 0 && (!schema.required || (Array.isArray(schema.required) && !schema.required.length));
    };
    function csrf() {
        const entry = document.cookie.split('; ').find(item => item.startsWith('csrftoken='));
        return entry ? decodeURIComponent(entry.slice('csrftoken='.length)) : '';
    }
    function modal(script) {
        if (!availability[key(script)] || !schemaSupported(script) || script.approved !== true || script.revoked !== false) return;
        const overlay = element('div', undefined, 'install-credential-overlay');
        const dialog = element('section', undefined, 'install-credential-modal');
        dialog.setAttribute('role','dialog'); dialog.setAttribute('aria-modal','true'); dialog.setAttribute('aria-label','Executar script aprovado');
        dialog.append(element('h2','Executar script aprovado'), element('p',`${script.script_id} · versão ${script.version}`),
            element('p',script.description || ''), element('p','Este script não exige parâmetros.'));
        const feedback = element('p','', 'install-feedback'); feedback.setAttribute('role','status');
        const footer = element('footer'), cancel = element('button','Cancelar','install-button'), submit = element('button','Executar','install-button install-primary');
        cancel.type = submit.type = 'button'; footer.append(cancel,submit); dialog.append(feedback,footer); overlay.append(dialog);
        document.body.append(overlay); const previous = document.activeElement;
        function close() { overlay.remove(); document.removeEventListener('keydown', onKey); previous?.focus(); }
        function onKey(event) { if (event.key === 'Escape' && !submit.disabled) close(); }
        document.addEventListener('keydown', onKey); cancel.addEventListener('click',close); cancel.focus();
        submit.addEventListener('click', async () => {
            submit.disabled = cancel.disabled = true; feedback.textContent = 'Enfileirando...';
            try {
                const response = await fetch(`/api/agent/operational-actions/${encodeURIComponent(root.dataset.endpointId)}/`, {
                    method:'POST',credentials:'same-origin',cache:'no-store',
                    headers:{'Content-Type':'application/json','X-CSRFToken':csrf(),'Accept':'application/json'},
                    body:JSON.stringify({action_id:'run_approved_script',action_version:1,parameters:{script_id:script.script_id,script_version:script.version,sha256:script.sha256,parameters:{}}})
                });
                const body = await response.json();
                if (!response.ok || !body.job_id) throw new Error('dispatch_rejected');
                close(); root.dispatchEvent(new CustomEvent('nightowl:approved-script-queued',{bubbles:true}));
            } catch (_) { feedback.textContent = 'Resultado não confirmado. Consulte o histórico antes de tentar novamente.'; cancel.disabled = false; }
        });
    }
    function insertCard() {
        const placement = root.querySelector('[data-approved-scripts-placement]');
        if (!placement) return;
        placement.replaceChildren();
        if (failure) { placement.append(element('p','Catálogo indisponível ou acesso não autorizado.')); return; }
        if (!catalog) { placement.append(element('p','Abra a categoria para consultar o catálogo aprovado.')); return; }
        if (!catalog.length) { placement.append(element('p','Nenhum script aprovado disponível.')); return; }
        catalog.forEach(script => {
            const row = element('article',undefined,'endpoint-approved-script-row'), body = element('div');
            const title = element('strong',script.script_id || 'Script');
            const version = element('small',`Versão ${script.version}`);
            body.append(title, element('p',script.description || ''), version);
            const eligible = script.approved === true && script.revoked === false && availability[key(script)] === true && schemaSupported(script);
            const reason = script.revoked ? 'Esta versão foi revogada.' : !schemaSupported(script) ? 'Parâmetros não suportados nesta interface.' : reasons[key(script)];
            const button = element('button',eligible ? 'Executar' : 'Indisponível','endpoint-job-action-button');
            button.type='button'; button.disabled=!eligible;
            if (!eligible) button.title = typeof reason === 'string' && reason ? reason : 'Indisponível para este endpoint ou operador';
            button.addEventListener('click',() => modal(script));
            row.append(body,button); placement.append(row);
        });
    }
    async function load() {
        if (catalog || loading || failure) return;
        loading = true;
        try {
            const response = await fetch('/api/agent/approved-scripts/',{credentials:'same-origin',cache:'no-store',headers:{'Accept':'application/json'}});
            if (!response.ok) throw new Error();
            const body = await response.json();
            if (!Array.isArray(body.scripts)) throw new Error();
            catalog = body.scripts;
        } catch (_) { failure = true; }
        finally { loading = false; insertCard(); }
    }
    root.addEventListener('click', event => { if (event.target.closest('[data-endpoint-tab="tasks"]')) load(); });
    insertCard();
})();
