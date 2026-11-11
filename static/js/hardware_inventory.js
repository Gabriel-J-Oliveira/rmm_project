(function () {
  'use strict';
  const root = document.querySelector('[data-hardware-inventory]');
  if (!root) return;
  const $ = selector => root.querySelector(selector);
  const state = { rows: [], query: '', status: '', os: '', manufacturer: '', sort: 'hostname', page: 1, pageSize: 25, selected: null };
  const esc = value => String(value == null ? '' : value).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const num = value => Number.isFinite(value) ? new Intl.NumberFormat('pt-BR', { maximumFractionDigits: 1 }).format(value) : '—';
  const bytes = value => Number.isFinite(value) && value >= 0 ? `${num(value / 1073741824)} GB` : '—';
  const date = value => {
    if (!value) return 'Sem evidência';
    const parsed = new Date(value);
    return Number.isNaN(parsed.getTime()) ? 'Sem evidência' : new Intl.DateTimeFormat('pt-BR', { dateStyle: 'short', timeStyle: 'short' }).format(parsed);
  };
  const icon = () => window.lucide?.createIcons?.();
  const known = (value, suffix = '') => value == null || value === '' ? 'Não informado' : `${esc(value)}${suffix}`;
  const rows = () => state.rows.filter(row => {
    const textMatch = !state.query || [row.hostname, row.fqdn, row.last_logged_user, row.manufacturer, row.model, row.os_name, row.os_version]
      .some(value => typeof value === 'string' && value.toLocaleLowerCase().includes(state.query));
    const os = [row.os_name, row.os_version].filter(Boolean).join(' ');
    return textMatch && (!state.status || row.status === state.status) && (!state.os || os === state.os) &&
      (!state.manufacturer || row.manufacturer === state.manufacturer);
  }).sort((a, b) => ({
    hostname: () => a.hostname.localeCompare(b.hostname),
    last_seen: () => (b.last_seen || '').localeCompare(a.last_seen || ''),
    inventory_at: () => (b.inventory_at || '').localeCompare(a.inventory_at || ''),
    ram: () => (b.memory?.total_bytes ?? -1) - (a.memory?.total_bytes ?? -1),
    disk: () => (b.max_disk_used_percent ?? -1) - (a.max_disk_used_percent ?? -1),
  }[state.sort] || (() => 0))());
  function renderFilterOptions() {
    const update = (selector, values, selected, label) => {
      const control = $(`[data-filter="${selector}"]`), options = [...new Set(values.filter(Boolean))].sort((a, b) => a.localeCompare(b));
      control.innerHTML = `<option value="">Todos</option>${options.map(value => `<option value="${esc(value)}">${esc(value)}</option>`).join('')}`;
      control.value = selected;
    };
    update('os', state.rows.map(row => [row.os_name, row.os_version].filter(Boolean).join(' ')), state.os);
    update('manufacturer', state.rows.map(row => row.manufacturer), state.manufacturer);
  }
  function renderKpis() {
    const c = state.payload.counts;
    const cards = [
      ['Endpoints', c.endpoints], ['Online', c.online], ['Offline', c.offline],
      ['Com inventário recebido', c.inventory_known], ['Sem snapshot disponível', c.inventory_missing],
      ['Com alerta crítico ativo', c.critical_alerts]
    ];
    $('[data-kpis]').innerHTML = cards.map(([label, value]) => `<article><span>${esc(label)}</span><strong>${num(value)}</strong></article>`).join('');
  }
  function chart(selector, entries, total, missing = 0) {
    const target = $(`[data-chart="${selector}"]`);
    if (!entries.length || !total) {
      const message = selector === 'alerts' ? 'Nenhum alerta ativo.' : 'Sem inventário suficiente para esta distribuição.';
      target.innerHTML = `<p class="hardware-empty">${message}</p>`;
      return;
    }
    const max = Math.max(1, ...entries.map(item => item[1]));
    target.innerHTML = `<ul class="hardware-bars">${entries.map(([label, count]) => `<li><span title="${esc(label)}">${esc(label)}</span><div><i style="width:${count / max * 100}%"></i></div><strong>${num(count)}</strong></li>`).join('')}</ul>${missing ? `<p class="hardware-note">${num(missing)} sem dado observado nesta categoria.</p>` : ''}`;
  }
  function distribution(values, missingLabel = 'Não informado') {
    const counts = new Map(); let missing = 0;
    values.forEach(value => { if (value == null || value === '') { missing++; return; } counts.set(value, (counts.get(value) || 0) + 1); });
    const sorted = [...counts].sort((a, b) => b[1] - a[1] || String(a[0]).localeCompare(String(b[0]))).slice(0, 8);
    if (missing) sorted.push([missingLabel, missing]);
    return { entries: sorted, missing };
  }
  function renderCharts() {
    const data = rows();
    const mem = distribution(data.map(row => Number.isFinite(row.memory?.total_bytes) && row.memory.total_bytes > 0 ? `${Math.round(row.memory.total_bytes / 1073741824)} GB` : null));
    const cores = distribution(data.map(row => Number.isFinite(row.cpu?.physical_cores) ? `${row.cpu.physical_cores} núcleos` : null));
    const maker = distribution(data.map(row => [row.manufacturer, row.model].filter(Boolean).join(' · ') || null));
    const os = distribution(data.map(row => [row.os_name, row.os_version].filter(Boolean).join(' ') || null));
    const diskBuckets = [['<70%', 0], ['70–84%', 0], ['85–94%', 0], ['95%+', 0], ['Não informado', 0]];
    data.forEach(row => { const value = row.max_disk_used_percent; diskBuckets[!Number.isFinite(value) ? 4 : value < 70 ? 0 : value < 85 ? 1 : value < 95 ? 2 : 3][1]++; });
    const alertCounts = new Map();
    data.flatMap(row => row.alerts || []).forEach(item => alertCounts.set(item.title || item.alert_type || 'Alerta', (alertCounts.get(item.title || item.alert_type || 'Alerta') || 0) + item.count));
    const knownRam = data.filter(row => Number.isFinite(row.memory?.total_bytes) && row.memory.total_bytes > 0);
    const knownDisk = data.filter(row => Number.isFinite(row.max_disk_used_percent));
    const upgrades = [
      ['RAM ≤ 8 GB', knownRam.filter(row => row.memory.total_bytes <= 8 * 1073741824).length],
      ['RAM ≤ 16 GB', knownRam.filter(row => row.memory.total_bytes <= 16 * 1073741824).length],
      ['Volume > 85%', knownDisk.filter(row => row.max_disk_used_percent > 85).length],
      ['Volume > 95%', knownDisk.filter(row => row.max_disk_used_percent > 95).length],
    ];
    chart('memory', mem.entries, data.length, mem.missing);
    chart('cpu', cores.entries, data.length, cores.missing);
    chart('makers', maker.entries, data.length, maker.missing);
    chart('os', os.entries, data.length, os.missing);
    chart('disk', diskBuckets, data.length);
    chart('alerts', [...alertCounts].sort((a, b) => b[1] - a[1]).slice(0, 8), [...alertCounts.values()].reduce((sum, n) => sum + n, 0));
    chart('upgrade', upgrades, data.length);
  }
  function renderTable() {
    const filtered = rows(), pages = Math.max(1, Math.ceil(filtered.length / state.pageSize));
    state.page = Math.min(state.page, pages);
    const start = (state.page - 1) * state.pageSize;
    $('[data-result-count]').textContent = `${filtered.length} de ${state.rows.length} computadores`;
    $('[data-table]').innerHTML = filtered.slice(start, start + state.pageSize).map(row => `<tr data-open="${esc(row.id)}"><td><a href="${esc(row.endpoint_url)}" data-endpoint>${esc(row.hostname)}</a><small>${esc(row.fqdn || 'FQDN não informado')}${row.last_logged_user ? ` · ${esc(row.last_logged_user)}` : ''}</small></td><td>${esc(row.status || 'Desconhecido')}</td><td>${esc([row.manufacturer, row.model].filter(Boolean).join(' · ') || 'Não informado')}</td><td>${esc([row.os_name, row.os_version, row.os_build].filter(Boolean).join(' ') || 'Não informado')}</td><td>${esc(row.cpu?.name || 'Não informado')}<small>${known(row.cpu?.physical_cores, ' físicos')}</small></td><td>${bytes(row.memory?.total_bytes)}<small>${row.memory?.slots_used == null || row.memory?.slots_total == null ? 'Slots não informados' : `${num(row.memory.slots_used)} / ${num(row.memory.slots_total)} slots usados`}</small></td><td>${row.disk_count == null ? 'Não informado' : `${num(row.disk_count)} volume(s)`}<small>${Number.isFinite(row.max_disk_used_percent) ? `Maior ocupação ${num(row.max_disk_used_percent)}%` : 'Ocupação não informada'}</small></td><td>${date(row.inventory_at)}${row.inventory_stale ? '<small class="hardware-warning">Snapshot com mais de 7 dias</small>' : ''}</td></tr>`).join('') || '<tr><td colspan="8" class="hardware-empty">Nenhum computador corresponde à pesquisa.</td></tr>';
    const choices = [10, 25, 50].map(size => `<option value="${size}" ${size === state.pageSize ? 'selected' : ''}>${size}</option>`).join('');
    $('[data-pagination]').innerHTML = `<label>Por página <select data-size>${choices}</select></label><span>${filtered.length ? start + 1 : 0}–${Math.min(start + state.pageSize, filtered.length)} de ${filtered.length}</span><button type="button" data-page="${state.page - 1}" ${state.page === 1 ? 'disabled' : ''} aria-label="Página anterior">‹</button><span>Página ${state.page} / ${pages}</span><button type="button" data-page="${state.page + 1}" ${state.page === pages ? 'disabled' : ''} aria-label="Próxima página">›</button>`;
  }
  function fact(label, value) { return `<div><small>${esc(label)}</small><strong>${value == null || value === '' ? 'Não informado' : esc(value)}</strong></div>`; }
  function renderDetail(row) {
    const modules = row.memory?.modules || [], disks = row.physical_disks || [], volumes = row.volumes || [];
    $('[data-drawer-title]').textContent = row.hostname;
    $('[data-drawer-meta]').textContent = `${row.fqdn || 'FQDN não informado'} · ${row.status || 'Desconhecido'} · snapshot ${date(row.inventory_at)}`;
    $('[data-endpoint-link]').href = row.endpoint_url;
    $('[data-alert-link]').href = row.alerts_url;
    const moduleRows = modules.length ? modules.map(module => `<tr><td>${esc(module.manufacturer || 'Não informado')}</td><td>${esc(module.part_number || 'Não informado')}</td><td>${bytes(module.capacity_bytes ?? module.size_bytes)}</td><td>${known(module.speed_mhz, ' MHz')}</td></tr>`).join('') : '<tr><td colspan="4">Módulos não detalhados no snapshot.</td></tr>';
    const diskRows = disks.length ? disks.map(disk => `<tr><td>${esc(disk.model || 'Não informado')}</td><td>${bytes(disk.size_bytes)}</td><td>${esc(disk.media_type || 'Não informado')}</td><td>${esc(disk.health_status || disk.operational_status || 'Não informado')}</td><td>${esc((disk.drive_letters || []).join(', ') || 'Associação não confirmada')}</td></tr>`).join('') : '<tr><td colspan="5">Discos físicos não reportados.</td></tr>';
    const volumeRows = volumes.length ? volumes.map(volume => `<tr><td>${esc(volume.name || 'Não informado')}</td><td>${bytes(volume.size_bytes)}</td><td>${bytes(volume.free_bytes)}</td><td>${Number.isFinite(volume.size_bytes) && volume.size_bytes > 0 && Number.isFinite(volume.free_bytes) ? `${num((volume.size_bytes - volume.free_bytes) / volume.size_bytes * 100)}%` : 'Não informado'}</td></tr>`).join('') : '<tr><td colspan="4">Volumes não reportados.</td></tr>';
    const battery = row.battery || {};
    $('[data-drawer-content]').innerHTML = `<section><h3>Identificação</h3><div class="hardware-facts">${fact('Fabricante', row.manufacturer)}${fact('Modelo', row.model)}${fact('Placa-mãe', typeof row.motherboard === 'string' ? row.motherboard : row.motherboard?.product || row.motherboard?.model)}${fact('BIOS', typeof row.bios === 'string' ? row.bios : row.bios?.version)}${fact('Sistema operacional', [row.os_name, row.os_version, row.os_build].filter(Boolean).join(' '))}${fact('Usuário reportado', row.last_logged_user)}</div></section><section><h3>Processador</h3><div class="hardware-facts">${fact('Modelo', row.cpu?.name)}${fact('Núcleos físicos', row.cpu?.physical_cores)}${fact('Processadores lógicos', row.cpu?.logical_processors)}${fact('Frequência máxima reportada', row.cpu?.max_clock_mhz == null ? null : `${num(row.cpu.max_clock_mhz)} MHz`)}</div></section><section><h3>Memória física</h3><div class="hardware-facts">${fact('Instalada', bytes(row.memory?.total_bytes))}${fact('Slots usados / totais / livres', row.memory?.slots_used == null || row.memory?.slots_total == null ? null : `${row.memory.slots_used} / ${row.memory.slots_total} / ${row.memory.slots_free ?? 'não informado'}`)}</div><div class="hardware-table-wrap"><table><thead><tr><th>Fabricante</th><th>Part number</th><th>Capacidade</th><th>Velocidade</th></tr></thead><tbody>${moduleRows}</tbody></table></div></section><section><h3>Discos físicos</h3><div class="hardware-table-wrap"><table><thead><tr><th>Modelo</th><th>Capacidade</th><th>Mídia</th><th>Saúde / estado</th><th>Volume associado</th></tr></thead><tbody>${diskRows}</tbody></table></div></section><section><h3>Volumes</h3><div class="hardware-table-wrap"><table><thead><tr><th>Volume</th><th>Total</th><th>Livre</th><th>Ocupação</th></tr></thead><tbody>${volumeRows}</tbody></table></div></section><section><h3>Bateria</h3><div class="hardware-facts">${fact('Presença', battery.present == null ? null : battery.present ? 'Presente' : 'Não presente')}${fact('Fabricante / modelo', [battery.manufacturer, battery.model].filter(Boolean).join(' / '))}${fact('Saúde reportada', battery.health_percent == null ? null : `${num(battery.health_percent)}%`)}${fact('Ciclos', battery.cycle_count)}</div></section><section><h3>Origem e qualidade</h3><div class="hardware-facts">${fact('Origem', row.inventory_source)}${fact('Coletado em', date(row.inventory_provenance?.collected_at))}${fact('Recebido em', date(row.inventory_received_at))}${fact('Seções reportadas', Object.keys(row.inventory_provenance?.sections || {}).join(', ') || 'Não informado')}${fact('Avisos de consistência', (row.inventory_warnings || []).join(', ') || 'Nenhum aviso conhecido')}</div></section><section><h3>Alertas ativos</h3><ul>${(row.alerts || []).map(alert => `<li>${esc(alert.title || alert.alert_type)} · ${esc(alert.severity)} (${num(alert.count)})</li>`).join('') || '<li>Nenhum alerta ativo.</li>'}</ul></section>`;
    $('[data-backdrop]').hidden = false; $('[data-drawer]').hidden = false; document.body.classList.add('hardware-drawer-open');
    icon();
  }
  async function loadDetail(id) {
    state.selectedId = id;
    state.selected = state.rows.find(row => row.id === id) || null;
    $('[data-drawer-title]').textContent = 'Carregando detalhes';
    $('[data-drawer-meta]').textContent = '';
    $('[data-drawer-content]').innerHTML = '<p class="hardware-empty" role="status">Consultando inventário do endpoint…</p>';
    $('[data-backdrop]').hidden = false; $('[data-drawer]').hidden = false; document.body.classList.add('hardware-drawer-open');
    const url = root.dataset.detailTemplate.replace('00000000-0000-4000-8000-000000000000', encodeURIComponent(id));
    const response = await fetch(url, { credentials: 'same-origin', cache: 'no-store' });
    if (!response.ok) throw new Error('detail');
    const data = await response.json(); state.selected = data.endpoint; renderDetail(data.endpoint);
  }
  function renderDetailError() {
    $('[data-drawer-title]').textContent = state.selected?.hostname || 'Detalhes indisponíveis';
    $('[data-drawer-meta]').textContent = 'Não foi possível consultar o inventário deste endpoint.';
    $('[data-drawer-content]').innerHTML = '<div class="hardware-error" role="alert">Falha ao carregar os detalhes. Verifique a conexão e tente novamente. <button type="button" data-detail-retry>Tentar novamente</button></div>';
    $('[data-backdrop]').hidden = false; $('[data-drawer]').hidden = false; document.body.classList.add('hardware-drawer-open');
  }
  async function load() {
    $('[data-error]').hidden = true;
    try {
      const response = await fetch(root.dataset.overviewUrl, { credentials: 'same-origin', cache: 'no-store' });
      if (!response.ok) throw new Error('overview');
      state.payload = await response.json(); state.rows = state.payload.endpoints;
      $('[data-updated]').textContent = `Inventário consultado ${date(state.payload.generated_at)}`;
      renderKpis(); renderFilterOptions(); renderCharts(); renderTable(); icon();
    } catch (_) { $('[data-error]').hidden = false; $('[data-updated]').textContent = 'Dados indisponíveis'; }
  }
  function close() { $('[data-drawer]').hidden = true; $('[data-backdrop]').hidden = true; document.body.classList.remove('hardware-drawer-open'); state.selected = null; state.selectedId = null; }
  root.addEventListener('click', event => {
    if (event.target.closest('[data-refresh]')) { load(); return; }
    if (event.target.closest('[data-close]') || event.target.matches('[data-backdrop]')) { close(); return; }
    const page = event.target.closest('[data-page]'); if (page && !page.disabled) { state.page = Number(page.dataset.page); renderTable(); return; }
    if (event.target.closest('[data-detail-retry]')) { loadDetail(state.selectedId).catch(renderDetailError); return; }
    const row = event.target.closest('tr[data-open]'); if (row && !event.target.closest('a')) loadDetail(row.dataset.open).catch(renderDetailError);
  });
  root.addEventListener('change', event => {
    if (event.target.matches('[data-size]')) state.pageSize = Number(event.target.value);
    if (event.target.matches('[data-filter="status"]')) state.status = event.target.value;
    if (event.target.matches('[data-filter="os"]')) state.os = event.target.value;
    if (event.target.matches('[data-filter="manufacturer"]')) state.manufacturer = event.target.value;
    if (event.target.matches('[data-sort]')) state.sort = event.target.value;
    if (event.target.matches('[data-size], [data-filter], [data-sort]')) { state.page = 1; renderCharts(); renderTable(); }
  });
  $('[data-search]').addEventListener('input', event => { state.query = event.target.value.trim().toLocaleLowerCase(); state.page = 1; renderCharts(); renderTable(); });
  document.addEventListener('keydown', event => { if (event.key === 'Escape' && !$('[data-drawer]').hidden) close(); });
  load();
})();
