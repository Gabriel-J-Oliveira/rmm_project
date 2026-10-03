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
    const visibleCount = document.getElementById('ad-visible-count');
    const selectedCount = document.getElementById('ad-selected-count');
    const selectionNote = document.getElementById('ad-selection-note');
    const selectVisible = document.getElementById('ad-select-visible');
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
    let filter = 'enabled';
    let page = 1;
    let scanInFlight = false;
    let installPollTimer = null;
    const selected = new Set();

    function selectionKey(computer) {
        return String(computer.distinguished_name || computer.fqdn ||
            `${computer.hostname || ''}|${computer.ou_dn || ''}`).toLocaleLowerCase();
    }

    function isEligible(computer) {
        return computer.selectable === true && computer.enabled === true &&
            computer.correlation_status === 'UNMANAGED';
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
        prepareButton.disabled = selected.size !== 1;
        selectionNote.textContent = selected.size > 1 ? 'Preflight individual nesta versão.' : '';
        const eligible = visible.filter(isEligible);
        selectVisible.disabled = eligible.length === 0;
        selectVisible.checked = eligible.length > 0 && eligible.every((computer) => selected.has(selectionKey(computer)));
        selectVisible.indeterminate = eligible.some((computer) => selected.has(selectionKey(computer))) && !selectVisible.checked;
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
            checkbox.title = isEligible(computer) ? 'Selecionar' : 'Somente computadores habilitados, sem NightOwl e sem conflito';
            checkbox.addEventListener('change', () => {
                if (!isEligible(computer)) return;
                if (checkbox.checked) selected.add(selectionKey(computer));
                else selected.delete(selectionKey(computer));
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
            } else if (isEligible(computer)) {
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
            const eligibleKeys = new Set(computers.filter(isEligible).map(selectionKey));
            for (const key of selected) if (!eligibleKeys.has(key)) selected.delete(key);
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

    for (const input of [search, ouFilter, osFilter]) input.addEventListener('input', () => { page = 1; render(); });
    document.querySelectorAll('[data-ad-filter]').forEach((button) => button.addEventListener('click', () => {
        filter = button.dataset.adFilter;
        page = 1;
        document.querySelectorAll('[data-ad-filter]').forEach((item) => item.setAttribute('aria-pressed', item === button ? 'true' : 'false'));
        render();
    }));
    pageSizeInput.addEventListener('change', () => { page = 1; render(); });
    pagePrev.addEventListener('click', () => { if (page > 1) { page--; render(); } });
    pageNext.addEventListener('click', () => { page++; render(); });
    selectVisible.addEventListener('change', () => {
        const pageSize = Number(pageSizeInput.value) || 10;
        for (const computer of visibleItems().slice((page - 1) * pageSize, page * pageSize)) {
            if (isEligible(computer)) {
                if (selectVisible.checked) selected.add(selectionKey(computer));
                else selected.delete(selectionKey(computer));
            }
        }
        render();
    });
    prepareButton.addEventListener('click', () => {
        drawerContent.replaceChildren();
        if (selected.size !== 1) {
            drawerContent.appendChild(element('p', 'ad-preview-note', 'Selecione um computador para o preflight.'));
            openDrawer('Preflight de instalação');
            return;
        }
        const computer = computers.find((item) => isEligible(item) && selectionKey(item) === [...selected][0]);
        if (!computer) return;
        section('Alvo', [['Computador', computer.hostname], ['FQDN', computer.fqdn],
            ['IP', computer.primary_ipv4 || 'Sem DNS'], ['SO', computer.operating_system]]);
        drawerContent.appendChild(element('p', 'ad-preview-note',
            'A credencial será utilizada somente nesta operação e não será armazenada pelo NightOwl.'));
        const preflightForm = element('form', 'ad-preflight-form');
        const userLabel = element('label', '', 'Usuário administrativo');
        const userInput = element('input');
        userInput.type = 'text';
        userInput.required = true;
        userInput.autocomplete = 'off';
        userLabel.appendChild(userInput);
        const passwordLabel = element('label', '', 'Senha');
        const passwordInput = element('input');
        passwordInput.type = 'password';
        passwordInput.required = true;
        passwordInput.autocomplete = 'off';
        passwordLabel.appendChild(passwordInput);
        const submit = element('button', '', 'Executar preflight');
        submit.type = 'submit';
        const feedback = element('p', 'ad-preflight-feedback');
        feedback.setAttribute('role', 'status');
        const checks = element('dl', 'ad-preflight-checks');
        preflightForm.append(userLabel, passwordLabel, submit, feedback, checks);
        let installButton = null;

        function showInstallConfirmation(preflight) {
            if (installButton) return;
            installButton = element('button', 'agent-secondary-button', 'Instalar NightOwl');
            installButton.type = 'button';
            drawerContent.appendChild(installButton);
            installButton.addEventListener('click', () => {
                installButton.remove();
                preflightForm.remove();
                section(`Instalar NightOwl em ${computer.hostname}?`, [
                    ['Hostname', computer.hostname], ['FQDN', computer.fqdn],
                    ['IP', preflight.target?.ip], ['Windows', preflight.windows?.name],
                    ['Status AD', 'Habilitado'], ['NightOwl', 'Não instalado'],
                ]);
                const installForm = element('form', 'ad-preflight-form');
                const installUserLabel = element('label', '', 'Usuário administrativo');
                const installUser = element('input');
                installUser.type = 'text';
                installUser.required = true;
                installUser.autocomplete = 'off';
                installUserLabel.appendChild(installUser);
                const installPasswordLabel = element('label', '', 'Senha');
                const installPassword = element('input');
                installPassword.type = 'password';
                installPassword.required = true;
                installPassword.autocomplete = 'off';
                installPasswordLabel.appendChild(installPassword);
                const confirm = element('button', '', 'Confirmar instalação');
                confirm.type = 'submit';
                const progress = element('p', 'ad-preflight-feedback');
                progress.setAttribute('role', 'status');
                installForm.append(installUserLabel, installPasswordLabel, confirm, progress);
                drawerContent.appendChild(installForm);

                async function poll(url) {
                    if (drawer.hidden) return;
                    try {
                        const response = await fetch(url, { credentials: 'same-origin', cache: 'no-store' });
                        if (!response.ok) throw new Error('status unavailable');
                        const job = await response.json();
                        const stages = {
                            QUEUED: 'Preparando', VALIDATING_TARGET: 'Validando alvo', CONNECTING: 'Conectando',
                            AUTHENTICATED: 'Autenticado',
                            PREFLIGHT_OK: 'Autenticado e validado', PREPARING_ENROLLMENT: 'Preparando enrollment',
                            INSTALLING: 'Instalando agente', VALIDATING_SERVICE: 'Validando serviço',
                            WAITING_ENROLLMENT: 'Aguardando enrollment', WAITING_HEARTBEAT: 'Aguardando heartbeat',
                        };
                        const failures = {
                            WINRM_UNAVAILABLE: 'WinRM HTTPS indisponível', AUTHENTICATION_FAILED: 'Credencial inválida',
                            ADMIN_REQUIRED: 'Credencial sem privilégio administrativo', INSTALLER_FAILED: 'Instalador retornou falha',
                            SERVICE_NOT_RUNNING: 'Serviço não iniciou', ENROLLMENT_TIMEOUT: 'Enrollment não confirmado',
                            HEARTBEAT_TIMEOUT: 'Heartbeat não recebido', RUNNER_INTERRUPTED: 'Operação interrompida',
                        };
                        if (job.status === 'COMPLETED') {
                            progress.textContent = 'NightOwl instalado com sucesso. Primeiro heartbeat: ' + date(job.first_heartbeat_at);
                            if (job.endpoint_url) {
                                const link = element('a', 'ad-endpoint-link', 'Abrir endpoint');
                                link.href = job.endpoint_url;
                                drawerContent.appendChild(link);
                            }
                            scan();
                        } else if (['FAILED', 'INTERRUPTED', 'INSTALLED_UNVERIFIED', 'OUTCOME_UNKNOWN'].includes(job.status)) {
                            progress.textContent = job.status === 'OUTCOME_UNKNOWN' ?
                                'Resultado da instalação desconhecido. Não repita sem revisão do endpoint.' :
                                job.status === 'INSTALLED_UNVERIFIED' ?
                                'Instalação executada, mas gerenciamento ainda não confirmado. Não repita sem revisão.' :
                                (failures[job.error_code] || `Falha em ${job.stage || 'instalação'}: ${job.error_code || 'verifique o estado'}`);
                        } else {
                            progress.textContent = stages[job.stage] || 'Preparando';
                            installPollTimer = window.setTimeout(() => poll(url), 2000);
                        }
                    } catch (_error) {
                        progress.textContent = 'Não foi possível consultar o progresso. Reabra o painel antes de tentar novamente.';
                    }
                }

                installForm.addEventListener('submit', async (event) => {
                    event.preventDefault();
                    confirm.disabled = true;
                    progress.textContent = 'Preparando instalação...';
                    const body = new FormData();
                    body.append('csrfmiddlewaretoken', form.querySelector('[name=csrfmiddlewaretoken]').value);
                    body.append('fqdn', computer.fqdn);
                    body.append('username', installUser.value);
                    body.append('password', installPassword.value);
                    installPassword.value = '';
                    try {
                        const response = await fetch(root.dataset.installUrl, {
                            method: 'POST', credentials: 'same-origin', cache: 'no-store', body,
                        });
                        const data = await response.json();
                        if (!response.ok || !data.status_url || !data.status_url.startsWith('/agent-install/install-jobs/')) {
                            progress.textContent = data.error_code || 'Instalação não iniciada.';
                            confirm.disabled = false;
                            return;
                        }
                        installForm.querySelectorAll('input').forEach((input) => { input.value = ''; });
                        confirm.remove();
                        poll(data.status_url);
                    } catch (_error) {
                        progress.textContent = 'Instalação não iniciada ou estado desconhecido. Consulte o operador antes de repetir.';
                    }
                });
            });
        }
        preflightForm.addEventListener('submit', async (event) => {
            event.preventDefault();
            submit.disabled = true;
            feedback.textContent = 'Executando preflight...';
            checks.replaceChildren();
            try {
                const response = await fetch(root.dataset.preflightUrl, {
                    method: 'POST', credentials: 'same-origin', cache: 'no-store',
                    headers: { 'Content-Type': 'application/json',
                        'X-CSRFToken': form.querySelector('[name=csrfmiddlewaretoken]').value },
                    body: JSON.stringify({ fqdn: computer.fqdn, username: userInput.value, password: passwordInput.value }),
                });
                if (!response.ok) throw new Error('preflight failed');
                const data = await response.json();
                const labels = {
                    TARGET: 'Alvo válido', DNS: 'DNS', REMOTE_TRANSPORT: 'WinRM',
                    AUTHENTICATION: 'Autenticação', ADMIN_PRIVILEGE: 'Administrador local',
                    WINDOWS_COMPATIBILITY: 'Windows compatível', NIGHTOWL_ABSENCE: 'NightOwl não detectado',
                };
                const reasons = {
                    INVALID_TARGET: 'Alvo inválido', AD_DISCOVERY_UNAVAILABLE: 'AD indisponível',
                    TARGET_NOT_UNIQUE_OR_MISSING: 'Alvo ausente ou duplicado',
                    AD_COMPUTER_DISABLED: 'Computador desabilitado',
                    CORRELATION_UNAVAILABLE: 'Correlação indisponível',
                    TARGET_MANAGED_OR_CONFLICT: 'Gerenciado ou em conflito',
                    DNS_UNRESOLVED: 'DNS não resolveu', UNSAFE_TARGET_ADDRESS: 'Endereço do alvo inseguro',
                    WINRM_UNAVAILABLE: 'WinRM indisponível',
                    WINRM_CLIENT_UNAVAILABLE: 'Cliente WinRM indisponível',
                    WINRM_REDIRECT_BLOCKED: 'Redirecionamento WinRM bloqueado',
                    REMOTE_TIMEOUT: 'Tempo esgotado', REMOTE_PROBE_FAILED: 'Probe remoto falhou',
                    REMOTE_OUTPUT_INVALID: 'Resposta remota inválida',
                    CREDENTIAL_REQUIRED: 'Credencial obrigatória', AUTHENTICATION_FAILED: 'Autenticação falhou',
                    ADMIN_REQUIRED: 'Administrador local obrigatório',
                    WINDOWS_INCOMPATIBLE_OR_IDENTITY_MISMATCH: 'Windows incompatível ou identidade divergente',
                    NIGHTOWL_INSTALLATION_DETECTED: 'NightOwl já instalado',
                    NIGHTOWL_STATE_UNKNOWN: 'Estado do NightOwl desconhecido',
                };
                for (const [key, label] of Object.entries(labels)) {
                    const check = data.checks?.[key];
                    checks.appendChild(element('dt', '', label));
                    checks.appendChild(element('dd', '', check?.status === 'PASS' ? 'OK' :
                        check?.status === 'FAIL' ? (reasons[check.code] || 'Falhou') : 'Não executado'));
                }
                feedback.textContent = data.status === 'READY' ? 'PRONTO PARA INSTALAÇÃO' : 'NÃO PRONTO PARA INSTALAÇÃO';
                if (data.status === 'READY') showInstallConfirmation(data);
                else if (installButton) { installButton.remove(); installButton = null; }
            } catch (_error) {
                feedback.textContent = 'Não foi possível concluir o preflight.';
            } finally {
                passwordInput.value = '';
                submit.disabled = false;
            }
        });
        drawerContent.appendChild(preflightForm);
        openDrawer('Preflight de instalação');
    });
    function closeDrawer() {
        if (installPollTimer) window.clearTimeout(installPollTimer);
        const password = drawerContent.querySelector('input[type="password"]');
        if (password) password.value = '';
        drawer.hidden = true;
        backdrop.hidden = true;
    }
    closeButton.addEventListener('click', closeDrawer);
    backdrop.addEventListener('click', closeDrawer);
    document.addEventListener('keydown', (event) => { if (event.key === 'Escape' && !drawer.hidden) closeDrawer(); });
    scan();
});
