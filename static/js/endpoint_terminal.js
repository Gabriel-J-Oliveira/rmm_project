/* D.3: bounded ephemeral VT; no content storage, logging, clipboard or external code. */
(() => {
    'use strict';
    const panel = document.querySelector('[data-terminal-panel]');
    if (!panel) return;
    const start = panel.querySelector('[data-terminal-start]'), stop = panel.querySelector('[data-terminal-stop]');
    const state = panel.querySelector('[data-terminal-state]'), identity = panel.querySelector('[data-terminal-identity]');
    const screen = panel.querySelector('[data-terminal-screen]'), notice = panel.querySelector('[data-terminal-notice]');
    const csrf = panel.querySelector('[name=csrfmiddlewaretoken]').value;
    let session = null, socket = null, terminal = null, fit = null, timer = null, observer = null;
    let generation = 0, sent = 0n, received = 0n, pendingBytes = 0, totalInput = 0, ready = false;
    let inputAt = null, enterAt = null, resizeTimer = null, lastResize = '';
    const MAX_DATA = 16384, MAX_BUFFER = 262144;
    const metric = (stage, elapsed) => {
        if (!Number.isFinite(elapsed) || elapsed < 0) return;
        panel.dispatchEvent(new CustomEvent('nightowl:terminal-metric', {bubbles:true,detail:{stage,duration_ms:elapsed,session_id:session?.session_id || null}}));
    };
    const show = (text) => { state.textContent = text; };
    function identify(value) {
        if (!value || value.elevated !== false || value.integrity > 8192 || /^(S-1-5-(18|19|20))$/.test(value.sid)) throw new Error('identity');
        identity.textContent = `${value.sid} · sessão Windows ${value.windows_session_id} · não elevado`;
    }
    async function request(url, value) {
        const result = await fetch(url, {method:value ? 'POST':'GET', credentials:'same-origin', cache:'no-store',
            headers:value ? {'Content-Type':'application/json','X-CSRFToken':csrf}:{}, body:value ? JSON.stringify(value):undefined});
        if (!result.ok) throw new Error('authorization');
        return result.json();
    }
    function cancel(id, beacon=false) {
        if (!id) return;
        const url = `/api/agent/terminal/cancel/${id}/`;
        if (beacon) navigator.sendBeacon(url, new URLSearchParams({type:'cancel',csrfmiddlewaretoken:csrf}));
        else request(url,{type:'cancel'}).catch(() => {});
    }
    function end(label, notify=true, beacon=false) {
        generation++; ready = false; clearTimeout(timer); clearTimeout(resizeTimer);
        observer?.disconnect(); observer = null;
        const id = session?.session_id;
        if (socket) { socket.onopen = socket.onclose = socket.onerror = socket.onmessage = null; socket.close(1000,'operator_closed'); socket = null; }
        if (notify) cancel(id, beacon);
        terminal?.dispose(); terminal = fit = null; screen.replaceChildren(); screen.hidden = true;
        session = null; pendingBytes = totalInput = 0; inputAt = enterAt = null;
        start.disabled = false; stop.disabled = true; show(label);
        identity.textContent = 'Identidade não ativa';
    }
    function control(value) {
        if (!socket || socket.readyState !== WebSocket.OPEN || socket.bufferedAmount > MAX_BUFFER) throw new Error('pressure');
        socket.send(JSON.stringify(value));
    }
    function send(value) {
        if (!ready) return;
        const bytes = new TextEncoder().encode(value);
        if (!bytes.length) return;
        if (bytes.length > MAX_BUFFER || totalInput + bytes.length > 1048576 || socket.bufferedAmount + bytes.length > MAX_BUFFER) { end('Erro: conexão lenta ou limite de entrada'); return; }
        totalInput += bytes.length;
        if (inputAt === null) inputAt = performance.now();
        if (value.includes('\r')) enterAt = performance.now();
        for (let offset=0;offset<bytes.length;offset+=MAX_DATA) {
            const payload = bytes.subarray(offset,offset+MAX_DATA), frame = new Uint8Array(payload.length+9);
            frame[0]=0; new DataView(frame.buffer).setBigUint64(1,++sent,false); frame.set(payload,9);
            socket.send(frame);
        }
    }
    function resize() {
        clearTimeout(resizeTimer);
        resizeTimer = setTimeout(() => {
            if (!ready || !terminal) return;
            fit.fit();
            const cols = Math.min(240,Math.max(2,terminal.cols)), rows = Math.min(80,Math.max(2,terminal.rows));
            terminal.resize(cols,rows);
            const size = `${cols}:${rows}`;
            if (size !== lastResize) { control({type:'resize',cols,rows}); lastResize = size; }
        },150);
    }
    function connect(info, gen) {
        if (gen !== generation) return;
        if (info.mode !== 'conpty' || info.execution !== true || !/^\/ws\/terminal\/[0-9a-f-]{36}\/operator\/$/.test(info.websocket_path)) throw new Error('contract');
        let credential = info.credential; delete info.credential;
        session = info; sent = received = 0n; lastResize = '';
        const url = new URL(info.websocket_path,location.origin); url.protocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
        if (url.origin.replace(/^ws/,'http') !== location.origin) throw new Error('origin');
        show('Conectando');
        socket = new WebSocket(url,'nightowl-terminal.v1'); socket.binaryType = 'arraybuffer';
        socket.onopen = () => {
            if (gen !== generation) { credential=null; return; }
            control({type:'authenticate',credential}); credential = null;
        };
        socket.onerror = () => end('Erro de conexão');
        socket.onclose = () => end('Desconectado');
        socket.onmessage = event => {
            if (gen !== generation) return;
            try {
                if (typeof event.data === 'string') {
                    if (event.data.length > 1024) throw new Error('control');
                    const message = JSON.parse(event.data);
                    if (message.type === 'ping') control({type:'pong',nonce:message.nonce});
                    else if (message.type === 'ready') {
                        if (message.mode !== 'conpty' || message.execution !== true) throw new Error('mode');
                        identify(message.identity || info.identity);
                        terminal = new Terminal({cols:80,rows:24,scrollback:0,fontSize:14,fontFamily:'Consolas, monospace',
                            allowProposedApi:true,allowTransparency:false,disableStdin:false,
                            linkHandler:{activate:() => {}},theme:{background:'#0b1020',foreground:'#e2e8f0'}});
                        fit = new FitAddon.FitAddon(); terminal.loadAddon(fit);
                        terminal.parser.registerOscHandler(52,() => true); // Never read/write clipboard through terminal escapes.
                        screen.hidden=false; terminal.open(screen); terminal.onData(send); ready=true;
                        observer = new ResizeObserver(resize); observer.observe(screen); resize(); terminal.focus();
                        show('Conectado'); notice.textContent='Acesso com consentimento local. Encerrar descarta a tela e finaliza o processo.';
                    } else if (message.type === 'closed' || message.type === 'error') end(message.type === 'error' ? 'Erro na sessão' : 'Encerrado');
                    return;
                }
                const frame = new Uint8Array(event.data);
                if (!ready || frame.length<=9 || frame.length>MAX_DATA+9 || ![1,2].includes(frame[0])) throw new Error('binary');
                const seq = new DataView(frame.buffer,frame.byteOffset,frame.byteLength).getBigUint64(1,false);
                if (seq !== received+1n) throw new Error('sequence'); received=seq;
                const bytes=frame.subarray(9), arrived=performance.now(); pendingBytes+=bytes.length;
                if (pendingBytes>MAX_BUFFER) throw new Error('render_pressure');
                terminal.write(bytes,() => {
                    if (gen !== generation) return;
                    pendingBytes-=bytes.length;
                    requestAnimationFrame(() => requestAnimationFrame(() => {
                        if (gen !== generation) return;
                        metric('receive_to_render',performance.now()-arrived);
                        if (inputAt !== null) { metric('input_to_next_render',performance.now()-inputAt); inputAt=null; }
                        if (enterAt !== null) { metric('enter_to_next_render',performance.now()-enterAt); enterAt=null; }
                    }));
                });
            } catch (_) { end('Erro de protocolo ou conexão lenta'); }
        };
    }
    async function poll(gen) {
        if (gen !== generation || !session) return;
        try {
            const status = await request(`/api/agent/terminal/status/${session.session_id}/`);
            if (gen !== generation) return;
            if (['authorized','connecting'].includes(status.status)) {
                identify(status.identity);
                const info = await request(`/api/agent/terminal/connect/${session.session_id}/`,{type:'connect',protocol:1});
                if (gen !== generation) return;
                connect(info,gen); return;
            }
            if (status.status !== 'awaiting_consent') { end('Encerrado',false); return; }
            timer = setTimeout(() => poll(gen),1000); // Consent/control only; interactive bytes use WS exclusively.
        } catch (_) { if (gen===generation) end('Erro de autorização'); }
    }
    start.addEventListener('click',async () => {
        const taskPanel = document.querySelector('[data-endpoint-tab-panel="tasks"]');
        if (taskPanel?.hidden) document.querySelector('[data-endpoint-tab="tasks"]')?.click();
        const disclosure = document.querySelector('[data-terminal-conpty-panel]');
        if (disclosure) disclosure.open = true;
        start.disabled=true; stop.disabled=false; show('Aguardando autorização');
        const gen=++generation, at=performance.now();
        try {
            const value = await request(panel.dataset.requestUrl,{type:'request',protocol:1,mode:'conpty'});
            if (gen !== generation) { cancel(value.session_id); return; }
            session=value; metric('request_to_authorized',performance.now()-at);
            show('Aguardando consentimento'); poll(gen);
        } catch (_) { if(gen===generation) end('Erro de autorização'); }
    });
    stop.addEventListener('click',() => end('Encerrado'));
    window.addEventListener('pagehide',() => end('Encerrado',true,true));
    start.disabled=false;
    const launchButton = document.querySelector('[data-endpoint-detail][data-terminal-mode="conpty"] [data-terminal-open]');
    launchButton?.addEventListener('click', () => {
        const disclosure = document.querySelector('[data-terminal-conpty-panel]');
        if (disclosure) disclosure.open = true;
        if (!start.disabled) start.click();
    });
})();
