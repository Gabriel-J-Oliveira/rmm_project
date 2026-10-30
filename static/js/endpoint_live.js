(function (scope) {
    "use strict";
    function escape(value) { return String(value == null ? "-" : value).replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c])); }
    function chart(points, names, maximum, labels) {
        const valid = points.slice(-90);
        const numbers = valid.flatMap(p => names.map(name => p.metrics[name])).filter(Number.isFinite);
        const cap = maximum || Math.max(1, ...numbers);
        if (!numbers.length) return '<p class="muted">Sem medicao disponivel</p>';
        const paths = names.map((name, index) => {
            let path = "", drawing = false;
            valid.forEach((point, i) => {
                const value = point.metrics[name];
                const previous = valid[i - 1];
                if (!Number.isFinite(value)) { drawing = false; return; }
                if (previous && Date.parse(point.collected_at) - Date.parse(previous.collected_at) > 25000) drawing = false;
                const elapsed = Date.parse(point.collected_at) - Date.parse(valid[0].collected_at);
                const span = Date.parse(valid[valid.length - 1].collected_at) - Date.parse(valid[0].collected_at);
                const x = span > 0 ? elapsed * 600 / span : 300;
                const y = 120 - Math.max(0, Math.min(1, value / cap)) * 110;
                path += (drawing ? " L" : " M") + x.toFixed(2) + " " + y.toFixed(2);
                drawing = true;
            });
            return '<path d="' + path + '" fill="none" stroke="' + ["#39b7cf", "#8fd174"][index] + '" stroke-width="2"/>';
        }).join("");
        const last = valid[valid.length - 1];
        const legend = names.map((name, index) => '<span style="color:' + ["#39b7cf", "#8fd174"][index] + '">' +
            escape(labels && labels[index] || name) + ': ' + (Number.isFinite(last.metrics[name]) ? last.metrics[name].toFixed(2) : 'Desconhecido') + '</span>').join(' | ');
        return '<svg viewBox="0 0 600 130" role="img" aria-label="Serie de medicoes recebidas">' + paths + '</svg><small>' + legend +
            '<br>Escala: 0 a ' + cap.toFixed(2) + ' · ' + escape(new Date(valid[0].collected_at).toLocaleTimeString()) + ' a ' +
            escape(new Date(last.collected_at).toLocaleTimeString()) + '</small>';
    }
    if (typeof module !== "undefined" && module.exports) module.exports = {chart, escape};
    if (typeof document === "undefined") return;
    const root = document.querySelector('[data-endpoint-detail]');
    if (!root || root.dataset.liveAllowed !== 'yes') return;
    const id = root.dataset.endpointId;
    if (!id) return;
    const panel = document.createElement('section');
    panel.className = 'endpoint-live panel';
    panel.innerHTML = '<header><h2>Monitoramento ao vivo</h2><button type="button" class="copy-btn" data-live-toggle><i data-lucide="activity"></i>Iniciar</button></header><p data-live-state>Sessao inativa</p><div data-live-body hidden><label>Interface<select data-live-interface></select></label><label>Disco apresentado pelo Windows<select data-live-disk></select></label><div class="endpoint-live-grid"><div><h3>CPU (%)</h3><div data-live-cpu></div></div><div><h3>RAM utilizada / disponivel (%)</h3><div data-live-memory></div></div><div><h3>RX / TX (bytes/s)</h3><div data-live-network></div></div><div><h3>Latencia de leitura / escrita (ms)</h3><div data-live-storage></div></div></div><dl data-live-facts class="endpoint-fact-list"></dl></div>';
    root.appendChild(panel);
    if (scope.lucide) scope.lucide.createIcons();
    let session = null, points = [], timer = null, polling = false, lastRenewal = 0;
    const button = panel.querySelector('[data-live-toggle]');
    const status = panel.querySelector('[data-live-state]');
    const url = '/api/agent/telemetry/live/' + encodeURIComponent(id) + '/';
    function csrf() {
        const match = document.cookie.match(/(?:^|;\s*)csrftoken=([^;]+)/);
        return match ? decodeURIComponent(match[1]) : document.querySelector('[name=csrfmiddlewaretoken]')?.value || '';
    }
    async function request(action, keepalive) {
        const response = await fetch(url, {method:'POST', credentials:'same-origin', keepalive:!!keepalive, signal:AbortSignal.timeout(10000),
            headers:{'Content-Type':'application/json', 'X-CSRFToken':csrf()},
            body:JSON.stringify({action, session_id:session && session.session_id})});
        const result = await response.json();
        if (!response.ok) throw new Error(result.error || 'live_unavailable');
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
        panel.querySelector('[data-live-body]').hidden = !session;
        if (!session) return;
        const rx = selector('interface', 'network.', 'received_bytes_per_sec');
        const read = selector('disk', 'disk.', 'read_latency_ms');
        panel.querySelector('[data-live-cpu]').innerHTML = chart(points, ['cpu.usage_percent'], 100, ['CPU (%)']);
        panel.querySelector('[data-live-memory]').innerHTML = chart(points, ['memory.used_percent', 'memory.available_percent'], 100, ['Utilizada (%)', 'Disponivel (%)']);
        panel.querySelector('[data-live-network]').innerHTML = chart(points, [rx, rx.replace('received_bytes_per_sec','sent_bytes_per_sec')], null, ['RX (bytes/s)', 'TX (bytes/s)']);
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
    function expired(message) { session = null; clearTimeout(timer); button.textContent = 'Iniciar'; status.textContent = message || 'Sessao encerrada'; draw(); }
    async function poll() {
        if (!session || polling) return;
        if (Date.now() >= Date.parse(session.expires_at)) { expired('Sessao expirada'); return; }
        polling = true;
        try {
            if (!document.hidden && Date.now() - lastRenewal >= 15000) { session = await request('renew'); lastRenewal = Date.now(); }
            const response = await fetch(url, {credentials:'same-origin', cache:'no-store', signal:AbortSignal.timeout(10000)});
            const data = await response.json();
            if (!response.ok) throw new Error(data.error || 'live_unavailable');
            if (data.state !== 'active') { expired('Sessao expirada'); return; }
            points = data.points || [];
            status.textContent = (data.stale ? 'Sem dados recentes / offline' : 'Recebendo amostras') + (data.last_sample_at ? ' · Ultima amostra: ' + new Date(data.last_sample_at).toLocaleString() : '') + ' · Sem prova de saturacao da internet';
            draw();
        } catch (error) { status.textContent = 'Dados indisponiveis: ' + error.message; }
        finally { polling = false; if (session) timer = setTimeout(poll, 5000); }
    }
    button.addEventListener('click', async () => {
        button.disabled = true;
        try {
            if (session) { await request('stop'); expired(); }
            else { session = await request('start'); button.textContent = 'Encerrar'; lastRenewal = Date.now(); await poll(); }
        } catch (error) { status.textContent = 'Nao foi possivel iniciar: ' + error.message; }
        finally { button.disabled = false; }
    });
    panel.querySelectorAll('select').forEach(select => select.addEventListener('change', draw));
    scope.addEventListener('pagehide', () => { if (session) { request('stop', true).catch(() => {}); clearTimeout(timer); } });
})(typeof window !== 'undefined' ? window : globalThis);
