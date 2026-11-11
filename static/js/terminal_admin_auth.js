(() => {
    'use strict';
    const panel = document.querySelector('[data-admin-auth]');
    if (!panel) return;

    const form = panel.querySelector('form');
    const account = form.elements.account;
    const password = form.elements.password;
    const domain = form.elements.domain;
    const authModal = panel.querySelector('[data-auth-modal]');
    const terminalModal = panel.querySelector('[data-terminal-modal]');
    const status = panel.querySelector('[data-terminal-status]');
    const indicator = panel.querySelector('[data-connection-indicator]');
    const identity = panel.querySelector('[data-identity]');
    const screen = panel.querySelector('[data-screen]');
    const notice = panel.querySelector('[data-terminal-notice]');
    const stage = panel.querySelector('[data-terminal-stage]');
    const menu = panel.querySelector('[data-terminal-context-menu]');
    const copyButton = panel.querySelector('[data-terminal-copy]');
    const pasteButton = panel.querySelector('[data-terminal-paste]');
    const executeButton = panel.querySelector('[data-terminal-paste-execute]');
    const menuItems = [copyButton, pasteButton, executeButton];
    const csrf = form.elements.csrfmiddlewaretoken.value;
    let socket = null, sessionId = null, generation = 0, terminal = null, fit = null, observer = null;
    let resizeTimer = null, credentialDraft = null, sent = 0n, received = 0n, pending = 0, totalInput = 0;
    let ready = false, menuIndex = 0, stageStartedAt = 0, authStartedAt = 0, sessionStartedAt = 0;
    const MAX_DATA = 16 * 1024, MAX_BUFFER = 256 * 1024, MAX_COPY_BYTES = 256 * 1024, MAX_PASTE_BYTES = 64 * 1024;

    // Remove only the legacy account-history namespace owned by this terminal UI.
    try {
        const legacyKeys = [];
        for (let i = 0; i < localStorage.length; i++) {
            const key = localStorage.key(i);
            if (key && key.startsWith('nightowl.terminal.accounts.')) legacyKeys.push(key);
        }
        legacyKeys.forEach(key => localStorage.removeItem(key));
    } catch (_) { /* Storage may be unavailable in private browsing. */ }

    function metric(name, startedAt) {
        const elapsed = performance.now() - startedAt;
        if (Number.isFinite(elapsed) && elapsed >= 0) {
            panel.dispatchEvent(new CustomEvent('nightowl:terminal-metric', {
                bubbles: true, detail: {stage: name, duration_ms: elapsed, session_id: sessionId}
            }));
        }
    }
    function setState(label, state) {
        status.textContent = label;
        indicator.dataset.state = state;
    }
    function openCredentials() {
        account.value = '';
        account.readOnly = false;
        domain.value = '';
        password.value = '';
        panel.querySelector('[data-auth-status]').textContent = 'Aguardando solicitação';
        authModal.hidden = false;
        account.focus();
    }
    document.querySelector('[data-terminal-open]')?.addEventListener('click', () => {
        if (terminalModal.hidden) openCredentials();
    });
    panel.querySelector('[data-cancel]').addEventListener('click', () => finish('Cancelado'));
    panel.querySelector('[data-terminal-close]').addEventListener('click', () => finish('Desconectado'));
    document.addEventListener('keydown', event => {
        if (event.key !== 'Escape') return;
        if (!terminalModal.hidden) finish('Desconectado');
        else if (!authModal.hidden) finish('Cancelado');
    });

    function cancelSession(id) {
        if (!id) return;
        fetch(`/api/agent/terminal/cancel/${id}/`, {method: 'POST', credentials: 'same-origin', keepalive: true,
            headers: {'Content-Type': 'application/json', 'X-CSRFToken': csrf}, body: JSON.stringify({type: 'cancel'})}).catch(() => {});
    }
    function clearCredentials() {
        clearFields();
        if (credentialDraft) {
            credentialDraft.account = '';
            credentialDraft.domain = '';
            credentialDraft.password = '';
            credentialDraft = null;
        }
    }
    function clearFields() {
        account.value = '';
        password.value = '';
        domain.value = '';
    }
    function finish(label, keepDialog = false, notify = true) {
        generation++;
        ready = false;
        clearCredentials();
        authModal.hidden = true;
        clearTimeout(resizeTimer);
        observer?.disconnect(); observer = null;
        const oldSocket = socket;
        socket = null;
        if (oldSocket) {
            oldSocket.onopen = oldSocket.onclose = oldSocket.onerror = oldSocket.onmessage = null;
            try { oldSocket.close(1000, 'operator_closed'); } catch (_) {}
        }
        if (notify) cancelSession(sessionId);
        sessionId = null;
        terminal?.dispose(); terminal = fit = null;
        screen.replaceChildren(); screen.hidden = true;
        notice.textContent = '';
        closeMenu();
        copyButton.disabled = pasteButton.disabled = executeButton.disabled = true;
        pending = totalInput = 0;
        identity.textContent = 'Identidade Windows não confirmada';
        if (keepDialog) {
            setState(label, 'error');
            terminalModal.hidden = false;
        } else {
            terminalModal.hidden = true;
            setState(label, 'idle');
        }
        form.querySelector('[type=submit]').disabled = false;
    }

    function closeMenu(restoreFocus = false) {
        menu.hidden = true;
        menu.setAttribute('aria-hidden', 'true');
        if (restoreFocus && ready) terminal?.focus();
    }
    function feedback(message) { notice.textContent = message; }
    async function copySelection() {
        const activeTerminal = terminal, activeGeneration = generation;
        const value = activeTerminal?.getSelection() || '';
        if (!value) { feedback('Selecione o texto que deseja copiar.'); return; }
        if (new TextEncoder().encode(value).length > MAX_COPY_BYTES) { feedback('Seleção excede 256 KiB.'); return; }
        try {
            if (!navigator.clipboard?.writeText) throw new Error();
            await navigator.clipboard.writeText(value);
            if (ready && generation === activeGeneration) feedback('Texto copiado.');
        } catch (_) { if (ready && generation === activeGeneration) feedback('Clipboard indisponível ou permissão de cópia negada.'); }
    }
    function normalizePaste(value, execute) {
        if (typeof value !== 'string' || /[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f]/.test(value)) throw new Error('invalid');
        const normalized = value.replace(/\r\n?/g, '\n');
        if (new TextEncoder().encode(normalized).length > MAX_PASTE_BYTES) throw new Error('large');
        const multiline = normalized.includes('\n');
        if (execute) return {text: normalized.replace(/\n+$/g, '').replace(/\n/g, '\r') + '\r', multiline};
        return {text: normalized.replace(/\n/g, ' '), multiline};
    }
    async function pasteFromClipboard(execute = false) {
        if (!ready || !terminal) return;
        const activeTerminal = terminal, activeGeneration = generation;
        try {
            if (!navigator.clipboard?.readText) throw new Error('unavailable');
            const raw = await navigator.clipboard.readText();
            if (!ready || activeGeneration !== generation || activeTerminal !== terminal) return;
            const result = normalizePaste(raw, execute);
            if (execute && result.multiline && !window.confirm('O conteúdo contém várias linhas. Confirmar o envio e a execução dessas linhas no terminal?')) {
                feedback('Execução multilinha cancelada.'); return;
            }
            if (!ready || activeGeneration !== generation || activeTerminal !== terminal) return;
            if (!result.text) { feedback('Clipboard vazio.'); return; }
            activeTerminal.paste(result.text);
            feedback(execute ? 'Conteúdo enviado para execução.' : result.multiline ? 'Colado sem executar; quebras convertidas em espaços.' : 'Colado sem executar.');
        } catch (error) {
            if (!ready || activeGeneration !== generation) return;
            feedback(error.message === 'large' ? 'Colagem excede 64 KiB.' : error.message === 'invalid' ?
                'Clipboard contém caracteres de controle não permitidos.' : 'Clipboard indisponível ou permissão de leitura negada.');
        }
    }
    function openMenu(event) {
        event.preventDefault();
        if (!ready) return;
        const bounds = stage.getBoundingClientRect(), width = menu.offsetWidth || 190, height = menu.offsetHeight || 120;
        menu.style.left = `${Math.max(0, Math.min(event.clientX - bounds.left, bounds.width - width))}px`;
        menu.style.top = `${Math.max(0, Math.min(event.clientY - bounds.top, bounds.height - height))}px`;
        menu.hidden = false; menu.setAttribute('aria-hidden', 'false'); menuIndex = 0; copyButton.focus();
    }
    screen.addEventListener('keydown', event => {
        if (!ready || !event.ctrlKey || !event.shiftKey) return;
        const key = String(event.key || '').toLowerCase();
        if (key !== 'c' && key !== 'v') return;
        event.preventDefault(); event.stopPropagation();
        if (key === 'c') copySelection(); else pasteFromClipboard(false);
    }, true);
    screen.addEventListener('contextmenu', openMenu);
    menu.addEventListener('keydown', event => {
        const enabled = menuItems.filter(item => !item.disabled);
        const currentIndex = enabled.indexOf(document.activeElement);
        const current = currentIndex >= 0 ? currentIndex : Math.min(menuIndex, enabled.length - 1);
        let next = null;
        if (event.key === 'ArrowDown') next = (current + 1) % enabled.length;
        else if (event.key === 'ArrowUp') next = (current - 1 + enabled.length) % enabled.length;
        else if (event.key === 'Home') next = 0;
        else if (event.key === 'End') next = enabled.length - 1;
        if (next !== null && enabled.length) { event.preventDefault(); menuIndex = next; enabled[next].focus(); }
    });
    copyButton.addEventListener('click', () => { copySelection(); closeMenu(true); });
    pasteButton.addEventListener('click', () => { pasteFromClipboard(false); closeMenu(true); });
    executeButton.addEventListener('click', () => { pasteFromClipboard(true); closeMenu(true); });
    document.addEventListener('pointerdown', event => { if (!menu.hidden && !menu.contains(event.target)) closeMenu(); });
    document.addEventListener('keydown', event => { if (!menu.hidden && event.key === 'Escape') { event.preventDefault(); closeMenu(true); } });

    function identityIsValid(user) {
        return user && typeof user.sid === 'string' && user.elevated === true && Number.isInteger(user.integrity) &&
            user.integrity >= 12288 && Number.isInteger(user.windows_session_id) && user.windows_session_id >= 0 &&
            !/^S-1-5-(18|19|20)$/.test(user.sid);
    }
    function sendInput(value) {
        if (!ready || !socket || socket.readyState !== WebSocket.OPEN) return;
        const bytes = new TextEncoder().encode(value);
        if (bytes.length > MAX_BUFFER || totalInput + bytes.length > 1048576 || socket.bufferedAmount + bytes.length > MAX_BUFFER) {
            finish('Desconectado: limite de entrada ou conexão lenta', true); return;
        }
        totalInput += bytes.length;
        for (let offset = 0; offset < bytes.length; offset += MAX_DATA) {
            const chunk = bytes.subarray(offset, offset + MAX_DATA), frame = new Uint8Array(chunk.length + 9);
            frame[0] = 0; new DataView(frame.buffer).setBigUint64(1, ++sent, false); frame.set(chunk, 9); socket.send(frame);
        }
    }
    function resize(gen) {
        clearTimeout(resizeTimer);
        resizeTimer = setTimeout(() => {
            if (gen !== generation || !ready || !terminal || !socket || socket.readyState !== WebSocket.OPEN) return;
            fit.fit();
            const cols = Math.max(2, Math.min(240, terminal.cols)), rows = Math.max(2, Math.min(80, terminal.rows));
            terminal.resize(cols, rows);
            if (socket.bufferedAmount <= MAX_BUFFER) socket.send(JSON.stringify({type: 'resize', cols, rows}));
        }, 120);
    }
    function createTerminal(user, gen) {
        if (!identityIsValid(user)) throw new Error('identity');
        identity.textContent = `${user.sid} · sessão Windows ${user.windows_session_id} · administrador elevado confirmado`;
        setState('Iniciando terminal', 'starting');
        stageStartedAt = performance.now();
        terminal = new Terminal({cols: 80, rows: 24, scrollback: 2000, fontSize: 14, fontFamily: 'Consolas, monospace',
            allowProposedApi: true, allowTransparency: false, disableStdin: false,
            linkHandler: {activate: () => {}}, theme: {background: '#0b1020', foreground: '#e2e8f0'}});
        terminal.parser.registerOscHandler(52, () => true);
        fit = new FitAddon.FitAddon(); terminal.loadAddon(fit);
        screen.hidden = false; terminal.open(screen); terminal.onData(sendInput);
        ready = true;
        copyButton.disabled = pasteButton.disabled = executeButton.disabled = false;
        observer = new ResizeObserver(() => resize(gen)); observer.observe(screen); resize(gen);
        terminal.focus();
        metric('agent_ready_to_terminal_ready', stageStartedAt);
        metric('request_to_terminal_ready', sessionStartedAt);
        setState('Conectado', 'connected');
    }

    form.addEventListener('submit', async event => {
        event.preventDefault();
        if (!form.reportValidity()) return;
        if (location.protocol !== 'https:' && location.hostname !== '127.0.0.1') { finish('Conexão segura necessária', true, false); return; }
        let rawAccount = account.value.trim();
        const parts = rawAccount.split('\\');
        let accountName = parts[parts.length - 1];
        if (parts.length > 2 || !accountName) { finish('Formato de usuário inválido', true, false); return; }
        credentialDraft = {account: accountName, domain: parts.length === 2 ? parts[0] : '', password: password.value};
        rawAccount = accountName = '';
        parts.fill('');
        clearFields();
        const gen = ++generation;
        sessionStartedAt = performance.now();
        form.querySelector('[type=submit]').disabled = true;
        authModal.hidden = true;
        terminalModal.hidden = false;
        identity.textContent = 'Identidade Windows ainda não confirmada';
        setState('Solicitando conexão', 'pending');
        try {
            const response = await fetch(panel.dataset.url, {method: 'POST', credentials: 'same-origin', cache: 'no-store',
                headers: {'Content-Type': 'application/json', 'X-CSRFToken': csrf},
                body: JSON.stringify({type: 'request', protocol: 1, mode: 'admin_auth'})});
            if (!response.ok) throw new Error('request');
            const info = await response.json();
            if (gen !== generation) { cancelSession(info.session_id); return; }
            sessionId = info.session_id;
            const expectedPath = `/ws/terminal/${sessionId}/operator/`;
            if (typeof sessionId !== 'string' || !/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/.test(sessionId) ||
                info.mode !== 'admin_auth' || info.execution !== true || info.websocket_path !== expectedPath ||
                typeof info.credential !== 'string' || info.credential.length < 32 || info.credential.length > 128) throw new Error('contract');
            const url = new URL(info.websocket_path, location.origin);
            url.protocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
            if (url.origin.replace(/^ws/, 'http') !== location.origin) throw new Error('origin');
            socket = new WebSocket(url, 'nightowl-terminal.v1');
            socket.binaryType = 'arraybuffer'; sent = received = 0n;
            socket.onopen = () => {
                if (gen !== generation) return;
                const ticket = info.credential; delete info.credential;
                socket.send(JSON.stringify({type: 'authenticate', credential: ticket}));
                metric('request_to_transport_open', sessionStartedAt);
            };
            socket.onmessage = message => {
                if (gen !== generation) return;
                try {
                    if (typeof message.data !== 'string') {
                        const frame = new Uint8Array(message.data);
                        if (!ready || frame.length <= 9 || frame.length > MAX_DATA + 9 || ![1, 2].includes(frame[0])) throw new Error('frame');
                        const sequence = new DataView(frame.buffer, frame.byteOffset, frame.byteLength).getBigUint64(1, false);
                        if (sequence !== ++received) throw new Error('sequence');
                        pending += frame.length - 9;
                        if (pending > MAX_BUFFER) throw new Error('render_pressure');
                        terminal.write(frame.subarray(9), () => { if (gen === generation) pending -= frame.length - 9; });
                        return;
                    }
                    if (message.data.length > 1024) throw new Error('control');
                    const value = JSON.parse(message.data);
                    if (value.type === 'ping') socket.send(JSON.stringify({type: 'pong', nonce: value.nonce}));
                    else if (value.type === 'transport_ready' && value.mode === 'admin_auth' && value.execution === true) {
                        const transportReadyAt = performance.now();
                        setState('Autenticando no Windows', 'authenticating');
                        if (!credentialDraft) throw new Error('credential_unavailable');
                        const auth = {type: 'windows_auth', ...credentialDraft};
                        socket.send(JSON.stringify(auth));
                        auth.account = auth.domain = auth.password = '';
                        credentialDraft.account = credentialDraft.domain = credentialDraft.password = '';
                        credentialDraft = null;
                        authStartedAt = performance.now();
                        metric('transport_ready_to_credentials_sent', transportReadyAt);
                    } else if (value.type === 'windows_auth_result') {
                        if (value.result !== 'rejected') throw new Error('auth_result');
                        finish('Falha de autenticação', true);
                    } else if (value.type === 'ready' && value.mode === 'admin_auth' && value.execution === true) {
                        metric('credentials_sent_to_agent_ready', authStartedAt);
                        createTerminal(value.identity, gen);
                    } else if (value.type === 'error' || value.type === 'closed') {
                        const expired = ['session_expired_or_revoked', 'join_timeout', 'keepalive_timeout', 'control_timeout'].includes(value.code);
                        finish(value.type === 'error' ? 'Falha na sessão' : expired ? 'Sessão expirada ou revogada' : 'Desconectado', true, false);
                    }
                } catch (_) { finish('Falha na sessão', true); }
            };
            socket.onerror = () => { if (gen === generation) finish('Desconectado: falha de conexão', true); };
            socket.onclose = () => { if (gen === generation) finish('Desconectado', true, false); };
        } catch (_) {
            if (gen === generation) finish('Falha ao solicitar conexão', true);
        }
    });
    window.addEventListener('pagehide', () => finish('Desconectado', false));
})();
