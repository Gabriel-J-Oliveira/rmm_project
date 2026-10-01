(function () {
  'use strict';
  const root = document.querySelector('[data-capacity-root]');
  if (!root) return;
  const $ = selector => root.querySelector(selector);
  const demo = root.dataset.demo === '1';
  const state = { rows: [], counts: {}, filter: 'all', query: '', sort: 'priority', period: '24h', detail: null, tab: 'summary', lastFocus: null, request: 0, detailRequest: 0 };
  const labels = { NOT_EVALUATED: 'Sem evidência', NO_PRESSURE_OBSERVED: 'Sem pressão', OBSERVE: 'Observar', SUSTAINED_PRESSURE: 'Pressão sustentada', SUFFICIENT: 'Suficiente', PARTIAL: 'Parcial', INSUFFICIENT: 'Insuficiente' };
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
  const icons = () => { if (window.lucide && window.lucide.createIcons) window.lucide.createIcons(); };
  const priority = row => (row.classifications.sustained ? 10 : 0) + (row.alerts_critical ? 8 : 0) + (row.classifications.offline ? 6 : 0) + (row.classifications.observe ? 4 : 0) + (row.classifications.insufficient ? 2 : 0);

  function filtered() {
    let rows = state.rows.filter(row => state.filter === 'all' || (state.filter === 'critical' ? row.alerts_critical > 0 : state.filter === 'monitored' ? true : row.classifications[state.filter]));
    if (state.query) rows = rows.filter(row => `${row.hostname} ${row.os_name || ''} ${row.os_version || ''}`.toLocaleLowerCase().includes(state.query));
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
    $('[data-kpis]').innerHTML = items.map(([key, title, color]) => `<button type="button" class="capacity-kpi ${state.filter === key ? 'is-active' : ''}" style="--cap-accent:var(${color})" data-filter="${key}" aria-pressed="${state.filter === key}"><span>${esc(title)}</span><strong>${state.counts[key] || 0}</strong></button>`).join('');
    $('[data-clear-filter]').hidden = state.filter === 'all';
  }
  function card(row, spotlight) {
    const parts = `<div class="capacity-card-top"><strong>${esc(row.hostname)}</strong>${stateBadge(row.status)}</div><div class="capacity-card-facts"><div><small>CPU</small><b>${chip(row.cpu_capacity)}</b></div><div><small>Memória</small><b>${chip(row.memory_capacity)}</b></div><div><small>CPU p95</small><b>${percent(row.cpu_p95)}</b></div><div><small>RAM p95</small><b>${percent(row.memory_p95)}</b></div></div><span class="capacity-card-meta">Cobertura ${percent(row.coverage)} · ${esc(label(row.evidence))} · ${row.alerts_critical} crítico(s)</span><span class="capacity-card-meta">${esc(row.os_name || 'SO não informado')} · ${esc(row.cpu_name || 'CPU não informada')} · ${bytes(row.memory_total_bytes)}</span>`;
    if (!spotlight) return `<button type="button" class="capacity-tile" data-open="${esc(row.id)}" title="${esc(row.hostname)}: ${esc(label(row.cpu_capacity))} CPU; ${esc(label(row.memory_capacity))} memória. Abrir detalhes.">${parts}</button>`;
    return `<article class="capacity-spotlight">${parts}<div class="capacity-card-actions"><button type="button" data-open="${esc(row.id)}">Abrir detalhes</button>${row.endpoint_url ? `<a href="${esc(row.endpoint_url)}">Abrir endpoint</a>` : ''}<a href="${esc(row.alerts_url)}">Ver alertas</a></div></article>`;
  }
  function renderAttention() {
    const rows = [...state.rows].filter(row => row.classifications.attention).sort((a, b) => priority(b) - priority(a)).slice(0, 6);
    $('[data-attention]').innerHTML = rows.length ? rows.map(row => card(row, true)).join('') : empty('Nenhum endpoint requer atenção neste período.');
  }
  function renderDistribution() {
    const kinds = [['no_pressure', 'Sem pressão'], ['observe', 'Observar'], ['sustained', 'Pressão sustentada'], ['insufficient', 'Sem evidência']];
    const values = Object.fromEntries(kinds.map(([key]) => [key, 0]));
    state.rows.forEach(row => { const c = row.classifications; values[c.insufficient ? 'insufficient' : c.sustained ? 'sustained' : c.observe ? 'observe' : 'no_pressure']++; });
    $('[data-distribution]').innerHTML = kinds.map(([key, text]) => `<div class="capacity-dist-row" data-kind="${key}"><span>${esc(text)}</span><div class="capacity-dist-track" role="img" aria-label="${esc(text)}: ${values[key]}"><div class="capacity-dist-fill" style="width:${state.rows.length ? values[key] / state.rows.length * 100 : 0}%"></div></div><strong>${values[key]}</strong></div>`).join('');
  }
  function renderScatter() {
    const rows = state.rows.filter(row => Number.isFinite(row.cpu_p95) && Number.isFinite(row.memory_p95));
    $('[data-scatter]').innerHTML = rows.map(row => {
      const kind = row.classifications.sustained ? 'sustained' : row.classifications.observe ? 'observe' : '';
      return `<button type="button" class="capacity-dot ${kind}" data-open="${esc(row.id)}" style="left:${Math.max(2, Math.min(98, row.cpu_p95))}%;bottom:${Math.max(2, Math.min(98, row.memory_p95))}%" aria-label="${esc(row.hostname)}: CPU p95 ${percent(row.cpu_p95)}, RAM p95 ${percent(row.memory_p95)}" title="${esc(row.hostname)} · CPU ${percent(row.cpu_p95)} · RAM ${percent(row.memory_p95)} · ${esc(row.cpu_name || 'Hardware não informado')}"></button>`;
    }).join('');
    $('[data-scatter-note]').textContent = `${rows.length} endpoint(s) com CPU e RAM p95 disponíveis.`;
  }
  function renderWorkloads() {
    const entries = state.rows.flatMap(row => row.processes.map(item => ({ ...item, hostname: row.hostname })));
    entries.sort((a, b) => (b.cpu_percent ?? 0) - (a.cpu_percent ?? 0));
    $('[data-workloads]').innerHTML = entries.length ? entries.slice(0, 6).map(item => `<div class="capacity-workload" title="Observado na última amostra de ${esc(item.hostname)}. Não implica impacto causal."><strong>${esc(item.name)}</strong><small>${esc(item.hostname)} · ${esc(item.category)} · CPU ${percent(item.cpu_percent)} · RAM ${bytes(item.working_set_bytes)}</small></div>`).join('') : empty('Dados ainda não disponíveis para esta análise.');
  }
  function renderTable(rows) {
    $('[data-table-body]').innerHTML = rows.length ? rows.map(row => `<tr data-open="${esc(row.id)}" tabindex="0" aria-label="Abrir detalhes de ${esc(row.hostname)}"><td>${row.endpoint_url ? `<a href="${esc(row.endpoint_url)}" data-endpoint-link>${esc(row.hostname)}</a>` : esc(row.hostname)}</td><td>${stateBadge(row.status)}</td><td>${esc(row.os_name || '—')}<span class="sub">${esc(row.os_build || '')}</span></td><td>${esc(row.cpu_name || '—')}<span class="sub">${number(row.cpu_cores)} cores</span></td><td>${bytes(row.memory_total_bytes)}</td><td>${percent(row.cpu_p95)}</td><td>${percent(row.memory_p95)}</td><td>${esc(label(row.evidence))}<span class="sub">${percent(row.coverage)}</span></td><td>${chip(row.cpu_capacity)}</td><td>${chip(row.memory_capacity)}</td><td>${row.alerts_critical}</td><td>${stamp(row.last_seen)}</td></tr>`).join('') : '<tr><td colspan="12">Nenhum endpoint corresponde ao filtro.</td></tr>';
  }
  function renderList() {
    const rows = filtered();
    $('[data-filter-count]').textContent = `${rows.length} de ${state.rows.length}`;
    $('[data-grid]').innerHTML = rows.length ? rows.map(row => card(row, false)).join('') : empty('Nenhum endpoint corresponde ao filtro.');
    renderTable(rows); renderKpis();
  }
  function renderAll() { renderAttention(); renderDistribution(); renderScatter(); renderWorkloads(); renderList(); icons(); }
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
      $('[data-updated]').textContent = `Atualizado ${stamp(payload.generated_at)} · ${state.period} + contexto 7d`;
      renderAll();
    } catch (error) {
      if (seq === state.request) { $('[data-error]').hidden = false; $('[data-updated]').textContent = 'Dados indisponíveis'; }
    }
  }
  const tabs = [['summary', 'Resumo'], ['performance', 'Desempenho'], ['impact', 'Impacto'], ['hardware', 'SO & Hardware'], ['alerts', 'Alertas'], ['applications', 'Aplicações']];
  function seriesChart(series) {
    const values = series.filter(item => Number.isFinite(item.cpu) || Number.isFinite(item.memory));
    if (values.length < 2) return empty('Série temporal ainda não disponível.');
    const line = key => values.map((item, index) => `${index ? 'L' : 'M'}${(index / (values.length - 1) * 100).toFixed(2)},${(100 - (item[key] || 0)).toFixed(2)}`).join(' ');
    return `<svg class="capacity-series" viewBox="0 0 100 100" preserveAspectRatio="none" role="img" aria-label="Série de CPU e memória no período"><path d="${line('cpu')}" fill="none" stroke="#34d3b3" stroke-width="1.6" vector-effect="non-scaling-stroke"/><path d="${line('memory')}" fill="none" stroke="#f5bd62" stroke-width="1.6" vector-effect="non-scaling-stroke"/></svg><p class="capacity-chart-note">Verde: CPU · Amarelo: RAM · ${values.length} pontos</p>`;
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
    const box = $('[data-drawer-content]');
    const primary = data.primary, context = data.context, cap = data.capacity, hardware = data.hardware;
    if (!primary) {
      box.innerHTML = `<h3>Dados demonstrativos</h3><div class="capacity-detail-grid">${fact('CPU', label(endpoint.cpu_capacity))}${fact('Memória', label(endpoint.memory_capacity))}${fact('Inventário CPU', endpoint.cpu_inventory_status)}${fact('Inventário RAM', endpoint.memory_inventory_status)}</div>${empty('Este detalhe é sintético e não usa telemetria real.')}`;
    } else if (state.tab === 'summary') {
      box.innerHTML = `<h3>Avaliação</h3><div class="capacity-detail-grid">${fact('CPU', label(cap.capacity.cpu.status))}${fact('Memória', label(cap.capacity.memory.status))}${fact('Cobertura', percent(primary.quality.coverage_percent))}${fact('Contexto 7d', percent(context.quality.coverage_percent))}${fact('Evidência CPU', label(primary.diagnostics.evidence.metrics.cpu_percent.status))}${fact('Evidência RAM', label(primary.diagnostics.evidence.metrics.memory_used_percent.status))}${fact('Alertas ativos', data.alerts.length)}${fact('Última atividade', stamp(endpoint.last_seen))}</div><h3>Razões</h3>${empty(`CPU: ${cap.capacity.cpu.reasons.join(', ')} · Memória: ${cap.capacity.memory.reasons.join(', ')}`)}`;
    } else if (state.tab === 'performance') {
      const c = primary.stats.cpu, m = primary.stats.memory_used, mc = primary.stats.memory_committed;
      box.innerHTML = `<h3>Período principal</h3><div class="capacity-detail-grid">${fact('CPU média', percent(c.avg))}${fact('CPU p95 / p99', `${percent(c.p95)} / ${percent(c.p99)}`)}${fact('RAM média', percent(m.avg))}${fact('RAM p95 / p99', `${percent(m.p95)} / ${percent(m.p99)}`)}${fact('Memória comprometida p95', percent(mc.p95))}${fact('Amostras', `${primary.quality.received_samples} / ${primary.quality.expected_samples}`)}</div><h3>Histórico</h3>${seriesChart(data.series)}<h3>Contexto 7d</h3><div class="capacity-detail-grid">${fact('CPU', label(context.diagnostics.diagnostics.cpu.status))}${fact('Memória', label(context.diagnostics.diagnostics.memory.status))}${fact('Cobertura', percent(context.quality.coverage_percent))}${fact('Amostras', `${context.quality.received_samples} / ${context.quality.expected_samples}`)}</div>`;
    } else if (state.tab === 'impact') {
      box.innerHTML = `<h3>Consumo observado</h3>${data.processes.length ? data.processes.map(item => `<div class="capacity-detail-fact"><small>${esc(item.category)} · ${esc(item.name)}</small><strong>CPU ${percent(item.cpu_percent)} · RAM ${bytes(item.working_set_bytes)}</strong></div>`).join('') : empty('Dados ainda não disponíveis para esta análise.')}<p class="capacity-chart-note">Amostra recente; sem atribuição causal a aplicações instaladas.</p>`;
    } else if (state.tab === 'hardware') {
      box.innerHTML = `<h3>Sistema</h3><div class="capacity-detail-grid">${fact('SO', endpoint.os_name || '—')}${fact('Versão / build', `${endpoint.os_version || '—'} / ${endpoint.os_build || '—'}`)}${fact('Uptime', Number.isFinite(endpoint.uptime_seconds) ? `${number(endpoint.uptime_seconds / 3600)} h` : '—')}${fact('Agent', endpoint.agent_version)}</div><h3>Hardware</h3><div class="capacity-detail-grid">${fact('CPU', hardware.cpu.name)}${fact('Cores físicos / lógicos', `${number(hardware.cpu.physical_cores)} / ${number(hardware.cpu.logical_processors)}`)}${fact('Clock máximo', hardware.cpu.max_clock_mhz == null ? '—' : `${number(hardware.cpu.max_clock_mhz)} MHz`)}${fact('Memória', bytes(hardware.memory.total_bytes))}${fact('Slots usados / livres', `${number(hardware.memory.slots_used)} / ${number(hardware.memory.slots_free)}`)}${fact('Inventário', `${hardware.cpu.status} / ${hardware.memory.status}`)}</div><h3>Discos inventariados</h3>${data.disks.length ? data.disks.map(item => `<div class="capacity-detail-fact"><small>${esc(item.model || 'Disco')}</small><strong>${bytes(item.size_bytes)}</strong></div>`).join('') : empty('Nenhum disco no snapshot recente.')}`;
    } else if (state.tab === 'alerts') {
      box.innerHTML = `<h3>Alertas ativos</h3>${data.alerts.length ? data.alerts.map(item => `<div class="capacity-detail-fact"><small>${esc(item.severity)} · ${stamp(item.last_seen_at)}</small><strong>${esc(item.title)}</strong></div>`).join('') : empty('Nenhum alerta ativo para este endpoint.')}`;
    } else {
      box.innerHTML = `<h3>Aplicações inventariadas</h3><p class="capacity-chart-note">Presença instalada não indica consumo observado.</p>${data.applications.length ? data.applications.map(item => `<div class="capacity-detail-fact"><small>${esc(item.version)}</small><strong>${esc(item.name)}</strong></div>`).join('') : empty('Inventário de aplicações não disponível.')}<h3>Serviços</h3>${empty('Dados de serviços ainda não disponíveis nesta análise.')}`;
    }
    icons();
  }
  async function openDrawer(id) {
    const seq = ++state.detailRequest;
    state.lastFocus = document.activeElement; state.tab = 'summary'; state.detail = null;
    $('[data-drawer-backdrop]').hidden = false; $('[data-drawer]').hidden = false;
    document.body.style.overflow = 'hidden';
    $('[data-drawer-title]').textContent = 'Carregando…';
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
    state.detailRequest++;
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
    const target = event.target.closest('.capacity-tile[data-open],.capacity-dot[data-open]');
    if (target) showPopover(target);
  });
  root.addEventListener('pointerout', event => {
    const target = event.target.closest('.capacity-tile[data-open],.capacity-dot[data-open]');
    if (target && !target.contains(event.relatedTarget)) $('[data-popover]').hidden = true;
  });
  root.addEventListener('focusin', event => {
    const target = event.target.closest('.capacity-tile[data-open],.capacity-dot[data-open]');
    if (target) showPopover(target);
  });
  root.addEventListener('focusout', event => {
    if (event.target.closest('.capacity-tile[data-open],.capacity-dot[data-open]')) $('[data-popover]').hidden = true;
  });
  root.addEventListener('click', event => {
    if (event.target.closest('[data-endpoint-link]')) return;
    const opener = event.target.closest('[data-open]');
    if (opener) { openDrawer(opener.dataset.open); return; }
    const filter = event.target.closest('[data-filter]');
    if (filter) { state.filter = state.filter === filter.dataset.filter ? 'all' : filter.dataset.filter; renderList(); return; }
    if (event.target.closest('[data-clear-filter]')) { state.filter = 'all'; renderList(); return; }
    if (event.target.closest('[data-refresh]')) { load(); return; }
    if (event.target.closest('[data-close-drawer]') || event.target.closest('[data-drawer-backdrop]')) { closeDrawer(); return; }
    const tab = event.target.closest('[data-tab]');
    if (tab) {
      state.tab = tab.dataset.tab;
      renderDrawer();
      $('[data-tabs] [data-tab="' + state.tab + '"]').focus();
    }
  });
  root.addEventListener('keydown', event => {
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
  $('[data-search]').addEventListener('input', event => { state.query = event.target.value.trim().toLocaleLowerCase(); renderList(); });
  $('[data-sort]').addEventListener('change', event => { state.sort = event.target.value; renderList(); });
  $('[data-capacity-period]').addEventListener('change', event => { state.period = event.target.value; load(); });
  $('[data-kpis]').innerHTML = Array.from({ length: 7 }, () => '<span class="capacity-kpi" aria-hidden="true"><span>Carregando</span><strong>—</strong></span>').join('');
  load();
})();
