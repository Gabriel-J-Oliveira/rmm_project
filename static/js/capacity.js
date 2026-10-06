(function () {
  'use strict';
  const root = document.querySelector('[data-capacity-root]');
  if (!root) return;
  const $ = selector => root.querySelector(selector);
  const demo = root.dataset.demo === '1';
  const state = { rows: [], counts: {}, filter: 'all', query: '', filters: [], page: 1, pageSize: 25, now: Date.now(), sort: 'priority', period: '24h', detail: null, tab: 'summary', range: '24h', series: null, seriesRequest: 0, lastFocus: null, request: 0, detailRequest: 0 };
  const labels = { NOT_EVALUATED: 'Sem evidência', NO_PRESSURE_OBSERVED: 'Sem pressão', OBSERVE: 'Observar', SUSTAINED_PRESSURE: 'Pressão sustentada', SUFFICIENT: 'Suficiente', PARTIAL: 'Parcial', INSUFFICIENT: 'Insuficiente', AVAILABLE: 'Disponível', MISSING: 'Ausente' };
  const esc = value => String(value == null ? '' : value).replace(/[&<>"']/g, char => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[char]));
  const label = value => labels[value] || value || 'Não disponível';
  const number = value => Number.isFinite(value) ? new Intl.NumberFormat('pt-BR', { maximumFractionDigits: 1 }).format(value) : '—';
  const percent = value => Number.isFinite(value) ? `${number(value)}%` : '—';
  const bytes = value => Number.isFinite(value) ? `${number(value / 1073741824)} GB` : '—';
  const stamp = value => value ? new Intl.DateTimeFormat('pt-BR', { dateStyle: 'short', timeStyle: 'short' }).format(new Date(value)) : '—';
  const tone = value => value === 'SUSTAINED_PRESSURE' ? 'sustained' : value === 'OBSERVE' ? 'observe' : value === 'NOT_EVALUATED' ? 'insufficient' : '';
  const chip = value => `<span class="capacity-chip ${tone(value)}">${esc(label(value))}</span>`;
  const stateBadge = value => `<span class="capacity-state ${value === 'online' ? 'online' : ''}">${esc(value || 'unknown')}</span>`;
  const empty = text => `<p class="capacity-empty">${esc(text)}</p>`;
  const fact = (name, value) => `<div class="capacity-detail-fact"><small>${esc(name)}</small><strong>${esc(value == null ? '—' : value)}</strong></div>`;
  const statusText = value => ({ PRESSURE: 'Pressão', ELEVATED: 'Elevado', SPIKY: 'Picos', NO_PRESSURE: 'Sem pressão', NOT_EVALUATED: 'Não avaliado' })[value] || label(value);
  const reasonText = key => ({ primary_not_evaluated: 'Janela principal sem evidência suficiente', primary_no_pressure_observed: 'Sem pressão observada na janela principal', historical_signal_present: 'Há sinal no contexto histórico', primary_spiky: 'Picos na janela principal', primary_elevated: 'Uso elevado na janela principal', primary_pressure: 'Pressão na janela principal', context_corroborates_pressure: 'Contexto histórico corrobora a pressão', context_not_corroborated: 'Contexto não corrobora a pressão', context_window_unavailable: 'Contexto indisponível', context_not_evaluated: 'Contexto ainda não avaliado' })[key] || key.replaceAll('_', ' ');
  const icons = () => { if (window.lucide && window.lucide.createIcons) window.lucide.createIcons(); };
  function attentionReasons(row) {
    const reasons = [];
    if (row.alerts_critical > 0) reasons.push(`${row.alerts_critical} ALERTA(S) CRÍTICO(S)`);
    if (row.classifications.offline) reasons.push('ENDPOINT OFFLINE');
    if (row.memory_capacity === 'SUSTAINED_PRESSURE') reasons.push('PRESSÃO DE MEMÓRIA');
    if (row.cpu_capacity === 'SUSTAINED_PRESSURE') reasons.push('PRESSÃO DE CPU');
    if (row.classifications.observe) reasons.push('OBSERVAR CPU / RAM');
    if (row.classifications.insufficient) reasons.push('SEM EVIDÊNCIA SUFICIENTE');
    if (row.inventory_stale) reasons.push('INVENTÁRIO DESATUALIZADO');
    if (row.security_alert) reasons.push('ALERTA DE SEGURANÇA');
    if (row.agent_update_alert) reasons.push('ALERTA DO AGENTE');
    return reasons;
  }
  const priority = row => row.alerts_critical ? 7 : row.classifications.offline ? 6 : row.classifications.sustained ? 5 : row.classifications.observe ? 4 : row.classifications.insufficient ? 3 : row.inventory_stale ? 2 : 1;
  const issue = row => attentionReasons(row)[0] || 'ACOMPANHAR';
  const age = (value, unit) => { const at = typeof value === 'string' ? Date.parse(value) : NaN; return Number.isFinite(at) ? Math.max(0, state.now - at) / unit : null; };
  const fields = {
    status: ['Estado', 'enum', r => r.status], last_ip: ['IP', 'text', r => r.last_ip],
    user: ['Usuário', 'text', r => r.last_logged_user], os: ['SO', 'text', r => [r.os_name, r.os_version, r.os_build].filter(v => typeof v === 'string').join(' ')],
    agent: ['Versão do agente', 'text', r => r.agent_version], ram: ['RAM instalada (GB)', 'number', r => Number.isFinite(r.memory_total_bytes) ? r.memory_total_bytes / 1073741824 : null],
    cores: ['CPU cores físicos', 'number', r => r.cpu_cores], system_disk: ['Disco C: utilizado (%)', 'number', r => r.system_disk_used_percent],
    disk: ['Maior uso de disco (%)', 'number', r => r.max_disk_used_percent], free_disk: ['Menor espaço livre (GB)', 'number', r => Number.isFinite(r.min_disk_free_bytes) ? r.min_disk_free_bytes / 1073741824 : null],
    cpu: ['CPU p95 (%)', 'number', r => r.cpu_p95], memory: ['RAM p95 (%)', 'number', r => r.memory_p95],
    coverage: ['Cobertura (%)', 'number', r => r.coverage], samples: ['Quantidade de amostras', 'number', r => r.received_samples],
    critical: ['Alertas críticos', 'number', r => r.alerts_critical], alerts: ['Alertas totais', 'number', r => r.alerts_total],
    last_seen: ['Horas desde last seen', 'number', r => age(r.last_seen, 3600000)], first_seen: ['Dias desde first seen', 'number', r => age(r.first_seen, 86400000)]
  };
  const operators = { number: ['>', '>=', '<', '<=', '=', 'entre'], text: ['contém', 'não contém', 'igual'], enum: ['igual'] };
  function filterMatches(row, f) {
    const [, type, read] = fields[f.field], value = read(row);
    if (type !== 'number') {
      if (typeof value !== 'string' || !value) return false;
      const text = value.toLocaleLowerCase(), wanted = f.value.toLocaleLowerCase();
      return f.operator === 'igual' ? text === wanted : f.operator === 'contém' ? text.includes(wanted) : !text.includes(wanted);
    }
    if (!Number.isFinite(value)) return false;
    return ({ '>': () => value > f.value, '>=': () => value >= f.value, '<': () => value < f.value, '<=': () => value <= f.value, '=': () => value === f.value, entre: () => value >= f.value && value <= f.end })[f.operator]();
  }
  const matches = (row, filter) => filter === 'all' || filter === 'monitored' || (filter === 'critical' ? row.alerts_critical > 0 : filter === 'no_pressure' ? !row.classifications.insufficient && !row.classifications.sustained && !row.classifications.observe : filter === 'cpu_problem' ? ['OBSERVE', 'SUSTAINED_PRESSURE'].includes(row.cpu_capacity) : filter === 'ram_problem' ? ['OBSERVE', 'SUSTAINED_PRESSURE'].includes(row.memory_capacity) : Boolean(row.classifications[filter] ?? row[filter]));

  function analysisRows() {
    let rows = state.rows.filter(row => matches(row, state.filter) && state.filters.every(f => filterMatches(row, f)));
    if (state.query) rows = rows.filter(row => ['hostname', 'fqdn', 'last_ip', 'last_logged_user', 'os_name', 'os_version', 'os_build', 'windows_build', 'cpu_name', 'manufacturer', 'model', 'agent_version'].some(key => typeof row[key] === 'string' && row[key].toLocaleLowerCase().includes(state.query)));
    const sorters = {
      priority: (a, b) => priority(b) - priority(a), hostname: (a, b) => a.hostname.localeCompare(b.hostname),
      cpu: (a, b) => (b.cpu_p95 ?? -1) - (a.cpu_p95 ?? -1), memory: (a, b) => (b.memory_p95 ?? -1) - (a.memory_p95 ?? -1),
      coverage: (a, b) => (a.coverage ?? -1) - (b.coverage ?? -1), last_seen: (a, b) => (b.last_seen || '').localeCompare(a.last_seen || '')
    };
    return [...rows].sort(sorters[state.sort] || sorters.priority);
  }
  function renderKpis() {
    const items = [
      ['monitored', 'Monitorados', '--cap-blue'], ['attention', 'Requerem atenção', '--cap-amber'],
      ['sustained', 'Pressão sustentada', '--cap-red'], ['observe', 'Observar', '--cap-amber'],
      ['insufficient', 'Sem evidência', '--cap-blue'], ['offline', 'Offline', '--cap-red'], ['critical', 'Alerta crítico', '--cap-red']
    ];
    const rows = analysisRows();
    $('[data-kpis]').innerHTML = items.map(([key, title, color]) => `<button type="button" class="capacity-kpi ${state.filter === key ? 'is-active' : ''}" style="--cap-accent:var(${color})" data-filter="${key}" aria-pressed="${state.filter === key}"><span>${esc(title)}</span><strong>${rows.filter(row => matches(row, key)).length}</strong></button>`).join('');
    $('[data-clear-filter]').hidden = state.filter === 'all';
  }
  function card(row) {
    const name = row.endpoint_url ? `<a href="${esc(row.endpoint_url)}">${esc(row.hostname)}</a>` : esc(row.hostname);
    const parts = `<div class="capacity-card-top"><strong>${name}</strong>${stateBadge(row.status)}</div><p class="capacity-card-cause">${esc(issue(row))}</p><span class="capacity-card-meta">${esc(attentionReasons(row).slice(1).join(' · '))}</span><div class="capacity-card-facts"><div><small>CPU p95</small><b>${percent(row.cpu_p95)}</b></div><div><small>RAM p95</small><b>${percent(row.memory_p95)}</b></div></div><span class="capacity-card-meta">Cobertura ${percent(row.coverage)} · ${esc(label(row.evidence))}</span>`;
    return `<article class="capacity-spotlight">${parts}<div class="capacity-card-actions"><button type="button" data-open="${esc(row.id)}">Abrir detalhes</button>${row.endpoint_url ? `<a href="${esc(row.endpoint_url)}">Abrir endpoint</a>` : ''}<a href="${esc(row.alerts_url)}">Ver alertas</a></div></article>`;
  }
  function renderAttention() {
    const rows = analysisRows().filter(row => attentionReasons(row).length).sort((a, b) => priority(b) - priority(a));
    $('[data-attention]').innerHTML = rows.length ? rows.map(card).join('') : empty('Nenhum endpoint requer atenção neste período.');
  }
  function renderRecent() {
    const rows = analysisRows().filter(row => { const days = age(row.first_seen, 86400000); return days !== null && days <= 7; }).sort((a, b) => Date.parse(b.first_seen) - Date.parse(a.first_seen));
    $('[data-recent-section]').hidden = !rows.length;
    // Hourly flush can lag sampling: two hours is a display tolerance, not a new alert SLA.
    $('[data-recent]').innerHTML = rows.map(row => {
      const hours = age(row.first_seen, 3600000), telemetryAge = age(row.telemetry_last_at, 3600000);
      const telemetry = telemetryAge === null ? 'Sem amostras' : telemetryAge <= 2 ? 'Recebendo' : 'Desatualizada';
      return `<article class="capacity-recent-card"><div class="capacity-card-top"><strong>${esc(row.hostname)}</strong>${stateBadge(row.status)}</div><span class="capacity-recent-badge">${hours <= 24 ? 'NOVO' : 'RECENTE'} · há ${number(hours < 24 ? hours : hours / 24)} ${hours < 24 ? 'h' : 'd'}</span><p>${esc(row.last_ip || '—')}<br>${esc(row.last_logged_user || '—')}</p><dl><dt>Agente</dt><dd>${esc(row.agent_version || '—')}</dd><dt>Inventário</dt><dd>${row.inventory_at ? 'Recebido' : 'Aguardando'}</dd><dt>Telemetria</dt><dd>${telemetry}</dd><dt>Amostras (${esc(state.period)})</dt><dd>${number(row.received_samples)}</dd></dl><div class="capacity-card-actions"><button data-open="${esc(row.id)}">Detalhes</button>${row.endpoint_url ? `<a href="${esc(row.endpoint_url)}">Endpoint</a>` : ''}</div></article>`;
    }).join('');
  }
  function renderDistribution() {
    const kinds = [['no_pressure', 'Sem pressão'], ['observe', 'Observar'], ['sustained', 'Pressão sustentada'], ['insufficient', 'Sem evidência']];
    const values = Object.fromEntries(kinds.map(([key]) => [key, 0]));
    const rows = analysisRows();
    rows.forEach(row => { const c = row.classifications; values[c.insufficient ? 'insufficient' : c.sustained ? 'sustained' : c.observe ? 'observe' : 'no_pressure']++; });
    $('[data-distribution]').innerHTML = kinds.map(([key, text]) => `<button type="button" class="capacity-dist-row" data-filter="${key}" data-kind="${key}"><span>${esc(text)}</span><span class="capacity-dist-track" role="img" aria-label="${esc(text)}: ${values[key]}"><span class="capacity-dist-fill" style="width:${rows.length ? values[key] / rows.length * 100 : 0}%"></span></span><strong>${values[key]}</strong></button>`).join('');
  }
  function renderProblems() {
    const categories = [
      ['cpu_problem', 'Capacidade CPU'], ['ram_problem', 'Capacidade RAM'], ['offline', 'Offline'],
      ['critical', 'Alertas críticos'], ['insufficient', 'Evidência insuficiente'],
      ['inventory_stale', 'Inventário desatualizado'], ['security_alert', 'Segurança / antivírus'],
      ['agent_update_alert', 'Agent / update']
    ];
    const rows = analysisRows(), max = Math.max(1, rows.length);
    $('[data-problems]').innerHTML = categories.map(([key, title]) => {
      const count = rows.filter(row => matches(row, key)).length;
      return `<button type="button" class="capacity-dist-row" data-filter="${key}"><span>${esc(title)}</span><span class="capacity-dist-track"><span class="capacity-dist-fill" style="width:${count / max * 100}%"></span></span><strong>${count}</strong></button>`;
    }).join('');
  }
  function bucket(title, count, total) {
    return `<div class="capacity-profile-row"><span>${esc(title)}</span><span class="capacity-dist-track"><span class="capacity-dist-fill" style="width:${total ? count / total * 100 : 0}%"></span></span><strong>${count}</strong></div>`;
  }
  function renderProfiles() {
    const rows = analysisRows();
    const ram = [['≤8 GB', 0], ['>8–16 GB', 0], ['>16–32 GB', 0], ['>32 GB', 0]];
    const cpu = [['≤4 cores', 0], ['5–8 cores', 0], ['9–16 cores', 0], ['>16 cores', 0]];
    const os = new Map(), alerts = new Map();
    rows.forEach(row => {
      if (Number.isFinite(row.memory_total_bytes)) ram[row.memory_total_bytes <= 8 * 1073741824 ? 0 : row.memory_total_bytes <= 16 * 1073741824 ? 1 : row.memory_total_bytes <= 32 * 1073741824 ? 2 : 3][1]++;
      if (Number.isFinite(row.cpu_cores)) cpu[row.cpu_cores <= 4 ? 0 : row.cpu_cores <= 8 ? 1 : row.cpu_cores <= 16 ? 2 : 3][1]++;
      if (row.os_name) os.set(row.os_name, (os.get(row.os_name) || 0) + 1);
      (row.alert_types || []).filter(item => item.severity === 'critical').forEach(item => alerts.set(item.alert_type, (alerts.get(item.alert_type) || 0) + item.count));
    });
    const render = (selector, entries, total, missing) => { $(selector).innerHTML = entries.length ? entries.map(([name, count]) => bucket(name, count, total)).join('') : empty('Dados ainda não disponíveis.'); if (missing) $(selector).innerHTML += `<p class="capacity-chart-note">${missing} endpoint(s) sem inventário.</p>`; };
    render('[data-ram-distribution]', ram, rows.length, rows.length - ram.reduce((n, item) => n + item[1], 0));
    render('[data-cpu-distribution]', cpu, rows.length, rows.length - cpu.reduce((n, item) => n + item[1], 0));
    render('[data-os-distribution]', [...os].sort((a, b) => b[1] - a[1]).slice(0, 6), rows.length, rows.length - [...os.values()].reduce((a, b) => a + b, 0));
    render('[data-alert-types]', [...alerts].sort((a, b) => b[1] - a[1]).slice(0, 6), Math.max(1, [...alerts.values()].reduce((a, b) => a + b, 0)), 0);
  }
  function renderScatter() {
    const rows = analysisRows().filter(row => Number.isFinite(row.cpu_p95) && Number.isFinite(row.memory_p95));
    $('[data-scatter]').innerHTML = rows.map(row => {
      const kind = row.classifications.sustained ? 'sustained' : row.classifications.observe ? 'observe' : '';
      return `<button type="button" class="capacity-dot ${kind}" data-open="${esc(row.id)}" style="left:${Math.max(2, Math.min(98, row.cpu_p95))}%;bottom:${Math.max(2, Math.min(98, row.memory_p95))}%" aria-label="${esc(row.hostname)}: CPU p95 ${percent(row.cpu_p95)}, RAM p95 ${percent(row.memory_p95)}" title="${esc(row.hostname)} · CPU ${percent(row.cpu_p95)} · RAM ${percent(row.memory_p95)} · ${esc(row.cpu_name || 'Hardware não informado')} · ${bytes(row.memory_total_bytes)} · ${esc(label(row.cpu_capacity))}/${esc(label(row.memory_capacity))} · evidência ${esc(label(row.evidence))}"></button>`;
    }).join('');
    $('[data-scatter-note]').textContent = `${rows.length} endpoint(s) do conjunto filtrado possuem CPU e RAM p95.`;
  }
  function renderWorkloads() {
    const entries = analysisRows().flatMap(row => row.processes.map(item => ({ ...item, hostname: row.hostname })));
    entries.sort((a, b) => (b.cpu_percent ?? 0) - (a.cpu_percent ?? 0));
    $('[data-workloads]').innerHTML = entries.length ? entries.slice(0, 6).map(item => `<div class="capacity-workload" title="Observado na última amostra de ${esc(item.hostname)}. Não implica impacto causal."><strong>${esc(item.name)}</strong><small>${esc(item.hostname)} · ${esc(item.category)} · CPU ${percent(item.cpu_percent)} · RAM ${bytes(item.working_set_bytes)}</small></div>`).join('') : empty('Dados ainda não disponíveis para esta análise.');
  }
  function renderTable(rows) {
    const pages = Math.max(1, Math.ceil(rows.length / state.pageSize));
    state.page = Math.max(1, Math.min(pages, state.page));
    const offset = (state.page - 1) * state.pageSize;
    $('[data-table-body]').innerHTML = rows.length ? rows.slice(offset, offset + state.pageSize).map(row => `<tr data-open="${esc(row.id)}" tabindex="0" aria-label="Abrir detalhes de ${esc(row.hostname)}"><td>${row.endpoint_url ? `<a href="${esc(row.endpoint_url)}" data-endpoint-link>${esc(row.hostname)}</a>` : esc(row.hostname)}</td><td>${stateBadge(row.status)}</td><td>${esc(row.last_ip || '—')}</td><td>${esc(row.last_logged_user || '—')}</td><td>${esc(row.os_name || '—')}<span class="sub">${esc(row.os_build || '')}</span></td><td>${esc(row.cpu_name || '—')}<span class="sub">${number(row.cpu_cores)} cores</span></td><td>${bytes(row.memory_total_bytes)}</td><td>${Number.isFinite(row.system_disk_used_percent) ? `C: ${percent(row.system_disk_used_percent)}` : Number.isFinite(row.max_disk_used_percent) ? `Máx. ${percent(row.max_disk_used_percent)}` : '—'}</td><td>${percent(row.cpu_p95)}</td><td>${percent(row.memory_p95)}</td><td>${esc(label(row.evidence))}<span class="sub">${percent(row.coverage)}</span></td><td>${number(row.alerts_critical)}</td><td>${stamp(row.last_seen)}</td></tr>`).join('') : '<tr><td colspan="13">Nenhum endpoint corresponde ao filtro.</td></tr>';
    const visible = new Set([1, pages, state.page - 1, state.page, state.page + 1]);
    let previous = 0;
    const buttons = [...visible].filter(n => n > 0 && n <= pages).sort((a, b) => a - b).map(n => {
      const gap = previous && n - previous > 1 ? '<span>…</span>' : ''; previous = n;
      return `${gap}<button data-page="${n}" ${n === state.page ? 'aria-current="page"' : ''}>${n}</button>`;
    }).join('');
    $('[data-pagination]').innerHTML = `<span>Mostrando ${rows.length ? offset + 1 : 0}–${Math.min(offset + state.pageSize, rows.length)} de ${rows.length}</span><button data-page="${state.page - 1}" aria-label="Página anterior" ${state.page === 1 ? 'disabled' : ''}>‹</button>${buttons}<button data-page="${state.page + 1}" aria-label="Próxima página" ${state.page === pages ? 'disabled' : ''}>›</button>`;
  }
  function renderList() {
    const rows = analysisRows();
    $('[data-filter-count]').textContent = `${rows.length} de ${state.rows.length} endpoints`;
    $('[data-filter-chips]').innerHTML = state.filters.map((f, i) => `<button class="capacity-action" data-remove-filter="${i}" aria-label="Remover filtro ${esc(fields[f.field][0])}">${esc(fields[f.field][0])} ${esc(f.operator)} ${esc(f.value)}${f.operator === 'entre' ? `–${esc(f.end)}` : ''} <i data-lucide="x"></i></button>`).join('');
    renderTable(rows); renderKpis();
  }
  function renderAll() { renderAttention(); renderRecent(); renderDistribution(); renderProblems(); renderProfiles(); renderScatter(); renderWorkloads(); renderList(); icons(); requestAnimationFrame(updateCarousels); }
  function updateCarousels() {
    ['attention', 'recent'].forEach(key => {
      const box = $(`[data-${key}]`), controls = $(`[data-carousel-controls="${key}"]`);
      controls.hidden = box.scrollWidth <= box.clientWidth + 1;
      controls.querySelector('[data-direction="-1"]').disabled = box.scrollLeft <= 1;
      controls.querySelector('[data-direction="1"]').disabled = box.scrollLeft + box.clientWidth >= box.scrollWidth - 1;
    });
  }
  async function load() {
    const seq = ++state.request;
    $('[data-error]').hidden = true; $('[data-updated]').textContent = 'Atualizando capacidade…';
    const url = new URL(root.dataset.overviewUrl, location.origin);
    url.searchParams.set('period', state.period);
    if (demo) url.searchParams.set('demo', '1');
    try {
      const response = await fetch(url, { credentials: 'same-origin' });
      if (!response.ok) throw new Error();
      const payload = await response.json();
      if (seq !== state.request) return;
      state.rows = payload.endpoints; state.counts = payload.counts;
      state.now = Date.parse(payload.generated_at); state.page = 1;
      $('[data-updated]').textContent = `Atualizado ${stamp(payload.generated_at)} · ${state.period} + contexto 7d`;
      renderAll();
    } catch (error) {
      if (seq === state.request) { $('[data-error]').hidden = false; $('[data-updated]').textContent = 'Dados indisponíveis'; }
    }
  }
  const tabs = [['summary', 'Resumo'], ['performance', 'Desempenho'], ['impact', 'Impacto'], ['hardware', 'SO & Hardware'], ['alerts', 'Alertas'], ['applications', 'Aplicações']];
  function seriesChart(series) {
    if (!series || series.length < 2) return empty('Série temporal ainda não disponível.');
    const path = key => {
      let drawing = false;
      return series.map((item, index) => {
        if (!Number.isFinite(item[key])) { drawing = false; return ''; }
        const command = drawing ? 'L' : 'M'; drawing = true;
        return `${command}${(index / (series.length - 1) * 100).toFixed(2)},${(100 - item[key]).toFixed(2)}`;
      }).join(' ');
    };
    const bins = key => [0, 0, 0, 0].map((_, index) => series.filter(item => Number.isFinite(item[key]) && Math.min(3, Math.floor(item[key] / 25)) === index).length);
    const distribution = (title, key) => `<div class="capacity-mini-hist"><strong>${title}</strong>${bins(key).map((count, index) => `<div><small>${index * 25}–${(index + 1) * 25}%</small><span class="capacity-dist-track"><span class="capacity-dist-fill" style="width:${series.length ? count / series.length * 100 : 0}%"></span></span><b>${count}</b></div>`).join('')}</div>`;
    return `<div class="capacity-series-wrap"><span>100%</span><svg class="capacity-series" viewBox="0 0 100 100" preserveAspectRatio="none" role="img" tabindex="0" aria-label="Série de CPU e memória no período; use as setas para explorar amostras"><path d="${path('cpu')}" fill="none" stroke="#34d3b3" stroke-width="1.6" vector-effect="non-scaling-stroke"/><path d="${path('memory')}" fill="none" stroke="#f5bd62" stroke-width="1.6" vector-effect="non-scaling-stroke"/></svg><span>0%</span></div><div class="capacity-series-legend"><span>● CPU</span><span>● RAM</span><small>${stamp(series[0].at)} – ${stamp(series[series.length - 1].at)}</small></div><div class="capacity-series-hover" data-series-hover aria-live="polite">Toque ou mova o cursor no gráfico para ver uma amostra.</div><div class="capacity-histograms">${distribution('CPU', 'cpu')}${distribution('Memória', 'memory')}</div>`;
  }
  function assessmentStatus(cap) {
    const statuses = [cap.capacity.cpu.status, cap.capacity.memory.status];
    return statuses.includes('SUSTAINED_PRESSURE') ? 'Pressão sustentada observada' : statuses.includes('OBSERVE') ? 'Requer observação' : statuses.includes('NOT_EVALUATED') ? 'Evidência insuficiente' : 'Sem pressão observada';
  }
  function explanation(cap, context) {
    const statuses = [cap.capacity.cpu.status, cap.capacity.memory.status];
    let text = statuses.includes('SUSTAINED_PRESSURE') ? 'A janela principal mostrou pressão corroborada pelo contexto histórico.' : statuses.includes('OBSERVE') ? 'Há sinal de uso que merece acompanhamento; a evidência não sustenta uma conclusão de pressão contínua.' : statuses.includes('NOT_EVALUATED') ? 'A janela principal ainda não permite avaliar todos os recursos.' : 'Não foram observados sinais de pressão de CPU ou memória na janela principal.';
    if (context && Object.values(context.diagnostics.diagnostics).some(item => item.status === 'NOT_EVALUATED')) text += ' O contexto de 7 dias ainda não permite conclusão de longo prazo.';
    return text;
  }
  function why(resource, data) {
    const cap = data.capacity.capacity[resource], diag = data.primary.diagnostics.diagnostics[resource];
    const stats = resource === 'cpu' ? data.primary.stats.cpu : data.primary.stats.memory_used;
    return `<details class="capacity-why"><summary aria-label="Por que ${esc(resource)} está ${esc(label(cap.status))}?" title="Explicação do diagnóstico"><i data-lucide="circle-help"></i></summary><div class="capacity-why-body"><strong>${esc(label(cap.status))}</strong><span>Média ${percent(stats.avg)} · P95 ${percent(stats.p95)} · P99 ${percent(stats.p99)}</span><span>Evidência ${data.primary.quality.received_samples} / ${data.primary.quality.expected_samples} amostras</span><span>Principal: ${esc(statusText(cap.primary_diagnostic))} · Contexto: ${esc(statusText(cap.context_diagnostic))}</span><span>${esc(cap.reasons.map(reasonText).join('; '))}</span><small>Diagnóstico M5: ${esc(diag.reasons.map(reasonText).join('; '))}</small></div></details>`;
  }
  function renderInsight(data) {
    const endpoint = data.endpoint;
    if (!data.primary) { $('[data-drawer-insight]').innerHTML = `<strong>Dados demonstrativos</strong><p>Sem telemetria real neste detalhe.</p>`; $('[data-quick-facts]').innerHTML = ''; return; }
    const cap = data.capacity, primary = data.primary, context = data.context, hardware = data.hardware;
    $('[data-drawer-insight]').innerHTML = `<small>SITUAÇÃO DE CAPACIDADE</small><h3>${esc(assessmentStatus(cap))}</h3><div class="capacity-insight-status"><span>CPU ${chip(cap.capacity.cpu.status)}</span><span>Memória ${chip(cap.capacity.memory.status)}</span><span>Evidência ${primary.quality.received_samples} / ${primary.quality.expected_samples}</span><span>Contexto 7d ${percent(context.quality.coverage_percent)}</span></div><p>${esc(explanation(cap, context))}</p>`;
    const quick = [
      ['CPU p95', percent(primary.stats.cpu.p95), label(cap.capacity.cpu.status), 'performance'],
      ['RAM p95', percent(primary.stats.memory_used.p95), label(cap.capacity.memory.status), 'performance'],
      ['Dados', `${primary.quality.received_samples}/${primary.quality.expected_samples}`, label(primary.diagnostics.evidence.overall.status), 'summary'],
      ['Histórico 7d', percent(context.quality.coverage_percent), statusText(context.diagnostics.diagnostics.cpu.status), 'performance'],
      ['RAM física', bytes(hardware.memory.total_bytes), label(hardware.memory.status), 'hardware'],
      ['Alertas críticos', String(data.alert_counts.critical), 'Ativos', 'alerts']
    ];
    $('[data-quick-facts]').innerHTML = quick.map(([title, value, sub, tab]) => `<button type="button" data-quick-tab="${tab}"><small>${esc(title)}</small><strong>${esc(value)}</strong><span>${esc(sub)}</span></button>`).join('');
  }
  function renderProcessRanking(processes, category) {
    const grouped = new Map();
    processes.filter(item => item.category === category).forEach(item => {
      const value = category === 'cpu' ? item.cpu_percent : item.working_set_bytes;
      const existing = grouped.get(item.name);
      if (!existing || (Number.isFinite(value) && value > (category === 'cpu' ? existing.cpu_percent : existing.working_set_bytes))) grouped.set(item.name, item);
    });
    const metric = item => category === 'cpu' ? item.cpu_percent : item.working_set_bytes;
    const ranked = [...grouped.values()].filter(item => Number.isFinite(metric(item))).sort((a, b) => metric(b) - metric(a)).slice(0, 8);
    const max = Math.max(1, ...ranked.map(metric));
    return ranked.length ? ranked.map((item, index) => `<div class="capacity-rank" title="${esc(item.name)} · CPU ${percent(item.cpu_percent)} · RAM ${bytes(item.working_set_bytes)} · amostra recente"><b>${index + 1}</b><span>${esc(item.name)}</span><span class="capacity-dist-track"><span class="capacity-dist-fill" style="width:${metric(item) / max * 100}%"></span></span><strong>${category === 'cpu' ? percent(item.cpu_percent) : bytes(item.working_set_bytes)}</strong></div>`).join('') : empty('Dados ainda não disponíveis para esta análise.');
  }
  function renderDrawer() {
    const data = state.detail;
    if (!data) return;
    const endpoint = data.endpoint;
    $('[data-drawer-title]').textContent = endpoint.hostname;
    $('[data-drawer-subtitle]').textContent = `${endpoint.status} · ${endpoint.os_name || 'SO não informado'} · ${endpoint.agent_version || 'Versão não informada'}`;
    $('[data-drawer-endpoint]').hidden = !endpoint.endpoint_url;
    if (endpoint.endpoint_url) $('[data-drawer-endpoint]').href = endpoint.endpoint_url;
    $('[data-drawer-alerts]').href = endpoint.alerts_url || '/alerts/';
    $('[data-tabs]').innerHTML = tabs.map(([key, text]) => `<button type="button" role="tab" data-tab="${key}" aria-selected="${state.tab === key}">${text}</button>`).join('');
    renderInsight(data);
    const box = $('[data-drawer-content]');
    const primary = data.primary, context = data.context, cap = data.capacity, hardware = data.hardware;
    if (!primary) {
      box.innerHTML = `<h3>Dados demonstrativos</h3><div class="capacity-detail-grid">${fact('CPU', label(endpoint.cpu_capacity))}${fact('Memória', label(endpoint.memory_capacity))}${fact('Inventário CPU', endpoint.cpu_inventory_status)}${fact('Inventário RAM', endpoint.memory_inventory_status)}</div>${empty('Este detalhe é sintético e não usa telemetria real.')}`;
    } else if (state.tab === 'summary') {
      const quality = primary.quality, history = context.quality;
      box.innerHTML = `<section class="capacity-summary-group"><h3>Avaliação</h3><div class="capacity-assess-grid"><div><small>CPU ${why('cpu', data)}</small>${chip(cap.capacity.cpu.status)}<strong>P95 ${percent(primary.stats.cpu.p95)}</strong><p>${esc(cap.capacity.cpu.reasons.map(reasonText).join('; '))}</p></div><div><small>Memória ${why('memory', data)}</small>${chip(cap.capacity.memory.status)}<strong>P95 ${percent(primary.stats.memory_used.p95)}</strong><p>${esc(cap.capacity.memory.reasons.map(reasonText).join('; '))}</p></div></div></section><section class="capacity-summary-group"><h3>Evidência</h3><div class="capacity-evidence-line"><strong>${quality.received_samples} / ${quality.expected_samples}</strong><span>${percent(quality.coverage_percent)} · ${esc(label(primary.diagnostics.evidence.overall.status))}</span></div><div class="capacity-dist-track"><div class="capacity-dist-fill" style="width:${Math.max(0, Math.min(100, quality.coverage_percent || 0))}%"></div></div></section><section class="capacity-summary-group"><h3>Contexto histórico · 7d</h3><div class="capacity-evidence-line"><strong>${history.received_samples} / ${history.expected_samples}</strong><span>${percent(history.coverage_percent)} · CPU ${esc(statusText(context.diagnostics.diagnostics.cpu.status))} · RAM ${esc(statusText(context.diagnostics.diagnostics.memory.status))}</span></div><div class="capacity-dist-track"><div class="capacity-dist-fill" style="width:${Math.max(0, Math.min(100, history.coverage_percent || 0))}%"></div></div></section><div class="capacity-summary-columns"><section><h3>Hardware</h3><div class="capacity-detail-grid">${fact('Cores físicos', number(hardware.cpu.physical_cores))}${fact('RAM', bytes(hardware.memory.total_bytes))}${fact('Discos', data.disks.length)}</div></section><section><h3>Operação</h3><div class="capacity-detail-grid">${fact('Estado', endpoint.status)}${fact('Uptime', Number.isFinite(endpoint.uptime_seconds) ? `${number(endpoint.uptime_seconds / 3600)} h` : '—')}${fact('Agent', endpoint.agent_version)}${fact('Última atividade', stamp(endpoint.last_seen))}</div></section></div><section class="capacity-summary-group"><h3>Problemas ativos</h3><div class="capacity-detail-grid">${fact('Críticos', data.alert_counts.critical)}${fact('Warnings', data.alert_counts.warning)}${fact('Capacidade', [cap.capacity.cpu.status, cap.capacity.memory.status].filter(s => s === 'OBSERVE' || s === 'SUSTAINED_PRESSURE').length)}${fact('Outros alertas', data.alert_counts.other)}</div></section>`;
    } else if (state.tab === 'performance') {
      const c = primary.stats.cpu, m = primary.stats.memory_used, mc = primary.stats.memory_committed;
      box.innerHTML = `<h3>Período principal</h3><div class="capacity-detail-grid">${fact('CPU · média / p95 / p99', `${percent(c.avg)} / ${percent(c.p95)} / ${percent(c.p99)}`)}${fact('RAM · média / p95 / p99', `${percent(m.avg)} / ${percent(m.p95)} / ${percent(m.p99)}`)}${fact('Memória comprometida p95', percent(mc.p95))}${fact('Cobertura', `${primary.quality.received_samples} / ${primary.quality.expected_samples}`)}</div><div class="capacity-section-heading"><h3>Série temporal</h3><div class="capacity-range" role="group" aria-label="Período da série">${['6h', '24h', '3d', '7d'].map(range => `<button type="button" data-range="${range}" aria-pressed="${state.range === range}" ${data.series_ranges[range] ? '' : 'disabled title="Sem dados suficientes"'}>${range}</button>`).join('')}</div></div><div data-series-body>${!data.series_ranges[state.range] ? empty('Sem dados suficientes neste período.') : state.series ? seriesChart(state.series) : empty('Carregando série temporal…')}</div><h3>Contexto 7d</h3><div class="capacity-detail-grid">${fact('CPU', statusText(context.diagnostics.diagnostics.cpu.status))}${fact('Memória', statusText(context.diagnostics.diagnostics.memory.status))}${fact('Cobertura', percent(context.quality.coverage_percent))}${fact('Amostras', `${context.quality.received_samples} / ${context.quality.expected_samples}`)}</div>`;
    } else if (state.tab === 'impact') {
      box.innerHTML = `<h3>Consumo observado recentemente</h3><p class="capacity-chart-note">Observação recente; não implica relação causal com problemas de desempenho.</p><h3>Maiores consumidores de CPU</h3>${renderProcessRanking(data.processes, 'cpu')}<h3>Maiores consumidores de memória</h3>${renderProcessRanking(data.processes, 'memory')}`;
    } else if (state.tab === 'hardware') {
      const slots = hardware.memory.slots_total, used = hardware.memory.slots_used;
      const slotView = Number.isInteger(slots) && Number.isInteger(used) && slots <= 32 && used <= slots ? `<div class="capacity-slots" aria-label="${used} slots usados de ${slots}">${Array.from({ length: slots }, (_, index) => `<span class="${index < used ? 'used' : ''}" title="${index < used ? 'Ocupado' : 'Livre'}"></span>`).join('')}</div>` : '';
      box.innerHTML = `<h3>Sistema</h3><div class="capacity-hardware-grid"><section>${fact('SO', endpoint.os_name || '—')}${fact('Versão / build', `${endpoint.os_version || '—'} / ${endpoint.os_build || '—'}`)}${fact('Uptime', Number.isFinite(endpoint.uptime_seconds) ? `${number(endpoint.uptime_seconds / 3600)} h` : '—')}${fact('Agent', endpoint.agent_version)}</section></div><h3>CPU</h3><div class="capacity-hardware-grid"><section>${fact('Modelo', hardware.cpu.name)}${fact('Cores físicos', number(hardware.cpu.physical_cores))}${fact('Processadores lógicos', number(hardware.cpu.logical_processors))}${fact('Clock máximo', hardware.cpu.max_clock_mhz == null ? '—' : `${number(hardware.cpu.max_clock_mhz)} MHz`)}</section></div><h3>Memória</h3><div class="capacity-hardware-grid"><section>${fact('Total', bytes(hardware.memory.total_bytes))}${fact('Slots usados / livres', `${number(hardware.memory.slots_used)} / ${number(hardware.memory.slots_free)}`)}${fact('Módulos', number(hardware.memory.module_count))}${slotView}</section></div><h3>Discos físicos</h3><div class="capacity-disk-grid">${data.disks.length ? data.disks.map(item => `<article><strong>${esc(item.model || 'Disco sem modelo')}</strong><div class="capacity-detail-grid">${fact('Tamanho', bytes(item.size_bytes))}${fact('Mídia / barramento', `${item.media_type || '—'} / ${item.bus_type || '—'}`)}${fact('Saúde reportada', item.health_status || '—')}${fact('Estado operacional', item.operational_status || '—')}</div><small>${item.drive_letters.length ? `Volumes associados: ${esc(item.drive_letters.join(', '))}` : 'Associação ao volume não determinada'}${item.is_system_disk === true ? ' · Disco de sistema' : ''}</small></article>`).join('') : empty('Discos físicos ainda não disponíveis no inventário.')}</div>`;
    } else if (state.tab === 'alerts') {
      box.innerHTML = `<h3>Resumo de alertas</h3><div class="capacity-detail-grid">${fact('Críticos', data.alert_counts.critical)}${fact('Warnings', data.alert_counts.warning)}${fact('Abertos', data.alert_counts.open)}${fact('Resolvidos recentes', data.alert_counts.resolved)}</div><h3>Atividade recente</h3><div class="capacity-alert-timeline">${data.alerts.length ? data.alerts.map(item => `<div><time>${stamp(item.last_seen_at)}</time><strong>${esc(item.title)}</strong><small>${esc(item.alert_type)} · ${esc(item.severity)} · ${esc(item.status)}</small></div>`).join('') : empty('Nenhum alerta recente para este endpoint.')}</div><a class="capacity-inline-link" href="${esc(endpoint.alerts_url)}">Ver todos na Central de Alertas</a>`;
    } else {
      box.innerHTML = `<h3>Aplicações instaladas</h3><p class="capacity-chart-note">Aplicação instalada não significa aplicação responsável por consumo.</p>${data.applications.length ? data.applications.map(item => `<div class="capacity-detail-fact"><small>${esc(item.version)}</small><strong>${esc(item.name)}</strong></div>`).join('') : empty('Inventário de aplicações não disponível.')}<h3>Observadas em execução</h3>${data.processes.length ? [...new Set(data.processes.map(item => item.name))].map(name => `<div class="capacity-detail-fact"><strong>${esc(name)}</strong></div>`).join('') : empty('Processos observados ainda não disponíveis.')}<h3>Serviços</h3>${empty('Dados de serviços ainda não disponíveis nesta análise.')}`;
    }
    icons();
  }
  async function loadSeries() {
    if (!state.detail || state.tab !== 'performance' || !state.detail.primary || !state.detail.series_ranges[state.range]) return;
    const seq = ++state.seriesRequest;
    state.series = null;
    state.chartIndex = 0;
    const body = $('[data-series-body]');
    if (body) body.innerHTML = '<div class="capacity-series-skeleton" aria-label="Carregando série temporal"></div>';
    const url = new URL(root.dataset.seriesTemplate.replace('00000000-0000-4000-8000-000000000000', state.detail.endpoint.id), location.origin);
    url.searchParams.set('period', state.range);
    if (demo) url.searchParams.set('demo', '1');
    try {
      const response = await fetch(url, { credentials: 'same-origin' });
      if (!response.ok) throw new Error();
      const payload = await response.json();
      if (seq !== state.seriesRequest || state.tab !== 'performance') return;
      state.series = payload.series;
      const target = $('[data-series-body]'); if (target) target.innerHTML = seriesChart(state.series);
    } catch (error) {
      if (seq === state.seriesRequest) { const target = $('[data-series-body]'); if (target) target.innerHTML = empty('Não foi possível carregar a série temporal.'); }
    }
  }
  async function openDrawer(id) {
    const seq = ++state.detailRequest;
    state.lastFocus = document.activeElement; state.tab = 'summary'; state.range = '24h'; state.series = null; state.seriesRequest++; state.detail = null;
    $('[data-drawer-backdrop]').hidden = false; $('[data-drawer]').hidden = false;
    document.body.style.overflow = 'hidden';
    $('[data-drawer-title]').textContent = 'Carregando…';
    $('[data-drawer-insight]').innerHTML = '<div class="capacity-series-skeleton" aria-hidden="true"></div>';
    $('[data-quick-facts]').innerHTML = '';
    $('[data-drawer-content]').innerHTML = '<p class="capacity-empty">Carregando detalhe…</p>';
    $('[data-close-drawer]').focus();
    const url = new URL(root.dataset.detailTemplate.replace('00000000-0000-4000-8000-000000000000', id), location.origin);
    url.searchParams.set('period', state.period);
    if (demo) url.searchParams.set('demo', '1');
    try {
      const response = await fetch(url, { credentials: 'same-origin' });
      if (!response.ok) throw new Error();
      const detail = await response.json();
      if (seq !== state.detailRequest) return;
      state.detail = detail;
      if (!$('[data-drawer]').hidden) renderDrawer();
    } catch (error) {
      if (seq !== state.detailRequest) return;
      $('[data-drawer-content]').innerHTML = empty('Não foi possível carregar este endpoint.');
      $('[data-drawer-title]').textContent = 'Detalhe indisponível';
    }
  }
  function closeDrawer() {
    state.detailRequest++; state.seriesRequest++;
    $('[data-drawer]').hidden = true; $('[data-drawer-backdrop]').hidden = true;
    document.body.style.overflow = '';
    if (state.lastFocus && state.lastFocus.isConnected) state.lastFocus.focus();
  }
  function showPopover(target) {
    const row = state.rows.find(item => item.id === target.dataset.open);
    if (!row) return;
    const popover = $('[data-popover]');
    popover.innerHTML = `<strong>${esc(row.hostname)}</strong>${esc(row.os_name || 'SO não informado')} · ${esc(row.status)}<br>CPU ${percent(row.cpu_p95)} · RAM ${percent(row.memory_p95)}<br>${esc(row.cpu_name || 'CPU não informada')} · ${bytes(row.memory_total_bytes)}<br>CPU ${esc(label(row.cpu_capacity))} · Memória ${esc(label(row.memory_capacity))}<br>Cobertura ${percent(row.coverage)} · ${row.alerts_critical} alerta(s) crítico(s)`;
    const rect = target.getBoundingClientRect();
    popover.style.left = `${Math.max(10, Math.min(innerWidth - 250, rect.left))}px`;
    popover.style.top = `${Math.max(10, Math.min(innerHeight - 150, rect.bottom + 8))}px`;
    popover.hidden = false;
  }
  root.addEventListener('pointerover', event => {
    const target = event.target.closest('.capacity-dot[data-open]');
    if (target) showPopover(target);
  });
  root.addEventListener('pointerout', event => {
    const target = event.target.closest('.capacity-dot[data-open]');
    if (target && !target.contains(event.relatedTarget)) $('[data-popover]').hidden = true;
  });
  root.addEventListener('focusin', event => {
    const target = event.target.closest('.capacity-dot[data-open]');
    if (target) showPopover(target);
  });
  root.addEventListener('focusout', event => {
    if (event.target.closest('.capacity-dot[data-open]')) $('[data-popover]').hidden = true;
  });
  root.addEventListener('click', event => {
    if (event.target.closest('[data-endpoint-link]')) return;
    const opener = event.target.closest('[data-open]');
    if (opener) { openDrawer(opener.dataset.open); return; }
    const filter = event.target.closest('[data-filter]');
    if (filter) { state.filter = state.filter === filter.dataset.filter ? 'all' : filter.dataset.filter; state.page = 1; renderAll(); return; }
    if (event.target.closest('[data-clear-filter]')) { state.filter = 'all'; state.page = 1; renderAll(); return; }
    if (event.target.closest('[data-clear-analysis]')) { state.filter = 'all'; state.query = ''; state.filters = []; state.page = 1; $('[data-search]').value = ''; $('[data-filter-error]').textContent = ''; renderAll(); return; }
    const remove = event.target.closest('[data-remove-filter]');
    if (remove) { state.filters.splice(Number(remove.dataset.removeFilter), 1); state.page = 1; renderAll(); return; }
    const page = event.target.closest('[data-page]');
    if (page) { state.page = Number(page.dataset.page); renderList(); return; }
    const scroll = event.target.closest('[data-scroll]');
    if (scroll) { const box = $(`[data-${scroll.dataset.scroll}]`); box.scrollBy({ left: Number(scroll.dataset.direction) * box.clientWidth * 0.8, behavior: matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth' }); return; }
    if (event.target.closest('[data-refresh]')) { load(); return; }
    if (event.target.closest('[data-close-drawer]') || event.target.closest('[data-drawer-backdrop]')) { closeDrawer(); return; }
    const tab = event.target.closest('[data-tab]');
    if (tab) {
      state.tab = tab.dataset.tab;
      renderDrawer();
      if (state.tab === 'performance' && state.series === null) loadSeries();
      $('[data-tabs] [data-tab="' + state.tab + '"]').focus();
      return;
    }
    const quick = event.target.closest('[data-quick-tab]');
    if (quick) { state.tab = quick.dataset.quickTab; renderDrawer(); if (state.tab === 'performance' && state.series === null) loadSeries(); return; }
    const range = event.target.closest('[data-range]');
    if (range) { state.range = range.dataset.range; renderDrawer(); loadSeries(); }
  });
  root.addEventListener('pointermove', event => {
    const chart = event.target.closest('.capacity-series');
    if (!chart || !state.series || state.series.length < 2) return;
    const rect = chart.getBoundingClientRect();
    const index = Math.max(0, Math.min(state.series.length - 1, Math.round((event.clientX - rect.left) / rect.width * (state.series.length - 1))));
    state.chartIndex = index;
    const item = state.series[index], target = $('[data-series-hover]');
    if (target) target.textContent = `${stamp(item.at)} · CPU ${percent(item.cpu)} · RAM ${percent(item.memory)}`;
  });
  root.addEventListener('keydown', event => {
    if (event.target.matches('.capacity-series') && (event.key === 'ArrowLeft' || event.key === 'ArrowRight') && state.series?.length) {
      event.preventDefault();
      state.chartIndex = Math.max(0, Math.min(state.series.length - 1, (state.chartIndex ?? 0) + (event.key === 'ArrowRight' ? 1 : -1)));
      const item = state.series[state.chartIndex];
      $('[data-series-hover]').textContent = `${stamp(item.at)} · CPU ${percent(item.cpu)} · RAM ${percent(item.memory)}`;
      return;
    }
    if (event.key === 'Escape' && !$('[data-drawer]').hidden) closeDrawer();
    if ((event.key === 'Enter' || event.key === ' ') && event.target.matches('tr[data-open]')) { event.preventDefault(); openDrawer(event.target.dataset.open); }
    if (event.key === 'Tab' && !$('[data-drawer]').hidden) {
      const focusable = [...$('[data-drawer]').querySelectorAll('button:not([hidden]),a:not([hidden])')];
      if (!focusable.length) return;
      const first = focusable[0], last = focusable[focusable.length - 1];
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
      else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
    }
  });
  $('[data-search]').addEventListener('input', event => { state.query = event.target.value.trim().toLocaleLowerCase(); state.page = 1; renderAll(); });
  $('[data-sort]').addEventListener('change', event => { state.sort = event.target.value; renderList(); });
  $('[data-page-size]').addEventListener('change', event => { state.pageSize = Number(event.target.value); state.page = 1; renderList(); });
  $('[data-filter-field]').innerHTML = Object.entries(fields).map(([key, [name]]) => `<option value="${key}">${esc(name)}</option>`).join('');
  function configureOperator() {
    const type = fields[$('[data-filter-field]').value][1];
    $('[data-filter-operator]').innerHTML = operators[type].map(op => `<option>${esc(op)}</option>`).join('');
    $('[data-filter-value]').value = ''; $('[data-filter-value]').type = type === 'number' ? 'number' : 'text';
    $('[data-filter-value]').step = 'any'; $('[data-filter-end]').type = 'number'; $('[data-filter-end]').step = 'any';
    configureRange();
  }
  function configureRange() { const range = $('[data-filter-operator]').value === 'entre'; $('[data-filter-end-label]').hidden = !range; $('[data-filter-end]').required = range; }
  $('[data-filter-field]').addEventListener('change', configureOperator);
  $('[data-filter-operator]').addEventListener('change', configureRange);
  $('[data-filter-form]').addEventListener('submit', event => {
    event.preventDefault();
    const field = $('[data-filter-field]').value, operator = $('[data-filter-operator]').value;
    const raw = $('[data-filter-value]').value.trim(), rawEnd = $('[data-filter-end]').value.trim();
    const type = fields[field][1], value = type === 'number' ? Number(raw) : raw, end = Number(rawEnd);
    if (!raw || (type === 'number' && (!Number.isFinite(value) || value < 0)) ||
        (operator === 'entre' && (!rawEnd || !Number.isFinite(end) || end < value)) ||
        (type === 'enum' && !['online', 'offline', 'unknown', 'uninstalled'].includes(raw.toLowerCase()))) {
      $('[data-filter-error]').textContent = 'Informe um valor válido para o filtro.'; return;
    }
    $('[data-filter-error]').textContent = ''; state.filters.push({ field, operator, value, end }); state.page = 1; renderAll();
  });
  ['attention', 'recent'].forEach(key => $(`[data-${key}]`).addEventListener('scroll', updateCarousels, { passive: true }));
  window.addEventListener('resize', updateCarousels);
  configureOperator();
  $('[data-capacity-period]').addEventListener('change', event => { state.period = event.target.value; load(); });
  $('[data-kpis]').innerHTML = Array.from({ length: 7 }, () => '<span class="capacity-kpi" aria-hidden="true"><span>Carregando</span><strong>—</strong></span>').join('');
  load();
})();
