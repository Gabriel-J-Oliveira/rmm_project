(function () {
    "use strict";

    var root = document.querySelector("[data-software-root]");
    if (!root) return;

    var api = window.MockNightowlApi;
    var operational = window.NightOwlOperational;
    var toast = root.querySelector("[data-software-toast]");
    var activeTab = "inventory";
    var activeView = "cards";
    var state = {
        endpoints: [],
        inventory: [],
        catalog: [],
        packages: [],
        deployments: [],
        updates: [],
        rules: [],
        events: [],
        jobs: []
    };

    var labels = {
        category: { microsoft: "Microsoft", security: "Seguranca", remote: "Acesso remoto", admin: "Admin/Rede", browser: "Navegador", office: "Produtividade", other: "Outros" },
        risk: { low: "Baixo", medium: "Medio", high: "Alto", critical: "Critico", warning: "Atencao", security: "Seguranca", info: "Info" },
        status: {
            approved: "Aprovado", sensitive: "Sensivel", forbidden: "Proibido", prohibited: "Proibido", unknown: "Desconhecido",
            evaluating: "Em avaliacao", required: "Obrigatorio",
            draft: "Rascunho", testing: "Em teste", retired: "Obsoleto",
            queued: "Em fila", running: "Em execucao", completed: "Concluida", failed: "Falha", cancelled: "Cancelada"
        },
        packageType: { MSI: "MSI", EXE: "EXE", PS1: "PS1", ZIP: "ZIP", MSIX: "MSIX" },
        fileType: { MSI: "MSI", EXE: "EXE", PS1: "PS1", ZIP: "ZIP", MSIX: "MSIX" },
        architecture: { x64: "x64", x86: "x86", arm64: "arm64", universal: "Universal" },
        executionContext: { system: "System", user: "Usuario" },
        detectionMethod: {
            software_name_version: "Software por nome/versao",
            file_exists: "Arquivo existe",
            file_version: "Versao de arquivo",
            service_exists: "Servico existe",
            registry_key: "Chave de registro",
            custom_command: "Comando customizado"
        }
    };

    function escapeHtml(value) {
        return String(value == null ? "" : value)
            .replace(/&/g, "&amp;")
            .replace(/</g, "&lt;")
            .replace(/>/g, "&gt;")
            .replace(/"/g, "&quot;")
            .replace(/'/g, "&#039;");
    }

    function icon(name) {
        return '<i data-lucide="' + escapeHtml(name) + '"></i>';
    }

    function renderIcons() {
        if (window.lucide && typeof window.lucide.createIcons === "function") window.lucide.createIcons();
    }

    function showToast(message) {
        if (operational && typeof operational.showToast === "function") {
            operational.showToast(message, { target: toast, timeout: 2800 });
            return;
        }
        if (!toast) return;
        toast.textContent = message || "Acao registrada.";
        toast.hidden = false;
        toast.classList.add("is-visible");
        window.clearTimeout(toast.__softwareTimer);
        toast.__softwareTimer = window.setTimeout(function () {
            toast.classList.remove("is-visible");
            toast.hidden = true;
        }, 2800);
    }

    function formatDate(value) {
        if (!value) return "--";
        var date = new Date(value);
        if (Number.isNaN(date.getTime())) return "--";
        return date.toLocaleDateString("pt-BR") + " " + date.toLocaleTimeString("pt-BR", { hour: "2-digit", minute: "2-digit" });
    }

    function badge(type, value) {
        return '<span class="software-badge ' + escapeHtml(type) + '-' + escapeHtml(value || "unknown") + '">' + escapeHtml((labels[type] && labels[type][value]) || value || "-") + '</span>';
    }

    function shortText(value, length) {
        var text = String(value || "");
        return text.length > length ? text.slice(0, length - 1) + "..." : text;
    }

    function endpointName(id) {
        var endpoint = state.endpoints.find(function (item) { return item.id === id; });
        return endpoint ? endpoint.hostname : id || "-";
    }

    function softwareFromCatalog(catalogId) {
        var catalog = state.catalog.find(function (item) { return item.id === catalogId; });
        if (!catalog) return null;
        var inventory = state.inventory.find(function (item) {
            return item.catalogId === catalog.id || item.name.toLowerCase() === catalog.name.toLowerCase();
        });
        return Object.assign({}, inventory || {
            id: catalog.id,
            name: catalog.name,
            publisher: catalog.publisher,
            category: catalog.category,
            categoryLabel: labels.category[catalog.category] || catalog.category,
            risk: "low",
            riskLabel: "Baixo",
            status: catalog.status === "approved" ? "approved" : "evaluating",
            statusLabel: catalog.status === "approved" ? "Aprovado" : "Em avaliacao",
            endpointCount: 0,
            installCount: 0,
            versions: catalog.approvedVersion ? [catalog.approvedVersion] : [],
            versionsDisplay: catalog.approvedVersion || "-",
            latestSeenAt: catalog.updatedAt,
            endpointIds: [],
            endpoints: [],
            catalogId: catalog.id
        }, { catalogItem: catalog });
    }

    function inventoryByAnyId(id) {
        return state.inventory.find(function (item) { return item.id === id || item.catalogId === id; }) || softwareFromCatalog(id);
    }

    function relatedRules(item) {
        if (!item) return [];
        return state.rules.filter(function (rule) {
            return String(rule.condition || "").toLowerCase().indexOf(item.name.toLowerCase()) >= 0 ||
                String(rule.name || "").toLowerCase().indexOf(item.name.toLowerCase()) >= 0;
        });
    }

    function relatedPackages(item) {
        if (!item) return [];
        return state.packages.filter(function (pkg) {
            return pkg.softwareId === item.catalogId || pkg.softwareId === item.id ||
                String(pkg.name || "").toLowerCase().indexOf(item.name.toLowerCase()) >= 0;
        });
    }

    function relatedDeployments(item) {
        if (!item) return [];
        return state.deployments.filter(function (deployment) {
            return deployment.softwareId === item.catalogId || deployment.softwareId === item.id ||
                String(deployment.software || "").toLowerCase() === item.name.toLowerCase();
        });
    }

    function deploymentsForPackage(pkg) {
        return state.deployments.filter(function (deployment) {
            return deployment.packageId === pkg.id;
        });
    }

    function eventsForPackage(pkg) {
        return state.events.filter(function (event) {
            return String(event.description || "").toLowerCase().indexOf(String(pkg.name || "").toLowerCase()) >= 0 ||
                String(event.title || "").toLowerCase().indexOf("pacote") >= 0 ||
                String(event.eventType || "").indexOf("software.package") === 0;
        }).slice(-5).reverse();
    }

    function relatedEvents(item) {
        if (!item) return [];
        var endpointIds = item.endpointIds || [];
        return state.events.filter(function (event) {
            return String(event.description || "").toLowerCase().indexOf(item.name.toLowerCase()) >= 0 ||
                String(event.title || "").toLowerCase().indexOf(item.name.toLowerCase()) >= 0 ||
                endpointIds.indexOf(event.endpointId) >= 0;
        }).slice(-5).reverse();
    }

    function filters() {
        return {
            q: root.querySelector('[data-software-filter="q"]')?.value.trim() || "",
            category: root.querySelector('[data-software-filter="category"]')?.value || "all",
            risk: root.querySelector('[data-software-filter="risk"]')?.value || "all",
            status: root.querySelector('[data-software-filter="status"]')?.value || "all",
            endpointId: root.querySelector('[data-software-filter="endpointId"]')?.value || ""
        };
    }

    function fillEndpointOptions() {
        var select = root.querySelector('[data-software-filter="endpointId"]');
        if (!select) return;
        var current = select.value;
        select.innerHTML = '<option value="">Todos</option>' + state.endpoints.map(function (endpoint) {
            return '<option value="' + escapeHtml(endpoint.id) + '">' + escapeHtml(endpoint.hostname) + '</option>';
        }).join("");
        select.value = current;
    }

    function metricCard(key, label, value, iconName, filter) {
        return '<button type="button" class="metric-card command-metric" data-software-metric="' + escapeHtml(key) + '" data-filter="' + escapeHtml(filter || "") + '">' +
            '<span class="metric-icon">' + icon(iconName) + '</span><span class="metric-label">' + escapeHtml(label) + '</span><strong class="metric-value">' + escapeHtml(value) + '</strong></button>';
    }

    function renderMetrics() {
        var metrics = root.querySelector("[data-software-metrics]");
        if (!metrics) return;
        var installCount = state.inventory.reduce(function (sum, item) { return sum + (item.installCount || item.endpointCount || 0); }, 0);
        metrics.innerHTML = [
            metricCard("unique", "Softwares unicos", state.inventory.length, "boxes", ""),
            metricCard("install", "Instalacoes totais", installCount, "package", ""),
            metricCard("remote", "Acesso remoto", state.inventory.filter(function (i) { return i.category === "remote"; }).length, "shield-alert", "category:remote"),
            metricCard("security", "Seguranca", state.inventory.filter(function (i) { return i.category === "security"; }).length, "shield", "category:security"),
            metricCard("admin", "Admin/Rede", state.inventory.filter(function (i) { return i.category === "admin"; }).length, "network", "category:admin"),
            metricCard("unknown", "Desconhecidos", state.inventory.filter(function (i) { return i.status === "unknown"; }).length, "circle-help", "status:unknown"),
            metricCard("outdated", "Desatualizados", state.updates.reduce(function (sum, item) { return sum + item.outdatedEndpoints; }, 0), "download", ""),
            metricCard("deployments", "Implantacoes ativas", state.deployments.filter(function (i) { return ["queued", "running"].indexOf(i.status) >= 0; }).length, "rocket", ""),
            metricCard("packages", "Pacotes disponiveis", state.packages.filter(function (i) { return i.status === "approved"; }).length, "package-open", "")
        ].join("");
        renderIcons();
    }

    function inventoryMatches(item, f) {
        var q = f.q.toLowerCase();
        var text = [item.name, item.publisher, item.versionsDisplay].concat((item.endpoints || []).map(function (e) { return e.hostname; })).join(" ").toLowerCase();
        if (q && text.indexOf(q) < 0) return false;
        if (f.category !== "all" && item.category !== f.category) return false;
        if (f.risk !== "all" && item.risk !== f.risk) return false;
        if (f.status !== "all" && item.status !== f.status) return false;
        if (f.endpointId && (item.endpointIds || []).indexOf(f.endpointId) < 0) return false;
        return true;
    }

    function filteredInventory() {
        var f = filters();
        return state.inventory.filter(function (item) { return inventoryMatches(item, f); });
    }

    function renderInventory() {
        var items = filteredInventory();
        var cards = root.querySelector("[data-software-inventory-cards]");
        var table = root.querySelector("[data-software-inventory-table]");
        if (!cards || !table) return;
        cards.hidden = activeView !== "cards";
        table.hidden = activeView !== "table";
        if (!items.length) {
            cards.innerHTML = '<div class="panel empty-state padded">' + icon("package-x") + '<strong>Nenhum software encontrado</strong><span>Ajuste busca ou filtros.</span></div>';
            table.innerHTML = cards.innerHTML;
            renderIcons();
            return;
        }
        cards.innerHTML = items.map(function (item) {
            return '<article class="software-operation-card risk-' + escapeHtml(item.risk) + '" data-software-item="' + escapeHtml(item.id) + '">' +
                '<header><div><strong>' + escapeHtml(item.name) + '</strong><small>' + escapeHtml(item.publisher || "Fabricante nao informado") + '</small></div><div>' + badge("status", item.status) + '</div></header>' +
                '<div class="software-card-badges">' + badge("category", item.category) + badge("risk", item.risk) + '</div>' +
                '<dl><div><dt>Endpoints</dt><dd>' + escapeHtml(item.endpointCount) + '</dd></div><div><dt>Versoes</dt><dd>' + escapeHtml(item.versionsDisplay) + '</dd></div><div><dt>Ultima deteccao</dt><dd>' + escapeHtml(formatDate(item.latestSeenAt)) + '</dd></div></dl>' +
                '<footer><button type="button" data-software-action="details" data-id="' + escapeHtml(item.id) + '">' + icon("panel-right-open") + 'Detalhes</button><button type="button" data-software-action="rule" data-id="' + escapeHtml(item.id) + '">' + icon("shield-plus") + 'Criar regra</button><button type="button" data-software-action="package" data-id="' + escapeHtml(item.id) + '">' + icon("package-plus") + 'Criar pacote</button><button type="button" data-software-action="task-removal" data-id="' + escapeHtml(item.id) + '">' + icon("clipboard-list") + 'Tarefa</button></footer>' +
                '</article>';
        }).join("");
        table.innerHTML = tableWrap(["Software", "Fabricante", "Categoria", "Risco", "Status", "Endpoints", "Versoes", "Ultima deteccao", "Acoes"], items.map(function (item) {
            return '<tr data-software-item="' + escapeHtml(item.id) + '"><td><strong>' + escapeHtml(item.name) + '</strong></td><td>' + escapeHtml(item.publisher || "-") + '</td><td>' + badge("category", item.category) + '</td><td>' + badge("risk", item.risk) + '</td><td>' + badge("status", item.status) + '</td><td>' + escapeHtml(item.endpointCount) + '</td><td>' + escapeHtml(item.versionsDisplay) + '</td><td>' + escapeHtml(formatDate(item.latestSeenAt)) + '</td><td class="software-row-actions"><button type="button" data-software-action="details" data-id="' + escapeHtml(item.id) + '">' + icon("panel-right-open") + '</button><button type="button" data-software-action="approved" data-id="' + escapeHtml(item.id) + '">' + icon("check") + '</button><button type="button" data-software-action="forbidden" data-id="' + escapeHtml(item.id) + '">' + icon("ban") + '</button></td></tr>';
        }));
        renderIcons();
    }

    function tableWrap(headers, rows) {
        return '<div class="table-wrap software-table-wrap"><table class="endpoint-table software-command-table"><thead><tr>' + headers.map(function (h) { return '<th>' + escapeHtml(h) + '</th>'; }).join("") + '</tr></thead><tbody>' + rows.join("") + '</tbody></table></div>';
    }

    function simplePanel(kind, title, rows, headers) {
        var panel = root.querySelector('[data-software-panel="' + kind + '"]');
        if (!panel) return;
        panel.innerHTML = '<section class="panel software-table-panel"><div class="section-heading"><div><h2>' + title + '</h2><p><strong>' + rows.length + '</strong> itens mockados nesta visao.</p></div></div>' + (rows.length ? tableWrap(headers, rows) : '<div class="empty-state padded">' + icon("inbox") + '<strong>Nada encontrado</strong><span>Dados mockados indisponiveis.</span></div>') + '</section>';
    }

    function renderCatalog() {
        simplePanel("catalog", "Catalogo de softwares instalaveis", state.catalog.map(function (item) {
            var pkg = state.packages.find(function (p) { return p.id === item.packageId; });
            return '<tr data-software-catalog-item="' + escapeHtml(item.id) + '"><td><strong>' + escapeHtml(item.name) + '</strong><small>' + escapeHtml(item.publisher) + '</small></td><td>' + badge("category", item.category) + '</td><td class="mono">' + escapeHtml(item.approvedVersion || "-") + '</td><td>' + escapeHtml(pkg ? pkg.name : "Sem pacote") + '</td><td>' + badge("status", item.status) + '</td><td>' + (item.requiresLicense ? "Sim" : "Nao") + '</td><td>' + (item.requiresReboot ? "Sim" : "Nao") + '</td><td>' + escapeHtml(formatDate(item.updatedAt)) + '</td><td class="software-row-actions"><button data-software-action="details" data-id="' + escapeHtml(item.id) + '">' + icon("panel-right-open") + '</button><button data-software-action="deploy-catalog" data-id="' + escapeHtml(item.id) + '">' + icon("rocket") + '</button><button data-software-action="mock">' + icon("edit") + '</button></td></tr>';
        }), ["Software", "Categoria", "Versao aprovada", "Pacote", "Status", "Licenca", "Reboot", "Atualizado", "Acoes"]);
    }

    function renderPackages() {
        simplePanel("packages", "Pacotes de instalacao", state.packages.map(function (item) {
            var catalog = state.catalog.find(function (c) { return c.id === item.softwareId; });
            return '<tr data-software-package-item="' + escapeHtml(item.id) + '"><td><strong>' + escapeHtml(item.name) + '</strong><small>' + escapeHtml(item.file) + '</small></td><td><strong>' + escapeHtml(catalog ? catalog.name : item.softwareId || "-") + '</strong><small>' + escapeHtml(item.publisher || "-") + '</small></td><td>' + badge("fileType", item.fileType) + badge("architecture", item.architecture) + '</td><td><span class="mono">' + escapeHtml(shortText(item.logicalPath, 46)) + '</span><small>' + escapeHtml(item.sizeMb || 0) + ' MB · SHA ' + escapeHtml((item.sha256 || "").slice(0, 12)) + '...</small></td><td><span class="mono">' + escapeHtml(shortText(item.installCommand, 44) || "-") + '</span><small>' + escapeHtml(labels.executionContext[item.executionContext] || item.executionContext || "system") + '</small></td><td><span class="mono">' + escapeHtml(shortText(item.detectionRule, 38) || "-") + '</span><small>' + escapeHtml(labels.detectionMethod[item.detectionMethod] || item.detectionMethod || "-") + '</small></td><td>' + escapeHtml(item.timeoutMinutes || 30) + ' min</td><td>' + (item.requiresReboot ? '<span class="software-badge reboot-yes">Reboot</span>' : '<span class="software-badge reboot-no">Sem reboot</span>') + '</td><td>' + badge("status", item.status) + '</td><td><strong>' + escapeHtml(item.approvedBy || "-") + '</strong><small>' + escapeHtml(formatDate(item.approvedAt)) + '</small></td><td class="software-row-actions"><button data-software-action="package-details" data-id="' + escapeHtml(item.id) + '">' + icon("panel-right-open") + '</button><button data-software-action="package-testing" data-id="' + escapeHtml(item.id) + '">' + icon("flask-conical") + '</button><button data-software-action="approve-package" data-id="' + escapeHtml(item.id) + '">' + icon("check-circle") + '</button><button data-software-action="package-retire" data-id="' + escapeHtml(item.id) + '">' + icon("archive") + '</button><button data-software-action="duplicate-package" data-id="' + escapeHtml(item.id) + '">' + icon("copy") + '</button><button data-software-action="test-package" data-id="' + escapeHtml(item.id) + '">' + icon("play") + '</button><button data-software-action="deploy-package" data-id="' + escapeHtml(item.id) + '">' + icon("rocket") + '</button></td></tr>';
        }), ["Pacote", "Software", "Tipo", "Repositorio", "Instalacao", "Deteccao", "Timeout", "Reboot", "Status", "Aprovacao", "Acoes"]);
    }

    function renderDeployments() {
        simplePanel("deployments", "Implantacoes remotas", state.deployments.map(function (item) {
            return '<tr data-deployment-id="' + escapeHtml(item.id) + '"><td><strong>' + escapeHtml(item.software) + '</strong><small>' + escapeHtml(item.packageName) + '</small></td><td>' + escapeHtml(item.targetType + ": " + item.targetLabel) + '</td><td>' + badge("status", item.status) + '</td><td><span class="software-progress"><i style="width:' + escapeHtml(item.progress || 0) + '%"></i></span><small>' + escapeHtml(item.progress || 0) + '%</small></td><td>' + escapeHtml(item.createdBy) + '</td><td>' + escapeHtml(formatDate(item.createdAt)) + '</td><td>' + escapeHtml(formatDate(item.finishedAt)) + '</td><td>' + escapeHtml((item.endpointIds || []).length) + '</td><td>' + escapeHtml(item.failures || 0) + '</td><td class="software-row-actions"><button data-software-action="deployment-details" data-id="' + escapeHtml(item.id) + '">' + icon("panel-right-open") + '</button><button data-software-action="rerun-deployment" data-id="' + escapeHtml(item.id) + '">' + icon("rotate-cw") + '</button><button data-software-action="mock">' + icon("ban") + '</button></td></tr>';
        }), ["Software", "Alvo", "Status", "Progresso", "Criado por", "Criado em", "Finalizado", "Endpoints", "Falhas", "Acoes"]);
    }

    function renderUpdates() {
        simplePanel("updates", "Divergencia de versoes", state.updates.map(function (item) {
            return '<tr><td><strong>' + escapeHtml(item.software) + '</strong></td><td class="mono">' + escapeHtml(item.approvedVersion) + '</td><td class="mono">' + escapeHtml((item.detectedVersions || []).join(", ") || "-") + '</td><td>' + escapeHtml(item.outdatedEndpoints) + '</td><td>' + badge("risk", item.risk) + '</td><td>' + escapeHtml(item.suggestedAction) + '</td><td>' + escapeHtml(formatDate(item.latestSeenAt)) + '</td><td class="software-row-actions"><button data-software-action="details" data-id="' + escapeHtml(item.softwareId) + '">' + icon("panel-right-open") + '</button><button data-software-action="deploy-update" data-id="' + escapeHtml(item.softwareId) + '">' + icon("rocket") + '</button><button data-software-action="approve-version" data-id="' + escapeHtml(item.softwareId) + '">' + icon("check-circle") + '</button><button data-software-action="view-update-endpoints" data-id="' + escapeHtml(item.softwareId) + '">' + icon("monitor") + '</button><button data-software-action="ignore-version" data-id="' + escapeHtml(item.softwareId) + '">' + icon("eye-off") + '</button><button data-software-action="task-update" data-id="' + escapeHtml(item.softwareId) + '">' + icon("clipboard-list") + '</button></td></tr>';
        }), ["Software", "Versao aprovada", "Versoes detectadas", "Endpoints atrasados", "Risco", "Acao sugerida", "Ultima deteccao", "Acoes"]);
    }

    function renderRules() {
        simplePanel("rules", "Regras de software", state.rules.map(function (item) {
            return '<tr><td><strong>' + escapeHtml(item.name) + '</strong><small>' + escapeHtml(item.condition) + '</small></td><td>' + escapeHtml(item.scope) + '</td><td>' + badge("risk", item.severity) + '</td><td>' + escapeHtml(item.action) + '</td><td>' + (item.active ? "Ativa" : "Inativa") + '</td><td>' + escapeHtml(formatDate(item.lastRunAt)) + '</td><td>' + escapeHtml(item.occurrences) + '</td><td class="software-row-actions"><button data-software-action="mock">' + icon("edit") + '</button><button data-software-action="toggle-rule" data-id="' + escapeHtml(item.id) + '">' + icon(item.active ? "pause" : "play") + '</button><button data-software-action="mock">' + icon("flask-conical") + '</button></td></tr>';
        }), ["Regra", "Escopo", "Severidade", "Acao", "Status", "Ultima execucao", "Ocorrencias", "Acoes"]);
    }

    function renderActiveTab() {
        root.querySelectorAll("[data-software-panel]").forEach(function (panel) {
            var active = panel.dataset.softwarePanel === activeTab;
            panel.hidden = !active;
            panel.classList.toggle("is-active", active);
        });
        root.querySelectorAll("[data-software-tab]").forEach(function (button) {
            button.classList.toggle("is-active", button.dataset.softwareTab === activeTab);
        });
        root.querySelectorAll("[data-software-view]").forEach(function (button) {
            button.classList.toggle("is-active", button.dataset.softwareView === activeView);
            button.hidden = activeTab !== "inventory";
        });
        if (activeTab === "inventory") renderInventory();
        if (activeTab === "catalog") renderCatalog();
        if (activeTab === "packages") renderPackages();
        if (activeTab === "deployments") renderDeployments();
        if (activeTab === "updates") renderUpdates();
        if (activeTab === "rules") renderRules();
        renderIcons();
    }

    function openDrawer(title, subtitle, body, kicker) {
        var drawer = root.querySelector("[data-software-drawer]");
        if (!drawer) return;
        root.querySelector("[data-software-drawer-kicker]").textContent = kicker || "Software";
        root.querySelector("[data-software-drawer-title]").textContent = title || "Detalhes";
        root.querySelector("[data-software-drawer-subtitle]").textContent = subtitle || "Central de Softwares";
        root.querySelector("[data-software-drawer-body]").innerHTML = body || "";
        drawer.classList.add("is-open");
        drawer.setAttribute("aria-hidden", "false");
        var backdrop = root.querySelector("[data-software-drawer-backdrop]");
        if (backdrop) backdrop.hidden = false;
        renderIcons();
    }

    function closeDrawer() {
        var drawer = root.querySelector("[data-software-drawer]");
        if (!drawer) return;
        drawer.classList.remove("is-open");
        drawer.setAttribute("aria-hidden", "true");
        var backdrop = root.querySelector("[data-software-drawer-backdrop]");
        if (backdrop) backdrop.hidden = true;
    }

    function showSoftwareDetails(id) {
        var item = inventoryByAnyId(id);
        if (!item) return;
        var rules = relatedRules(item);
        var packages = relatedPackages(item);
        var deployments = relatedDeployments(item);
        var events = relatedEvents(item);
        var endpointRows = (item.endpoints || []).map(function (endpoint) {
            return '<tr><td><a class="table-link" href="/endpoints/' + escapeHtml(endpoint.id) + '/">' + escapeHtml(endpoint.hostname) + '</a></td><td>' + escapeHtml(endpoint.sector || "-") + '</td><td>' + escapeHtml(endpoint.user || "-") + '</td><td class="mono">' + escapeHtml(endpoint.version || "-") + '</td><td>' + escapeHtml(formatDate(endpoint.installedAt)) + '</td></tr>';
        });
        var ruleList = rules.length ? rules.map(function (rule) { return '<span class="software-badge risk-' + escapeHtml(rule.severity) + '">' + escapeHtml(rule.name) + '</span>'; }).join(" ") : '<span class="software-muted">Nenhuma regra relacionada.</span>';
        var eventList = events.length ? '<div class="software-mini-list">' + events.map(function (event) {
            return '<article><strong>' + escapeHtml(event.eventType || event.title) + '</strong><span>' + escapeHtml(shortText(event.description || event.title, 84)) + '</span><small>' + escapeHtml(formatDate(event.createdAt || event.timestamp)) + '</small></article>';
        }).join("") + '</div>' : '<span class="software-muted">Sem eventos recentes relacionados.</span>';
        var packageList = packages.length ? packages.map(function (pkg) { return '<span class="software-badge status-' + escapeHtml(pkg.status) + '">' + escapeHtml(pkg.name + " " + pkg.version) + '</span>'; }).join(" ") : '<span class="software-muted">Nenhum pacote associado.</span>';
        var deploymentList = deployments.length ? deployments.map(function (deployment) { return '<span class="software-badge status-' + escapeHtml(deployment.status) + '">' + escapeHtml(deployment.id + " · " + deployment.targetLabel) + '</span>'; }).join(" ") : '<span class="software-muted">Nenhuma implantacao relacionada.</span>';
        openDrawer(item.name, item.publisher || "Fabricante nao informado", '<section><h3>Governanca</h3><div class="software-drawer-grid"><div><span>Status</span><strong>' + badge("status", item.status) + '</strong></div><div><span>Categoria</span><strong>' + escapeHtml(item.categoryLabel || labels.category[item.category] || item.category || "-") + '</strong></div><div><span>Risco</span><strong>' + escapeHtml(item.riskLabel || labels.risk[item.risk] || item.risk || "-") + '</strong></div><div><span>Endpoints</span><strong>' + escapeHtml(item.endpointCount || 0) + '</strong></div><div><span>Versoes</span><strong>' + escapeHtml(item.versionsDisplay || (item.versions || []).join(", ") || "-") + '</strong></div><div><span>Ultima deteccao</span><strong>' + escapeHtml(formatDate(item.latestSeenAt)) + '</strong></div></div></section>' +
            '<section><h3>Acoes operacionais</h3><div class="software-drawer-actions"><button data-software-action="view-endpoints" data-id="' + escapeHtml(item.id) + '">' + icon("monitor") + 'Ver endpoints</button><button data-software-action="rule" data-id="' + escapeHtml(item.id) + '">' + icon("shield-plus") + 'Criar regra</button><button data-software-action="package" data-id="' + escapeHtml(item.id) + '">' + icon("package-plus") + 'Criar pacote</button><button data-software-action="approved" data-id="' + escapeHtml(item.id) + '">' + icon("check") + 'Aprovado</button><button data-software-action="sensitive" data-id="' + escapeHtml(item.id) + '">' + icon("eye") + 'Sensivel</button><button data-software-action="forbidden" data-id="' + escapeHtml(item.id) + '">' + icon("ban") + 'Proibido</button><button data-software-action="required" data-id="' + escapeHtml(item.id) + '">' + icon("badge-check") + 'Obrigatorio</button><button data-software-action="evaluating" data-id="' + escapeHtml(item.id) + '">' + icon("hourglass") + 'Em avaliacao</button><button data-software-action="task-removal" data-id="' + escapeHtml(item.id) + '">' + icon("clipboard-list") + 'Tarefa de remocao</button><button data-software-action="deployment-wizard" data-id="' + escapeHtml(item.catalogId || item.id) + '">' + icon("rocket") + 'Criar implantacao</button><button data-software-action="export-item" data-id="' + escapeHtml(item.id) + '">' + icon("download") + 'Exportar</button></div></section>' +
            '<section><h3>Endpoints onde aparece</h3>' + (endpointRows.length ? tableWrap(["Endpoint", "Setor", "Usuario", "Versao", "Detectado"], endpointRows) : '<span class="software-muted">Nenhum endpoint detectado no inventario atual.</span>') + '</section>' +
            '<section><h3>Regras relacionadas</h3><p>' + ruleList + '</p></section><section><h3>Pacotes relacionados</h3><p>' + packageList + '</p></section><section><h3>Implantacoes relacionadas</h3><p>' + deploymentList + '</p></section><section><h3>Eventos recentes</h3>' + eventList + '</section>');
    }

    function showDeploymentDetails(id) {
        var item = state.deployments.find(function (entry) { return entry.id === id; });
        if (!item) return;
        var pkg = state.packages.find(function (entry) { return entry.id === item.packageId; }) || {};
        var jobs = (item.jobIds || []).map(function (jobId) { return '<span class="software-badge status-running">' + escapeHtml(jobId) + '</span>'; }).join(" ");
        openDrawer(item.software, item.packageName, '<section><h3>Implantacao</h3><div class="software-drawer-grid"><div><span>Status</span><strong>' + escapeHtml(labels.status[item.status] || item.status) + '</strong></div><div><span>Alvo</span><strong>' + escapeHtml(item.targetLabel) + '</strong></div><div><span>Endpoints</span><strong>' + escapeHtml((item.endpointIds || []).length) + '</strong></div><div><span>Falhas</span><strong>' + escapeHtml(item.failures || 0) + '</strong></div><div><span>Pacote</span><strong>' + escapeHtml(pkg.name || item.packageName || "-") + '</strong></div><div><span>Versao</span><strong>' + escapeHtml(pkg.version || "-") + '</strong></div></div></section><section><h3>Comando silencioso</h3><pre>' + escapeHtml(pkg.installCommand || "Nao configurado") + '</pre><p class="software-muted">Deteccao: ' + escapeHtml(pkg.detectionRule || "-") + '</p></section><section><h3>Jobs tecnicos</h3><p>' + (jobs || "Nenhum job vinculado.") + '</p></section><section><h3>Stdout</h3><pre>' + escapeHtml(item.stdout || "Sem saida.") + '</pre><h3>Stderr</h3><pre class="stderr">' + escapeHtml(item.stderr || "Sem erros.") + '</pre></section>', "Implantacao");
    }

    function showPackageDetails(id) {
        var item = state.packages.find(function (entry) { return entry.id === id; });
        if (!item) return;
        var catalog = state.catalog.find(function (entry) { return entry.id === item.softwareId; }) || {};
        var deployments = deploymentsForPackage(item);
        var events = eventsForPackage(item);
        var deployList = deployments.length ? deployments.map(function (deployment) {
            return '<span class="software-badge status-' + escapeHtml(deployment.status) + '">' + escapeHtml(deployment.id + " · " + deployment.targetLabel) + '</span>';
        }).join(" ") : '<span class="software-muted">Nenhuma implantacao com este pacote.</span>';
        var eventList = events.length ? '<div class="software-mini-list">' + events.map(function (event) {
            return '<article><strong>' + escapeHtml(event.eventType || event.title) + '</strong><span>' + escapeHtml(shortText(event.description || event.title, 84)) + '</span><small>' + escapeHtml(formatDate(event.createdAt || event.timestamp)) + '</small></article>';
        }).join("") + '</div>' : '<span class="software-muted">Sem eventos recentes do pacote.</span>';
        openDrawer(item.name, catalog.name || item.softwareId || "Pacote interno", '<section><h3>Repositorio interno</h3><div class="software-drawer-grid"><div><span>Status</span><strong>' + badge("status", item.status) + '</strong></div><div><span>Tipo</span><strong>' + badge("fileType", item.fileType) + '</strong></div><div><span>Arquitetura</span><strong>' + escapeHtml(item.architecture || "-") + '</strong></div><div><span>Arquivo</span><strong>' + escapeHtml(item.file || "-") + '</strong></div><div><span>Tamanho</span><strong>' + escapeHtml(item.sizeMb || 0) + ' MB</strong></div><div><span>Execucao</span><strong>' + escapeHtml(labels.executionContext[item.executionContext] || item.executionContext || "system") + '</strong></div></div><pre>' + escapeHtml(item.logicalPath || "-") + '</pre><p class="software-muted">Cache futuro no endpoint: ' + escapeHtml(item.endpointCachePath || "C:\\ProgramData\\NightOwl\\Packages\\") + ' · Logs: ' + escapeHtml(item.endpointLogsPath || "C:\\ProgramData\\NightOwl\\Logs\\") + '</p></section>' +
            '<section><h3>Instalacao silenciosa</h3><pre>' + escapeHtml(item.installCommand || "-") + '</pre><p class="software-muted">Timeout: ' + escapeHtml(item.timeoutMinutes || 30) + ' min · Reboot: ' + escapeHtml(item.requiresReboot ? "sim" : "nao") + ' · Usuario deslogado: ' + escapeHtml(item.requiresLoggedOff ? "sim" : "nao") + '</p></section>' +
            '<section><h3>Desinstalacao</h3><pre>' + escapeHtml(item.uninstallCommand || "Nao configurado") + '</pre></section>' +
            '<section><h3>Deteccao</h3><div class="software-drawer-grid"><div><span>Metodo</span><strong>' + escapeHtml(labels.detectionMethod[item.detectionMethod] || item.detectionMethod || "-") + '</strong></div><div><span>SHA256</span><strong class="mono">' + escapeHtml((item.sha256 || "").slice(0, 18)) + '...</strong></div><div><span>Aprovado por</span><strong>' + escapeHtml(item.approvedBy || "-") + '</strong></div></div><pre>' + escapeHtml(item.detectionRule || "-") + '</pre></section>' +
            '<section><h3>Acoes</h3><div class="software-drawer-actions"><button data-software-action="package-testing" data-id="' + escapeHtml(item.id) + '">' + icon("flask-conical") + 'Marcar em teste</button><button data-software-action="approve-package" data-id="' + escapeHtml(item.id) + '">' + icon("check-circle") + 'Aprovar pacote</button><button data-software-action="package-retire" data-id="' + escapeHtml(item.id) + '">' + icon("archive") + 'Desativar</button><button data-software-action="duplicate-package" data-id="' + escapeHtml(item.id) + '">' + icon("copy") + 'Duplicar</button><button data-software-action="test-package" data-id="' + escapeHtml(item.id) + '">' + icon("play") + 'Piloto</button><button data-software-action="deploy-package" data-id="' + escapeHtml(item.id) + '">' + icon("rocket") + 'Criar implantacao</button></div></section>' +
            '<section><h3>Aviso operacional</h3><p class="software-muted">Valide o comando silencioso antes de implantar em massa. Teste em endpoint piloto antes de aprovar. O hash sera usado futuramente para validar integridade. A execucao real dependera do agente NightOwl rodando como servico/administrador.</p></section>' +
            '<section><h3>Historico de aprovacoes</h3><p>' + (item.approvedBy ? '<span class="software-badge status-approved">Aprovado por ' + escapeHtml(item.approvedBy) + ' em ' + escapeHtml(formatDate(item.approvedAt)) + '</span>' : '<span class="software-muted">Pacote ainda nao aprovado.</span>') + '</p></section><section><h3>Implantacoes relacionadas</h3><p>' + deployList + '</p></section><section><h3>Eventos relacionados</h3>' + eventList + '</section>', "Pacote");
    }

    function catalogOptions(selectedId) {
        return state.catalog.map(function (item) {
            return '<option value="' + escapeHtml(item.id) + '" ' + (item.id === selectedId ? "selected" : "") + '>' + escapeHtml(item.name + " · " + (item.approvedVersion || "sem versao")) + '</option>';
        }).join("");
    }

    function packageOptions(selectedCatalogId, selectedPackageId) {
        var options = state.packages.filter(function (pkg) { return !selectedCatalogId || pkg.softwareId === selectedCatalogId || pkg.id === selectedPackageId; });
        if (!options.length) options = state.packages;
        return options.map(function (pkg) {
            return '<option value="' + escapeHtml(pkg.id) + '" ' + (pkg.id === selectedPackageId ? "selected" : "") + '>' + escapeHtml(pkg.name + " · " + pkg.version + " · " + pkg.status) + '</option>';
        }).join("");
    }

    function endpointChecks(selectedIds) {
        selectedIds = selectedIds && selectedIds.length ? selectedIds : (state.endpoints[0] ? [state.endpoints[0].id] : []);
        return state.endpoints.map(function (endpoint) {
            return '<label class="software-check-row"><input type="checkbox" data-deploy-endpoint value="' + escapeHtml(endpoint.id) + '" ' + (selectedIds.indexOf(endpoint.id) >= 0 ? "checked" : "") + '><span><strong>' + escapeHtml(endpoint.hostname) + '</strong><small>' + escapeHtml(endpoint.sector + " · " + endpoint.ip + " · " + endpoint.status) + '</small></span></label>';
        }).join("");
    }

    function fileTypeOptions(selected) {
        return ["MSI", "EXE", "PS1", "ZIP", "MSIX"].map(function (type) {
            return '<option value="' + type + '" ' + (type === selected ? "selected" : "") + '>' + type + '</option>';
        }).join("");
    }

    function architectureOptions(selected) {
        return ["x64", "x86", "arm64", "universal"].map(function (item) {
            return '<option value="' + item + '" ' + (item === selected ? "selected" : "") + '>' + escapeHtml(labels.architecture[item] || item) + '</option>';
        }).join("");
    }

    function detectionOptions(selected) {
        return Object.keys(labels.detectionMethod).map(function (key) {
            return '<option value="' + key + '" ' + (key === selected ? "selected" : "") + '>' + escapeHtml(labels.detectionMethod[key]) + '</option>';
        }).join("");
    }

    function openPackageUploadWizard(softwareId) {
        var catalog = state.catalog.find(function (item) { return item.id === softwareId; }) || state.catalog[0] || {};
        var suggestedFile = catalog.name ? slugForUi(catalog.name) + "-setup.msi" : "setup-preview.msi";
        openDrawer("Upload pacote", "Repositorio interno mockado", '<form class="software-deployment-wizard" data-software-package-form>' +
            '<section><h3>1. Arquivo</h3><div class="software-form-grid"><label><span>Arquivo mockado</span><input name="file" value="' + escapeHtml(suggestedFile) + '"></label><label><span>Nome do pacote</span><input name="name" value="' + escapeHtml((catalog.name || "Software") + " instalador silencioso") + '"></label><label><span>Software relacionado</span><select name="softwareId">' + catalogOptions(catalog.id) + '</select></label><label><span>Versao</span><input name="version" value="' + escapeHtml(catalog.approvedVersion || "1.0.0") + '"></label><label><span>Tipo</span><select name="fileType">' + fileTypeOptions("MSI") + '</select></label><label><span>Arquitetura</span><select name="architecture">' + architectureOptions("x64") + '</select></label><label><span>Fabricante</span><input name="publisher" value="' + escapeHtml(catalog.publisher || "") + '"></label><label><span>Categoria</span><select name="category"><option value="browser">Navegador</option><option value="security">Seguranca</option><option value="office">Produtividade</option><option value="admin">Admin/Rede</option><option value="remote">Acesso remoto</option><option value="other">Outros</option></select></label></div></section>' +
            '<section><h3>2. Instalacao</h3><div class="software-form-grid"><label><span>Comando silencioso</span><input name="installCommand" value="msiexec /i setup.msi /qn /norestart"></label><label><span>Argumentos</span><input name="installArguments" value="/qn /norestart"></label><label><span>Timeout</span><input type="number" name="timeoutMinutes" value="30"></label><label><span>Executar como</span><select name="executionContext"><option value="system">System</option><option value="user">Usuario</option></select></label><label><span>Usuario deslogado?</span><select name="requiresLoggedOff"><option value="false">Nao</option><option value="true">Sim</option></select></label><label><span>Requer reboot?</span><select name="requiresReboot"><option value="false">Nao</option><option value="true">Sim</option></select></label></div></section>' +
            '<section><h3>3. Desinstalacao</h3><div class="software-form-grid"><label><span>Comando</span><input name="uninstallCommand" value=""></label><label><span>Argumentos</span><input name="uninstallArguments" value="/quiet /norestart"></label></div></section>' +
            '<section><h3>4. Deteccao</h3><div class="software-form-grid"><label><span>Metodo</span><select name="detectionMethod">' + detectionOptions("software_name_version") + '</select></label><label><span>Regra</span><input name="detectionRule" value="' + escapeHtml((catalog.name || "Software") + " >= " + (catalog.approvedVersion || "1.0.0")) + '"></label></div></section>' +
            '<section><h3>5. Revisao</h3><div class="software-form-grid"><label><span>Status inicial</span><select name="status"><option value="draft">Rascunho</option><option value="testing">Em teste</option></select></label><label><span>Hash mockado</span><input name="sha256" value="mock-' + Date.now() + '" readonly></label></div><div class="software-review-box"><span>Caminho logico sugerido</span><strong data-package-review-path></strong><span>Cache futuro no endpoint</span><strong>C:\\ProgramData\\NightOwl\\Packages\\</strong><span>Logs futuros</span><strong>C:\\ProgramData\\NightOwl\\Logs\\</strong><span>Status inicial</span><strong data-package-review-status>Rascunho</strong></div><p class="software-muted">Repositorio Linux: /opt/nightowl/packages/. O hash SHA256 sera usado futuramente para validar integridade.</p><div class="software-drawer-actions"><button type="button" data-software-action="save-package">' + icon("save") + 'Salvar pacote</button><button type="button" data-software-drawer-close>' + icon("x") + 'Cancelar</button></div></section>' +
            '</form>', "Pacote");
        updatePackageReview();
    }

    function slugForUi(value) {
        return String(value || "software").toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "") || "software";
    }

    function packagePayloadFromForm() {
        var form = root.querySelector("[data-software-package-form]");
        if (!form) return null;
        var catalog = state.catalog.find(function (item) { return item.id === form.elements.softwareId.value; }) || {};
        var version = form.elements.version.value || catalog.approvedVersion || "1.0.0";
        var file = form.elements.file.value || (slugForUi(form.elements.name.value) + ".msi");
        var logicalPath = "/opt/nightowl/packages/" + slugForUi(catalog.name || form.elements.name.value) + "/" + version + "/" + file;
        return {
            name: form.elements.name.value,
            softwareId: form.elements.softwareId.value,
            version: version,
            publisher: form.elements.publisher.value || catalog.publisher || "",
            category: form.elements.category.value || catalog.category || "other",
            file: file,
            fileType: form.elements.fileType.value,
            architecture: form.elements.architecture.value,
            logicalPath: logicalPath,
            installCommand: form.elements.installCommand.value,
            installArguments: form.elements.installArguments.value,
            uninstallCommand: form.elements.uninstallCommand.value,
            uninstallArguments: form.elements.uninstallArguments.value,
            detectionMethod: form.elements.detectionMethod.value,
            detectionRule: form.elements.detectionRule.value,
            timeoutMinutes: Number(form.elements.timeoutMinutes.value || 30),
            executionContext: form.elements.executionContext.value,
            requiresLoggedOff: form.elements.requiresLoggedOff.value === "true",
            requiresReboot: form.elements.requiresReboot.value === "true",
            status: form.elements.status.value || "draft",
            sha256: form.elements.sha256.value,
            sizeMb: 128
        };
    }

    function updatePackageReview() {
        var form = root.querySelector("[data-software-package-form]");
        if (!form) return;
        var catalog = state.catalog.find(function (item) { return item.id === form.elements.softwareId.value; }) || {};
        var version = form.elements.version.value || catalog.approvedVersion || "1.0.0";
        var file = form.elements.file.value || (slugForUi(form.elements.name.value) + ".msi");
        var path = "/opt/nightowl/packages/" + slugForUi(catalog.name || form.elements.name.value) + "/" + version + "/" + file;
        var pathNode = form.querySelector("[data-package-review-path]");
        var statusNode = form.querySelector("[data-package-review-status]");
        if (pathNode) pathNode.textContent = path;
        if (statusNode) statusNode.textContent = labels.status[form.elements.status.value] || form.elements.status.value;
    }

    function openDeploymentWizard(catalogId) {
        var selectedPackage = state.packages.find(function (entry) { return entry.id === catalogId; });
        var item = inventoryByAnyId(catalogId);
        var catalog = state.catalog.find(function (entry) { return entry.id === catalogId || (selectedPackage && entry.id === selectedPackage.softwareId) || (item && entry.id === item.catalogId); }) || (item && item.catalogId ? state.catalog.find(function (entry) { return entry.id === item.catalogId; }) : null) || state.catalog[0];
        if (!catalog) {
            showToast("Nenhum software de catalogo disponivel para implantacao.");
            return;
        }
        var pkg = selectedPackage || state.packages.find(function (entry) { return entry.id === catalog.packageId; }) || state.packages.find(function (entry) { return entry.softwareId === catalog.id; }) || state.packages[0] || {};
        var selectedIds = item && item.endpointIds && item.endpointIds.length ? item.endpointIds.slice(0, 3) : (state.endpoints[0] ? [state.endpoints[0].id] : []);
        openDrawer("Nova implantacao", "Wizard mockado de software remoto", '<form class="software-deployment-wizard" data-software-deployment-form>' +
            '<section><h3>1. Software e pacote</h3><div class="software-form-grid"><label><span>Software</span><select name="softwareId" data-deploy-software>' + catalogOptions(catalog.id) + '</select></label><label><span>Pacote / versao</span><select name="packageId" data-deploy-package>' + packageOptions(catalog.id, pkg.id) + '</select></label></div></section>' +
            '<section><h3>2. Alvos</h3><div class="software-target-grid">' + endpointChecks(selectedIds) + '</div><label><span>Setor/tag/grupo mockado</span><input name="targetLabel" value="' + escapeHtml(item && item.endpointCount ? item.endpointCount + " endpoint(s) afetados" : "Selecao manual") + '"></label></section>' +
            '<section><h3>3. Agendamento</h3><div class="software-form-grid"><label><span>Execucao</span><select name="scheduleMode"><option value="now">Executar agora</option><option value="scheduled">Agendar</option></select></label><label><span>Data/hora</span><input type="datetime-local" name="scheduledAt"></label></div></section>' +
            '<section><h3>4. Opcoes de execucao</h3><div class="software-form-grid"><label><span>Timeout</span><input type="number" name="timeoutMinutes" value="45" min="5"></label><label><span>Reboot</span><select name="allowReboot"><option value="false">Nao permitir reboot</option><option value="true">Permitir reboot</option></select></label><label><span>Falha</span><select name="retryOnFailure"><option value="true">Reexecutar em falha</option><option value="false">Nao reexecutar</option></select></label></div><label><span>Observacoes</span><textarea name="notes" rows="3" placeholder="Contexto operacional, janela ou evidencias esperadas"></textarea></label></section>' +
            '<section><h3>5. Revisao</h3><p class="software-muted">Ao confirmar, o mock criara uma implantacao, um Job tecnico por endpoint selecionado e eventos compartilhados com Eventos/Tarefas.</p><p class="software-muted">Pacotes Rascunho nao devem ser usados em massa. Pacotes Em teste devem ser aplicados apenas em piloto.</p><div class="software-drawer-actions"><button type="button" data-software-action="confirm-deployment">' + icon("rocket") + 'Confirmar implantacao</button><button type="button" data-software-drawer-close>' + icon("x") + 'Cancelar</button></div></section>' +
            '</form>', "Implantacao");
    }

    function deploymentPayloadFromForm() {
        var form = root.querySelector("[data-software-deployment-form]");
        if (!form) return null;
        var softwareId = form.elements.softwareId.value;
        var packageId = form.elements.packageId.value;
        var catalog = state.catalog.find(function (item) { return item.id === softwareId; }) || {};
        var pkg = state.packages.find(function (item) { return item.id === packageId; }) || {};
        var endpointIds = Array.from(form.querySelectorAll("[data-deploy-endpoint]:checked")).map(function (input) { return input.value; });
        return {
            softwareId: softwareId,
            packageId: packageId,
            software: catalog.name,
            packageName: pkg.name,
            targetType: "endpoint",
            targetLabel: form.elements.targetLabel.value || endpointIds.length + " endpoint(s)",
            endpointIds: endpointIds,
            scheduleMode: form.elements.scheduleMode.value,
            scheduledAt: form.elements.scheduledAt.value,
            allowReboot: form.elements.allowReboot.value === "true",
            retryOnFailure: form.elements.retryOnFailure.value === "true",
            timeoutMinutes: Number(form.elements.timeoutMinutes.value || 45),
            notes: form.elements.notes.value
        };
    }

    function reload(message) {
        if (!api) {
            showToast("MockNightowlApi indisponivel.");
            return Promise.resolve();
        }
        return Promise.all([
            api.getEndpoints({}),
            api.getSoftwareInventory({}),
            api.getSoftwareCatalog({}),
            api.getSoftwarePackages({}),
            api.getSoftwareDeployments(),
            api.getSoftwareUpdates(),
            api.getSoftwareRules(),
            api.getEvents({}),
            api.getJobs({})
        ]).then(function (results) {
            state.endpoints = results[0] || [];
            state.inventory = results[1] || [];
            state.catalog = results[2] || [];
            state.packages = results[3] || [];
            state.deployments = results[4] || [];
            state.updates = results[5] || [];
            state.rules = results[6] || [];
            state.events = results[7] || [];
            state.jobs = results[8] || [];
            fillEndpointOptions();
            renderMetrics();
            renderActiveTab();
            if (message) showToast(message);
        });
    }

    function createDeployment(catalogId) {
        var catalog = state.catalog.find(function (item) { return item.id === catalogId; }) || state.catalog[0];
        if (!catalog) return;
        var pkg = state.packages.find(function (item) { return item.id === catalog.packageId; }) || state.packages[0] || {};
        api.createSoftwareDeployment({
            softwareId: catalog.id,
            packageId: pkg.id,
            targetType: "endpoint",
            targetLabel: state.endpoints[0] ? state.endpoints[0].hostname : "Endpoint mockado",
            endpointIds: state.endpoints[0] ? [state.endpoints[0].id] : []
        }).then(function () {
            return reload("Implantacao mockada criada e jobs enfileirados.");
        });
    }

    function handleAction(action, id) {
        if (!action) return;
        if (action === "details") return showSoftwareDetails(id);
        if (action === "package-details") return showPackageDetails(id);
        if (action === "deployment-details") return showDeploymentDetails(id);
        if (["approved", "sensitive", "forbidden", "prohibited", "required", "evaluating"].indexOf(action) >= 0) {
            return api.setSoftwareInventoryStatus(id, action).then(function () { return reload("Governanca do software atualizada."); });
        }
        if (action === "rule") {
            return api.createSoftwareRuleFromInventory(id).then(function () {
                activeTab = "rules";
                return reload("Regra mockada criada. Alertas relacionados podem aparecer na Central de Alertas.");
            });
        }
        if (action === "task-removal" || action === "task-update") {
            return api.createSoftwareTask(id, action === "task-update" ? "update" : "removal").then(function () {
                return reload("Tarefa operacional mockada criada e vinculada ao software.");
            });
        }
        if (action === "upload") return openPackageUploadWizard();
        if (action === "save-package") {
            var packagePayload = packagePayloadFromForm();
            if (!packagePayload || !packagePayload.name) return showToast("Informe o nome do pacote.");
            return api.createSoftwarePackage(packagePayload).then(function () {
                closeDrawer();
                activeTab = "packages";
                return reload("Pacote salvo no repositorio interno mockado.");
            });
        }
        if (action === "package") {
            var item = inventoryByAnyId(id);
            return api.createSoftwarePackage({
                name: item ? "Pacote " + item.name : "Pacote mockado " + Date.now(),
                softwareId: item ? (item.catalogId || item.id) : "",
                version: item && item.versions && item.versions[0] ? item.versions[0] : "1.0.0",
                status: "draft"
            }).then(function () {
                activeTab = "packages";
                return reload("Pacote mockado criado.");
            });
        }
        if (action === "new-software") {
            activeTab = "catalog";
            showToast("Formulario real de software fica para a proxima fase.");
            return renderActiveTab();
        }
        if (action === "new-deployment" || action === "deploy-catalog" || action === "deploy-update" || action === "deployment-wizard") return openDeploymentWizard(id);
        if (action === "confirm-deployment") {
            var payload = deploymentPayloadFromForm();
            if (!payload || !payload.endpointIds.length) return showToast("Selecione pelo menos um endpoint alvo.");
            var selectedPkg = state.packages.find(function (item) { return item.id === payload.packageId; });
            if (selectedPkg && selectedPkg.status === "draft" && payload.endpointIds.length > 1) {
                return showToast("Pacote em rascunho so pode ser testado em endpoint piloto.");
            }
            return api.createSoftwareDeployment(payload).then(function () {
                closeDrawer();
                activeTab = "deployments";
                return reload("Implantacao mockada criada, jobs enfileirados e eventos registrados.");
            });
        }
        if (action === "validate-package") return showToast("Pacote validado no mock.");
        if (action === "package-testing") {
            return api.updateSoftwarePackage(id, { status: "testing" }).then(function () { return reload("Pacote marcado como Em teste."); });
        }
        if (action === "approve-package") {
            return api.updateSoftwarePackage(id, { status: "approved" }).then(function () { return reload("Pacote aprovado no mock."); });
        }
        if (action === "package-retire") {
            return api.updateSoftwarePackage(id, { status: "retired" }).then(function () { return reload("Pacote desativado no mock."); });
        }
        if (action === "duplicate-package") {
            return api.duplicateSoftwarePackage(id).then(function () { return reload("Pacote duplicado como rascunho."); });
        }
        if (action === "test-package") {
            return api.createSoftwareDeployment({ packageId: id, endpointIds: state.endpoints[0] ? [state.endpoints[0].id] : [], targetType: "endpoint", targetLabel: "Piloto" }).then(function () {
                activeTab = "deployments";
                return reload("Teste em endpoint piloto criado no mock.");
            });
        }
        if (action === "deploy-package") return openDeploymentWizard(id);
        if (action === "rerun-deployment") return createDeployment();
        if (action === "view-endpoints") {
            var software = inventoryByAnyId(id);
            if (software && software.endpointIds && software.endpointIds[0]) window.location.href = "/endpoints/" + software.endpointIds[0] + "/";
            else showToast("Nenhum endpoint vinculado para abrir.");
            return;
        }
        if (action === "export-item") return showToast("Exportacao do software preparada no mock.");
        if (action === "approve-version") return showToast("Versao aprovada no catalogo mockado.");
        if (action === "ignore-version") return showToast("Divergencia ignorada no mock para esta versao.");
        if (action === "view-update-endpoints") return showSoftwareDetails(id);
        if (action === "mock") return showToast("Acao mockada registrada.");
    }

    root.addEventListener("click", function (event) {
        var tab = event.target.closest("[data-software-tab]");
        if (tab) {
            activeTab = tab.dataset.softwareTab;
            renderActiveTab();
            return;
        }
        var view = event.target.closest("[data-software-view]");
        if (view) {
            activeView = view.dataset.softwareView;
            renderActiveTab();
            return;
        }
        var metric = event.target.closest("[data-software-metric]");
        if (metric && metric.dataset.filter) {
            var parts = metric.dataset.filter.split(":");
            var field = root.querySelector('[data-software-filter="' + parts[0] + '"]');
            if (field) field.value = parts[1];
            activeTab = "inventory";
            renderActiveTab();
            return;
        }
        if (event.target.closest("[data-software-clear]")) {
            root.querySelectorAll("[data-software-filter]").forEach(function (field) { field.value = field.tagName === "SELECT" && field.dataset.softwareFilter !== "endpointId" ? "all" : ""; });
            renderActiveTab();
            return;
        }
        if (event.target.closest("[data-software-export]")) {
            window.location.href = "/software/export/";
            return;
        }
        if (event.target.closest("[data-software-refresh]")) {
            reload("Inventario atualizado.");
            return;
        }
        var action = event.target.closest("[data-software-action]");
        if (action) {
            handleAction(action.dataset.softwareAction, action.dataset.id);
            return;
        }
        var card = event.target.closest("[data-software-item]");
        if (card && !event.target.closest("button,a")) showSoftwareDetails(card.dataset.softwareItem);
        var catalogRow = event.target.closest("[data-software-catalog-item]");
        if (catalogRow && !event.target.closest("button,a")) showSoftwareDetails(catalogRow.dataset.softwareCatalogItem);
        var packageRow = event.target.closest("[data-software-package-item]");
        if (packageRow && !event.target.closest("button,a")) showPackageDetails(packageRow.dataset.softwarePackageItem);
    });

    root.addEventListener("change", function (event) {
        if (event.target.closest("[data-deploy-software]")) {
            var form = root.querySelector("[data-software-deployment-form]");
            var pkgSelect = form && form.querySelector("[data-deploy-package]");
            if (pkgSelect) pkgSelect.innerHTML = packageOptions(event.target.value, "");
        }
        if (event.target.closest("[data-software-package-form]")) updatePackageReview();
    });

    root.addEventListener("input", function (event) {
        if (event.target.closest("[data-software-package-form]")) updatePackageReview();
    });

    root.querySelector("[data-software-filters]")?.addEventListener("submit", function (event) {
        event.preventDefault();
        renderActiveTab();
    });

    root.querySelectorAll("[data-software-drawer-close]").forEach(function (button) {
        button.addEventListener("click", closeDrawer);
    });
    root.querySelector("[data-software-drawer-backdrop]")?.addEventListener("click", closeDrawer);
    document.addEventListener("keydown", function (event) {
        if (event.key === "Escape") closeDrawer();
    });

    reload();
}());
