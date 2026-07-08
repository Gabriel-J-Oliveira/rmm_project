(function () {
    "use strict";

    const root = document.querySelector("[data-endpoint-detail]");
    if (!root) {
        return;
    }

    const api = window.MockNightowlApi;
    const operational = window.NightOwlOperational;
    const toast = document.querySelector("[data-endpoint-toast]");
    const drawer = document.querySelector("[data-endpoint-drawer]");
    const drawerBackdrop = document.querySelector("[data-endpoint-drawer-backdrop]");
    const drawerTitle = document.querySelector("[data-endpoint-drawer-title]");
    const drawerSubtitle = document.querySelector("[data-endpoint-drawer-subtitle]");
    const drawerKicker = document.querySelector("[data-endpoint-drawer-kicker]");
    const drawerBody = document.querySelector("[data-endpoint-drawer-body]");

    let endpointDetail = null;
    let activeTab = "overview";
    let softwareSearch = "";
    let softwareCategory = "all";
    let softwareRisk = "all";
    let activityCategory = "all";

    const labels = {
        online: "Online",
        offline: "Offline",
        unknown: "Unknown",
        critical: "Critico",
        success: "OK",
        warning: "Atencao",
        info: "Info",
        security: "Seguranca",
        open: "Novo",
        acknowledged: "Reconhecido",
        muted: "Silenciado",
        resolved: "Resolvido",
        queued: "Em fila",
        sent: "Enviado",
        running: "Em execucao",
        completed: "Concluido",
        failed: "Falha",
        expired: "Expirado",
        cancelled: "Cancelado",
        force_inventory: "Forcar inventario",
        defender_check: "Verificar Defender",
        disk_check: "Verificar disco",
        collect_logs: "Coletar logs",
        ping: "Ping",
        cleanup_temp: "Limpeza temporaria",
        run_script: "Executar script",
        windows_update_scan: "Windows Update Scan",
        install_software: "Instalar software"
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

    function showToast(message) {
        if (operational && typeof operational.showToast === "function") {
            operational.showToast(message, { target: toast, timeout: 3000 });
            return;
        }
        if (!toast) return;
        toast.textContent = message || "Acao registrada.";
        toast.hidden = false;
        toast.classList.add("is-visible");
        window.clearTimeout(toast.__endpointToastTimer);
        toast.__endpointToastTimer = window.setTimeout(function () {
            toast.classList.remove("is-visible");
            toast.hidden = true;
        }, 2800);
    }

    function formatDate(value) {
        if (!value) return "-";
        const date = new Date(value);
        if (Number.isNaN(date.getTime())) return value;
        return date.toLocaleDateString("pt-BR") + " " + date.toLocaleTimeString("pt-BR", { hour: "2-digit", minute: "2-digit" });
    }

    function formatDuration(ms) {
        if (!ms) return "-";
        if (ms < 60000) return Math.round(ms / 1000) + "s";
        return Math.round(ms / 60000) + "min";
    }

    function badge(kind, value, customLabel) {
        const label = customLabel || labels[value] || value || "-";
        return '<span class="' + escapeHtml(kind) + ' ' + escapeHtml(kind + "-" + (value || "info")) + '">' + escapeHtml(label) + "</span>";
    }

    function severityBadge(value, text) {
        return '<span class="severity-badge severity-' + escapeHtml(value || "info") + '">' + escapeHtml(text || labels[value] || value || "Info") + "</span>";
    }

    function statusBadge(value, text) {
        return '<span class="alert-status alert-status-' + escapeHtml(value || "open") + '">' + escapeHtml(text || labels[value] || value || "Novo") + "</span>";
    }

    function jobBadge(value) {
        return '<span class="job-status-badge job-status-' + escapeHtml(value || "queued") + '">' + escapeHtml(labels[value] || value || "Em fila") + "</span>";
    }

    function jobType(value) {
        return '<span class="job-type-chip">' + icon(jobTypeIcon(value)) + escapeHtml(labels[value] || value || "Tarefa") + "</span>";
    }

    function jobTypeIcon(value) {
        return {
            force_inventory: "package-search",
            defender_check: "shield-check",
            disk_check: "hard-drive",
            collect_logs: "file-search",
            ping: "activity",
            cleanup_temp: "sparkles",
            run_script: "code-2",
            install_software: "package-plus",
            windows_update_scan: "badge-check"
        }[value] || "terminal";
    }

    function eventIcon(category) {
        return {
            agent: "radio",
            alerts: "alert-triangle",
            jobs: "briefcase-business",
            security: "shield",
            inventory: "package-search",
            maintenance: "wrench",
            system: "activity"
        }[category] || "activity";
    }

    function emptyState(title, text, iconName) {
        return '<div class="endpoint-empty-state">' + icon(iconName || "inbox") + '<strong>' + escapeHtml(title) + '</strong><p>' + escapeHtml(text || "") + "</p></div>";
    }

    function factList(items) {
        return '<dl class="endpoint-fact-list">' + items.map(function (item) {
            return '<div><dt>' + escapeHtml(item.label) + '</dt><dd class="' + (item.mono ? "mono" : "") + '">' + escapeHtml(item.value || "-") + "</dd></div>";
        }).join("") + "</dl>";
    }

    function actionButton(action, label, iconName) {
        return '<button type="button" data-endpoint-action="' + escapeHtml(action) + '">' + icon(iconName || "play") + escapeHtml(label) + "</button>";
    }

    function healthClass(score) {
        if (score == null) return "health-unknown";
        if (score < 50) return "health-critical";
        if (score < 75) return "health-warning";
        return "health-good";
    }

    function healthParts(detail) {
        const diskScore = (detail.disks || []).some(function (disk) { return disk.usedPercent >= 90; }) ? 35 : (detail.disks || []).some(function (disk) { return disk.usedPercent >= 80; }) ? 70 : 95;
        const securityScore = detail.security && detail.security.status === "critical" ? 25 : detail.security && detail.security.status === "attention" ? 70 : detail.security && detail.security.status === "unknown" ? 55 : 95;
        const alertScore = (detail.alerts || []).some(function (alert) { return alert.severity === "critical" && alert.status !== "resolved"; }) ? 35 : (detail.alerts || []).length ? 70 : 95;
        return [
            { label: "Conectividade", score: detail.status === "online" ? 96 : detail.status === "offline" ? 15 : 50 },
            { label: "Agente", score: detail.agent && detail.agent.state === "current" ? 95 : detail.agent && detail.agent.state === "outdated" ? 62 : 42 },
            { label: "Seguranca", score: securityScore },
            { label: "Disco", score: diskScore },
            { label: "Inventario", score: detail.attention && detail.attention.toLowerCase().indexOf("inventario") >= 0 ? 45 : 88 },
            { label: "Alertas", score: alertScore }
        ];
    }

    function renderHealth(detail) {
        const parts = healthParts(detail);
        return '<article class="panel endpoint-dense-card endpoint-health-dense">' +
            '<header><h2>' + icon("activity") + 'Saude do endpoint</h2><span class="endpoint-health-pill ' + healthClass(detail.healthScore) + '">' + escapeHtml(detail.healthScore || 0) + "/100</span></header>" +
            '<div class="endpoint-score-block"><strong>' + escapeHtml(detail.healthScore || 0) + '</strong><span>/100</span></div>' +
            '<div class="health-bar"><span class="health-fill ' + healthClass(detail.healthScore) + '" style="width:' + escapeHtml(detail.healthScore || 0) + '%"></span></div>' +
            '<div class="health-breakdown">' + parts.map(function (part) {
                const cls = part.score < 50 ? "critical" : part.score < 75 ? "warning" : "good";
                return '<div><span>' + escapeHtml(part.label) + '</span><strong class="health-part-' + cls + '">' + part.score + '</strong><em><i style="width:' + part.score + '%"></i></em></div>';
            }).join("") + "</div></article>";
    }

    function renderDiskList(disks, compact) {
        if (!disks || !disks.length) return emptyState("Nenhum disco coletado", "O proximo inventario deve preencher esta secao.", "hard-drive");
        if (compact) {
            return '<div class="endpoint-mini-disk-list">' + disks.map(function (disk) {
                return '<div><strong class="mono">' + escapeHtml(disk.name) + '</strong><span>' + escapeHtml(disk.usedPercent) + '%</span><em><i class="disk-' + escapeHtml(disk.severity) + '" style="width:' + escapeHtml(disk.usedPercent) + '%"></i></em><small>' + escapeHtml(disk.freeGb) + ' GB livres de ' + escapeHtml(disk.totalGb) + " GB</small></div>";
            }).join("") + "</div>";
        }
        return '<div class="endpoint-disk-table">' + disks.map(function (disk) {
            return '<article class="disk-row"><div class="disk-row-header"><strong class="mono">' + escapeHtml(disk.name) + '</strong><span class="disk-pill disk-pill-' + escapeHtml(disk.severity) + '">' + escapeHtml(disk.usedPercent) + '% usado</span></div>' +
                '<div class="disk-meta"><span>Total: ' + escapeHtml(disk.totalGb) + ' GB</span><span>Livre: ' + escapeHtml(disk.freeGb) + ' GB</span></div>' +
                '<div class="disk-bar"><span class="disk-fill disk-' + escapeHtml(disk.severity) + '" style="width:' + escapeHtml(disk.usedPercent) + '%"></span></div>' +
                '<footer>' + actionButton("check_disk", "Verificar disco", "hard-drive") + actionButton("run_cleanup", "Executar limpeza", "sparkles") + actionButton("create_ticket", "Criar chamado", "ticket") + "</footer></article>";
        }).join("") + "</div>";
    }

    function renderAlerts(alerts, limit) {
        const rows = (alerts || []).slice(0, limit || alerts.length);
        if (!rows.length) return emptyState("Sem alertas ativos", "Nada pendente para este endpoint.", "check-circle");
        return '<div class="endpoint-alert-list">' + rows.map(function (alert) {
            return '<article class="endpoint-alert-item severity-border-' + escapeHtml(alert.severity) + '" data-alert-card data-alert-id="' + escapeHtml(alert.id) + '" data-endpoint="' + escapeHtml(alert.endpoint) + '">' +
                severityBadge(alert.severity) + '<div><strong>' + escapeHtml(alert.title) + '</strong><p>' + escapeHtml(alert.description || alert.age || "") + '</p></div><span>' + statusBadge(alert.status) + "</span></article>";
        }).join("") + "</div>";
    }

    function renderTickets(tickets) {
        if (!tickets || !tickets.length) return emptyState("Sem chamados relacionados", "Crie um chamado se a acao operacional exigir acompanhamento.", "ticket");
        return '<div class="endpoint-task-list">' + tickets.map(function (ticket) {
            return '<article><span class="agent-version-pill agent-current">' + escapeHtml(ticket.number) + '</span><div><strong>' + escapeHtml(ticket.title) + '</strong><p>' + escapeHtml(ticket.status) + ' · ' + escapeHtml(ticket.priority) + "</p></div></article>";
        }).join("") + "</div>";
    }

    function renderEvents(events, limit, applyFilter) {
        const rows = (events || []).filter(function (event) {
            return !applyFilter || activityCategory === "all" || event.category === activityCategory;
        }).slice(0, limit || events.length);
        if (!rows.length) return emptyState("Nenhum evento encontrado", "Eventos de heartbeat, inventario, jobs e alertas aparecem aqui.", "history");
        return '<div class="endpoint-event-timeline">' + rows.map(function (event) {
            return '<article class="endpoint-event-row severity-border-' + escapeHtml(event.severity) + '" data-open-event="' + escapeHtml(event.id) + '">' +
                '<div class="event-icon severity-' + escapeHtml(event.severity || "info") + '">' + icon(eventIcon(event.category)) + '</div>' +
                '<div><div class="event-title-line"><code class="event-type-badge event-type-' + escapeHtml(event.category || "system") + '">' + escapeHtml(event.eventType || "system.event") + '</code><strong>' + escapeHtml(event.title) + '</strong></div>' +
                '<p>' + escapeHtml(event.description || "") + '</p><small>' + escapeHtml(formatDate(event.timestamp)) + ' · ' + escapeHtml(event.source || "System") + ' · ' + escapeHtml(event.actor || "-") + '</small></div></article>';
        }).join("") + "</div>";
    }

    function renderJobs(jobs, limit) {
        const rows = (jobs || []).slice(0, limit || jobs.length);
        if (!rows.length) return emptyState("Nenhuma tarefa registrada", "Use as acoes rapidas para simular jobs do agente.", "list-checks");
        return '<div class="table-wrap"><table class="endpoint-table endpoint-job-table"><thead><tr><th>Status</th><th>Tipo</th><th>Criado por</th><th>Criado em</th><th>Duracao</th><th>Resultado</th><th>Acoes</th></tr></thead><tbody>' +
            rows.map(function (job) {
                const canCancel = ["queued", "sent", "running"].indexOf(job.status) >= 0;
                return '<tr><td>' + jobBadge(job.status) + '</td><td>' + jobType(job.type) + '</td><td>' + escapeHtml(job.createdBy || "-") + '</td><td>' + escapeHtml(formatDate(job.createdAt)) + '</td><td>' + escapeHtml(formatDuration(job.durationMs)) + '</td><td>' + escapeHtml(job.result || "-") + '</td><td class="software-actions">' +
                    '<button type="button" data-open-job="' + escapeHtml(job.id) + '">Detalhes</button>' +
                    '<button type="button" data-copy-job="' + escapeHtml(job.id) + '">Copiar saida</button>' +
                    '<button type="button" data-rerun-job="' + escapeHtml(job.id) + '">Reexecutar</button>' +
                    (canCancel ? '<button type="button" data-cancel-job="' + escapeHtml(job.id) + '">Cancelar</button>' : "") +
                    "</td></tr>";
            }).join("") + "</tbody></table></div>";
    }

    function renderOverview(detail) {
        const inv = detail.inventory || {};
        const security = detail.security || {};
        const agent = detail.agent || {};
        return '<div class="endpoint-compact-grid">' +
            renderHealth(detail) +
            '<article class="panel endpoint-dense-card"><header><h2>' + icon("id-card") + 'Resumo tecnico</h2></header>' + factList([
                { label: "Hostname", value: detail.hostname, mono: true },
                { label: "IP principal", value: detail.ip, mono: true },
                { label: "Usuario", value: detail.user },
                { label: "Setor/tag", value: detail.sector },
                { label: "Sistema", value: detail.os },
                { label: "Dominio", value: detail.domain }
            ]) + '</article>' +
            '<article class="panel endpoint-dense-card"><header><h2>' + icon("bot") + 'Agente NightOwl</h2>' + badge("agent-version-pill agent", agent.state, agent.state === "current" ? "Atual" : labels[agent.state] || agent.state) + '</header>' + factList([
                { label: "Instalada", value: agent.version, mono: true },
                { label: "Recomendada", value: agent.recommendedVersion, mono: true },
                { label: "Modo", value: agent.mode },
                { label: "Runtime", value: agent.runtime },
                { label: "Ultima execucao", value: agent.lastRun },
                { label: "Proximo heartbeat", value: agent.nextHeartbeat }
            ]) + '</article>' +
            '<article class="panel endpoint-dense-card"><header><h2>' + icon("shield") + 'Seguranca</h2>' + severityBadge(security.status === "critical" ? "critical" : security.status === "attention" ? "warning" : security.status === "ok" ? "success" : "info", security.status || "unknown") + '</header>' + factList([
                { label: "Antivirus", value: security.antivirus },
                { label: "Assinatura", value: security.signature, mono: true },
                { label: "Firewall", value: security.firewall },
                { label: "BitLocker", value: security.bitlocker }
            ]) + '</article>' +
            '<article class="panel endpoint-dense-card"><header><h2>' + icon("cpu") + 'Inventario rapido</h2></header>' + factList([
                { label: "Fabricante", value: inv.manufacturer },
                { label: "Modelo", value: inv.model },
                { label: "Serial", value: inv.serial, mono: true },
                { label: "CPU", value: inv.cpu },
                { label: "Memoria", value: inv.memoryGb ? inv.memoryGb + " GB" : "-" },
                { label: "Ultimo inventario", value: inv.lastFullInventory }
            ]) + '</article>' +
            '<article class="panel endpoint-dense-card endpoint-disk-summary"><header><h2>' + icon("hard-drive") + 'Discos</h2><button type="button" data-endpoint-tab-jump="inventory">Ver inventario</button></header>' + renderDiskList(detail.disks, true) + '</article>' +
            '<article class="panel endpoint-dense-card endpoint-span-2"><header><h2>' + icon("alert-triangle") + 'Alertas ativos</h2><a href="/alerts/?q=' + encodeURIComponent(detail.hostname) + '">Central de Alertas</a></header>' + renderAlerts(detail.alerts, 4) + '</article>' +
            '<article class="panel endpoint-dense-card endpoint-span-2"><header><h2>' + icon("ticket") + 'Chamados relacionados</h2>' + actionButton("create_ticket", "Criar chamado", "ticket") + '</header>' + renderTickets(detail.tickets) + '</article>' +
            '<article class="panel endpoint-dense-card endpoint-span-2"><header><h2>' + icon("history") + 'Ultimos eventos</h2><a href="/events/?q=' + encodeURIComponent(detail.hostname) + '">Ver todos</a></header>' + renderEvents(detail.events, 5, false) + '</article>' +
            '<article class="panel endpoint-dense-card endpoint-span-2"><header><h2>' + icon("zap") + 'Acoes rapidas</h2></header><div class="endpoint-remote-actions-grid">' +
            actionButton("force_inventory", "Forcar inventario", "refresh-ccw") +
            actionButton("check_defender", "Verificar Defender", "shield-check") +
            actionButton("check_disk", "Verificar disco", "hard-drive") +
            actionButton("collect_logs", "Coletar logs", "file-search") +
            actionButton("ping", "Ping", "activity") +
            actionButton("execute_check", "Executar script", "code-2") +
            "</div></article></div>";
    }

    function renderInventory(detail) {
        const inv = detail.inventory || {};
        return '<div class="endpoint-inventory-grid">' +
            '<article class="panel endpoint-dense-card"><header><h2>' + icon("monitor") + 'Sistema operacional</h2></header>' + factList([
                { label: "Sistema", value: detail.os },
                { label: "Versao", value: inv.osVersion, mono: true },
                { label: "Build", value: inv.build, mono: true },
                { label: "Arquitetura", value: inv.architecture },
                { label: "Dominio", value: detail.domain },
                { label: "Uptime", value: inv.uptime }
            ]) + '</article>' +
            '<article class="panel endpoint-dense-card"><header><h2>' + icon("cpu") + 'Hardware</h2></header>' + factList([
                { label: "Fabricante", value: inv.manufacturer },
                { label: "Modelo", value: inv.model },
                { label: "Serial", value: inv.serial, mono: true },
                { label: "CPU", value: inv.cpu },
                { label: "Memoria", value: inv.memoryGb ? inv.memoryGb + " GB" : "-" },
                { label: "BIOS/UEFI", value: inv.bios }
            ]) + '</article>' +
            '<article class="panel endpoint-dense-card endpoint-span-2"><header><h2>' + icon("network") + 'Rede e coleta</h2></header>' + factList([
                { label: "IP principal", value: detail.ip, mono: true },
                { label: "MACs", value: (inv.macs || []).join(", "), mono: true },
                { label: "Ultimo inventario completo", value: inv.lastFullInventory },
                { label: "Agente", value: detail.agent && detail.agent.version, mono: true }
            ]) + '</article></div>' +
            '<section class="panel endpoint-dense-card endpoint-disk-panel"><header><h2>' + icon("hard-drive") + 'Discos</h2></header>' + renderDiskList(detail.disks, false) + "</section>";
    }

    function softwareCounters(items) {
        return [
            { label: "Total", value: items.length },
            { label: "Seguranca", value: items.filter(function (s) { return s.category === "security"; }).length },
            { label: "Acesso remoto", value: items.filter(function (s) { return s.category === "remote"; }).length },
            { label: "Microsoft", value: items.filter(function (s) { return s.category === "microsoft"; }).length },
            { label: "Sensiveis", value: items.filter(function (s) { return ["high", "medium"].indexOf(s.risk) >= 0; }).length }
        ];
    }

    function renderSoftware(detail) {
        const items = detail.software || [];
        const term = softwareSearch.toLowerCase();
        const filtered = items.filter(function (item) {
            const text = [item.name, item.category, item.risk, item.version, item.publisher].join(" ").toLowerCase();
            if (term && text.indexOf(term) < 0) return false;
            if (softwareCategory !== "all" && item.category !== softwareCategory) return false;
            if (softwareRisk !== "all" && item.risk !== softwareRisk) return false;
            return true;
        });
        const counters = softwareCounters(items);
        return '<section class="panel endpoint-dense-card software-panel">' +
            '<div class="panel-header software-header"><div><h2><span class="section-icon">' + icon("package") + '</span>Softwares</h2><p>Programas do inventario mockado centralizado deste endpoint.</p></div>' +
            '<label class="software-search"><span>Buscar</span><input data-software-search type="search" value="' + escapeHtml(softwareSearch) + '" placeholder="Nome, versao ou fabricante"></label></div>' +
            '<div class="endpoint-inline-metrics">' + counters.map(function (counter) { return '<div><span>' + escapeHtml(counter.label) + '</span><strong>' + counter.value + '</strong></div>'; }).join("") + '</div>' +
            '<div class="software-chip-row" role="group" aria-label="Categorias">' + ["all", "microsoft", "security", "remote", "admin", "other"].map(function (cat) {
                return '<button class="software-chip ' + (softwareCategory === cat ? "active" : "") + '" type="button" data-software-category="' + cat + '">' + escapeHtml(cat === "all" ? "Todos" : cat) + "</button>";
            }).join("") + '</div>' +
            '<div class="software-chip-row" role="group" aria-label="Risco">' + ["all", "low", "medium", "high"].map(function (risk) {
                return '<button class="software-chip ' + (softwareRisk === risk ? "active" : "") + '" type="button" data-software-risk="' + risk + '">' + escapeHtml(risk === "all" ? "Todos os riscos" : risk) + "</button>";
            }).join("") + '</div>' +
            (filtered.length ? '<div class="table-wrap software-table-wrap"><table class="endpoint-table software-table"><thead><tr><th>Nome</th><th>Categoria</th><th>Risco</th><th>Versao</th><th>Fabricante</th><th>Instalado em</th><th>Acoes futuras</th></tr></thead><tbody>' + filtered.map(function (software) {
                return '<tr><td><strong>' + escapeHtml(software.name) + '</strong></td><td><span class="software-badge category-' + escapeHtml(software.category) + '">' + escapeHtml(software.category) + '</span></td><td><span class="software-badge risk-' + escapeHtml(software.risk) + '">' + escapeHtml(software.risk) + '</span></td><td class="mono">' + escapeHtml(software.version || "-") + '</td><td>' + escapeHtml(software.publisher || "-") + '</td><td>' + escapeHtml(formatDate(software.installedAt)) + '</td><td class="software-actions"><button type="button" data-endpoint-action="copy_summary">Permitir</button><button type="button" data-endpoint-action="copy_summary">Proibir</button><button type="button" data-endpoint-action="create_ticket">Solicitar remocao</button></td></tr>';
            }).join("") + "</tbody></table></div>" : emptyState("Nenhum software no filtro", "Ajuste busca, categoria ou risco.", "package")) + "</section>";
    }

    function renderSecurity(detail) {
        const security = detail.security || {};
        const admins = detail.localAdmins || [];
        const violations = detail.policyViolations || [];
        const securityEvents = (detail.events || []).filter(function (event) { return event.category === "security" || event.eventType.indexOf("security") >= 0 || event.eventType.indexOf("policy") >= 0; });
        return '<div class="endpoint-compact-grid security-grid">' +
            '<article class="panel endpoint-dense-card"><header><h2>' + icon("shield") + 'Protecao local</h2>' + severityBadge(security.status === "critical" ? "critical" : security.status === "attention" ? "warning" : security.status === "ok" ? "success" : "info", security.status || "unknown") + '</header>' + factList([
                { label: "Antivirus", value: security.antivirus },
                { label: "Assinatura", value: security.signature, mono: true },
                { label: "Firewall", value: security.firewall },
                { label: "BitLocker", value: security.bitlocker }
            ]) + '<div class="endpoint-remote-actions-grid">' + actionButton("check_defender", "Verificar Defender", "shield-check") + actionButton("execute_check", "Verificar seguranca", "shield-alert") + actionButton("create_ticket", "Criar chamado", "ticket") + actionButton("copy_summary", "Criar regra", "file-plus-2") + '</div></article>' +
            '<article class="panel endpoint-dense-card"><header><h2>' + icon("radio-tower") + 'Acesso remoto</h2></header>' + ((security.remoteTools || []).length ? '<div class="endpoint-task-list">' + security.remoteTools.map(function (tool) { return '<article><span class="severity-badge severity-security">Risco</span><div><strong>' + escapeHtml(tool) + '</strong><p>Ferramenta de acesso remoto detectada.</p></div></article>'; }).join("") + '</div>' : emptyState("Sem acesso remoto de risco", "Nenhuma ferramenta sensivel listada.", "check-circle")) + '</article>' +
            '<article class="panel endpoint-dense-card"><header><h2>' + icon("users") + 'Administradores locais</h2></header>' + (admins.length ? '<div class="endpoint-task-list">' + admins.map(function (admin) { return '<article><span class="severity-badge severity-info">Admin</span><div><strong class="mono">' + escapeHtml(admin) + '</strong><p>Coleta mockada de grupo local.</p></div></article>'; }).join("") + '</div>' : emptyState("Sem administradores coletados", "A coleta ainda nao retornou membros locais.", "users")) + '</article>' +
            '<article class="panel endpoint-dense-card"><header><h2>' + icon("shield-alert") + 'Politicas violadas</h2></header>' + (violations.length ? '<div class="endpoint-task-list">' + violations.map(function (item) { return '<article><span class="severity-badge severity-' + escapeHtml(item.severity) + '">' + escapeHtml(item.severity) + '</span><div><strong>' + escapeHtml(item.policy) + '</strong><p>' + escapeHtml(item.item) + '</p></div></article>'; }).join("") + '</div>' : emptyState("Sem violacoes de politica", "Nada pendente para este endpoint.", "check-circle")) + '</article>' +
            '<article class="panel endpoint-dense-card endpoint-span-2"><header><h2>' + icon("history") + 'Ultimos eventos de seguranca</h2></header>' + renderEvents(securityEvents, 5, false) + '</article></div>';
    }

    function renderPatches(detail) {
        const patches = detail.patches || {};
        const pending = patches.pending || [];
        const history = patches.history || [];
        return '<div class="endpoint-compact-grid security-grid">' +
            '<article class="panel endpoint-dense-card"><header><h2>' + icon("badge-check") + 'Compliance</h2><span class="endpoint-health-pill ' + healthClass(patches.compliance) + '">' + escapeHtml(patches.compliance || 0) + '%</span></header>' +
            '<div class="endpoint-score-block"><strong>' + escapeHtml(patches.compliance || 0) + '</strong><span>%</span></div><div class="health-bar"><span class="health-fill ' + healthClass(patches.compliance) + '" style="width:' + escapeHtml(patches.compliance || 0) + '%"></span></div>' +
            factList([{ label: "Ultima verificacao", value: patches.lastScan }, { label: "Criticos pendentes", value: patches.criticalPending }, { label: "Importantes pendentes", value: patches.importantPending }, { label: "Reboot pendente", value: patches.rebootPending ? "Sim" : "Nao" }]) +
            '<div class="endpoint-remote-actions-grid">' + actionButton("execute_check", "Verificar atualizacoes", "search-check") + actionButton("execute_check", "Instalar patches", "download-cloud") + actionButton("copy_summary", "Agendar manutencao", "calendar-clock") + actionButton("execute_check", "Criar tarefa", "list-plus") + '</div></article>' +
            '<article class="panel endpoint-dense-card"><header><h2>' + icon("download") + 'Patches pendentes</h2></header>' + (pending.length ? '<div class="endpoint-task-list">' + pending.map(function (patch) { return '<article><span class="severity-badge severity-' + escapeHtml(patch.severity || "info") + '">' + escapeHtml(patch.kb || "KB") + '</span><div><strong>' + escapeHtml(patch.title) + '</strong><p>' + escapeHtml(labels[patch.severity] || patch.severity || "Info") + '</p></div></article>'; }).join("") + '</div>' : emptyState("Sem patches pendentes", "Endpoint em conformidade no mock atual.", "check-circle")) + '</article>' +
            '<article class="panel endpoint-dense-card endpoint-span-2"><header><h2>' + icon("history") + 'Historico recente</h2></header>' + (history.length ? '<div class="endpoint-task-list">' + history.map(function (item) { return '<article><span class="agent-version-pill agent-current">' + escapeHtml(item.status || "ok") + '</span><div><strong>' + escapeHtml(item.title) + '</strong><p>' + escapeHtml(item.when || "-") + '</p></div></article>'; }).join("") + '</div>' : emptyState("Sem historico de patches", "Nenhuma execucao registrada.", "history")) + '</article></div>';
    }

    function renderActivity(detail) {
        const categories = ["all", "agent", "inventory", "alerts", "jobs", "security", "system"];
        return '<section class="panel endpoint-dense-card"><header><h2>' + icon("history") + 'Timeline do endpoint</h2><a href="/events/?q=' + encodeURIComponent(detail.hostname) + '">Ver todos os eventos deste endpoint</a></header>' +
            '<div class="software-chip-row">' + categories.map(function (cat) { return '<button type="button" class="software-chip ' + (activityCategory === cat ? "active" : "") + '" data-activity-category="' + cat + '">' + escapeHtml(cat === "all" ? "Todos" : cat) + "</button>"; }).join("") + '</div>' +
            renderEvents(detail.events, 30, true) + "</section>";
    }

    function renderTasks(detail) {
        return '<section class="panel endpoint-dense-card"><header><h2>' + icon("list-checks") + 'Tarefas e jobs</h2><button type="button" data-endpoint-action="execute_check">' + icon("plus") + 'Nova tarefa mockada</button></header>' +
            renderJobs(detail.jobs, 50) +
            '<div class="endpoint-remote-actions-grid">' +
            actionButton("force_inventory", "Forcar inventario", "refresh-ccw") +
            actionButton("check_defender", "Verificar Defender", "shield-check") +
            actionButton("check_disk", "Verificar disco", "hard-drive") +
            actionButton("collect_logs", "Coletar logs", "file-search") +
            actionButton("run_cleanup", "Executar limpeza", "sparkles") +
            actionButton("ping", "Ping", "activity") +
            actionButton("execute_check", "Executar script", "code-2") +
            "</div></section>";
    }

    function panel(name) {
        return root.querySelector('[data-endpoint-tab-panel="' + name + '"]');
    }

    function renderActivePanel() {
        if (!endpointDetail) return;
        const renderers = {
            overview: renderOverview,
            inventory: renderInventory,
            software: renderSoftware,
            security: renderSecurity,
            patches: renderPatches,
            activity: renderActivity,
            tasks: renderTasks
        };
        Object.keys(renderers).forEach(function (name) {
            const target = panel(name);
            if (target) {
                target.innerHTML = renderers[name](endpointDetail);
            }
        });
        if (window.lucide && typeof window.lucide.createIcons === "function") {
            window.lucide.createIcons();
        }
        if (operational && typeof operational.applyMockAlertState === "function") {
            operational.applyMockAlertState(root);
        }
    }

    function activateTab(name) {
        activeTab = name || activeTab;
        root.querySelectorAll("[data-endpoint-tab]").forEach(function (button) {
            button.classList.toggle("is-active", button.dataset.endpointTab === activeTab);
        });
        root.querySelectorAll("[data-endpoint-tab-panel]").forEach(function (target) {
            const active = target.dataset.endpointTabPanel === activeTab;
            target.hidden = !active;
            target.classList.toggle("is-active", active);
        });
        if (window.lucide && typeof window.lucide.createIcons === "function") {
            window.lucide.createIcons();
        }
    }

    function reloadEndpoint() {
        if (!api || typeof api.getEndpointById !== "function") {
            showToast("Camada mockNightowlApi indisponivel.");
            return Promise.resolve(null);
        }
        const id = root.dataset.endpointId || root.dataset.endpoint;
        return api.getEndpointById(id).then(function (detail) {
            if (!detail) {
                root.querySelectorAll("[data-dynamic-endpoint-panel]").forEach(function (target) {
                    target.innerHTML = emptyState("Endpoint nao encontrado", "A camada mockada nao retornou dados para este identificador.", "monitor-x");
                });
                return null;
            }
            endpointDetail = detail;
            renderActivePanel();
            activateTab(activeTab);
            return detail;
        });
    }

    function runEndpointAction(action) {
        if (!endpointDetail) return;
        const endpoint = endpointDetail.hostname || endpointDetail.id;
        if (operational && typeof operational.runAction === "function") {
            const result = operational.runAction(action, {
                endpoint: endpoint,
                endpointId: endpointDetail.id,
                title: "Atendimento RMM - " + endpointDetail.hostname,
                copyText: buildEndpointSummary(endpointDetail),
                summary: buildEndpointSummary(endpointDetail),
                toastOptions: { target: toast, timeout: 3000 },
                description: (labels[action] || action) + " solicitado para " + endpointDetail.hostname + "."
            });
            const promise = result && result.apiPromise && typeof result.apiPromise.then === "function" ? result.apiPromise : Promise.resolve();
            promise.finally(function () {
                window.setTimeout(reloadEndpoint, 120);
            });
            return;
        }
        showToast((labels[action] || action) + " solicitado para " + endpointDetail.hostname + ".");
    }

    function buildEndpointSummary(detail) {
        return [
            "Endpoint: " + detail.hostname,
            "Status: " + (labels[detail.status] || detail.status),
            "IP: " + detail.ip,
            "Usuario/Setor: " + detail.user + " / " + detail.sector,
            "Atencao: " + detail.attention,
            "Saude: " + detail.healthScore + "/100"
        ].join("\n");
    }

    function findEvent(id) {
        return (endpointDetail && endpointDetail.events || []).find(function (item) { return item.id === id; });
    }

    function findJob(id) {
        return (endpointDetail && endpointDetail.jobs || []).find(function (item) { return item.id === id; });
    }

    function openDrawer(kind, title, subtitle, body) {
        if (!drawer || !drawerBody) return;
        drawerKicker.textContent = kind || "Detalhe";
        drawerTitle.textContent = title || "Detalhe";
        drawerSubtitle.textContent = subtitle || "Contexto operacional";
        drawerBody.innerHTML = body || "";
        drawer.classList.add("is-open");
        drawer.setAttribute("aria-hidden", "false");
        if (drawerBackdrop) drawerBackdrop.hidden = false;
        if (window.lucide && typeof window.lucide.createIcons === "function") {
            window.lucide.createIcons();
        }
    }

    function closeDrawer() {
        if (!drawer) return;
        drawer.classList.remove("is-open");
        drawer.setAttribute("aria-hidden", "true");
        if (drawerBackdrop) drawerBackdrop.hidden = true;
    }

    function openEventDrawer(id) {
        const event = findEvent(id);
        if (!event) return;
        openDrawer("Evento", event.title, event.eventType, '<section><h3>Resumo</h3>' + factList([
            { label: "Timestamp", value: formatDate(event.timestamp) },
            { label: "Origem", value: event.source },
            { label: "Ator", value: event.actor },
            { label: "Endpoint", value: event.endpoint },
            { label: "Severidade", value: event.severity }
        ]) + '<p>' + escapeHtml(event.description || "") + '</p></section><section><h3>Payload tecnico</h3><pre>' + escapeHtml(JSON.stringify(event, null, 2)) + '</pre></section><div class="event-drawer-actions"><button type="button" data-copy-drawer>Copiar detalhes</button><a href="/events/?q=' + encodeURIComponent(event.endpoint || "") + '">Abrir em Eventos</a></div>');
    }

    function openJobDrawer(id) {
        const job = findJob(id);
        if (!job) return;
        openDrawer("Tarefa", job.name, job.command, '<section><h3>Execucao</h3>' + factList([
            { label: "Status", value: labels[job.status] || job.status },
            { label: "Tipo", value: labels[job.type] || job.type },
            { label: "Criado por", value: job.createdBy },
            { label: "Criado em", value: formatDate(job.createdAt) },
            { label: "Iniciado em", value: formatDate(job.startedAt) },
            { label: "Finalizado em", value: formatDate(job.finishedAt) },
            { label: "Duracao", value: formatDuration(job.durationMs) },
            { label: "Exit code", value: job.exitCode == null ? "-" : job.exitCode }
        ]) + '</section><section><h3>Payload</h3><pre>' + escapeHtml(JSON.stringify(job.payload || {}, null, 2)) + '</pre></section><section><h3>Stdout</h3><pre>' + escapeHtml(job.stdout || "Sem saida.") + '</pre></section><section><h3>Stderr</h3><pre>' + escapeHtml(job.stderr || "Sem erro.") + '</pre></section><section><h3>Timeline</h3><p>' + escapeHtml((job.timeline || []).join(" -> ") || "-") + '</p></section><div class="event-drawer-actions"><button type="button" data-copy-job="' + escapeHtml(job.id) + '">Copiar saida</button><button type="button" data-rerun-job="' + escapeHtml(job.id) + '">Reexecutar</button></div>');
    }

    function copyText(value) {
        if (operational && typeof operational.copyText === "function") {
            return operational.copyText(value);
        }
        return navigator.clipboard.writeText(value);
    }

    function handleJobCopy(id) {
        const job = findJob(id);
        if (!job) return;
        copyText([job.name, job.command, job.stdout, job.stderr].filter(Boolean).join("\n")).then(function () {
            showToast("Saida da tarefa copiada.");
        }).catch(function () {
            showToast("Nao foi possivel copiar a saida.");
        });
    }

    function handleJobRerun(id) {
        if (!api || typeof api.rerunJob !== "function") return;
        api.rerunJob(id).then(function () {
            showToast("Tarefa reenfileirada no mock.");
            return reloadEndpoint();
        });
    }

    function handleJobCancel(id) {
        if (!api || typeof api.cancelJob !== "function") return;
        api.cancelJob(id).then(function () {
            showToast("Tarefa cancelada no mock.");
            return reloadEndpoint();
        });
    }

    root.addEventListener("click", function (event) {
        const tab = event.target.closest("[data-endpoint-tab]");
        if (tab) {
            activateTab(tab.dataset.endpointTab);
            return;
        }

        const tabJump = event.target.closest("[data-endpoint-tab-jump]");
        if (tabJump) {
            activateTab(tabJump.dataset.endpointTabJump);
            return;
        }

        const menuToggle = event.target.closest("[data-remote-menu-toggle]");
        if (menuToggle) {
            event.stopPropagation();
            const menu = menuToggle.parentElement.querySelector(".endpoint-remote-popover");
            if (!menu) return;
            const willOpen = menu.hidden;
            root.querySelectorAll(".endpoint-remote-popover").forEach(function (item) { item.hidden = true; });
            menu.hidden = !willOpen;
            return;
        }

        const action = event.target.closest("[data-endpoint-action]");
        if (action) {
            event.preventDefault();
            runEndpointAction(action.dataset.endpointAction || "execute_check");
            root.querySelectorAll(".endpoint-remote-popover").forEach(function (item) { item.hidden = true; });
            return;
        }

        const softwareCat = event.target.closest("[data-software-category]");
        if (softwareCat) {
            softwareCategory = softwareCat.dataset.softwareCategory || "all";
            renderActivePanel();
            activateTab(activeTab);
            return;
        }

        const softwareRiskButton = event.target.closest("[data-software-risk]");
        if (softwareRiskButton) {
            softwareRisk = softwareRiskButton.dataset.softwareRisk || "all";
            renderActivePanel();
            activateTab(activeTab);
            return;
        }

        const activityButton = event.target.closest("[data-activity-category]");
        if (activityButton) {
            activityCategory = activityButton.dataset.activityCategory || "all";
            renderActivePanel();
            activateTab(activeTab);
            return;
        }

        const eventRow = event.target.closest("[data-open-event]");
        if (eventRow) {
            openEventDrawer(eventRow.dataset.openEvent);
            return;
        }

        const jobOpen = event.target.closest("[data-open-job]");
        if (jobOpen) {
            openJobDrawer(jobOpen.dataset.openJob);
            return;
        }

        const jobCopy = event.target.closest("[data-copy-job]");
        if (jobCopy) {
            handleJobCopy(jobCopy.dataset.copyJob);
            return;
        }

        const jobRerun = event.target.closest("[data-rerun-job]");
        if (jobRerun) {
            handleJobRerun(jobRerun.dataset.rerunJob);
            return;
        }

        const jobCancel = event.target.closest("[data-cancel-job]");
        if (jobCancel) {
            handleJobCancel(jobCancel.dataset.cancelJob);
        }
    });

    root.addEventListener("input", function (event) {
        const input = event.target.closest("[data-software-search]");
        if (!input) return;
        softwareSearch = input.value || "";
        renderActivePanel();
        activateTab(activeTab);
        const next = root.querySelector("[data-software-search]");
        if (next) {
            next.focus();
            next.setSelectionRange(next.value.length, next.value.length);
        }
    });

    document.addEventListener("click", function (event) {
        if (!event.target.closest(".endpoint-remote-menu")) {
            root.querySelectorAll(".endpoint-remote-popover").forEach(function (item) { item.hidden = true; });
        }
        if (drawer && drawer.classList.contains("is-open") && !event.target.closest("[data-endpoint-drawer]") && !event.target.closest("[data-open-event]") && !event.target.closest("[data-open-job]")) {
            closeDrawer();
        }
    });

    document.addEventListener("keydown", function (event) {
        if (event.key === "Escape") {
            closeDrawer();
        }
    });

    document.querySelectorAll("[data-copy]").forEach(function (button) {
        button.addEventListener("click", function () {
            const value = button.getAttribute("data-copy") || "";
            if (!value.trim()) {
                showToast("Valor indisponivel para copia.");
                return;
            }
            copyText(value).then(function () {
                showToast("Valor copiado.");
            }).catch(function () {
                showToast("Nao foi possivel copiar.");
            });
        });
    });

    document.querySelectorAll("[data-endpoint-drawer-close], [data-endpoint-drawer-backdrop]").forEach(function (button) {
        button.addEventListener("click", closeDrawer);
    });

    document.addEventListener("click", function (event) {
        const copyDrawer = event.target.closest("[data-copy-drawer]");
        if (!copyDrawer || !drawerBody) return;
        copyText(drawerBody.textContent || "").then(function () {
            showToast("Detalhes copiados.");
        }).catch(function () {
            showToast("Nao foi possivel copiar.");
        });
    });

    document.addEventListener("click", function (event) {
        if (root.contains(event.target)) return;
        const jobCopy = event.target.closest("[data-copy-job]");
        if (jobCopy) {
            handleJobCopy(jobCopy.dataset.copyJob);
            return;
        }
        const jobRerun = event.target.closest("[data-rerun-job]");
        if (jobRerun) {
            handleJobRerun(jobRerun.dataset.rerunJob);
            return;
        }
        const jobCancel = event.target.closest("[data-cancel-job]");
        if (jobCancel) {
            handleJobCancel(jobCancel.dataset.cancelJob);
        }
    });

    root.querySelectorAll("a[href*='tickets']").forEach(function (link) {
        link.addEventListener("click", function (event) {
            event.preventDefault();
            runEndpointAction("create_ticket");
        });
    });

    ["nightowl:job-created", "nightowl:job-updated", "nightowl:event-created", "nightowl:alert-updated", "nightowl:mock-api-event"].forEach(function (eventName) {
        window.addEventListener(eventName, function () {
            window.setTimeout(reloadEndpoint, 140);
        });
    });

    reloadEndpoint();
}());
