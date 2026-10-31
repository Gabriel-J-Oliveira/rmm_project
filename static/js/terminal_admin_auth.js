(() => {
    'use strict';
    const panel = document.querySelector('[data-admin-auth]');
    if (!panel) return;
    const form = panel.querySelector('form'), status = panel.querySelector('[data-status]');
    const password = form.elements.password, account = form.elements.account, domain = form.elements.domain;
    let socket = null, id = null, generation = 0;
    const csrf = form.elements.csrfmiddlewaretoken.value;
    function finish(label) {
        generation++;
        password.value = '';
        if (socket) { socket.onclose = socket.onerror = socket.onmessage = null; socket.close(); socket = null; }
        if (id) {
            fetch(`/api/agent/terminal/cancel/${id}/`, {method:'POST',credentials:'same-origin',keepalive:true,
                headers:{'Content-Type':'application/json','X-CSRFToken':csrf},body:JSON.stringify({type:'cancel'})}).catch(() => {});
            id = null;
        }
        form.querySelector('[type=submit]').disabled = false;
        status.textContent = label;
    }
    form.addEventListener('submit', async event => {
        event.preventDefault();
        if (location.protocol !== 'https:' && location.hostname !== '127.0.0.1') { finish('Conexão segura necessária'); return; }
        const current = ++generation;
        form.querySelector('[type=submit]').disabled = true;
        status.textContent = 'Aguardando agente';
        try {
            const response = await fetch(panel.dataset.url, {method:'POST',credentials:'same-origin',cache:'no-store',
                headers:{'Content-Type':'application/json','X-CSRFToken':csrf},body:JSON.stringify({type:'request',protocol:1,mode:'admin_auth'})});
            if (!response.ok) throw Error();
            const info = await response.json();
            if (current !== generation) {
                fetch(`/api/agent/terminal/cancel/${info.session_id}/`, {method:'POST',credentials:'same-origin',keepalive:true,
                    headers:{'Content-Type':'application/json','X-CSRFToken':csrf},body:JSON.stringify({type:'cancel'})}).catch(() => {});
                return;
            }
            id = info.session_id;
            if (info.mode !== 'admin_auth' || info.execution || !/^\/ws\/terminal\/[0-9a-f-]{36}\/operator\/$/.test(info.websocket_path)) throw Error();
            const url = new URL(info.websocket_path, location.origin); url.protocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
            socket = new WebSocket(url,'nightowl-terminal.v1');
            socket.onopen = () => { socket.send(JSON.stringify({type:'authenticate',credential:info.credential})); delete info.credential; };
            socket.onmessage = message => {
                try {
                    const value = JSON.parse(message.data);
                    if (value.type === 'ping') socket.send(JSON.stringify({type:'pong',nonce:value.nonce}));
                    else if (value.type === 'transport_ready' && value.mode === 'admin_auth') {
                        socket.send(JSON.stringify({type:'windows_auth',account:account.value,domain:domain.value,password:password.value}));
                        password.value = ''; status.textContent = 'Validando credenciais';
                    } else if (value.type === 'windows_auth_result') finish(value.result === 'accepted' ? 'Autenticação aprovada' : 'Autenticação rejeitada');
                    else if (value.type === 'error' || value.type === 'closed') finish('Autenticação encerrada');
                } catch (_) { finish('Erro de autenticação'); }
            };
            socket.onerror = () => finish('Erro de conexão');
            socket.onclose = () => finish('Conexão encerrada');
        } catch (_) { if (current === generation) finish('Solicitação rejeitada'); }
    });
    panel.querySelector('[data-cancel]').addEventListener('click', () => finish('Cancelado'));
    window.addEventListener('pagehide', () => finish('Encerrado'));
})();
