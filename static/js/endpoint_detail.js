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
    const sourceBadge = document.querySelector("[data-endpoint-source-badge]");
    const realPayloadScript = document.getElementById("endpoint-detail-real-payload");

    let endpointDetail = null;
    let realEndpointPayload = null;
    let activeTab = "overview";
    let softwareSearch = "";
    let softwareCategory = "all";
    let softwareRisk = "all";
    let activityCategory = "all";
    let reloadTimer = null;
    let pollingUntil = 0;

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
        queued: "Pendente",
        pending: "Pendente",
        sent: "Despachado",
        dispatched: "Despachado",
        waiting_agent: "Aguardando agente",
        running: "Em execucao",
        completed: "Concluido",
        failed: "Falha",
        expired: "Expirado",
        cancelled: "Cancelado",
        canceled: "Cancelado",
        force_inventory: "Forcar inventario",
        defender_check: "Verificar Defender",
        disk_check: "Verificar disco",
        collect_disks: "Coletar discos",
        collect_security: "Verificar seguranca",
        collect_software: "Coletar software",
        collect_logs: "Coletar logs",
        ping: "Ping",
        cleanup_temp: "Limpeza temporaria",
        run_script: "Executar script",
        windows_update_scan: "Windows Update Scan",
        install_software: "Instalar software",
        update_agent: "Atualizar agente",
        restart_agent: "Reiniciar agente"
    };

    if (realPayloadScript && realPayloadScript.textContent) {
        try {
            realEndpointPayload = JSON.parse(realPayloadScript.textContent);
        } catch (error) {
            realEndpointPayload = null;
        }
    }

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

    function getCookie(name) {
        const parts = document.cookie ? document.cookie.split(";") : [];
        for (let i = 0; i < parts.length; i += 1) {
            const part = parts[i].trim();
            if (part.substring(0, name.length + 1) === name + "=") {
                return decodeURIComponent(part.substring(name.length + 1));
            }
        }
        return "";
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

    function parseVersion(value) {
        const parts = String(value || "").trim().split(".");
        if (!parts.length || parts.some(function (part) { return !/^\d+$/.test(part); })) return null;
        while (parts.length < 4) parts.push("0");
        return parts.slice(0, 4).map(function (part) { return Number(part); });
    }

    function compareVersions(left, right) {
        const a = parseVersion(left);
        const b = parseVersion(right);
        if (!a || !b) return null;
        for (let index = 0; index < 4; index += 1) {
            if (a[index] < b[index]) return -1;
            if (a[index] > b[index]) return 1;
        }
        return 0;
    }

    function agentState(installed, latest, fallback) {
        if (!installed || installed === "-") return "unknown";
        const comparison = latest && latest !== "-" ? compareVersions(installed, latest) : null;
        if (comparison == null) return fallback || "current";
        return comparison < 0 ? "outdated" : "current";
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

    function jobProgress(job) {
        const statusProgress = {
            queued: 10,
            pending: 10,
            sent: 25,
            dispatched: 25,
            waiting_agent: 25,
            running: 60,
            completed: 100,
            failed: 100,
            expired: 100,
            cancelled: 100,
            canceled: 100
        };
        if (job && Object.prototype.hasOwnProperty.call(statusProgress, job.status)) {
            return statusProgress[job.status];
        }
        return job && job.progress != null ? job.progress : 0;
    }

    function jobType(value) {
        return '<span class="job-type-chip">' + icon(jobTypeIcon(value)) + escapeHtml(labels[value] || value || "Tarefa") + "</span>";
    }

    function jobTypeIcon(value) {
        return {
            force_inventory: "package-search",
            defender_check: "shield-check",
            collect_security: "shield-check",
            disk_check: "hard-drive",
            collect_disks: "hard-drive",
            collect_software: "package-search",
            collect_logs: "file-search",
            ping: "activity",
            cleanup_temp: "sparkles",
            run_script: "code-2",
            install_software: "package-plus",
            windows_update_scan: "badge-check",
            update_agent: "download-cloud",
            restart_agent: "rotate-ccw"
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

    function setSourceBadge(label) {
        if (!sourceBadge || !label) return;
        sourceBadge.textContent = label;
    }

    function asObject(value) {
        return value && typeof value === "object" && !Array.isArray(value) ? value : {};
    }

    function asArray(value) {
        if (Array.isArray(value)) return value;
        if (value == null) return [];
        return [value];
    }

    function maybeObject(value) {
        return value && typeof value === "object" && !Array.isArray(value) ? value : null;
    }

    function normalizeRealEndpointPayload(payload) {
        if (!payload || !payload.endpoint) return null;
        const endpoint = asObject(payload.endpoint);
        const agentHealth = asObject(payload.agent_health);
        const latestAgentVersion = payload.latest_agent_version || payload.recommended_agent_version || endpoint.latest_agent_version || agentHealth.latest_agent_version || "-";
        const currentAgentState = agentState(endpoint.agent_version, latestAgentVersion, endpoint.agent_version_state || "current");
        const inventory = maybeObject(payload.inventory);
        const health = asObject(payload.health);
        const attention = asObject(payload.attention);
        const collectionState = asObject(payload.collection_state);
        const patches = maybeObject(payload.patches);
        const patchPending = patches && Array.isArray(patches.pending_updates_sample) ? patches.pending_updates_sample.map(function (item) {
            item = asObject(item);
            return { title: item.title || "Atualizacao pendente", severity: item.reboot_required ? "warning" : "info", kb: item.kb || "WU" };
        }) : [];
        const hotfixHistory = patches && Array.isArray(patches.installed_hotfixes) ? patches.installed_hotfixes.slice(0, 8).map(function (item) {
            item = asObject(item);
            return {
                title: item.hotfix_id || item.description || "Hotfix",
                status: "instalado",
                when: item.installed_on || "-"
            };
        }) : [];
        const security = maybeObject(payload.security);

        return {
            id: endpoint.id,
            hostname: endpoint.hostname || root.dataset.endpoint || "Endpoint",
            status: endpoint.status || "unknown",
            ip: endpoint.ip || root.dataset.ip || "",
            user: endpoint.user || "-",
            sector: endpoint.sector || "-",
            os: endpoint.os || "-",
            domain: endpoint.domain || "-",
            type: endpoint.type || "-",
            healthScore: health.score == null ? 0 : health.score,
            attention: attention.label || "Dados parciais",
            source: "real",
            dataSource: payload.data_source || "real",
            collectionState: collectionState,
            agent: {
                machineId: endpoint.machine_id || endpoint.id || "",
                version: endpoint.agent_version || "-",
                recommendedVersion: latestAgentVersion || "-",
                state: currentAgentState,
                mode: agentHealth.agent_mode || agentHealth.install_mode || "-",
                installMode: agentHealth.install_mode || "-",
                runtime: agentHealth.service_name ? "Servico Windows" : "-",
                lastRun: agentHealth.last_heartbeat_at ? formatDate(agentHealth.last_heartbeat_at) : "-",
                nextHeartbeat: "~5 min",
                serviceName: agentHealth.service_name || "NightOwlAgent",
                serviceStatus: agentHealth.service_status || "-",
                serviceStartType: agentHealth.service_start_type || "-",
                serviceAccount: agentHealth.service_account || "-",
                installPath: agentHealth.install_path || "-",
                legacyInstallPath: agentHealth.legacy_install_path || "-",
                configPath: agentHealth.config_path || "-",
                logPath: agentHealth.log_path || "-",
                logFile: agentHealth.log_file || "C:\\ProgramData\\NightOwl\\Logs\\agent-service.jsonl",
                heartbeatUrl: agentHealth.heartbeat_url || "-",
                jobsPullUrl: agentHealth.jobs_pull_url || "-",
                jobsResultUrl: agentHealth.jobs_result_url || "-",
                collectionEndpoints: agentHealth.collection_endpoints || {},
                lastHeartbeatAt: agentHealth.last_heartbeat_at || "",
                lastInventoryAt: agentHealth.last_inventory_at || "",
                lastSoftwareInventoryAt: agentHealth.last_software_inventory_at || "",
                lastSecurityInventoryAt: agentHealth.last_security_inventory_at || "",
                lastDiskInventoryAt: agentHealth.last_disk_inventory_at || "",
                lastPatchScanAt: agentHealth.last_patch_scan_at || "",
                lastJobResultAt: collectionState.last_job_result_at || "",
                lastError: agentHealth.last_error || "-"
            },
            inventory: inventory,
            hardware: maybeObject(payload.hardware),
            network: maybeObject(payload.network) || { interfaces: [] },
            disks: asArray(payload.disks).filter(function (item) { return item && typeof item === "object"; }),
            software: asArray(payload.software).filter(function (item) { return item && typeof item === "object"; }),
            security: security,
            patches: patches ? {
                compliance: patches.pending_updates_count ? 70 : 100,
                lastScan: patches.last_windows_update_check || patches.collected_at || "-",
                criticalPending: patches.pending_updates_count || 0,
                importantPending: patches.pending_updates_count || 0,
                rebootPending: !!patches.reboot_pending,
                rebootReasons: asArray(patches.reboot_pending_reasons),
                installedHotfixCount: patches.installed_hotfix_count || 0,
                lastInstall: patches.last_windows_update_install || "-",
                windowsBuild: patches.windows_build || "",
                pending: patchPending,
                history: hotfixHistory,
                raw: patches
            } : null,
            events: asArray(payload.events).filter(function (item) { return item && typeof item === "object"; }),
            jobs: asArray(payload.jobs).filter(function (item) { return item && typeof item === "object"; }).map(function (event) {
                if (event.type || event.status || event.createdAt) {
                    event.progress = jobProgress(event);
                    return event;
                }
                return {
                    id: event.id,
                    name: event.title,
                    type: event.metadata && event.metadata.job_type || "collect_logs",
                    command: event.eventType,
                    status: event.metadata && event.metadata.status || "completed",
                    createdBy: event.actor || "NightOwlAgent",
                    createdAt: event.timestamp,
                    startedAt: event.timestamp,
                    finishedAt: event.timestamp,
                    durationMs: event.metadata && event.metadata.duration_seconds ? event.metadata.duration_seconds * 1000 : 0,
                    result: event.description,
                    stdout: JSON.stringify(event.metadata || {}, null, 2),
                    stderr: event.metadata && event.metadata.error_message || "",
                    exitCode: event.metadata && event.metadata.exit_code,
                    payload: event.metadata || {},
                    resultJson: event.metadata || {},
                    progress: 100,
                    timeline: [event.eventType]
                };
            }),
            alerts: asArray(payload.alerts).filter(function (item) {
                if (!item || typeof item !== "object") return false;
                const title = String(item.title || "").toLowerCase();
                const type = String(item.alertType || item.alert_type || item.type || "").toLowerCase();
                return !(currentAgentState === "current" && (type === "agent_outdated" || title.indexOf("agente desatualizado") >= 0));
            }),
            tickets: asArray(payload.tickets).filter(function (item) { return item && typeof item === "object"; }),
            localAdmins: security && Array.isArray(security.localAdmins) ? security.localAdmins :
                security && security.raw && Array.isArray(security.raw.local_admins) ? security.raw.local_admins.map(function (item) { return typeof item === "string" ? item : asObject(item).name; }).filter(Boolean) :
                    security && security.raw && Array.isArray(security.raw.local_administrators) ? security.raw.local_administrators.map(function (item) { return asObject(item).name; }).filter(Boolean) : [],
            policyViolations: []
        };
    }

    function mergeEndpointDetails(realDetail, mockDetail) {
        if (!realDetail) return mockDetail || null;
        if (!mockDetail) return realDetail;
        const merged = Object.assign({}, mockDetail, realDetail);
        merged.source = "mixed";
        merged.inventory = realDetail.inventory || null;
        merged.disks = realDetail.disks && realDetail.disks.length ? realDetail.disks : [];
        merged.software = realDetail.software && realDetail.software.length ? realDetail.software : [];
        merged.security = realDetail.security || null;
        merged.patches = realDetail.patches || null;
        merged.events = realDetail.events && realDetail.events.length ? realDetail.events : [];
        merged.jobs = realDetail.jobs && realDetail.jobs.length ? realDetail.jobs : [];
        merged.alerts = realDetail.alerts && realDetail.alerts.length ? realDetail.alerts : mockDetail.alerts || [];
        merged.tickets = realDetail.tickets && realDetail.tickets.length ? realDetail.tickets : mockDetail.tickets || [];
        merged.collectionState = realDetail.collectionState || {};
        return merged;
    }

    function factList(items) {
        return '<dl class="endpoint-fact-list">' + items.map(function (item) {
            const rawValue = item.value == null || item.value === "" ? "-" : item.value;
            const renderedValue = item.html ? rawValue : escapeHtml(rawValue);
            const title = item.html ? "" : ' title="' + escapeHtml(rawValue) + '"';
            return '<div class="' + escapeHtml(item.className || "") + '"><dt>' + escapeHtml(item.label) + '</dt><dd class="' + (item.mono ? "mono" : "") + '"' + title + '>' + renderedValue + "</dd></div>";
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

    function agentVersionMarkup(agent) {
        return agentVersionDisplay(agent);
        const installed = agent.version || "-";
        const latest = agent.recommendedVersion || "-";
        if (agent.state === "outdated" && latest && latest !== "-") {
            return '<span class="agent-version-compare"><strong class="agent-installed-outdated">' + escapeHtml(installed) + '</strong><i>→</i><strong class="agent-latest-version">' + escapeHtml(latest) + '</strong></span>';
        }
        return '<span class="agent-version-compare"><strong class="agent-installed-current">' + escapeHtml(installed) + '</strong></span>';
    }

    function agentVersionDisplay(agent) {
        const installed = agent.version || "-";
        const latest = agent.recommendedVersion || "-";
        if (agent.state === "outdated" && latest && latest !== "-") {
            return '<span class="agent-version-compare"><strong class="agent-installed-outdated">' + escapeHtml(installed) + '</strong><i>&rarr;</i><strong class="agent-latest-version">' + escapeHtml(latest) + '</strong></span>';
        }
        return '<span class="agent-version-compare"><strong class="agent-installed-current">' + escapeHtml(installed) + '</strong></span>';
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
        return '<article class="panel endpoint-dense-card endpoint-health-dense">' +
            '<header><h2>' + icon("activity") + 'Saude do endpoint</h2><span class="endpoint-health-pill ' + healthClass(detail.healthScore) + '">' + escapeHtml(detail.healthScore || 0) + "/100</span></header>" +
            renderHealthBody(detail) + "</article>";
    }

    function renderHealthBody(detail) {
        const parts = healthParts(detail);
        return '<div class="endpoint-score-block"><strong>' + escapeHtml(detail.healthScore || 0) + '</strong><span>/100</span></div>' +
            '<div class="health-bar"><span class="health-fill ' + healthClass(detail.healthScore) + '" style="width:' + escapeHtml(detail.healthScore || 0) + '%"></span></div>' +
            '<div class="health-breakdown">' + parts.map(function (part) {
                const cls = part.score < 50 ? "critical" : part.score < 75 ? "warning" : "good";
                return '<div><span>' + escapeHtml(part.label) + '</span><strong class="health-part-' + cls + '">' + part.score + '</strong><em><i style="width:' + part.score + '%"></i></em></div>';
            }).join("") + "</div>";
    }

    function renderDiskList(disks, compact) {
        if (!disks || !disks.length) return emptyState("Nenhum disco coletado", "O proximo inventario deve preencher esta secao.", "hard-drive");
        if (compact) {
            return '<div class="endpoint-mini-disk-list">' + disks.map(function (disk) {
                return '<div><strong class="mono">' + escapeHtml(disk.name) + '</strong><span>' + escapeHtml(disk.usedPercent) + '%</span><em><i class="disk-' + escapeHtml(disk.severity) + '" style="width:' + escapeHtml(disk.usedPercent) + '%"></i></em><small>' + escapeHtml(disk.freeGb) + ' GB livres de ' + escapeHtml(disk.totalGb) + ' GB · ' + escapeHtml(disk.filesystem || "-") + "</small></div>";
            }).join("") + "</div>";
        }
        return '<div class="endpoint-disk-table">' + disks.map(function (disk) {
            return '<article class="disk-row"><div class="disk-row-header"><strong class="mono">' + escapeHtml(disk.name) + '</strong><span class="disk-pill disk-pill-' + escapeHtml(disk.severity) + '">' + escapeHtml(disk.usedPercent) + '% usado</span></div>' +
                '<div class="disk-meta"><span>Total: ' + escapeHtml(disk.totalGb) + ' GB</span><span>Livre: ' + escapeHtml(disk.freeGb) + ' GB</span><span>FS: ' + escapeHtml(disk.filesystem || "-") + '</span><span>BitLocker: ' + escapeHtml(disk.bitlockerStatus || "-") + '</span><span>Saude: ' + escapeHtml(disk.healthStatus || "-") + '</span></div>' +
                '<div class="disk-bar"><span class="disk-fill disk-' + escapeHtml(disk.severity) + '" style="width:' + escapeHtml(disk.usedPercent) + '%"></span></div>' +
                '<footer>' + actionButton("check_disk", "Verificar disco", "hard-drive") + actionButton("run_cleanup", "Executar limpeza", "sparkles") + actionButton("create_ticket", "Criar chamado", "ticket") + "</footer></article>";
        }).join("") + "</div>";
    }

    function renderAlerts(alerts, limit) {
        const severityOrder = { critical: 0, warning: 1, security: 2, info: 3, success: 4 };
        const rows = (alerts || []).slice().sort(function (left, right) {
            const leftOrder = severityOrder[left.severity] == null ? 9 : severityOrder[left.severity];
            const rightOrder = severityOrder[right.severity] == null ? 9 : severityOrder[right.severity];
            return leftOrder - rightOrder;
        }).slice(0, limit || alerts.length);
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
            const type = String(event.eventType || "").toLowerCase();
            const title = String(event.title || "").toLowerCase();
            if (!applyFilter && (type === "job.pull_requested" || type === "job.pull" || (title.indexOf("pull") >= 0 && title.indexOf("job") >= 0))) return false;
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
        if (!rows.length) return emptyState("Nenhuma tarefa tecnica executada neste endpoint", "Use as acoes rapidas para enfileirar jobs reais para o agente.", "list-checks");
        return '<div class="endpoint-job-list">' + rows.map(function (job) {
            const progress = jobProgress(job);
            const result = jobResultLabel(job);
            const primaryTime = job.finishedAt || job.startedAt || job.dispatchedAt || job.createdAt;
            return '<article class="endpoint-job-item endpoint-job-item-' + escapeHtml(job.status || "pending") + '">' +
                '<div class="endpoint-job-main">' +
                    '<div class="endpoint-job-title">' + jobBadge(job.status) + jobType(job.type) + '<small>por ' + escapeHtml(job.createdBy || "-") + '</small></div>' +
                    '<div class="endpoint-job-progressline"><div class="endpoint-job-progress endpoint-job-progress-' + escapeHtml(job.status || "pending") + '"><span style="width:' + escapeHtml(progress) + '%"></span></div><strong>' + escapeHtml(progress) + '%</strong></div>' +
                    '<span class="job-result-text" title="' + escapeHtml(result) + '">' + escapeHtml(result) + '</span>' +
                '</div>' +
                '<div class="endpoint-job-meta">' +
                    '<span><b>Horario</b>' + escapeHtml(formatDate(primaryTime)) + '</span>' +
                    '<span><b>Duracao</b>' + escapeHtml(formatDuration(job.durationMs)) + '</span>' +
                '</div>' +
                '<div class="endpoint-job-actions">' +
                    '<button type="button" data-open-job="' + escapeHtml(job.id) + '">Detalhes</button>' +
                    '<button type="button" data-copy-job="' + escapeHtml(job.id) + '">Copiar saida</button>' +
                    (job.status === "completed" ? '<button type="button" data-refresh-endpoint>Atualizar dados</button>' : "") +
                '</div>' +
            '</article>';
        }).join("") + "</div>";
    }

    function actionGroup(title, buttons) {
        return '<section class="endpoint-action-group"><h3>' + escapeHtml(title) + '</h3><div>' + buttons.join("") + '</div></section>';
    }

    function renderQuickActions() {
        return '<div class="endpoint-action-groups">' +
            actionGroup("Inventario", [
                actionButton("force_inventory", "Forcar inventario", "refresh-ccw"),
                actionButton("collect_software", "Coletar software", "package-search")
            ]) +
            actionGroup("Seguranca", [
                actionButton("check_defender", "Verificar Defender", "shield-check"),
                actionButton("windows_update_scan", "Windows Update scan", "badge-check")
            ]) +
            actionGroup("Diagnostico", [
                actionButton("check_disk", "Coletar discos", "hard-drive"),
                actionButton("collect_logs", "Coletar logs", "file-search"),
                actionButton("ping", "Ping", "activity")
            ]) +
            actionGroup("Agente", [
                actionButton("update_agent", "Atualizar agente", "download-cloud"),
                actionButton("restart_agent", "Reiniciar agente", "rotate-ccw")
            ]) +
            actionGroup("Avancado", [
                '<button type="button" disabled title="Execucao arbitraria sera habilitada em fase futura">' + icon("code-2") + 'Script futuro</button>'
            ]) +
        '</div>';
    }

    function jobResultLabel(job) {
        const result = job.resultJson || {};
        if (job.type === "update_agent") {
            const updateStatus = result.update_status || (result.details && result.details.reason);
            if (updateStatus === "no_update_available" || result.already_up_to_date || (result.details && result.details.reason === "already_current")) return "Agente ja atualizado";
            if (updateStatus === "success" || job.status === "completed") {
                const details = result.details || {};
                const version = result.installed_version || result.installedVersion || details.installed_version || details.installedVersion || result.version || "";
                return version ? "Atualizado para " + version : "Atualizado com sucesso";
            }
            if (job.status === "failed") return job.errorMessage || result.message || "Falha na atualizacao";
        }
        return job.result || job.errorMessage || "-";
    }

    function lastUpdateJob(detail) {
        return (detail.jobs || []).find(function (job) { return job.type === "update_agent"; }) || null;
    }

    function lastAgentUpdate(detail) {
        const job = lastUpdateJob(detail);
        if (!job || job.status !== "completed") return "-";
        return formatDate(job.finishedAt);
    }

    function lastUpdateJobLabel(detail) {
        const job = lastUpdateJob(detail);
        if (!job) return "Nenhum";
        return (labels[job.status] || job.status) + " - " + jobResultLabel(job);
    }

    function overviewSlot(name) {
        return root.querySelector('[data-overview-slot="' + name + '"]');
    }

    function setSlot(name, html) {
        const target = overviewSlot(name);
        if (target) target.innerHTML = html;
    }

    function setSlotBadge(name, className, html) {
        const target = overviewSlot(name);
        if (!target) return;
        target.className = className;
        target.innerHTML = html;
    }

    function renderOverviewSlots(detail) {
        const inv = detail.inventory || {};
        const security = detail.security || {};
        const agent = detail.agent || {};
        const healthScore = detail.healthScore || 0;

        setSlotBadge("health-badge", "endpoint-health-pill " + healthClass(healthScore), escapeHtml(healthScore) + "/100");
        setSlot("health-body", renderHealthBody(detail));

        setSlotBadge("agent-badge", "agent-version-pill agent-" + escapeHtml(agent.state || "unknown"), escapeHtml(agent.state === "current" ? "Atual" : labels[agent.state] || agent.state || "Sem informacao"));
        setSlot("agent-body",
            '<div class="agent-version-panel">' + agentVersionDisplay(agent) + (agent.state === "outdated" ? '<small>Atualizacao disponivel</small>' : '<small>Versao atual</small>') + '</div>' +
            factList([
                { label: "Servico/status", value: (agent.serviceName || "-") + " / " + (agent.serviceStatus || "-") },
                { label: "Modo/runtime", value: (agent.mode || "-") + " / " + (agent.runtime || "-") },
                { label: "Ultima comunicacao", value: agent.lastRun },
                { label: "Ultimo job update", value: lastUpdateJobLabel(detail), className: "endpoint-fact-wide" },
                { label: "Proximo heartbeat", value: agent.nextHeartbeat },
                { label: "Log atual", value: agent.logFile, mono: true, className: "endpoint-fact-wide" }
            ])
        );

        setSlotBadge("security-badge", "severity-badge severity-" + escapeHtml(security.status === "critical" ? "critical" : security.status === "attention" ? "warning" : security.status === "ok" ? "success" : "info"), escapeHtml(security.status || "unknown"));
        setSlot("security-body", factList([
            { label: "Defender/AV", value: security.antivirus },
            { label: "Assinatura", value: security.signature, mono: true },
            { label: "Firewall", value: security.firewall },
            { label: "BitLocker", value: security.bitlocker },
            { label: "Ultima seguranca", value: formatDate(agent.lastSecurityInventoryAt), className: "endpoint-fact-wide" }
        ]));

        setSlot("summary-body", factList([
            { label: "Hostname", value: detail.hostname, mono: true },
            { label: "IP principal", value: detail.ip, mono: true },
            { label: "Usuario", value: detail.user },
            { label: "Setor/tag", value: detail.sector },
            { label: "Sistema", value: detail.os },
            { label: "Dominio", value: detail.domain }
        ]));

        setSlot("inventory-body", factList([
            { label: "Fabricante", value: inv.manufacturer },
            { label: "Modelo", value: inv.model },
            { label: "Serial", value: inv.serial, mono: true },
            { label: "CPU", value: inv.cpu },
            { label: "Memoria", value: inv.memoryGb ? inv.memoryGb + " GB" : "-" },
            { label: "Disponivel", value: inv.availableMemoryGb ? inv.availableMemoryGb + " GB" : "-" },
            { label: "Ultimo inventario", value: inv.lastFullInventory, className: "endpoint-fact-wide" }
        ]));

        setSlot("disks-body", renderDiskList(detail.disks, true));
        setSlot("jobs-body", renderJobs(detail.jobs, 6));
        setSlot("actions-body", renderQuickActions());
        setSlot("alerts-body", renderAlerts(detail.alerts, 4));
        setSlot("events-body", renderEvents(detail.events, 6, false));
        setSlot("tickets-body", renderTickets(detail.tickets));
    }

    function renderInventory(detail) {
        if (detail.collectionState && !detail.collectionState.inventory) {
            return '<section class="panel endpoint-dense-card">' + emptyState("Inventario ainda nao coletado", "O agente ja comunicou heartbeat, mas a coleta completa de sistema/hardware/rede ainda nao chegou.", "cpu") + "</section>";
        }
        const inv = detail.inventory || {};
        return '<div class="endpoint-inventory-grid">' +
            '<article class="panel endpoint-dense-card"><header><h2>' + icon("monitor") + 'Sistema operacional</h2></header>' + factList([
                { label: "Sistema", value: detail.os },
                { label: "Versao", value: inv.osVersion, mono: true },
                { label: "Build", value: inv.build, mono: true },
                { label: "Arquitetura", value: inv.architecture },
                { label: "Dominio", value: detail.domain },
                { label: "Tipo", value: inv.machineType },
                { label: "Instalacao", value: formatDate(inv.installDate) },
                { label: "Ultimo boot", value: formatDate(inv.lastBootTime) },
                { label: "Timezone", value: inv.timezone },
                { label: "Locale", value: inv.locale },
                { label: "Uptime", value: inv.uptime }
            ]) + '</article>' +
            '<article class="panel endpoint-dense-card"><header><h2>' + icon("cpu") + 'Hardware</h2></header>' + factList([
                { label: "Fabricante", value: inv.manufacturer },
                { label: "Modelo", value: inv.model },
                { label: "Serial", value: inv.serial, mono: true },
                { label: "CPU", value: inv.cpu },
                { label: "Fabricante CPU", value: inv.cpuManufacturer },
                { label: "Cores fisicos", value: inv.physicalCores },
                { label: "Processadores logicos", value: inv.logicalProcessors },
                { label: "Memoria", value: inv.memoryGb ? inv.memoryGb + " GB" : "-" },
                { label: "Memoria disponivel", value: inv.availableMemoryGb ? inv.availableMemoryGb + " GB" : "-" },
                { label: "BIOS/UEFI", value: inv.bios },
                { label: "BIOS data", value: formatDate(inv.biosReleaseDate) },
                { label: "Placa-mae", value: inv.motherboard },
                { label: "TPM", value: inv.tpmPresent == null ? "-" : inv.tpmPresent ? (inv.tpmEnabled ? "Presente/ativo" : "Presente/inativo") : "Ausente" },
                { label: "Bateria", value: inv.batteryPresent ? ("Presente · " + (inv.batteryStatus || "-")) : "Nao detectada" }
            ]) + '</article>' +
            '<article class="panel endpoint-dense-card endpoint-span-2"><header><h2>' + icon("network") + 'Rede e coleta</h2></header>' + factList([
                { label: "IP principal", value: inv.primaryIp || detail.ip, mono: true },
                { label: "MAC principal", value: inv.primaryMac, mono: true },
                { label: "Gateway", value: inv.defaultGateway, mono: true },
                { label: "DNS", value: (inv.dnsServers || []).join(", "), mono: true },
                { label: "MACs", value: (inv.macs || []).join(", "), mono: true },
                { label: "Ultimo inventario completo", value: inv.lastFullInventory },
                { label: "Agente", value: detail.agent && detail.agent.version, mono: true }
            ]) + ((inv.adapters || []).length ? '<div class="endpoint-task-list">' + inv.adapters.map(function (adapter) { adapter = asObject(adapter); return '<article><span class="severity-badge severity-info">' + escapeHtml(adapter.adapter_type || adapter.type || "NIC") + '</span><div><strong>' + escapeHtml(adapter.name || adapter.description || "Adaptador") + '</strong><p class="mono">' + escapeHtml((adapter.ipv4_addresses || adapter.ips || []).join(", ") || "-") + ' · ' + escapeHtml(adapter.mac_address || adapter.mac || "-") + '</p></div></article>'; }).join("") + '</div>' : '') + '</article></div>' +
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
        if (detail.collectionState && !detail.collectionState.software) {
            return '<section class="panel endpoint-dense-card software-panel">' + emptyState("Inventario de software ainda nao coletado", "Assim que a rotina de software enviar dados, esta aba sera preenchida.", "package") + "</section>";
        }
        const items = detail.software || [];
        const term = softwareSearch.toLowerCase();
        const filtered = items.filter(function (item) {
            const text = [item.name, item.category, item.risk, item.version, item.publisher, item.architecture, item.source].join(" ").toLowerCase();
            if (term && text.indexOf(term) < 0) return false;
            if (softwareCategory !== "all" && item.category !== softwareCategory) return false;
            if (softwareRisk !== "all" && item.risk !== softwareRisk) return false;
            return true;
        });
        const counters = softwareCounters(items);
        return '<section class="panel endpoint-dense-card software-panel">' +
            '<div class="panel-header software-header"><div><h2><span class="section-icon">' + icon("package") + '</span>Softwares</h2><p>Programas detectados pelo inventario real deste endpoint.</p></div>' +
            '<label class="software-search"><span>Buscar</span><input data-software-search type="search" value="' + escapeHtml(softwareSearch) + '" placeholder="Nome, versao ou fabricante"></label></div>' +
            '<div class="endpoint-inline-metrics">' + counters.map(function (counter) { return '<div><span>' + escapeHtml(counter.label) + '</span><strong>' + counter.value + '</strong></div>'; }).join("") + '</div>' +
            '<div class="software-chip-row" role="group" aria-label="Categorias">' + ["all", "microsoft", "security", "remote", "admin", "other"].map(function (cat) {
                return '<button class="software-chip ' + (softwareCategory === cat ? "active" : "") + '" type="button" data-software-category="' + cat + '">' + escapeHtml(cat === "all" ? "Todos" : cat) + "</button>";
            }).join("") + '</div>' +
            '<div class="software-chip-row" role="group" aria-label="Risco">' + ["all", "low", "medium", "high"].map(function (risk) {
                return '<button class="software-chip ' + (softwareRisk === risk ? "active" : "") + '" type="button" data-software-risk="' + risk + '">' + escapeHtml(risk === "all" ? "Todos os riscos" : risk) + "</button>";
            }).join("") + '</div>' +
            (filtered.length ? '<div class="table-wrap software-table-wrap"><table class="endpoint-table software-table"><thead><tr><th>Nome</th><th>Categoria</th><th>Risco</th><th>Versao</th><th>Fabricante</th><th>Arquitetura</th><th>Origem</th><th>Instalado em</th><th>Acoes futuras</th></tr></thead><tbody>' + filtered.map(function (software) {
                return '<tr><td><strong>' + escapeHtml(software.name) + '</strong></td><td><span class="software-badge category-' + escapeHtml(software.category) + '">' + escapeHtml(software.category) + '</span></td><td><span class="software-badge risk-' + escapeHtml(software.risk) + '">' + escapeHtml(software.risk) + '</span></td><td class="mono">' + escapeHtml(software.version || "-") + '</td><td>' + escapeHtml(software.publisher || "-") + '</td><td>' + escapeHtml(software.architecture || "-") + '</td><td class="mono">' + escapeHtml(software.source || "-") + '</td><td>' + escapeHtml(formatDate(software.installedAt)) + '</td><td class="software-actions"><button type="button" data-endpoint-action="copy_summary">Permitir</button><button type="button" data-endpoint-action="copy_summary">Proibir</button><button type="button" data-endpoint-action="create_ticket">Solicitar remocao</button></td></tr>';
            }).join("") + "</tbody></table></div>" : emptyState("Nenhum software no filtro", "Ajuste busca, categoria ou risco.", "package")) + "</section>";
    }

    function renderSecurity(detail) {
        if (detail.collectionState && !detail.collectionState.security) {
            return '<section class="panel endpoint-dense-card">' + emptyState("Seguranca ainda nao coletada", "Defender, firewall, BitLocker e administradores locais aparecerao apos a coleta de seguranca.", "shield") + "</section>";
        }
        const security = detail.security || {};
        const admins = detail.localAdmins || [];
        const violations = detail.policyViolations || [];
        const securityEvents = (detail.events || []).filter(function (event) { return event.category === "security" || event.eventType.indexOf("security") >= 0 || event.eventType.indexOf("policy") >= 0; });
        return '<div class="endpoint-compact-grid security-grid">' +
            '<article class="panel endpoint-dense-card"><header><h2>' + icon("shield") + 'Protecao local</h2>' + severityBadge(security.status === "critical" ? "critical" : security.status === "attention" ? "warning" : security.status === "ok" ? "success" : "info", security.status || "unknown") + '</header>' + factList([
                { label: "Antivirus", value: security.antivirus },
                { label: "Defender ativo", value: security.defenderEnabled == null ? "-" : security.defenderEnabled ? "Sim" : "Nao" },
                { label: "Tempo real", value: security.realtimeEnabled == null ? "-" : security.realtimeEnabled ? "Sim" : "Nao" },
                { label: "Assinatura", value: security.signature, mono: true },
                { label: "Assinatura atualizada", value: formatDate(security.signatureUpdatedAt) },
                { label: "Ultimo quick scan", value: formatDate(security.lastQuickScan) },
                { label: "Ultimo full scan", value: formatDate(security.lastFullScan) },
                { label: "Firewall", value: security.firewall },
                { label: "BitLocker", value: security.bitlocker },
                { label: "RDP", value: security.rdpEnabled == null ? "-" : security.rdpEnabled ? "Habilitado" : "Desabilitado" },
                { label: "UAC", value: security.uacEnabled == null ? "-" : security.uacEnabled ? "Habilitado" : "Desabilitado" }
            ]) + '<div class="endpoint-remote-actions-grid">' + actionButton("check_defender", "Verificar Defender", "shield-check") + actionButton("execute_check", "Verificar seguranca", "shield-alert") + actionButton("create_ticket", "Criar chamado", "ticket") + actionButton("copy_summary", "Criar regra", "file-plus-2") + '</div></article>' +
            '<article class="panel endpoint-dense-card"><header><h2>' + icon("shield-check") + 'Produtos AV detectados</h2></header>' + ((security.antivirusProducts || []).length ? '<div class="endpoint-task-list">' + security.antivirusProducts.map(function (item) { item = asObject(item); return '<article><span class="severity-badge severity-info">AV</span><div><strong>' + escapeHtml(item.name || "Antivirus") + '</strong><p class="mono">' + escapeHtml(item.product_state || item.instance_guid || "-") + '</p></div></article>'; }).join("") + '</div>' : emptyState("Nenhum AV via SecurityCenter2", "Servidores podem nao expor essa classe WMI.", "shield")) + '</article>' +
            '<article class="panel endpoint-dense-card"><header><h2>' + icon("radio-tower") + 'Acesso remoto</h2></header>' + ((security.remoteTools || []).length ? '<div class="endpoint-task-list">' + security.remoteTools.map(function (tool) { return '<article><span class="severity-badge severity-security">Risco</span><div><strong>' + escapeHtml(tool) + '</strong><p>Ferramenta de acesso remoto detectada.</p></div></article>'; }).join("") + '</div>' : emptyState("Sem acesso remoto de risco", "Nenhuma ferramenta sensivel listada.", "check-circle")) + '</article>' +
            '<article class="panel endpoint-dense-card"><header><h2>' + icon("users") + 'Administradores locais</h2></header>' + (admins.length ? '<div class="endpoint-task-list">' + admins.map(function (admin) { return '<article><span class="severity-badge severity-info">Admin</span><div><strong class="mono">' + escapeHtml(admin) + '</strong><p>Membro local detectado pelo agente.</p></div></article>'; }).join("") + '</div>' : emptyState("Sem administradores coletados", "A coleta ainda nao retornou membros locais.", "users")) + '</article>' +
            '<article class="panel endpoint-dense-card"><header><h2>' + icon("shield-alert") + 'Politicas violadas</h2></header>' + (violations.length ? '<div class="endpoint-task-list">' + violations.map(function (item) { return '<article><span class="severity-badge severity-' + escapeHtml(item.severity) + '">' + escapeHtml(item.severity) + '</span><div><strong>' + escapeHtml(item.policy) + '</strong><p>' + escapeHtml(item.item) + '</p></div></article>'; }).join("") + '</div>' : emptyState("Sem violacoes de politica", "Nada pendente para este endpoint.", "check-circle")) + '</article>' +
            '<article class="panel endpoint-dense-card endpoint-span-2"><header><h2>' + icon("history") + 'Ultimos eventos de seguranca</h2></header>' + renderEvents(securityEvents, 5, false) + '</article></div>';
    }

    function renderPatches(detail) {
        if (detail.collectionState && !detail.collectionState.patches) {
            return '<section class="panel endpoint-dense-card">' + emptyState("Patches ainda nao coletados", "A rotina de Windows Update ainda nao enviou status para este endpoint.", "badge-check") + "</section>";
        }
        const patches = detail.patches || {};
        const pending = patches.pending || [];
        const history = patches.history || [];
        return '<div class="endpoint-compact-grid security-grid">' +
            '<article class="panel endpoint-dense-card"><header><h2>' + icon("badge-check") + 'Compliance</h2><span class="endpoint-health-pill ' + healthClass(patches.compliance) + '">' + escapeHtml(patches.compliance || 0) + '%</span></header>' +
            '<div class="endpoint-score-block"><strong>' + escapeHtml(patches.compliance || 0) + '</strong><span>%</span></div><div class="health-bar"><span class="health-fill ' + healthClass(patches.compliance) + '" style="width:' + escapeHtml(patches.compliance || 0) + '%"></span></div>' +
            factList([{ label: "Ultima verificacao", value: formatDate(patches.lastScan) }, { label: "Ultima instalacao", value: formatDate(patches.lastInstall) }, { label: "Pendentes", value: patches.criticalPending }, { label: "Hotfixes instalados", value: patches.installedHotfixCount }, { label: "Build Windows", value: patches.windowsBuild, mono: true }, { label: "Reboot pendente", value: patches.rebootPending ? "Sim" : "Nao" }, { label: "Motivos reboot", value: (patches.rebootReasons || []).join(", ") || "-" }]) +
            '<div class="endpoint-remote-actions-grid">' + actionButton("windows_update_scan", "Verificar atualizacoes", "search-check") + '<button type="button" disabled title="Instalacao de patches ainda nao foi liberada">' + icon("download-cloud") + 'Instalar patches futuro</button>' + actionButton("copy_summary", "Agendar manutencao", "calendar-clock") + actionButton("copy_summary", "Criar tarefa", "list-plus") + '</div></article>' +
            '<article class="panel endpoint-dense-card"><header><h2>' + icon("download") + 'Patches pendentes</h2></header>' + (pending.length ? '<div class="endpoint-task-list">' + pending.map(function (patch) { return '<article><span class="severity-badge severity-' + escapeHtml(patch.severity || "info") + '">' + escapeHtml(patch.kb || "KB") + '</span><div><strong>' + escapeHtml(patch.title) + '</strong><p>' + escapeHtml(labels[patch.severity] || patch.severity || "Info") + '</p></div></article>'; }).join("") + '</div>' : emptyState("Sem patches pendentes", "A coleta read-only nao retornou atualizacoes pendentes.", "check-circle")) + '</article>' +
            '<article class="panel endpoint-dense-card endpoint-span-2"><header><h2>' + icon("history") + 'Historico recente</h2></header>' + (history.length ? '<div class="endpoint-task-list">' + history.map(function (item) { return '<article><span class="agent-version-pill agent-current">' + escapeHtml(item.status || "ok") + '</span><div><strong>' + escapeHtml(item.title) + '</strong><p>' + escapeHtml(item.when || "-") + '</p></div></article>'; }).join("") + '</div>' : emptyState("Sem historico de patches", "Nenhuma execucao registrada.", "history")) + '</article></div>';
    }

    function renderActivity(detail) {
        const categories = ["all", "agent", "inventory", "alerts", "jobs", "security", "system"];
        return '<section class="panel endpoint-dense-card"><header><h2>' + icon("history") + 'Timeline do endpoint</h2><a href="/events/?q=' + encodeURIComponent(detail.hostname) + '">Ver todos os eventos deste endpoint</a></header>' +
            '<div class="software-chip-row">' + categories.map(function (cat) { return '<button type="button" class="software-chip ' + (activityCategory === cat ? "active" : "") + '" data-activity-category="' + cat + '">' + escapeHtml(cat === "all" ? "Todos" : cat) + "</button>"; }).join("") + '</div>' +
            renderEvents(detail.events, 30, true) + "</section>";
    }

    function renderTasks(detail) {
        return '<section class="panel endpoint-dense-card"><header><h2>' + icon("list-checks") + 'Acoes e Jobs</h2><button type="button" data-refresh-endpoint>' + icon("refresh-ccw") + 'Atualizar lista</button></header>' +
            '<div class="endpoint-remote-actions-grid">' +
            actionButton("ping", "Ping", "activity") +
            actionButton("force_inventory", "Forcar inventario", "refresh-ccw") +
            actionButton("collect_disks", "Coletar discos", "hard-drive") +
            actionButton("collect_software", "Coletar software", "package-search") +
            actionButton("restart_agent", "Reiniciar agente", "rotate-ccw") +
            actionButton("update_agent", "Atualizar agente", "download-cloud") +
            "</div>" +
            renderJobs(detail.jobs, 80) +
            "</section>";
    }

    function panel(name) {
        return root.querySelector('[data-endpoint-tab-panel="' + name + '"]');
    }

    function renderActivePanel() {
        if (!endpointDetail) return;
        renderOverviewSlots(endpointDetail);
        const renderers = {
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

    function fetchRealEndpointPayload() {
        const id = root.dataset.endpointId || "";
        if (!id) return Promise.resolve(realEndpointPayload);
        return fetch("/api/endpoints/" + encodeURIComponent(id) + "/?_=" + encodeURIComponent(Date.now()), {
            headers: {
                "Accept": "application/json",
                "Cache-Control": "no-cache"
            },
            cache: "no-store",
            credentials: "same-origin"
        }).then(function (response) {
            if (!response.ok) throw new Error("endpoint_fetch_failed");
            return response.json();
        }).then(function (payload) {
            realEndpointPayload = payload;
            return payload;
        });
    }

    function reloadEndpoint(skipFetch) {
        const fetchPromise = skipFetch ? Promise.resolve(realEndpointPayload) : fetchRealEndpointPayload().catch(function () {
            return realEndpointPayload;
        });
        return fetchPromise.then(function () {
        const realDetail = normalizeRealEndpointPayload(realEndpointPayload);
        if (realDetail) {
            endpointDetail = realDetail;
            setSourceBadge("Dados reais");
            renderActivePanel();
            activateTab(activeTab);
            return Promise.resolve(realDetail);
        }
        const id = root.dataset.endpointId || root.dataset.endpoint;
        if (!api || typeof api.getEndpointById !== "function") {
            if (endpointDetail) return Promise.resolve(endpointDetail);
            showToast("Camada mockNightowlApi indisponivel.");
            return Promise.resolve(null);
        }
        return api.getEndpointById(id).then(function (mockDetail) {
            if (mockDetail || !realDetail) return mockDetail;
            return api.getEndpointById(root.dataset.endpoint || "").then(function (byHostname) {
                return byHostname || mockDetail;
            });
        }).then(function (mockDetail) {
            const detail = mergeEndpointDetails(realDetail, mockDetail);
            if (!detail) {
                root.querySelectorAll("[data-dynamic-endpoint-panel]").forEach(function (target) {
                    target.innerHTML = emptyState("Endpoint nao encontrado", "A API real e a camada mockada nao retornaram dados para este identificador.", "monitor-x");
                });
                return null;
            }
            endpointDetail = detail;
            setSourceBadge(detail.source === "mixed" ? "Misto" : detail.source === "real" ? "Dados reais" : "Preview mockado");
            renderActivePanel();
            activateTab(activeTab);
            return detail;
        }).catch(function () {
            if (realDetail) {
                endpointDetail = realDetail;
                setSourceBadge("Dados reais");
                renderActivePanel();
                activateTab(activeTab);
                return realDetail;
            }
            return null;
        });
        });
    }

    function schedulePolling() {
        pollingUntil = Date.now() + 90000;
        if (reloadTimer) return;
        reloadTimer = window.setInterval(function () {
            if (Date.now() > pollingUntil) {
                window.clearInterval(reloadTimer);
                reloadTimer = null;
                return;
            }
            reloadEndpoint(false).then(function (detail) {
                const running = detail && (detail.jobs || []).some(function (job) {
                    return ["queued", "pending", "sent", "dispatched", "waiting_agent", "running"].indexOf(job.status) >= 0;
                });
                if (!running && reloadTimer) {
                    showToast("Dados do endpoint atualizados.");
                    window.clearInterval(reloadTimer);
                    reloadTimer = null;
                }
            });
        }, 5000);
    }

    function createRealJob(action) {
        const id = root.dataset.endpointId || "";
        const body = new URLSearchParams();
        body.set("action", action || "");
        if (action === "ping" && endpointDetail && endpointDetail.ip) {
            body.set("target", endpointDetail.ip);
        }
        return fetch("/api/endpoints/" + encodeURIComponent(id) + "/jobs/", {
            method: "POST",
            credentials: "same-origin",
            headers: {
                "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8",
                "X-CSRFToken": getCookie("csrftoken"),
                "Accept": "application/json"
            },
            body: body.toString()
        }).then(function (response) {
            return response.text().then(function (text) {
                let payload = {};
                try {
                    payload = text ? JSON.parse(text) : {};
                } catch (error) {
                    payload = { detail: text || response.statusText || "job_create_failed" };
                }
                if (!response.ok) {
                    if (payload && payload.error === "update_job_already_pending" && payload.job) {
                        payload.status = "already_pending";
                        return payload;
                    }
                    throw new Error(payload.detail || payload.error || response.statusText || "job_create_failed");
                }
                return payload;
            });
        });
    }

    function runEndpointAction(action) {
        if (!endpointDetail) return;
        if (action === "execute_check" || action === "run_cleanup") {
            showToast("Esta acao ainda esta bloqueada ate liberarmos scripts/limpeza remota com seguranca.");
            return;
        }
        const realActions = ["force_inventory", "check_defender", "check_disk", "collect_disks", "collect_logs", "ping", "collect_software", "windows_update_scan", "update_agent", "restart_agent"];
        if (endpointDetail.source !== "mock" && realActions.indexOf(action) >= 0) {
            if (action === "update_agent") {
                const confirmed = window.confirm("Deseja enviar um comando de atualizacao do agente para este endpoint? O servico pode ser reiniciado durante o processo.");
                if (!confirmed) return;
                showToast("Enviando job de atualizacao para o agente...");
            } else if (action === "restart_agent") {
                const confirmed = window.confirm("Deseja enviar um comando para reiniciar o agente neste endpoint?");
                if (!confirmed) return;
                showToast("Enviando job de reinicio para o agente...");
            }
            createRealJob(action).then(function (payload) {
                const isPendingUpdate = payload.status === "already_pending";
                showToast(action === "update_agent"
                    ? (isPendingUpdate ? "Ja existe um job de atualizacao pendente para este endpoint." : "Job enviado com sucesso.")
                    : action === "restart_agent"
                        ? "Job enviado com sucesso."
                    : "Job " + (labels[payload.job.type] || payload.job.type) + " enfileirado para o agente.");
                if (!endpointDetail.jobs) endpointDetail.jobs = [];
                if (payload.job && !endpointDetail.jobs.some(function (job) { return job.id === payload.job.id; })) {
                    endpointDetail.jobs.unshift(payload.job);
                }
                renderActivePanel();
                activateTab(action === "update_agent" || action === "restart_agent" ? "tasks" : activeTab);
                schedulePolling();
                return reloadEndpoint(false);
            }).catch(function (error) {
                showToast(error.message || "Nao foi possivel criar o job tecnico.");
            });
            return;
        }
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
        const result = job.resultJson || {};
        openDrawer("Tarefa", job.name, job.command, '<section><h3>Execucao</h3>' + factList([
            { label: "Status", value: labels[job.status] || job.status },
            { label: "Tipo", value: labels[job.type] || job.type },
            { label: "Endpoint", value: job.endpoint || (endpointDetail && endpointDetail.hostname) || "-" },
            { label: "Versao anterior", value: result.previous_version || result.previousVersion || (result.details && (result.details.previous_version || result.details.previousVersion)) || "-" },
            { label: "Versao nova", value: result.installed_version || result.installedVersion || (result.details && (result.details.installed_version || result.details.installedVersion)) || result.version || "-" },
            { label: "Resultado", value: jobResultLabel(job) },
            { label: "Criado por", value: job.createdBy },
            { label: "Criado em", value: formatDate(job.createdAt) },
            { label: "Despachado em", value: formatDate(job.dispatchedAt) },
            { label: "Iniciado em", value: formatDate(job.startedAt) },
            { label: "Finalizado em", value: formatDate(job.finishedAt) },
            { label: "Duracao", value: formatDuration(job.durationMs) },
            { label: "Exit code", value: job.exitCode == null ? "-" : job.exitCode }
        ]) + '</section><section><h3>Payload</h3><pre>' + escapeHtml(JSON.stringify(job.payload || {}, null, 2)) + '</pre></section><section><h3>Resultado JSON</h3><pre>' + escapeHtml(JSON.stringify(job.resultJson || {}, null, 2)) + '</pre></section><section><h3>Stdout</h3><pre>' + escapeHtml(job.stdout || "Sem saida.") + '</pre></section><section><h3>Stderr</h3><pre>' + escapeHtml(job.stderr || job.errorMessage || "Sem erro.") + '</pre></section><section><h3>Timeline</h3><p>' + escapeHtml((job.timeline || []).join(" -> ") || "-") + '</p></section><div class="event-drawer-actions"><button type="button" data-copy-job="' + escapeHtml(job.id) + '">Copiar saida</button><button type="button" data-refresh-endpoint>Atualizar dados</button></div>');
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

        const refresh = event.target.closest("[data-refresh-endpoint]");
        if (refresh) {
            reloadEndpoint(false).then(function () {
                showToast("Dados atualizados.");
            });
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
        const refresh = event.target.closest("[data-refresh-endpoint]");
        if (!refresh || root.contains(refresh)) return;
        reloadEndpoint(false).then(function () {
            showToast("Dados atualizados.");
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

    reloadEndpoint(false);
}());
