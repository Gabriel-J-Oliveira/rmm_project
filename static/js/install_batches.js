(function () {
    'use strict';
    if (!document.getElementById('install-batches')) return;
    const api = window.NightOwlInstallBatch;
    const $ = id => document.getElementById(id);
    const drawer = $('install-batch-drawer');
    const listRows = new Map(), itemRows = new Map();
    let listRequestInFlight = false, detailRequestInFlight = false;
    let listTimer, detailTimer, currentId = null, currentBatch = null, previousFocus;
    let page = 1, pages = 1, listFailures = 0, detailFailures = 0, detailPending = false;
    let confirmation = null, actionInFlight = false, closeDiagnostic = null;
    const batchLabels = { QUEUED: 'Aguardando início', RUNNING: 'Em andamento', COMPLETED: 'Concluída',
        COMPLETED_WITH_ERRORS: 'Concluída com falhas', INTERRUPTED: 'Interrompida' };
    const itemLabels = { WAITING: 'Aguardando', PREFLIGHT: 'Verificando', INSTALLING: 'Instalando',
        COMPLETED: 'Concluído', FAILED: 'Falha', REVIEW_REQUIRED: 'Requer revisão', SKIPPED: 'Ignorado' };
    const terminal = status => ['COMPLETED', 'COMPLETED_WITH_ERRORS', 'INTERRUPTED'].includes(status);
    const n = value => Number.isFinite(Number(value)) ? Math.max(0, Number(value)) : 0;
    const percent = value => Math.min(100, n(value));
    const el = (tag, cls = '', text) => {
        const node = document.createElement(tag); node.className = cls;
        if (text !== undefined) node.textContent = String(text);
        return node;
    };
    function icon(name) { const node = el('i'); node.setAttribute('data-lucide', name); return node; }
    function icons() { window.lucide?.createIcons?.(); }
    function statusClass(status) {
        return ['RUNNING', 'INSTALLING'].includes(status) ? 'running' :
            status === 'COMPLETED' ? 'success' : ['REVIEW_REQUIRED', 'COMPLETED_WITH_ERRORS'].includes(status) ? 'review' :
            ['FAILED', 'INTERRUPTED'].includes(status) ? 'failed' : status === 'PREFLIGHT' ? 'preflight' : 'waiting';
    }
    function badge(status, labels) { return el('span', 'install-batch-status ' + statusClass(status), labels[status] || 'Estado desconhecido'); }
    function progress(value, status) {
        const p = percent(value);
        const wrap = el('div', 'install-progress-line');
        const track = el('div', 'install-batch-progress ' + statusClass(status));
        track.setAttribute('role', 'progressbar'); track.setAttribute('aria-label', 'Progresso da instalação');
        track.setAttribute('aria-valuemin', '0'); track.setAttribute('aria-valuemax', '100'); track.setAttribute('aria-valuenow', String(p));
        const fill = el('span'); fill.style.width = p + '%'; track.append(fill);
        wrap.append(track, el('strong', '', p + '%')); return wrap;
    }
    function operational(batch) { return batch.status === 'RUNNING' ? `Instalando ${n(batch.current_index)} de ${n(batch.total_count)}` : batchLabels[batch.status] || 'Estado desconhecido'; }
    function button(label, name, callback) {
        const node = el('button', 'install-button'); node.type = 'button'; node.title = label;
        node.setAttribute('aria-label', label); node.append(icon(name)); node.addEventListener('click', callback); return node;
    }
    function date(value) { const d = new Date(value); return value && !Number.isNaN(d.getTime()) ? d.toLocaleString('pt-BR') : 'Aguardando início'; }
    function duration(batch) {
        if (!batch.started_at) return '—';
        const start = new Date(batch.started_at).getTime(), end = batch.finished_at ? new Date(batch.finished_at).getTime() : Date.now();
        if (!Number.isFinite(start) || !Number.isFinite(end)) return '—';
        const secs = Math.max(0, Math.floor((end - start) / 1000));
        return `${Math.floor(secs / 60)} min ${secs % 60}s`;
    }
    function reconcile(container, cache, records, draw) {
        const keep = new Set();
        for (const record of records) {
            if (!api.UUID.test(record.id)) continue;
            keep.add(record.id);
            let row = cache.get(record.id);
            if (!row) { row = el('tr'); cache.set(record.id, row); }
            const signature = JSON.stringify(record);
            if (row.__signature !== signature) {
                const focused = row.contains(document.activeElement) ? document.activeElement.getAttribute('aria-label') : null;
                draw(row, record); row.__signature = signature;
                if (focused) [...row.querySelectorAll('button')].find(b => b.getAttribute('aria-label') === focused)?.focus({ preventScroll: true });
            }
        }
        for (const [id, row] of cache) if (!keep.has(id)) { row.remove(); cache.delete(id); }
        // Insert only moved/new rows; unchanged rows retain focus and DOM identity.
        records.filter(r => keep.has(r.id)).forEach((r, index) => {
            const row = cache.get(r.id);
            if (container.children[index] !== row) container.insertBefore(row, container.children[index] || null);
        });
    }
    function drawBatch(row, batch) {
        row.replaceChildren(); row.className = 'install-batch-row';
        row.onclick = () => openDrawer(batch.id);
        const title = el('td'); title.append(el('strong', '', 'Instalação NightOwl'));
        if (batch.parent_batch_id) title.append(el('small', 'install-muted', 'Retry de lote anterior'));
        title.append(button('Abrir detalhes da instalação', 'panel-right-open', event => { event.stopPropagation(); openDrawer(batch.id); }));
        const failures = el('td', '', n(batch.failure_count) + n(batch.review_count));
        failures.title = `${n(batch.failure_count)} falhas · ${n(batch.review_count)} requer revisão`;
        if (n(batch.review_count)) failures.append(el('small', 'install-review-text', `${n(batch.review_count)} requer revisão`));
        const status = el('td'); status.append(badge(batch.status, batchLabels), el('small', '', operational(batch)), progress(batch.progress_percentage, batch.status));
        row.append(title, el('td', '', n(batch.total_count)), el('td', 'install-success-text', n(batch.success_count)), failures, status);
    }
    async function refreshList() {
        if (listRequestInFlight) return;
        window.clearTimeout(listTimer); listRequestInFlight = true;
        $('install-list-refresh').setAttribute('aria-busy', 'true');
        try {
            const payload = await api.request(api.BASE + '?page=' + page);
            if (!Array.isArray(payload.results)) throw new Error();
            if (!listRows.size) $('install-batch-rows').replaceChildren();
            reconcile($('install-batch-rows'), listRows, payload.results, drawBatch);
            if (!payload.results.length) {
                const row = el('tr'), cell = el('td', 'install-muted', 'Nenhuma instalação em lote registrada.'); cell.colSpan = 5; row.append(cell);
                $('install-batch-rows').replaceChildren(row);
            }
            page = n(payload.page) || 1; pages = n(payload.pages) || 1;
            $('install-list-page').textContent = `Página ${page} de ${pages}`;
            $('install-list-prev').disabled = page <= 1; $('install-list-next').disabled = page >= pages;
            // The global admission allows one active batch; newest-first page 1 owns the tab counter.
            if (page === 1) {
                const active = payload.results.filter(b => ['QUEUED', 'RUNNING'].includes(b.status)).length;
                $('install-active-count').hidden = !active; $('install-active-count').textContent = String(active);
            }
            $('install-list-feedback').textContent = ''; listFailures = 0; icons();
        } catch (_) {
            listFailures++; $('install-list-feedback').textContent = 'Atualização temporariamente indisponível. Último estado preservado.';
        } finally {
            listRequestInFlight = false; $('install-list-refresh').setAttribute('aria-busy', 'false');
            listTimer = window.setTimeout(refreshList, document.visibilityState !== 'visible' ? 15000 : Math.min(10000, 2000 * (listFailures + 1)));
        }
    }
    function tooltip(trigger, item) {
        let tip, timer, pinned = false, restoring = false;
        const position = () => {
            if (!tip) return;
            tip.style.maxHeight = Math.max(0, window.innerHeight - 16) + 'px';
            const r = trigger.getBoundingClientRect(), t = tip.getBoundingClientRect();
            const below = Math.max(0, window.innerHeight - r.bottom - 14), above = Math.max(0, r.top - 14);
            const useBelow = t.height <= below || (t.height > above && below >= above);
            tip.style.maxHeight = (useBelow ? below : above) + 'px';
            tip.style.left = Math.max(8, Math.min(window.innerWidth - t.width - 8, r.left)) + 'px';
            tip.style.top = (useBelow ? r.bottom + 6 : Math.max(8, r.top - tip.getBoundingClientRect().height - 6)) + 'px';
        };
        const hide = () => { window.clearTimeout(timer); window.removeEventListener('resize', position); tip?.remove(); tip = null; pinned = false; trigger.removeAttribute('aria-controls'); trigger.setAttribute('aria-expanded', 'false'); if (closeDiagnostic === hide) closeDiagnostic = null; };
        const restore = () => { hide(); restoring = true; trigger.focus(); restoring = false; };
        const leave = () => {
            window.clearTimeout(timer);
            timer = window.setTimeout(() => {
                if (!pinned && !tip?.matches(':hover') && !tip?.contains(document.activeElement) && document.activeElement !== trigger) hide();
            }, 300);
        };
        const details = () => {
            const d = item.diagnostic;
            const lines = [`Computador: ${item.hostname || item.fqdn}`, `FQDN: ${item.fqdn}`,
                `Resultado da tarefa: ${item.status}`, `Etapa da tarefa: ${item.stage || 'UNKNOWN'}`,
                `Codigo da tarefa: ${item.error_code || 'NONE'}`, `Job: ${item.job_id || 'Nao criado'}`];
            if (d) {
                for (const [label, key] of [['Correlacao', 'correlation_id'], ['Lote', 'batch_id'],
                    ['FQDN solicitado', 'requested_fqdn'], ['IP resolvido', 'resolved_ip'],
                    ['Inicio UTC', 'started_at'], ['Fim UTC', 'finished_at'], ['Fase', 'phase'], ['Resultado do diagnostico', 'result'],
                    ['Etapa do diagnostico', 'stage'], ['Codigo do diagnostico', 'code'], ['Tipo da excecao', 'exception_type']]) {
                    lines.push(`${label}: ${d[key] || 'Nao disponivel'}`);
                }
                for (const [name, check] of Object.entries(d.checks || {})) {
                    lines.push(`${name}: ${check.status}${check.code ? ' (' + check.code + ')' : ''}`);
                }
            } else lines.push('Sem diagnostico detalhado persistido para esta tentativa antiga.');
            return lines.join('\n');
        };
        const show = () => {
            window.clearTimeout(timer);
            if (tip?.isConnected) return;
            closeDiagnostic?.(); hide(); closeDiagnostic = hide;
            tip = el('div', 'install-batch-tooltip'); tip.id = 'install-error-tooltip'; tip.setAttribute('role', 'dialog');
            tip.setAttribute('aria-label', 'Diagnóstico da tentativa');
            tip.append(el('strong', '', item.error_message || 'Resultado não comprovado; revisão necessária.'), el('small', '', item.error_code || ''));
            const text = el('textarea', 'install-diagnostic-text'); text.readOnly = true; text.value = details();
            text.setAttribute('aria-label', 'Diagnóstico copiável');
            const copy = button('Copiar diagnóstico', 'copy', async () => {
                try { await navigator.clipboard.writeText(text.value); copy.title = 'Diagnóstico copiado'; }
                catch (_) { text.focus(); text.select(); document.execCommand('copy'); }
            });
            const close = button('Fechar diagnóstico', 'x', restore);
            const history = el('a', 'install-button'); history.href = '/admin/agents/auditevent/?q=' + encodeURIComponent(item.fqdn || '');
            history.title = 'Histórico no admin'; history.setAttribute('aria-label', history.title);
            history.target = '_blank'; history.rel = 'noopener'; history.append(icon('history'));
            const actions = el('div', 'install-diagnostic-actions'); actions.append(history, copy, close);
            tip.append(text, actions);
            tip.addEventListener('mouseenter', () => window.clearTimeout(timer));
            tip.addEventListener('mouseleave', leave);
            tip.addEventListener('focusout', leave);
            tip.addEventListener('keydown', event => { if (event.key === 'Escape') { event.stopPropagation(); restore(); } });
            document.body.append(tip); trigger.setAttribute('aria-controls', tip.id); trigger.setAttribute('aria-expanded', 'true');
            icons();
            position(); window.addEventListener('resize', position);
        };
        trigger.setAttribute('aria-haspopup', 'dialog'); trigger.setAttribute('aria-expanded', 'false');
        trigger.addEventListener('mouseenter', show); trigger.addEventListener('focus', () => { if (!restoring) show(); });
        trigger.addEventListener('mouseleave', leave); trigger.addEventListener('blur', leave);
        trigger.addEventListener('keydown', event => { if (event.key === 'Tab' && tip && !event.shiftKey) { event.preventDefault(); tip.querySelector('textarea').focus(); } });
        trigger.addEventListener('click', () => { show(); pinned = true; });
    }
    function drawItem(row, item) {
        row.replaceChildren();
        const identity = el('td'); identity.append(el('strong', '', item.hostname || item.fqdn), el('small', 'install-muted', item.fqdn));
        const status = el('td'); status.append(badge(item.status, itemLabels));
        if (['FAILED', 'REVIEW_REQUIRED'].includes(item.status)) {
            const info = button('Informações sobre o resultado', 'info', () => {}); tooltip(info, item); status.append(info);
        }
        const stage = el('td', '', item.error_message || item.progress_message);
        if (item.status === 'REVIEW_REQUIRED') stage.append(el('small', 'install-review-text', 'Resultado não comprovado. Reconciliação necessária antes de uma nova tentativa.'));
        const p = el('td'); p.append(progress(item.progress_percentage, item.status));
        const action = el('td');
        if (item.is_stalled && currentBatch.manual_stall_action_available) {
            action.append(el('small', 'install-review-text', `Runner sem atualização há ${n(item.stale_seconds)}s · intervenção manual disponível`));
            const confirm = el('button', 'install-button', confirmation === item.id ? 'Confirmar e seguir' : 'Marcar como travada e seguir'); confirm.type = 'button'; confirm.disabled = actionInFlight;
            confirm.setAttribute('aria-label', confirm.textContent);
            confirm.setAttribute('data-stall-confirm', '');
            confirm.addEventListener('click', async () => {
                if (confirmation !== item.id) {
                    confirmation = item.id; drawItem(row, item);
                    row.querySelector('[data-stall-confirm]')?.focus({ preventScroll: true });
                    return;
                }
                if (actionInFlight) return;
                actionInFlight = true; confirm.disabled = true;
                try { await api.request(api.url(currentId, 'mark-stalled/'), { item_id: item.id }); toast('Resultado registrado. O backend seguirá o lote.'); }
                catch (error) { toast(error.message); }
                finally { actionInFlight = false; confirmation = null; refreshDetail(); refreshList(); }
            });
            action.append(confirm);
            if (confirmation === item.id) {
                action.append(el('small', 'install-muted', 'O resultado pode exigir revisão; isto não cancela um instalador já iniciado.'));
                const cancel = el('button', 'install-button', 'Cancelar'); cancel.type = 'button';
                cancel.setAttribute('aria-label', 'Cancelar intervenção');
                cancel.addEventListener('click', () => { confirmation = null; drawItem(row, item); }); action.append(cancel);
            }
        }
        row.append(identity, status, stage, p, action);
    }
    function drawDetail(batch) {
        if (document.querySelector('.install-batch-tooltip')) return;
        currentBatch = batch;
        $('install-detail-meta').textContent = `${date(batch.started_at)} · ${duration(batch)} · ${n(batch.total_count)} computadores`;
        const summary = $('install-detail-summary'); summary.replaceChildren();
        const counts = el('div', 'install-counts');
        for (const [label, value, cls] of [['Total', batch.total_count, ''], ['Sucesso', batch.success_count, 'success'], ['Falhas', batch.failure_count, 'failed'], ['Revisão', batch.review_count, 'review']]) {
            const fact = el('div', 'install-count ' + cls); fact.append(el('span', '', label), el('strong', '', n(value))); counts.append(fact);
        }
        summary.append(badge(batch.status, batchLabels), counts,
            el('strong', 'install-overall-label', operational(batch)),
            el('p', 'install-muted', `${n(batch.total_count) - n(batch.waiting_count)} de ${n(batch.total_count)} processados`), progress(batch.progress_percentage, batch.status));
        if (terminal(batch.status)) summary.append(el('p', 'install-final-message', batch.status === 'COMPLETED' ?
            `Instalação concluída. ${n(batch.success_count)} de ${n(batch.total_count)} computadores gerenciados.` :
            `Instalação concluída com pendências: ${n(batch.success_count)} concluídos, ${n(batch.failure_count)} falhas, ${n(batch.review_count)} requer revisão.`));
        reconcile($('install-item-rows'), itemRows, batch.items, drawItem);
        $('install-detail-footer').hidden = !terminal(batch.status) || !batch.items.some(i => i.retry_eligible || i.reconciliation_required);
        icons();
    }
    async function refreshDetail() {
        if (!currentId) return;
        if (document.querySelector('.install-batch-tooltip')) { detailTimer = window.setTimeout(refreshDetail, 2000); return; }
        if (detailRequestInFlight) { detailPending = true; return; }
        window.clearTimeout(detailTimer); detailRequestInFlight = true; detailPending = false;
        const id = currentId;
        $('install-detail-refresh').setAttribute('aria-busy', 'true');
        try {
            const batch = await api.request(api.url(id));
            if (!Array.isArray(batch.items) || batch.id !== id) throw new Error();
            if (currentId === id) { drawDetail(batch); $('install-detail-feedback').textContent = ''; detailFailures = 0; }
        } catch (_) {
            if (currentId === id) { detailFailures++; $('install-detail-feedback').textContent = 'Atualização temporariamente indisponível. Último estado preservado.'; }
        } finally {
            detailRequestInFlight = false; $('install-detail-refresh').setAttribute('aria-busy', 'false');
            if (currentId) {
                if (detailPending || currentId !== id) refreshDetail();
                else detailTimer = window.setTimeout(refreshDetail, document.visibilityState !== 'visible' ? 15000 : Math.min(10000, 2000 * (detailFailures + 1)));
            }
        }
    }
    function openDrawer(id) {
        if (!api.UUID.test(id)) return;
        if (drawer.hidden) previousFocus = document.activeElement;
        if (currentId !== id) {
            itemRows.clear(); $('install-item-rows').replaceChildren(); $('install-detail-summary').replaceChildren();
            $('install-detail-footer').hidden = true; $('install-detail-meta').textContent = ''; currentBatch = null;
        }
        currentId = id; confirmation = null;
        drawer.hidden = $('install-batch-backdrop').hidden = false;
        document.body.classList.add('drawer-open'); $('install-detail-close').focus();
        refreshDetail();
    }
    function closeDrawer() {
        if ($('install-credential-host').hasChildNodes()) return;
        currentId = null; currentBatch = null; confirmation = null; window.clearTimeout(detailTimer);
        drawer.hidden = $('install-batch-backdrop').hidden = true; document.body.classList.remove('drawer-open');
        closeDiagnostic?.(); previousFocus?.focus();
    }
    function toast(message) { $('install-action-feedback').textContent = message; $('install-action-feedback').hidden = false; }
    $('install-list-refresh').addEventListener('click', refreshList);
    $('install-detail-refresh').addEventListener('click', refreshDetail);
    $('install-detail-close').addEventListener('click', closeDrawer);
    $('install-batch-backdrop').addEventListener('click', closeDrawer);
    for (const [id, delta] of [['install-list-prev', -1], ['install-list-next', 1]]) $(id).addEventListener('click', () => {
        if (listRequestInFlight) return; page = Math.min(pages, Math.max(1, page + delta)); refreshList();
    });
    document.querySelector('[data-jobs-refresh]')?.addEventListener('click', () => { refreshList(); if (currentId) refreshDetail(); });
    document.addEventListener('visibilitychange', () => { if (document.visibilityState === 'visible') { refreshList(); refreshDetail(); } });
    document.addEventListener('keydown', event => {
        if (drawer.hidden || $('install-credential-host').hasChildNodes()) return;
        if (event.key === 'Escape') closeDrawer();
        if (event.key === 'Tab') {
            const focusable = [...drawer.querySelectorAll('button')].filter(b => !b.disabled && b.getClientRects().length);
            const first = focusable[0], last = focusable[focusable.length - 1];
            if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
            else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
        }
    });
    $('install-batch-retry').addEventListener('click', () => {
        if (!currentBatch || !terminal(currentBatch.status) || !currentBatch.items.some(i => i.retry_eligible || i.reconciliation_required)) return;
        const id = currentId;
        api.modal({ title: 'Repetir falhas', description: 'O backend selecionará as falhas elegíveis. Resultados não comprovados exigem nova prova de ausência do NightOwl.', submitLabel: 'Repetir falhas',
            perform: credentials => api.request(api.url(id, 'retry/'), credentials),
            accepted: newId => { toast('Retry iniciado'); page = 1; refreshList(); openDrawer(newId); },
        });
    });
    refreshList();
    const initial = new URLSearchParams(window.location.search).get('batch');
    if (api.UUID.test(initial || '')) openDrawer(initial);
}());
