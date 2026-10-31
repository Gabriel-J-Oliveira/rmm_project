(() => {
    'use strict';
    const panel = document.querySelector('[data-admin-auth]');
    if (!panel) return;
    const form = panel.querySelector('form'), status = panel.querySelector('[data-status]');
    const password = form.elements.password, account = form.elements.account, domain = form.elements.domain;
    const screen = panel.querySelector('[data-screen]'), identity = panel.querySelector('[data-identity]');
    let socket = null, id = null, generation = 0, terminal = null, observer = null, resizeTimer = null;
    let sent = 0n, received = 0n, pending = 0, totalInput = 0, fit = null;
    const csrf = form.elements.csrfmiddlewaretoken.value;
    function finish(label) {
        generation++;
        password.value = '';
        clearTimeout(resizeTimer); observer?.disconnect(); observer = null;
        terminal?.dispose(); terminal = fit = null; screen.replaceChildren(); screen.hidden = true;
        identity.textContent = 'Identidade não ativa'; pending = totalInput = 0;
        if (socket) { socket.onopen = socket.onclose = socket.onerror = socket.onmessage = null; socket.close(); socket = null; }
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
            if (info.mode !== 'admin_auth' || info.execution !== true || !/^\/ws\/terminal\/[0-9a-f-]{36}\/operator\/$/.test(info.websocket_path)) throw Error();
            const url = new URL(info.websocket_path, location.origin); url.protocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
            socket = new WebSocket(url,'nightowl-terminal.v1');
            socket.binaryType = 'arraybuffer'; sent = received = 0n;
            socket.onopen = () => { socket.send(JSON.stringify({type:'authenticate',credential:info.credential})); delete info.credential; };
            socket.onmessage = message => {
                try {
                    if (typeof message.data !== 'string') {
                        const frame = new Uint8Array(message.data);
                        if (!terminal || frame.length <= 9 || frame.length > 16393 || ![1,2].includes(frame[0])) throw Error();
                        const seq = new DataView(frame.buffer, frame.byteOffset, frame.byteLength).getBigUint64(1,false);
                        if (seq !== ++received) throw Error();
                        const bytes = frame.subarray(9); pending += bytes.length;
                        if (pending > 262144) throw Error();
                        terminal.write(bytes, () => { pending -= bytes.length; });
                        return;
                    }
                    const value = JSON.parse(message.data);
                    if (value.type === 'ping') socket.send(JSON.stringify({type:'pong',nonce:value.nonce}));
                    else if (value.type === 'transport_ready' && value.mode === 'admin_auth') {
                        socket.send(JSON.stringify({type:'windows_auth',account:account.value,domain:domain.value,password:password.value}));
                        password.value = ''; status.textContent = 'Validando credenciais';
                    } else if (value.type === 'ready' && value.mode === 'admin_auth' && value.execution === true) {
                        const user = value.identity;
                        if (!user || user.elevated !== true || user.integrity < 12288 || /^(S-1-5-(18|19|20))$/.test(user.sid)) throw Error();
                        identity.textContent = `${user.sid} · sessão Windows ${user.windows_session_id} · administrador`;
                        terminal = new Terminal({cols:80,rows:24,scrollback:0,allowProposedApi:true,
                            linkHandler:{activate:() => {}},theme:{background:'#0b1020',foreground:'#e2e8f0'}});
                        terminal.parser.registerOscHandler(52, () => true);
                        fit = new FitAddon.FitAddon(); terminal.loadAddon(fit); screen.hidden = false; terminal.open(screen);
                        terminal.onData(value => {
                            const data = new TextEncoder().encode(value);
                            if (!data.length) return;
                            if (data.length > 262144 || totalInput + data.length > 1048576 || socket.bufferedAmount + data.length > 262144) { finish('Limite de entrada ou conexão lenta'); return; }
                            totalInput += data.length;
                            for (let i=0; i<data.length; i+=16384) {
                                const chunk = data.subarray(i,i+16384), frame = new Uint8Array(chunk.length+9);
                                frame[0] = 0; new DataView(frame.buffer).setBigUint64(1,++sent,false); frame.set(chunk,9); socket.send(frame);
                            }
                        });
                        const resize = () => { clearTimeout(resizeTimer); resizeTimer = setTimeout(() => {
                            if (!terminal || !socket || socket.readyState !== WebSocket.OPEN) return;
                            fit.fit(); const cols = Math.max(2,Math.min(240,terminal.cols)), rows = Math.max(2,Math.min(80,terminal.rows));
                            terminal.resize(cols,rows); socket.send(JSON.stringify({type:'resize',cols,rows}));
                        },150); };
                        observer = new ResizeObserver(resize); observer.observe(screen); resize(); terminal.focus(); status.textContent = 'Conectado';
                    } else if (value.type === 'windows_auth_result') finish('Autenticação rejeitada');
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
