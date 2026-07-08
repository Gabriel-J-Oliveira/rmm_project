(function () {
    /**
     * @typedef {"debug"|"info"|"success"|"warning"|"critical"|"security"} Severity
     * @typedef {"new"|"open"|"acknowledged"|"muted"|"resolved"} AlertStatus
     * @typedef {"all"|"agent"|"system"|"alerts"|"jobs"|"security"|"inventory"|"maintenance"} EventCategory
     * @typedef {"Agent"|"System"|"User"|"Policy"|"Job"} EventSource
     *
     * @typedef {Object} EndpointSummary
     * @property {string} id
     * @property {string} hostname
     * @property {"queued"|"sent"|"running"|"completed"|"failed"|string} status
     * @property {string=} ip
     * @property {string=} user
     * @property {string=} sector
     * @property {string=} os
     *
     * @typedef {Object} NocMetric
     * @property {string} key
     * @property {string} label
     * @property {number|string} value
     * @property {Severity} severity
     *
     * @typedef {Object} AlertItem
     * @property {string} id
     * @property {string} title
     * @property {string} endpoint
     * @property {Severity|string} severity
     * @property {AlertStatus|string} status
     * @property {string=} owner
     * @property {string=} type
     *
     * @typedef {Object} EventItem
     * @property {string} id
     * @property {string} title
     * @property {string} eventType
     * @property {Severity|string} severity
     * @property {EventCategory|string} category
     * @property {EventSource|string} source
     * @property {string=} endpoint
     * @property {string=} actor
     * @property {string=} description
     * @property {string=} timestamp
     *
     * @typedef {Object} JobSummary
     * @property {string} id
     * @property {string} name
     * @property {string} status
     * @property {string=} lastRun
     */

    var storageKey = "nightowl-operational-events";
    var jobStorageKey = "nightowl-operational-jobs";
    var futureApi = {
        alerts: "/api/alerts/",
        events: "/api/events/",
        endpoints: "/api/endpoints/",
        jobs: "/api/jobs/",
        agentPull: "/api/agent/jobs/pull/",
        agentResult: "/api/agent/jobs/result/"
    };
    var jobStatuses = ["queued", "sent", "running", "completed", "failed"];

    var mockApi = window.MockNightowlApi || null;
    var apiSnapshot = mockApi && typeof mockApi.getSnapshot === "function" ? mockApi.getSnapshot() : { endpoints: [], alerts: [], events: [], jobs: [] };

    /** @type {EndpointSummary[]} */
    var mockEndpoints = apiSnapshot.endpoints || [];

    /** @type {AlertItem[]} */
    var mockAlerts = apiSnapshot.alerts || [];

    /** @type {EventItem[]} */
    var mockEvents = apiSnapshot.events || [];

    /** @type {NocMetric[]} */
    var mockNocMetrics = [
        { key: "health", label: "Saude geral", value: mockEndpoints.length ? Math.round(mockEndpoints.reduce(function (total, item) { return total + (item.healthScore || 0); }, 0) / mockEndpoints.length) : 0, severity: "success" },
        { key: "critical", label: "Criticos", value: mockAlerts.filter(function (item) { return item.severity === "critical" && item.status !== "resolved"; }).length, severity: "critical" },
        { key: "offline", label: "Offline", value: mockEndpoints.filter(function (item) { return item.status === "offline"; }).length, severity: "warning" },
        { key: "affected", label: "Afetados", value: Array.from(new Set(mockAlerts.filter(function (item) { return item.status !== "resolved"; }).map(function (item) { return item.endpointId || item.endpoint; }))).length, severity: "info" }
    ];

    /** @type {JobSummary[]} */
    var mockJobs = apiSnapshot.jobs || [];

    function escapeHtml(value) {
        return String(value == null ? "" : value)
            .replace(/&/g, "&amp;")
            .replace(/</g, "&lt;")
            .replace(/>/g, "&gt;")
            .replace(/"/g, "&quot;")
            .replace(/'/g, "&#039;");
    }

    function component(tag, className, content, attrs) {
        var attributes = Object.keys(attrs || {}).map(function (key) {
            return " " + key + '="' + escapeHtml(attrs[key]) + '"';
        }).join("");
        return "<" + tag + ' class="' + escapeHtml(className || "") + '"' + attributes + ">" + (content || "") + "</" + tag + ">";
    }

    var Components = {
        SeverityBadge: function (severity, label) {
            return component("span", "severity-badge severity-" + escapeHtml(severity || "info"), escapeHtml(label || severity || "Info"));
        },
        StatusBadge: function (status, label) {
            return component("span", "alert-status alert-status-" + escapeHtml(status || "open"), escapeHtml(label || status || "Novo"));
        },
        EndpointBadge: function (endpoint) {
            return component("span", "mono endpoint-badge", escapeHtml(endpoint || "Sem endpoint"));
        },
        RelativeTime: function (value) {
            return component("span", "relative-chip", escapeHtml(value || "agora"));
        },
        OperationalMetricCard: function (metric) {
            return '<article class="metric-card command-metric">' +
                '<span class="metric-label">' + escapeHtml(metric.label) + '</span>' +
                '<strong class="metric-value">' + escapeHtml(metric.value) + '</strong>' +
                '</article>';
        },
        EmptyState: function (title, description) {
            return '<div class="panel empty-alert-state"><i data-lucide="check-circle"></i><strong>' + escapeHtml(title) + '</strong><p>' + escapeHtml(description || "") + '</p></div>';
        },
        DrawerPanel: function (title, body) {
            return '<aside class="shared-drawer-panel"><h2>' + escapeHtml(title) + '</h2><div>' + (body || "") + '</div></aside>';
        },
        ActionButton: function (label, action) {
            return '<button type="button" data-shared-action="' + escapeHtml(action || label) + '">' + escapeHtml(label) + '</button>';
        },
        FilterChip: function (label, active) {
            return '<button type="button" class="' + (active ? "is-active" : "") + '">' + escapeHtml(label) + '</button>';
        },
        TimelineItem: function (event) {
            return '<article class="event-row"><div class="event-main"><div class="event-title-line">' +
                Components.EventTypeBadge(event.eventType, event.category) +
                '<strong>' + escapeHtml(event.title) + '</strong></div><p>' + escapeHtml(event.description || "") + '</p></div></article>';
        },
        EventTypeBadge: function (eventType, category) {
            return '<code class="event-type-badge event-type-' + escapeHtml(category || "system") + '">' + escapeHtml(eventType || "system.event") + '</code>';
        }
    };

    function getStoredEvents() {
        if (mockApi && typeof mockApi.getSnapshot === "function") {
            return mockApi.getSnapshot().events.filter(function (event) {
                return String(event.id || "").indexOf("E-local-") === 0;
            });
        }
        try {
            return JSON.parse(window.localStorage.getItem(storageKey) || "[]");
        } catch (error) {
            return [];
        }
    }

    function saveStoredEvents(events) {
        if (mockApi) {
            return;
        }
        window.localStorage.setItem(storageKey, JSON.stringify(events.slice(-100)));
    }

    function addMockEvent(partial) {
        var event = Object.assign({
            id: "local-" + Date.now(),
            title: "Ação mockada registrada",
            eventType: "ui.mock_action",
            severity: "info",
            category: "system",
            source: "User",
            endpoint: "",
            actor: "Usuário atual",
            description: "Ação operacional executada no frontend.",
            timestamp: new Date().toISOString()
        }, partial || {});
        var emittedByApi = false;
        if (mockApi && typeof mockApi.addMockEvent === "function") {
            event = mockApi.addMockEvent(event);
            emittedByApi = true;
        } else {
            var events = getStoredEvents();
            events.push(event);
            saveStoredEvents(events);
        }
        if (!emittedByApi) {
            window.dispatchEvent(new CustomEvent("nightowl:event-created", { detail: event }));
        }
        return event;
    }

    function getStoredJobs() {
        if (mockApi && typeof mockApi.getSnapshot === "function") {
            return mockApi.getSnapshot().jobs.filter(function (job) {
                return String(job.id || "").indexOf("J-local-") === 0;
            });
        }
        try {
            return JSON.parse(window.localStorage.getItem(jobStorageKey) || "[]");
        } catch (error) {
            return [];
        }
    }

    function saveStoredJobs(jobs) {
        if (mockApi) {
            return;
        }
        window.localStorage.setItem(jobStorageKey, JSON.stringify(jobs.slice(-100)));
    }

    function updateStoredJob(jobId, patch) {
        var jobs = getStoredJobs();
        var job = jobs.find(function (item) { return item.id === jobId; });
        if (!job) {
            return null;
        }
        Object.assign(job, patch || {}, { updatedAt: new Date().toISOString() });
        saveStoredJobs(jobs);
        window.dispatchEvent(new CustomEvent("nightowl:job-updated", { detail: job }));
        return job;
    }

    function addMockJob(partial) {
        if (mockApi && typeof mockApi.createMockJob === "function") {
            var apiJob = mockApi.createMockJob(partial || {});
            return partial || apiJob;
        }
        var job = Object.assign({
            id: "job-" + Date.now(),
            name: "Execução remota",
            status: "queued",
            endpoint: "",
            command: "",
            sourceApi: futureApi.jobs,
            agentPullApi: futureApi.agentPull,
            agentResultApi: futureApi.agentResult,
            createdAt: new Date().toISOString(),
            updatedAt: new Date().toISOString()
        }, partial || {});
        var jobs = getStoredJobs();
        jobs.push(job);
        saveStoredJobs(jobs);
        addMockEvent({
            title: "Job remoto enfileirado",
            eventType: "job.queued",
            category: "jobs",
            source: "Job",
            severity: "info",
            endpoint: job.endpoint,
            description: job.name + " aguardando pull do agente."
        });
        window.setTimeout(function () {
            updateStoredJob(job.id, { status: "sent" });
            addMockEvent({
                title: "Job enviado ao agente",
                eventType: "job.sent",
                category: "jobs",
                source: "Job",
                severity: "info",
                endpoint: job.endpoint,
                description: job.name + " entregue via " + futureApi.agentPull + "."
            });
        }, 700);
        window.setTimeout(function () {
            updateStoredJob(job.id, { status: "running" });
            addMockEvent({
                title: "Job em execução",
                eventType: "job.running",
                category: "jobs",
                source: "Agent",
                severity: "info",
                endpoint: job.endpoint,
                description: job.name + " em execução no agente Windows."
            });
        }, 1600);
        window.setTimeout(function () {
            var failed = Boolean(job.forceFail);
            updateStoredJob(job.id, { status: failed ? "failed" : "completed" });
            addMockEvent({
                title: failed ? "Job falhou" : "Job concluído",
                eventType: failed ? "job.failed" : "job.completed",
                category: "jobs",
                source: "Agent",
                severity: failed ? "warning" : "success",
                endpoint: job.endpoint,
                description: job.name + (failed ? " retornou erro mockado." : " retornou resultado com sucesso em " + futureApi.agentResult + ".")
            });
        }, 3200);
        window.dispatchEvent(new CustomEvent("nightowl:job-created", { detail: job }));
        return job;
    }

    function normalizeAction(action) {
        var value = String(action || "").toLowerCase();
        if (value.indexOf("reconhe") >= 0 || value === "acknowledge_alert") return "acknowledge_alert";
        if (value.indexOf("resolver") >= 0 || value === "resolve_alert") return "resolve_alert";
        if (value.indexOf("silenciar") >= 0 || value === "mute_alert") return "mute_alert";
        if (value.indexOf("observ") >= 0 || value === "add_note") return "add_note";
        if (value.indexOf("chamado") >= 0 || value === "create_ticket") return "create_ticket";
        if (value.indexOf("invent") >= 0 || value === "force_inventory") return "force_inventory";
        if (value.indexOf("defender") >= 0 || value === "check_defender") return "check_defender";
        if (value.indexOf("disco") >= 0 || value === "check_disk") return "check_disk";
        if (value.indexOf("log") >= 0 || value === "collect_logs") return "collect_logs";
        if (value.indexOf("limpeza") >= 0 || value === "run_cleanup") return "run_cleanup";
        if (value === "ping" || value.indexOf("ping") >= 0) return "ping";
        if (value.indexOf("abrir eventos") >= 0 || value === "open_events") return "open_events";
        if (value.indexOf("abrir alertas") >= 0 || value === "open_alerts") return "open_alerts";
        if (value.indexOf("copiar") >= 0 || value === "copy_summary") return "copy_summary";
        if (value.indexOf("export") >= 0 || value === "export_events") return "export_events";
        if (value.indexOf("verifica") >= 0 || value === "execute_check") return "execute_check";
        return value || "mock_action";
    }

    var actionDefinitions = {
        acknowledge_alert: { label: "Reconhecer alerta", eventType: "alert.acknowledged_mocked", status: "acknowledged", toast: "Alerta reconhecido no mock operacional." },
        resolve_alert: { label: "Resolver alerta", eventType: "alert.resolved_mocked", status: "resolved", toast: "Alerta resolvido no mock operacional.", severity: "success" },
        mute_alert: { label: "Silenciar alerta", eventType: "alert.muted_mocked", status: "muted", toast: "Alerta silenciado no mock operacional." },
        add_note: { label: "Adicionar observacao", eventType: "alert.note_added_mocked", toast: "Observacao adicionada ao alerta." },
        create_ticket: { label: "Criar chamado", eventType: "ticket.created_from_alert_mocked", toast: "Chamado mockado criado a partir do contexto operacional." },
        execute_check: { label: "Executar verificação", eventType: "agent.job_check_requested", toast: "Verificação remota enfileirada.", remote: true, command: "nightowl.check" },
        force_inventory: { label: "Forçar inventário", eventType: "agent.job_inventory_requested", toast: "Inventário remoto enfileirado.", remote: true, command: "nightowl.inventory.collect" },
        check_defender: { label: "Verificar Defender", eventType: "agent.job_defender_requested", toast: "Verificação do Defender enfileirada.", remote: true, command: "nightowl.security.defender" },
        check_disk: { label: "Verificar disco", eventType: "agent.job_disk_requested", toast: "Verificação de disco enfileirada.", remote: true, command: "nightowl.disk.check" },
        collect_logs: { label: "Coletar logs", eventType: "agent.job_logs_requested", toast: "Coleta de logs enfileirada.", remote: true, command: "nightowl.logs.collect" },
        run_cleanup: { label: "Executar limpeza", eventType: "agent.job_cleanup_requested", toast: "Limpeza remota enfileirada.", remote: true, command: "nightowl.cleanup.temp" },
        ping: { label: "Ping", eventType: "agent.job_ping_requested", toast: "Ping enfileirado.", remote: true, command: "nightowl.network.ping" },
        open_events: { label: "Abrir eventos filtrados", eventType: "ui.open_events_filtered", toast: "Abrindo eventos filtrados." },
        open_alerts: { label: "Abrir alertas filtrados", eventType: "ui.open_alerts_filtered", toast: "Abrindo alertas filtrados." },
        copy_summary: { label: "Copiar resumo", eventType: "ui.summary_copied", toast: "Resumo copiado." },
        export_events: { label: "Exportar eventos", eventType: "event.export_requested_mocked", toast: "Exportação de eventos preparada no mock." }
    };

    function setAlertStatus(card, status) {
        if (!card) {
            return;
        }
        var labels = {
            acknowledged: "Reconhecido",
            resolved: "Resolvido",
            muted: "Silenciado",
            open: "Novo"
        };
        card.dataset.status = labels[status] || status;
        card.classList.toggle("status-card-acknowledged", status === "acknowledged");
        card.classList.toggle("status-card-resolved", status === "resolved");
        card.classList.toggle("is-muted", status === "muted");
        var badge = card.querySelector(".alert-status");
        if (badge) {
            badge.className = "alert-status alert-status-" + status;
            badge.textContent = labels[status] || status;
        }
        var inlineStatus = card.querySelector("[data-alert-inline-status]");
        if (inlineStatus) {
            inlineStatus.textContent = labels[status] || status;
        }
        if (card.matches("[data-noc-open-drawer]")) {
            card.dataset.status = labels[status] || status;
            card.classList.add("has-mock-status");
            card.setAttribute("data-mock-status-label", labels[status] || status);
        }
    }

    function alertIdentityFromCard(card) {
        if (!card) {
            return "";
        }
        return card.dataset.mockAlertId || card.dataset.alertId || card.dataset.title || "";
    }

    function copyText(value) {
        if (!value) {
            return Promise.reject(new Error("Valor vazio"));
        }
        if (navigator.clipboard && navigator.clipboard.writeText) {
            return navigator.clipboard.writeText(value);
        }
        var input = document.createElement("textarea");
        input.value = value;
        input.setAttribute("readonly", "readonly");
        input.style.position = "fixed";
        input.style.left = "-9999px";
        document.body.appendChild(input);
        input.select();
        var copied = document.execCommand("copy");
        document.body.removeChild(input);
        return copied ? Promise.resolve() : Promise.reject(new Error("Clipboard indisponivel"));
    }

    function routeFor(actionKey, ctx, endpoint) {
        if (actionKey === "open_events") {
            return ctx.eventsUrl || ("/events/?q=" + encodeURIComponent(endpoint || ""));
        }
        if (actionKey === "open_alerts") {
            return ctx.alertsUrl || ("/alerts/?q=" + encodeURIComponent(endpoint || ""));
        }
        return "";
    }

    function runAction(action, context) {
        var actionKey = normalizeAction(action);
        var definition = actionDefinitions[actionKey] || { label: actionKey, eventType: "ui.mock_action", toast: "Ação mockada registrada." };
        var ctx = context || {};
        var endpoint = ctx.endpoint || (ctx.card && (ctx.card.dataset.endpoint || ctx.card.dataset.eventEndpoint)) || "";
        if (definition.status && ctx.card) {
            setAlertStatus(ctx.card, definition.status);
        }
        var ticketCode = "";
        if (actionKey === "create_ticket" && ctx.card) {
            ticketCode = "#MOCK-" + String(Date.now()).slice(-4);
            ctx.card.dataset.ticket = ticketCode;
        }
        if (actionKey === "copy_summary") {
            copyText(ctx.copyText || ctx.summary || ctx.description || "Resumo NightOwl").then(function () {
                showToast(definition.toast, ctx.toastOptions || {});
            }).catch(function () {
                showToast("Nao foi possivel copiar automaticamente.", ctx.toastOptions || {});
            });
        }
        var route = routeFor(actionKey, ctx, endpoint);
        if (route) {
            window.setTimeout(function () {
                window.location.href = route;
            }, 350);
        }
        var event = addMockEvent({
            title: definition.label,
            eventType: definition.eventType,
            category: actionKey.indexOf("ticket") >= 0 ? "alerts" : actionKey.indexOf("export") >= 0 ? "system" : definition.remote ? "jobs" : "alerts",
            source: definition.remote ? "Job" : "User",
            severity: definition.severity || "info",
            endpoint: endpoint,
            description: ctx.description || definition.toast + (ticketCode ? " " + ticketCode : ""),
            relatedApi: definition.remote ? futureApi.jobs : actionKey.indexOf("alert") >= 0 ? futureApi.alerts : actionKey.indexOf("export") >= 0 ? futureApi.events : futureApi.endpoints
        });
        var job = null;
        var apiPromise = null;
        if (definition.remote) {
            if (mockApi) {
                if (actionKey === "force_inventory" && typeof mockApi.forceInventory === "function") {
                    apiPromise = mockApi.forceInventory(endpoint);
                } else if (typeof mockApi.runEndpointCheck === "function") {
                    apiPromise = mockApi.runEndpointCheck(endpoint, actionKey);
                }
                job = apiPromise;
            } else {
                job = addMockJob({
                    name: definition.label,
                    endpoint: endpoint,
                    command: definition.command,
                    action: actionKey
                });
            }
        } else if (mockApi) {
            var alertId = ctx.alertId || alertIdentityFromCard(ctx.card);
            if (actionKey === "acknowledge_alert" && typeof mockApi.acknowledgeAlert === "function") {
                apiPromise = mockApi.acknowledgeAlert(alertId);
            } else if (actionKey === "resolve_alert" && typeof mockApi.resolveAlert === "function") {
                apiPromise = mockApi.resolveAlert(alertId);
            } else if (actionKey === "mute_alert" && typeof mockApi.silenceAlert === "function") {
                apiPromise = mockApi.silenceAlert(alertId);
            } else if (actionKey === "add_note" && typeof mockApi.addAlertNote === "function") {
                apiPromise = mockApi.addAlertNote(alertId, ctx.note || ctx.description || "Observacao operacional registrada.");
            } else if (actionKey === "create_ticket" && alertId && typeof mockApi.createTicketFromAlert === "function") {
                apiPromise = mockApi.createTicketFromAlert(alertId);
            } else if (actionKey === "create_ticket" && typeof mockApi.createTicket === "function") {
                apiPromise = mockApi.createTicket({
                    endpoint: endpoint,
                    title: ctx.title || (endpoint ? "Atendimento RMM - " + endpoint : "Atendimento RMM"),
                    priority: ctx.priority || "Normal"
                });
            }
            if (apiPromise && typeof apiPromise.then === "function") {
                apiPromise.then(function (result) {
                    if (!result || !ctx.card) {
                        return;
                    }
                    if (result.status) {
                        setAlertStatus(ctx.card, result.status);
                    }
                    if (result.number) {
                        ctx.card.dataset.ticket = result.number;
                        var ticketNode = ctx.card.querySelector("[data-ticket-label]");
                        if (ticketNode) {
                            ticketNode.textContent = result.number;
                        }
                    }
                }).catch(function () {
                    showToast("Acao mockada nao encontrou o item na base local.", ctx.toastOptions || {});
                });
            }
        }
        if (actionKey !== "copy_summary") {
            showToast(definition.toast, ctx.toastOptions || {});
        }
        return { action: actionKey, event: event, job: job, apiPromise: apiPromise };
    }

    function showToast(message, options) {
        var target = options && options.target ? options.target : document.querySelector("[data-alert-action-toast], [data-event-toast], [data-noc-toast]");
        if (!target) {
            return;
        }
        target.textContent = message || "Ação registrada.";
        target.hidden = false;
        target.classList.add("is-visible");
        window.clearTimeout(target.__nightowlToastTimer);
        target.__nightowlToastTimer = window.setTimeout(function () {
            target.classList.remove("is-visible");
            target.hidden = true;
        }, options && options.timeout ? options.timeout : 2800);
    }

    function alertFromCard(card) {
        return {
            id: card.dataset.alertId || card.dataset.title || "",
            title: card.dataset.title || "",
            endpoint: card.dataset.endpoint || "",
            severity: card.dataset.severity || "info",
            status: card.dataset.status || "open",
            owner: card.dataset.owner || "Não atribuído",
            type: card.dataset.type || ""
        };
    }

    function eventFromCard(card) {
        return {
            id: card.dataset.eventId || "",
            title: card.dataset.eventTitle || "",
            eventType: card.dataset.eventType || "",
            severity: card.dataset.eventSeverity || "info",
            category: card.dataset.eventCategory || "system",
            source: card.dataset.eventOrigin || "System",
            endpoint: card.dataset.eventEndpoint || "",
            actor: card.dataset.eventActor || "",
            description: card.dataset.eventDescription || "",
            timestamp: card.dataset.eventTimestamp || ""
        };
    }

    function collectAlertItems(root) {
        return Array.prototype.slice.call((root || document).querySelectorAll("[data-alert-card]")).map(alertFromCard);
    }

    function applyMockAlertState(root) {
        if (!mockApi || typeof mockApi.getSnapshot !== "function") {
            return;
        }
        var alerts = mockApi.getSnapshot().alerts || [];
        Array.prototype.slice.call((root || document).querySelectorAll("[data-alert-card], [data-noc-open-drawer][data-mock-alert-id]")).forEach(function (card) {
            var identity = alertIdentityFromCard(card);
            var alert = alerts.find(function (item) {
                return item.id === identity || item.title === identity || item.title === card.dataset.title;
            });
            if (!alert) {
                return;
            }
            if (alert.status) {
                setAlertStatus(card, alert.status);
            }
            if (alert.ticket) {
                card.dataset.ticket = alert.ticket;
                var ticketNode = card.querySelector("[data-ticket-label]");
                if (ticketNode) {
                    ticketNode.textContent = alert.ticket;
                }
            }
        });
    }

    function collectEventItems(root) {
        return Array.prototype.slice.call((root || document).querySelectorAll("[data-event-card]")).map(eventFromCard);
    }

    function buildNocContext(root) {
        var cards = Array.prototype.slice.call((root || document).querySelectorAll("[data-noc-open-drawer]"));
        var events = cards
            .filter(function (card) { return (card.dataset.drawerKind || "").toLowerCase().indexOf("evento") >= 0; })
            .map(function (card) {
                return {
                    title: card.dataset.title || "",
                    eventType: "noc.live_event",
                    severity: card.dataset.status || "info",
                    category: "system",
                    source: "System",
                    endpoint: card.dataset.endpoint || "",
                    description: card.dataset.body || ""
                };
            });
        var alerts = cards
            .filter(function (card) { return (card.dataset.drawerKind || "").toLowerCase().indexOf("alerta") >= 0; })
            .map(function (card) {
                return {
                    title: card.dataset.title || "",
                    endpoint: card.dataset.endpoint || "",
                    severity: card.dataset.status || "info",
                    status: "open",
                    type: card.dataset.drawerKind || "noc_alert"
                };
            });
        return {
            alerts: alerts,
            events: events,
            criticalAlerts: alerts.filter(function (item) { return String(item.severity).toLowerCase().indexOf("critical") >= 0 || String(item.severity).toLowerCase().indexOf("cr") >= 0; }),
            liveEvents: events.slice(0, 8)
        };
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

    function renderStoredEvents(root) {
        var targetRoot = root || document;
        var timeline = targetRoot.querySelector("[data-events-timeline]");
        if (!timeline) {
            return;
        }
        getStoredEvents().slice().reverse().forEach(function (event) {
            var alreadyRendered = Array.prototype.slice.call(timeline.querySelectorAll("[data-event-id]")).some(function (item) {
                return item.dataset.eventId === event.id;
            });
            if (alreadyRendered) {
                return;
            }
            var article = document.createElement("article");
            var when = event.timestamp ? new Date(event.timestamp) : new Date();
            var date = isNaN(when.getTime()) ? new Date() : when;
            var timestamp = date.toLocaleDateString("pt-BR") + " " + date.toLocaleTimeString("pt-BR");
            article.className = "event-row event-log-entry severity-border-" + (event.severity || "info");
            article.dataset.eventCard = "";
            article.dataset.eventId = event.id;
            article.dataset.eventTitle = event.title || "Evento mockado";
            article.dataset.eventType = event.eventType || "ui.mock_action";
            article.dataset.eventOrigin = event.source || "User";
            article.dataset.eventCategory = event.category || "system";
            article.dataset.eventSeverity = event.severity || "info";
            article.dataset.eventActor = event.actor || "Usuário atual";
            article.dataset.eventEndpoint = event.endpoint || "Sem endpoint";
            article.dataset.eventDescription = event.description || "";
            article.dataset.eventTimestamp = timestamp;
            article.dataset.eventRelative = "agora";
            article.innerHTML =
                '<div class="event-time"><strong>' + escapeHtml(date.toLocaleTimeString("pt-BR")) + '</strong><span>' + escapeHtml(date.toLocaleDateString("pt-BR")) + '</span><small>local</small></div>' +
                '<div class="event-icon severity-' + escapeHtml(event.severity || "info") + '"><i data-lucide="' + escapeHtml(eventIcon(event.category)) + '"></i></div>' +
                '<div class="event-main"><div class="event-title-line">' +
                '<span class="event-origin-chip event-origin-' + escapeHtml(event.category || "system") + '">' + escapeHtml(event.source || "User") + '</span>' +
                Components.SeverityBadge(event.severity || "info", event.severity || "Info") +
                '<strong>' + escapeHtml(event.title || "Evento mockado") + '</strong>' +
                Components.EventTypeBadge(event.eventType || "ui.mock_action", event.category || "system") +
                '</div><p>' + escapeHtml(event.description || "") + '</p>' +
                '<div class="event-meta"><span><i data-lucide="monitor"></i>' + escapeHtml(event.endpoint || "Sem endpoint") + '</span>' +
                '<span><i data-lucide="user"></i>' + escapeHtml(event.actor || "Usuário atual") + '</span>' +
                '<button type="button" data-event-open><i data-lucide="panel-right-open"></i>Detalhes</button></div></div>';
            timeline.insertBefore(article, timeline.firstChild);
        });
        if (window.lucide && typeof window.lucide.createIcons === "function") {
            window.lucide.createIcons();
        }
    }

    function initOperationalChrome(root, options) {
        var base = root || document;
        var config = options || {};
        var openedAt = new Date();
        var initialLocalEvents = getStoredEvents().length;
        var ageNodes = Array.prototype.slice.call(base.querySelectorAll("[data-operational-age]"));
        var openedNodes = Array.prototype.slice.call(base.querySelectorAll("[data-operational-opened-at]"));
        var newNodes = Array.prototype.slice.call(base.querySelectorAll("[data-operational-new-count]"));
        var filteredNodes = Array.prototype.slice.call(base.querySelectorAll("[data-filtered-count]"));
        var skeletons = Array.prototype.slice.call(base.querySelectorAll("[data-skeleton]"));

        function renderAge() {
            var seconds = Math.floor((Date.now() - openedAt.getTime()) / 1000);
            var label = seconds < 60 ? "agora" : Math.floor(seconds / 60) + " min";
            ageNodes.forEach(function (node) {
                node.textContent = label;
                node.classList.toggle("is-stale", seconds >= (config.staleAfterSeconds || 180));
            });
        }

        function renderOpenedAt() {
            openedNodes.forEach(function (node) {
                node.textContent = openedAt.toLocaleTimeString("pt-BR", { hour: "2-digit", minute: "2-digit", second: "2-digit" });
            });
        }

        function renderNewCount() {
            var total = Math.max(0, getStoredEvents().length - initialLocalEvents);
            newNodes.forEach(function (node) {
                node.textContent = String(total);
                node.closest(".ops-chip")?.classList.toggle("has-new", total > 0);
            });
        }

        function renderFilteredCount() {
            if (!config.countSelector) {
                return;
            }
            var total = base.querySelectorAll(config.countSelector).length;
            filteredNodes.forEach(function (node) {
                node.textContent = String(total);
            });
        }

        renderOpenedAt();
        renderAge();
        renderNewCount();
        renderFilteredCount();
        window.setInterval(renderAge, 30000);
        window.addEventListener("nightowl:event-created", renderNewCount);
        window.setTimeout(function () {
            skeletons.forEach(function (node) {
                node.hidden = true;
            });
        }, 450);
    }

    window.NightOwlOperational = {
        mockEndpoints: mockEndpoints,
        mockAlerts: mockAlerts,
        mockEvents: mockEvents,
        mockNocMetrics: mockNocMetrics,
        mockJobs: mockJobs,
        jobStatuses: jobStatuses,
        Components: Components,
        futureApi: futureApi,
        actionDefinitions: actionDefinitions,
        showToast: showToast,
        runAction: runAction,
        copyText: copyText,
        applyMockAlertState: applyMockAlertState,
        addMockEvent: addMockEvent,
        addMockJob: addMockJob,
        getStoredEvents: getStoredEvents,
        getStoredJobs: getStoredJobs,
        collectAlertItems: collectAlertItems,
        collectEventItems: collectEventItems,
        buildNocContext: buildNocContext,
        renderStoredEvents: renderStoredEvents,
        initOperationalChrome: initOperationalChrome,
        alertFromCard: alertFromCard,
        eventFromCard: eventFromCard
    };
}());
