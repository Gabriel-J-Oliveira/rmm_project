document.addEventListener('DOMContentLoaded', () => {
    const root = document.getElementById('ad-discovery');
    if (!root) return;

    const form = document.getElementById('ad-discovery-form');
    const scanButton = document.getElementById('ad-scan-button');
    const status = document.getElementById('ad-scan-status');
    const results = document.getElementById('ad-discovery-results');
    const metrics = document.getElementById('ad-discovery-metrics');
    const table = document.getElementById('ad-computer-rows');
    const search = document.getElementById('ad-search');
    const ouFilter = document.getElementById('ad-ou-filter');
    const osFilter = document.getElementById('ad-os-filter');
    const visibleCount = document.getElementById('ad-visible-count');
    const selectedCount = document.getElementById('ad-selected-count');
    const selectVisible = document.getElementById('ad-select-visible');
    const prepareButton = document.getElementById('ad-prepare-button');
    const drawer = document.getElementById('ad-detail-drawer');
    const backdrop = document.getElementById('ad-detail-backdrop');
    const drawerTitle = document.getElementById('ad-detail-title');
    const drawerContent = document.getElementById('ad-detail-content');
    const closeButton = document.getElementById('ad-detail-close');
    let computers = [];
    let filter = 'enabled';
    const selected = new Set();

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
        return computers.map((computer, index) => ({ computer, index })).filter(({ computer }) => {
            const matches = {
                enabled: computer.enabled === true,
                unmanaged: computer.correlation_status === 'UNMANAGED',
                managed: computer.correlation_status === 'MANAGED',
                'no-dns': computer.dns_status !== 'RESOLVED',
                disabled: computer.enabled === false,
                conflict: computer.correlation_status === 'CONFLICT',
                old: computer.ad_activity_age === '>365d',
                all: true,
            };
            if (!matches[filter] || (ouFilter.value && computer.ou_dn !== ouFilter.value) ||
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
        if (computer.correlation_status === 'UNMANAGED') return 'NightOwl não instalado';
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

    function updateSelection(visible) {
        selectedCount.textContent = `${selected.size} selecionado${selected.size === 1 ? '' : 's'}`;
        prepareButton.disabled = selected.size === 0;
        const eligible = visible.filter(({ computer }) => computer.selectable);
        selectVisible.disabled = eligible.length === 0;
        selectVisible.checked = eligible.length > 0 && eligible.every(({ index }) => selected.has(index));
        selectVisible.indeterminate = eligible.some(({ index }) => selected.has(index)) && !selectVisible.checked;
    }

    function render() {
        const visible = visibleItems();
        table.replaceChildren();
        visibleCount.textContent = `${visible.length} de ${computers.length} computadores`;
        for (const { computer, index } of visible) {
            const tr = element('tr');
            const selectCell = element('td');
            const checkbox = element('input');
            checkbox.type = 'checkbox';
            checkbox.disabled = !computer.selectable;
            checkbox.checked = selected.has(index);
            checkbox.setAttribute('aria-label', `Selecionar ${computer.hostname || computer.fqdn}`);
            checkbox.title = computer.selectable ? 'Selecionar' : 'Somente computadores habilitados, sem NightOwl e sem conflito';
            checkbox.addEventListener('change', () => {
                if (checkbox.checked) selected.add(index);
                else selected.delete(index);
                updateSelection(visible);
            });
            selectCell.appendChild(checkbox);
            tr.appendChild(selectCell);
            const nameCell = cell(tr, computer.hostname, computer.fqdn || computer.ad_name);
            const detailButton = element('button', 'ad-detail-link', 'Detalhes');
            detailButton.type = 'button';
            detailButton.addEventListener('click', () => showComputer(computer));
            nameCell.appendChild(detailButton);
            cell(tr, computer.primary_ipv4, computer.dns_status !== 'RESOLVED' ? 'Sem DNS' : '');
            cell(tr, computer.operating_system, computer.operating_system_version);
            cell(tr, computer.ou, computer.enabled === false ? 'Desabilitado no AD' : '');
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
            } else if (computer.selectable) {
                const install = element('button', 'ad-install-unavailable', 'Instalar');
                install.type = 'button';
                install.disabled = true;
                install.title = 'Deploy remoto será habilitado na próxima etapa';
                action.appendChild(install);
            } else {
                action.appendChild(element('span', '', '—'));
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
        updateSelection(visible);
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
        const first = select.firstElementChild;
        select.replaceChildren(first);
        for (const value of [...new Set(values.filter(Boolean))].sort((a, b) => a.localeCompare(b))) {
            const option = element('option', '', value);
            option.value = value;
            select.appendChild(option);
        }
    }

    form.addEventListener('submit', async (event) => {
        event.preventDefault();
        scanButton.disabled = true;
        status.textContent = 'Buscando computadores do domínio...';
        try {
            const response = await fetch(root.dataset.scanUrl, {
                method: 'POST', credentials: 'same-origin',
                headers: { 'X-CSRFToken': form.querySelector('[name=csrfmiddlewaretoken]').value },
            });
            if (!response.ok) throw new Error('scan failed');
            const data = await response.json();
            computers = data.computers || [];
            selected.clear();
            filter = 'enabled';
            search.value = '';
            fillOptions(ouFilter, computers.map((computer) => computer.ou_dn));
            fillOptions(osFilter, computers.map((computer) => computer.operating_system));
            document.querySelectorAll('[data-ad-filter]').forEach((button) => {
                button.setAttribute('aria-pressed', button.dataset.adFilter === filter ? 'true' : 'false');
            });
            const labels = [
                ['Computadores AD', data.summary.total], ['Gerenciados', data.summary.managed],
                ['Sem NightOwl', data.summary.unmanaged], ['Desabilitados', data.summary.disabled],
                ['Sem DNS', data.summary.dns_unresolved], ['Conflitos', data.summary.conflicts],
                ['Atividade AD >365d', data.summary.old_ad_activity],
            ];
            metrics.replaceChildren();
            for (const [label, value] of labels) {
                const card = element('article', 'metric-card');
                card.appendChild(element('span', 'metric-label', label));
                card.appendChild(element('strong', 'metric-value', value));
                metrics.appendChild(card);
            }
            results.hidden = false;
            status.textContent = `${computers.length} computadores encontrados.`;
            render();
            window.lucide?.createIcons?.();
        } catch (error) {
            status.textContent = 'Não foi possível concluir o scan do Active Directory.';
        } finally {
            scanButton.disabled = false;
        }
    });

    for (const input of [search, ouFilter, osFilter]) input.addEventListener('input', render);
    document.querySelectorAll('[data-ad-filter]').forEach((button) => button.addEventListener('click', () => {
        filter = button.dataset.adFilter;
        document.querySelectorAll('[data-ad-filter]').forEach((item) => item.setAttribute('aria-pressed', item === button ? 'true' : 'false'));
        render();
    }));
    selectVisible.addEventListener('change', () => {
        for (const { computer, index } of visibleItems()) {
            if (computer.selectable) {
                if (selectVisible.checked) selected.add(index);
                else selected.delete(index);
            }
        }
        render();
    });
    prepareButton.addEventListener('click', () => {
        drawerContent.replaceChildren();
        const selectedNames = [...selected].map((index) => computers[index].hostname);
        section('Seleção', [['Computadores', selectedNames.join(', ')]]);
        drawerContent.appendChild(element('p', 'ad-preview-note', 'Deploy remoto será habilitado na próxima etapa.'));
        openDrawer(`${selected.size} selecionado${selected.size === 1 ? '' : 's'}`);
    });
    function closeDrawer() { drawer.hidden = true; backdrop.hidden = true; }
    closeButton.addEventListener('click', closeDrawer);
    backdrop.addEventListener('click', closeDrawer);
    document.addEventListener('keydown', (event) => { if (event.key === 'Escape' && !drawer.hidden) closeDrawer(); });
});
