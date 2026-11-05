(function (scope) {
    "use strict";
    function escape(value) { return String(value == null ? "-" : value).replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c])); }
    function errorMessage(code) {
        return ({
            live_cache_unavailable: 'Cache do monitoramento indisponível. Solicite ao administrador que verifique o serviço.',
            forbidden: 'Seu usuário não tem permissão para monitoramento ao vivo.',
            session_owned_by_another_operator: 'Outro operador já iniciou uma sessão neste endpoint.',
            endpoint_unavailable: 'Endpoint indisponível para monitoramento ao vivo.',
            session_expired: 'Sessão expirada. Inicie uma nova sessão.'
        })[code] || 'Não foi possível comunicar com o monitoramento. Verifique sua conexão e tente novamente.';
    }
    function chart(points, names, maximum, labels) {
        const valid = points.slice(-90).filter(p => p && p.metrics && Number.isFinite(Date.parse(p.collected_at)));
        const numbers = valid.flatMap(p => names.map(name => p.metrics[name])).filter(Number.isFinite);
        if (!numbers.length) return '<p class="muted">Sem medição disponível</p>';
        const network = names.some(name => name.startsWith('network.'));
        const peak = Math.max(...numbers), scale = network && peak >= 1048576 ? 1048576 : network && peak >= 1024 ? 1024 : 1;
        const unit = network ? (scale === 1048576 ? 'MiB/s' : scale === 1024 ? 'KiB/s' : 'B/s') : maximum === 100 ? '%' : 'ms';
        const cap = maximum || Math.max(1, peak / scale) * 1.05;
        const fmt = n => Number(n).toLocaleString('pt-BR', {maximumFractionDigits: 2});
        const time = t => new Date(t).toLocaleTimeString('pt-BR');
        const first = Date.parse(valid[0].collected_at), lastTime = Date.parse(valid[valid.length - 1].collected_at);
        const x = p => lastTime > first ? 55 + (Date.parse(p.collected_at) - first) * 530 / (lastTime - first) : 320;
        const y = v => 145 - Math.max(0, Math.min(1, v / scale / cap)) * 120;
        const grid = [0, .25, .5, .75, 1].map(f => `<line class="live-gridline" x1="55" x2="585" y1="${145-f*120}" y2="${145-f*120}"/><text x="48" y="${149-f*120}" text-anchor="end">${escape(fmt(cap*f))}</text>`).join('');
        const series = names.map((name, index) => {
            let path = '', drawing = false, circles = '';
            valid.forEach((point, i) => {
                const value = point.metrics[name], previous = valid[i-1];
                if (!Number.isFinite(value)) { drawing = false; return; }
                if (previous && Date.parse(point.collected_at) - Date.parse(previous.collected_at) > 25000) drawing = false;
                const px=x(point).toFixed(2), py=y(value).toFixed(2);
                path += (drawing ? ' L' : ' M') + px + ' ' + py; drawing = true;
                const text = `${labels && labels[index] || name} · ${time(point.collected_at)} · ${fmt(value/scale)} ${unit} (${value} ${network ? 'B/s' : unit})`;
                circles += `<circle class="live-point live-series-${index}" cx="${px}" cy="${py}" r="4" tabindex="0" role="button" aria-label="${escape(text)}" data-live-tip="${escape(text)}"><title>${escape(text)}</title></circle>`;
            });
            return `<path class="live-line live-series-${index}" d="${path}"/>${circles}`;
        }).join('');
        const legend = names.map((name, index) => {
            const values=valid.map(p=>p.metrics[name]).filter(Number.isFinite), current=valid[valid.length-1].metrics[name];
            const stat = n => Number.isFinite(n) ? `${fmt(n/scale)} ${unit}` : 'Não informado';
            return `<span class="live-legend live-series-${index}">${escape(labels && labels[index] || name)}: atual ${stat(current)} · mín ${stat(values.length ? Math.min(...values) : null)} · máx ${stat(values.length ? Math.max(...values) : null)}</span>`;
        }).join('');
        return `<svg viewBox="0 0 600 190" role="group" aria-label="Gráfico de medições; selecione um ponto para detalhes"><text x="55" y="13">${unit}</text>${grid}${series}<text x="55" y="169">${escape(time(first))}</text><text x="585" y="169" text-anchor="end">${escape(time(lastTime))}</text></svg><div class="live-legends">${legend}</div><p class="live-tooltip" data-chart-tooltip aria-live="polite">Selecione um ponto para ver horário e valor.</p>`;
    }
    if (typeof module !== "undefined" && module.exports) module.exports = {chart, escape, errorMessage};
    if (typeof document === "undefined") return;
    const root = document.querySelector('[data-endpoint-detail]');
    if (!root || root.dataset.liveAllowed !== 'yes') return;
    const id = root.dataset.endpointId;
    if (!id) return;
    const panel = document.createElement('section');
    panel.className = 'endpoint-live panel endpoint-dense-card';
    panel.innerHTML = '<header><h2>Monitoramento ao vivo</h2><div class="live-actions"><button type="button" class="copy-btn" data-live-toggle>Iniciar</button><button type="button" class="copy-btn" data-live-clear hidden>Limpar</button></div></header><p data-live-state>Sessao inativa</p><div data-live-body hidden><label>Interface<select data-live-interface></select></label><label>Disco apresentado pelo Windows<select data-live-disk></select></label><div class="endpoint-live-grid"><div><h3>CPU (%)</h3><div data-live-cpu></div></div><div><h3>RAM utilizada / disponivel (%)</h3><div data-live-memory></div></div><div><h3>RX / TX (bytes/s)</h3><div data-live-network></div></div><div><h3>Latencia de leitura / escrita (ms)</h3><div data-live-storage></div></div></div><dl data-live-facts class="endpoint-fact-list"></dl></div>';
    function mount() {
        const placement = root.querySelector('[data-endpoint-tab-panel="monitoring"] [data-live-placement]');
        if (!placement) return false;
        if (panel.parentNode !== placement) placement.appendChild(panel);
        return true;
    }
    if (!mount()) {
        const observer = new MutationObserver(() => { if (mount()) observer.disconnect(); });
        observer.observe(root, {childList:true, subtree:true});
    }
    if (scope.lucide) scope.lucide.createIcons();
    let session = null, points = [], timer = null, polling = false, lastRenewal = 0, state = 'idle', epoch = 0, busy = false;
    const button = panel.querySelector('[data-live-toggle]');
    const status = panel.querySelector('[data-live-state]');
    const clear = panel.querySelector('[data-live-clear]');
    const url = '/api/agent/telemetry/live/' + encodeURIComponent(id) + '/';
    function csrf() {
        const match = document.cookie.match(/(?:^|;\s*)csrftoken=([^;]+)/);
        return match ? decodeURIComponent(match[1]) : document.querySelector('[name=csrfmiddlewaretoken]')?.value || '';
    }
    async function request(action, keepalive, currentSession = session) {
        const response = await fetch(url, {method:'POST', credentials:'same-origin', keepalive:!!keepalive, signal:AbortSignal.timeout(10000),
            headers:{'Content-Type':'application/json', 'X-CSRFToken':csrf()},
            body:JSON.stringify({action, session_id:currentSession && currentSession.session_id})});
        const result = await response.json();
        if (!response.ok) throw new Error(result.error || 'live_unavailable');
        if ((action === 'start' || action === 'renew') &&
            (result.state !== 'active' || !result.session_id || !Number.isFinite(Date.parse(result.expires_at)))) {
            throw new Error('Resposta de sessão inválida');
        }
        return result;
    }
    function selector(name, prefix, suffix) {
        const select = panel.querySelector('[data-live-' + name + ']');
        const value = select.value;
        const keys = [...new Set(points.flatMap(p => Object.keys(p.metrics)).filter(key => key.startsWith(prefix)).map(key => key.split('.')[1]))];
        select.replaceChildren();
        const metadata = points.length ? points[points.length - 1].quality || {} : {};
        const names = metadata[name === 'interface' ? 'interfaces' : 'disk_instances'] || {};
        keys.forEach(key => {
            const option = document.createElement('option');
            option.value = key;
            option.textContent = (names[key] && (names[key].name || names[key].instance)) || key;
            select.appendChild(option);
        });
        if (keys.includes(value)) select.value = value;
        return prefix + select.value + '.' + suffix;
    }
    function draw() {
        panel.querySelector('[data-live-body]').hidden = state === 'idle';
        clear.hidden = state === 'idle';
        button.hidden = state === 'frozen';
        button.textContent = state === 'active' ? 'Encerrar' : 'Iniciar';
        if (state === 'idle') return;
        const rx = selector('interface', 'network.', 'received_bytes_per_sec');
        const read = selector('disk', 'disk.', 'read_latency_ms');
        panel.querySelector('[data-live-cpu]').innerHTML = chart(points, ['cpu.usage_percent'], 100, ['CPU (%)']);
        panel.querySelector('[data-live-memory]').innerHTML = chart(points, ['memory.used_percent', 'memory.available_percent'], 100, ['Utilizada (%)', 'Disponivel (%)']);
        panel.querySelector('[data-live-network]').innerHTML = chart(points, [rx, rx.replace('received_bytes_per_sec','sent_bytes_per_sec')], null, ['RX', 'TX']);
        panel.querySelector('[data-live-storage]').innerHTML = chart(points, [read, read.replace('read_latency_ms','write_latency_ms')], null, ['Leitura (ms)', 'Escrita (ms)']);
        const last = points[points.length - 1];
        const facts = panel.querySelector('[data-live-facts]');
        facts.replaceChildren();
        if (last) for (const [label, key, divisor, unit] of [
            ['RAM disponivel', 'memory.available_bytes', 1073741824, 'GiB'],
            ['RAM utilizada', 'memory.used_bytes', 1073741824, 'GiB'],
            ['Commit', 'memory.committed_percent', 1, '%'],
            ['Leitura de disco', read.replace('read_latency_ms','read_bytes_per_sec'), 1, 'bytes/s'],
            ['Escrita de disco', read.replace('read_latency_ms','write_bytes_per_sec'), 1, 'bytes/s'],
            ['Fila media de disco', read.replace('read_latency_ms','queue_length'), 1, 'operacoes']]) {
            const box = document.createElement('div'), title = document.createElement('dt'), detail = document.createElement('dd');
            title.textContent = label;
            detail.textContent = Number.isFinite(last.metrics[key]) ? (last.metrics[key] / divisor).toFixed(2) + ' ' + unit : 'Desconhecido';
            box.append(title, detail); facts.appendChild(box);
        }
    }
    function freeze(message, preserveEmpty = false) {
        ++epoch; session = null; clearTimeout(timer); timer = null;
        state = points.length || preserveEmpty ? 'frozen' : 'idle';
        status.textContent = (message || 'Análise encerrada') + ' · ' + new Date().toLocaleString('pt-BR');
        draw();
    }
    function reset() {
        ++epoch; clearTimeout(timer); timer = null; session = null; points = []; state = 'idle';
        status.textContent = 'Sessão inativa'; draw();
    }
    async function poll() {
        if (state !== 'active' || !session || polling || busy) return;
        const current = session, generation = epoch;
        if (Date.now() >= Date.parse(current.expires_at)) { freeze('Sessão expirada'); return; }
        polling = true;
        try {
            if (!document.hidden && Date.now() - lastRenewal >= 15000) {
                const renewed = await request('renew', false, current);
                if (generation !== epoch || state !== 'active') return;
                session = renewed; lastRenewal = Date.now();
            }
            const response = await fetch(url, {credentials:'same-origin', cache:'no-store', signal:AbortSignal.timeout(10000)});
            const data = await response.json();
            if (generation !== epoch || state !== 'active') return;
            if (!response.ok) throw new Error(data.error || 'live_unavailable');
            if (data.state !== 'active' || data.session_id !== current.session_id) { freeze('Sessão expirada'); return; }
            points = data.points || [];
            status.textContent = (!data.last_sample_at ? 'Aguardando dados do agente' : data.stale ? 'Sem dados recentes' : 'Recebendo amostras') + (data.last_sample_at ? ' · Última amostra: ' + new Date(data.last_sample_at).toLocaleString('pt-BR') : '');
            draw();
        } catch (error) {
            if (generation === epoch && state === 'active') {
                if (error.message === 'session_expired') freeze('Sessão expirada');
                else status.textContent = 'Dados indisponíveis: ' + errorMessage(error.message);
            }
        } finally {
            polling = false;
            if (state === 'active' && !busy && !timer) timer = setTimeout(() => { timer = null; poll(); }, 5000);
        }
    }
    async function stop(discard) {
        if (busy) return;
        if (state === 'frozen') { if (discard) reset(); return; }
        if (state !== 'active' || !session) return;
        const current = session; ++epoch; busy = true; clearTimeout(timer); timer = null;
        button.disabled = clear.disabled = true;
        try {
            const stopped = await request('stop', false, current);
            if (stopped.state !== 'expired') throw new Error('Encerramento não confirmado');
            if (discard) reset(); else freeze('Análise encerrada', true);
        } catch (error) {
            status.textContent = 'Não foi possível encerrar a sessão: ' + errorMessage(error.message);
        } finally {
            busy = false; button.disabled = clear.disabled = false;
            if (state === 'active' && !timer) timer = setTimeout(() => { timer = null; poll(); }, 5000);
        }
    }
    button.addEventListener('click', async () => {
        if (state === 'active') { await stop(false); return; }
        if (state !== 'idle' || busy) return;
        busy = true; button.disabled = true;
        try {
            session = await request('start'); ++epoch; state = 'active'; points = [];
            lastRenewal = Date.now(); status.textContent = 'Aguardando dados do agente'; draw();
        } catch (error) { status.textContent = errorMessage(error.message); }
        finally { busy = false; button.disabled = false; }
        if (state === 'active') poll();
    });
    clear.addEventListener('click', () => stop(true));
    function showPoint(event) {
        const point = event.target.closest('[data-live-tip]');
        if (!point || !panel.contains(point)) return;
        const tooltip = point.closest('[data-live-cpu], [data-live-memory], [data-live-network], [data-live-storage]').querySelector('[data-chart-tooltip]');
        if (tooltip) tooltip.textContent = point.dataset.liveTip;
    }
    ['mouseover', 'focusin', 'click'].forEach(name => panel.addEventListener(name, showPoint));
    panel.querySelectorAll('select').forEach(select => select.addEventListener('change', draw));
    scope.addEventListener('pagehide', () => { if (session) { request('stop', true).catch(() => {}); clearTimeout(timer); } });
})(typeof window !== 'undefined' ? window : globalThis);
