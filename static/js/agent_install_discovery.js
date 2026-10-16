document.addEventListener('DOMContentLoaded', () => {
    const root = document.getElementById('ad-discovery');
    if (!root) return;

    const form = document.getElementById('ad-discovery-form');
    const scanButton = document.getElementById('ad-scan-button');
    const status = document.getElementById('ad-scan-status');
    const results = document.getElementById('ad-discovery-results');
    const lastUpdated = document.getElementById('ad-last-updated');
    const kpis = [
        document.getElementById('ad-kpi-total'),
        document.getElementById('ad-kpi-unmanaged'),
        document.getElementById('ad-kpi-managed'),
    ];
    const table = document.getElementById('ad-computer-rows');
    const search = document.getElementById('ad-search');
    const ouFilter = document.getElementById('ad-ou-filter');
    const osFilter = document.getElementById('ad-os-filter');
    const stateFilter = document.getElementById('ad-state-filter');
    const agentFilter = document.getElementById('ad-agent-filter');
    const dnsFilter = document.getElementById('ad-dns-filter');
    const onlineFilter = document.getElementById('ad-online-filter');
    const activityFilter = document.getElementById('ad-activity-filter');
    const filters = [search, ouFilter, osFilter, stateFilter, agentFilter, dnsFilter, onlineFilter, activityFilter];
    const selectPage = document.getElementById('ad-select-page');
    const clearSelection = document.getElementById('ad-clear-selection');
    const hiddenSelection = document.getElementById('ad-hidden-selection');
    const visibleCount = document.getElementById('ad-visible-count');
    const selectedCount = document.getElementById('ad-selected-count');
    const selectionNote = document.getElementById('ad-selection-note');
    const prepareButton = document.getElementById('ad-prepare-button');
    const pageSummary = document.getElementById('ad-page-summary');
    const pageSizeInput = document.getElementById('ad-page-size');
    const pageNumbers = document.getElementById('ad-page-numbers');
    const pagePrev = document.getElementById('ad-page-prev');
    const pageNext = document.getElementById('ad-page-next');
    const drawer = document.getElementById('ad-detail-drawer');
    const backdrop = document.getElementById('ad-detail-backdrop');
    const drawerTitle = document.getElementById('ad-detail-title');
    const drawerContent = document.getElementById('ad-detail-content');
    const closeButton = document.getElementById('ad-detail-close');
    let computers = [];
    let page = 1;
    let scanInFlight = false;
    let drawerFocus = null;
    const selected = new Set();

    function selectionKey(computer) {
        return String(computer.sid || '').toLowerCase();
    }

    function isEligible(computer) {
        return Boolean(selectionKey(computer)) && computer.selectable === true && computer.enabled === true &&
            computer.correlation_status === 'UNMANAGED';
    }

    function unavailableReason(computer) {
        if (computer.correlation_status === 'MANAGED') return 'Já gerenciado';
        if (computer.correlation_status === 'CONFLICT') return 'Correlação ambígua';
        if (computer.enabled === false) return 'Desabilitado no AD';
        if (computer.enabled !== true) return 'Situação AD desconhecida';
        if (!selectionKey(computer)) return 'SID do objeto AD indisponível';
        return 'Indisponível para instalação';
    }

    function element(tag, className, value) {
        const node = document.createElement(tag);
        if (className) node.className = className;
        if (value !== undefined && value !== null) node.textContent = String(value);
        return node;
    }

    function date(value) {
        if (!value) return '—';
        const parsed = new Date(value);
        return Number.isNaN(parsed.getTime()) ? '—' : parsed.toLocaleString('pt-BR');
    }

    function visibleItems() {
        const query = search.value.trim().toLocaleLowerCase();
        return computers.filter((computer) => {
            const adState = computer.enabled === true ? 'enabled' : computer.enabled === false ? 'disabled' : 'unknown';
            const agent = ['MANAGED', 'UNMANAGED', 'CONFLICT'].includes(computer.correlation_status) ? computer.correlation_status : 'unknown';
            const dns = ({ RESOLVED: 'resolved', UNRESOLVED: 'unresolved', ERROR: 'error' })[computer.dns_status] || 'unknown';
            const online = ['online', 'offline'].includes(computer.nightowl_status) ? computer.nightowl_status : 'unknown';
            const activity = computer.ad_activity_age === '>365d' ? 'old' : computer.ad_activity_age === 'unknown' ? 'unknown' : 'recent';
            if ((stateFilter.value && stateFilter.value !== adState) ||
                (agentFilter.value && agentFilter.value !== agent) ||
                (dnsFilter.value && (dnsFilter.value === 'missing' ? dns === 'resolved' : dnsFilter.value !== dns)) ||
                (onlineFilter.value && onlineFilter.value !== online) ||
                (activityFilter.value && activityFilter.value !== activity) ||
                (ouFilter.value && computer.ou_dn !== ouFilter.value) ||
                (osFilter.value && computer.operating_system !== osFilter.value)) return false;
            const haystack = [computer.hostname, computer.fqdn, computer.primary_ipv4,
                ...(computer.ipv4_addresses || []), computer.reported_username,
                computer.ad_user?.display_name, computer.ad_user?.sam_account_name]
                .filter(Boolean).join(' ').toLocaleLowerCase();
            return !query || haystack.includes(query);
        });
    }

    function statusLabel(computer) {
        if (computer.correlation_status === 'CONFLICT') return 'Correlação ambígua';
        if (computer.correlation_status === 'UNMANAGED') return 'Sem NightOwl correlacionado';
        const labels = { online: 'Online', offline: 'Offline', unknown: 'Desconhecido', uninstalled: 'Desinstalado' };
        return labels[computer.nightowl_status] || 'Desconhecido';
    }

    function cell(row, primary, secondary, title) {
        const td = element('td');
        td.appendChild(element('strong', '', primary || '—'));
        if (secondary) td.appendChild(element('small', '', secondary));
        if (title) td.title = title;
        row.appendChild(td);
        return td;
    }

    function updateSelection(filtered, visible) {
        selectedCount.textContent = `${selected.size} selecionado${selected.size === 1 ? '' : 's'}`;
        prepareButton.disabled = selected.size < 1;
        prepareButton.textContent = selected.size === 0 ? 'Selecione computadores' :
            `Instalar selecionado${selected.size === 1 ? '' : 's'} (${selected.size})`;
        clearSelection.disabled = selected.size === 0;
        const countSelected = items => new Set(items.map(selectionKey).filter(key => selected.has(key))).size;
        hiddenSelection.textContent = `${selected.size - countSelected(visible)} fora desta página; ${selected.size - countSelected(filtered)} fora do filtro atual`;
        const eligible = [...new Set(visible.filter(isEligible).map(selectionKey))];
        const checked = eligible.filter(key => selected.has(key)).length;
        selectPage.disabled = eligible.length === 0;
        selectPage.checked = eligible.length > 0 && checked === eligible.length;
        selectPage.indeterminate = checked > 0 && checked < eligible.length;
    }

    function renderPagination(total, pages, pageSize) {
        const first = total ? (page - 1) * pageSize + 1 : 0;
        pageSummary.textContent = `Mostrando ${first}-${Math.min(page * pageSize, total)} de ${total}`;
        pagePrev.disabled = page <= 1;
        pageNext.disabled = page >= pages;
        pageNumbers.replaceChildren();
        const pageSet = new Set([1, pages, page - 1, page, page + 1]);
        let previous = 0;
        for (const number of [...pageSet].filter((value) => value >= 1 && value <= pages).sort((a, b) => a - b)) {
            if (number > previous + 1) pageNumbers.appendChild(element('span', 'ad-page-ellipsis', '…'));
            const button = element('button', '', number);
            button.type = 'button';
            button.setAttribute('aria-label', `Página ${number}`);
            if (number === page) button.setAttribute('aria-current', 'page');
            button.addEventListener('click', () => { page = number; render(); });
            pageNumbers.appendChild(button);
            previous = number;
        }
    }

    function render() {
        const filtered = visibleItems();
        const pageSize = Number(pageSizeInput.value) || 10;
        const pages = Math.max(1, Math.ceil(filtered.length / pageSize));
        page = Math.min(page, pages);
        const visible = filtered.slice((page - 1) * pageSize, page * pageSize);
        table.replaceChildren();
        visibleCount.textContent = `${filtered.length} de ${computers.length} computadores`;
        for (const computer of visible) {
            const tr = element('tr');
            const selectCell = element('td');
            const checkbox = element('input');
            checkbox.type = 'checkbox';
            checkbox.disabled = !isEligible(computer);
            checkbox.checked = selected.has(selectionKey(computer));
            checkbox.setAttribute('aria-label', `Selecionar ${computer.hostname || computer.fqdn}`);
            checkbox.title = isEligible(computer) ? 'Selecionar' : unavailableReason(computer);
            checkbox.addEventListener('change', () => {
                if (!isEligible(computer)) return;
                selectionNote.textContent = '';
                if (checkbox.checked) {
                    if (selected.size >= 50 && !selected.has(selectionKey(computer))) {
                        checkbox.checked = false;
                        selectionNote.textContent = 'Um lote pode conter no máximo 50 computadores.';
                        return;
                    }
                    selected.add(selectionKey(computer));
                }
                else selected.delete(selectionKey(computer));
                render();
            });
            selectCell.appendChild(checkbox);
            if (!isEligible(computer)) selectCell.appendChild(element('small', 'ad-selection-reason', unavailableReason(computer)));
            tr.appendChild(selectCell);
            const nameCell = cell(tr, computer.hostname, computer.fqdn || computer.ad_name);
            const detailButton = element('button', 'ad-detail-link', 'Detalhes');
            detailButton.type = 'button';
            detailButton.addEventListener('click', () => showComputer(computer));
            nameCell.appendChild(detailButton);
            cell(tr, computer.primary_ipv4, computer.dns_status !== 'RESOLVED' ? 'Sem DNS' : '');
            cell(tr, computer.operating_system, computer.operating_system_version);
            cell(tr, computer.ou, computer.enabled === false ? 'Desabilitado no AD' : computer.enabled !== true ? 'Situação AD desconhecida' : '');
            cell(tr, computer.ad_user?.display_name || computer.reported_username || 'Não identificado',
                computer.ad_user?.sam_account_name || '', computer.reported_user_source || '');
            cell(tr, date(computer.last_logon_at), computer.ad_activity_age === '>365d' ? 'Atividade AD antiga' : '');
            cell(tr, statusLabel(computer), computer.correlation_status === 'MANAGED' ? computer.correlation_method : '');
            cell(tr, computer.agent_version);
            cell(tr, date(computer.last_seen));
            const action = element('td');
            if (computer.endpoint_url) {
                const link = element('a', 'ad-endpoint-link', 'Abrir endpoint');
                link.href = computer.endpoint_url;
                action.appendChild(link);
            } else if (isEligible(computer)) {
                const prepare = element('button', 'agent-secondary-button', 'Instalar');
                prepare.type = 'button';
                prepare.addEventListener('click', () => {
                    selected.clear();
                    selected.add(selectionKey(computer));
                    render();
                    prepareButton.click();
                });
                action.appendChild(prepare);
            } else {
                action.appendChild(element('span', '', unavailableReason(computer)));
            }
            tr.appendChild(action);
            table.appendChild(tr);
        }
        if (!visible.length) {
            const tr = element('tr');
            const td = element('td', '', 'Nenhum computador para este filtro.');
            td.colSpan = 11;
            tr.appendChild(td);
            table.appendChild(tr);
        }
        updateSelection(filtered, visible);
        renderPagination(filtered.length, pages, pageSize);
    }

    function section(title, values) {
        const group = element('section', 'ad-detail-section');
        group.appendChild(element('h3', '', title));
        const dl = element('dl');
        for (const [label, value] of values) {
            dl.appendChild(element('dt', '', label));
            dl.appendChild(element('dd', '', value || '—'));
        }
        group.appendChild(dl);
        drawerContent.appendChild(group);
    }

    function openDrawer(title) {
        drawerFocus = document.activeElement;
        document.body.classList.add('drawer-open');
        drawerTitle.textContent = title;
        drawer.hidden = false;
        backdrop.hidden = false;
        closeButton.focus();
    }

    function showComputer(computer) {
        drawerContent.replaceChildren();
        section('Active Directory', [
            ['FQDN', computer.fqdn], ['Nome AD', computer.ad_name], ['OU', computer.ou_dn],
            ['Estado', computer.enabled === true ? 'Habilitado' : computer.enabled === false ? 'Desabilitado' : 'Desconhecido'],
            ['Sistema', [computer.operating_system, computer.operating_system_version].filter(Boolean).join(' ')],
            ['Última atividade AD', date(computer.last_logon_at)],
        ]);
        section('Rede', [['DNS', computer.dns_status], ['IPv4', (computer.ipv4_addresses || []).join(', ')]]);
        section('NightOwl', [['Correlação', statusLabel(computer)], ['Método', computer.correlation_method],
            ['Agent', computer.agent_version], ['Último contato', date(computer.last_seen)]]);
        section('Usuário reportado', [['Username', computer.reported_username],
            ['Nome', computer.ad_user?.display_name], ['Email', computer.ad_user?.email],
            ['OU', computer.ad_user?.ou], ['Fonte', computer.reported_user_source]]);
        if (computer.endpoint_url) {
            const link = element('a', 'ad-endpoint-link', 'Abrir endpoint');
            link.href = computer.endpoint_url;
            drawerContent.appendChild(link);
        }
        openDrawer(computer.hostname || computer.fqdn || 'Computador');
    }

    function fillOptions(select, values) {
        const oldValue = select.value;
        const first = select.firstElementChild;
        select.replaceChildren(first);
        const options = [...new Set(values.filter(Boolean))].sort((a, b) => a.localeCompare(b));
        for (const value of options) {
            const option = element('option', '', value);
            option.value = value;
            select.appendChild(option);
        }
        select.value = options.includes(oldValue) ? oldValue : '';
    }

    async function scan() {
        if (scanInFlight) return;
        scanInFlight = true;
        scanButton.disabled = true;
        status.textContent = computers.length ? 'Atualizando computadores do domínio...' : 'Buscando computadores do domínio...';
        try {
            const response = await fetch(root.dataset.scanUrl, {
                method: 'POST', credentials: 'same-origin',
                headers: { 'X-CSRFToken': form.querySelector('[name=csrfmiddlewaretoken]').value },
            });
            if (!response.ok) throw new Error('scan failed');
            const data = await response.json();
            if (!Array.isArray(data.computers) || !data.summary) throw new Error('invalid scan');
            computers = data.computers;
            const sidCounts = new Map();
            const fqdnCounts = new Map();
            for (const computer of computers) {
                const key = selectionKey(computer);
                const fqdn = String(computer.fqdn || '').toLowerCase().replace(/\.$/, '');
                sidCounts.set(key, (sidCounts.get(key) || 0) + 1);
                fqdnCounts.set(fqdn, (fqdnCounts.get(fqdn) || 0) + 1);
            }
            for (const computer of computers) {
                const fqdn = String(computer.fqdn || '').toLowerCase().replace(/\.$/, '');
                if (!fqdn || sidCounts.get(selectionKey(computer)) !== 1 || fqdnCounts.get(fqdn) !== 1) computer.selectable = false;
            }
            const eligibleKeys = new Set(computers.filter(isEligible).map(selectionKey));
            const before = selected.size;
            for (const key of selected) if (!eligibleKeys.has(key)) selected.delete(key);
            selectionNote.textContent = before !== selected.size ? `${before - selected.size} seleção(ões) removida(s): alvo indisponível ou ambíguo.` : '';
            fillOptions(ouFilter, computers.map((computer) => computer.ou_dn));
            fillOptions(osFilter, computers.map((computer) => computer.operating_system));
            for (const [index, value] of [data.summary.total, data.summary.unmanaged, data.summary.managed].entries()) {
                kpis[index].textContent = String(value ?? 0);
                kpis[index].className = 'metric-value';
                kpis[index].setAttribute('aria-busy', 'false');
            }
            results.hidden = false;
            status.textContent = `${computers.length} computadores encontrados.`;
            lastUpdated.textContent = `Última atualização: ${new Date().toLocaleString('pt-BR')}`;
            render();
            window.lucide?.createIcons?.();
        } catch (error) {
            status.textContent = computers.length ?
                'Não foi possível atualizar. Os dados anteriores permanecem visíveis.' :
                'Não foi possível concluir o scan do Active Directory.';
        } finally {
            scanInFlight = false;
            scanButton.disabled = false;
        }
    }

    form.addEventListener('submit', (event) => { event.preventDefault(); scan(); });

    for (const input of filters) input.addEventListener('input', () => { page = 1; selectionNote.textContent = ''; render(); });
    document.getElementById('ad-clear-filters').addEventListener('click', () => {
        for (const input of filters) input.value = '';
        page = 1;
        render();
    });
    clearSelection.addEventListener('click', () => { selected.clear(); selectionNote.textContent = ''; render(); });
    selectPage.addEventListener('change', () => {
        const size = Number(pageSizeInput.value) || 10;
        const keys = [...new Set(visibleItems().slice((page - 1) * size, page * size).filter(isEligible).map(selectionKey))];
        const union = new Set([...selected, ...keys]);
        if (selectPage.checked && union.size > 50) {
            selectionNote.textContent = 'Esta página excede o limite de 50 selecionados. Nenhuma seleção foi alterada.';
        } else {
            for (const key of keys) { if (selectPage.checked) selected.add(key); else selected.delete(key); }
            selectionNote.textContent = '';
        }
        render();
    });
    pageSizeInput.addEventListener('change', () => { page = 1; render(); });
    pagePrev.addEventListener('click', () => { if (page > 1) { page--; render(); } });
    pageNext.addEventListener('click', () => { page++; render(); });
    prepareButton.addEventListener('click', () => {
        const targets = computers.filter(item => isEligible(item) && selected.has(selectionKey(item)))
            .map(item => ({ fqdn: item.fqdn, sid: item.sid, distinguished_name: item.distinguished_name }));
        if (!targets.length || targets.length > 50) return;
        window.NightOwlInstallBatch.modal({
            title: 'Instalar NightOwl',
            description: `${targets.length} computador${targets.length === 1 ? '' : 'es'} selecionado${targets.length === 1 ? '' : 's'}. O NightOwl verificará cada computador antes da instalação. Falhas no preflight serão registradas e o lote continuará.`,
            submitLabel: 'Confirmar instalação',
            perform: credentials => window.NightOwlInstallBatch.request(window.NightOwlInstallBatch.BASE, { targets, ...credentials }),
            accepted: id => { window.location.href = '/jobs/?tab=installations&batch=' + encodeURIComponent(id); },
        });
    });
    function closeDrawer() {
        drawer.hidden = true;
        backdrop.hidden = true;
        if (!document.getElementById('install-credential-host')?.hasChildNodes()) document.body.classList.remove('drawer-open');
        drawerFocus?.focus();
    }
    closeButton.addEventListener('click', closeDrawer);
    backdrop.addEventListener('click', closeDrawer);
    document.addEventListener('keydown', (event) => { if (event.key === 'Escape' && !drawer.hidden) closeDrawer(); });
    scan();
});
