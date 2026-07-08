(function () {
    "use strict";

    var root = document.querySelector("[data-jobs-root]");
    if (!root) return;

    var api = window.MockNightowlApi;
    var operational = window.NightOwlOperational;
    var toast = root.querySelector("[data-jobs-toast]");

    var jobForm = root.querySelector("[data-jobs-filter-form]");
    var jobTbody = root.querySelector("[data-jobs-table-body]");
    var jobLoading = root.querySelector("[data-jobs-loading]");
    var jobDrawer = root.querySelector("[data-job-drawer]");
    var jobBackdrop = root.querySelector("[data-job-drawer-backdrop]");

    var taskFormFilters = root.querySelector("[data-task-filter-form]");
    var taskListBody = root.querySelector("[data-task-list-body]");
    var taskCalendar = root.querySelector("[data-task-calendar]");
    var taskTemplateGrid = root.querySelector("[data-task-template-grid]");
    var taskDrawer = root.querySelector("[data-task-drawer]");
    var taskBackdrop = root.querySelector("[data-task-drawer-backdrop]");
    var taskForm = root.querySelector("[data-task-form]");
    var taskChecklistEditor = root.querySelector("[data-task-checklist-editor]");
    var taskLinkSummary = root.querySelector("[data-task-link-summary]");
    var taskLinkedJobs = root.querySelector("[data-task-linked-jobs]");
    var taskTimeline = root.querySelector("[data-task-timeline]");

    var currentJob = null;
    var currentTask = null;
    var activeTab = "agenda";
    var calendarDate = new Date();

    var allJobs = [];
    var filteredJobs = [];
    var endpoints = [];
    var allTasks = [];
    var filteredTasks = [];
    var taskTemplates = [];

    var statusLabels = {
        queued: "Em fila",
        sent: "Enviada",
        running: "Em execucao",
        completed: "Concluida",
        failed: "Falha",
        expired: "Expirada",
        cancelled: "Cancelada"
    };

    var typeLabels = {
        force_inventory: "Forcar inventario",
        defender_check: "Verificar Defender",
        disk_check: "Verificar disco",
        collect_logs: "Coletar logs",
        ping: "Ping",
        cleanup_temp: "Limpeza temporaria",
        run_script: "Executar script",
        install_software: "Instalar software",
        windows_update_scan: "Windows Update Scan"
    };

    var typeIcons = {
        force_inventory: "package-search",
        defender_check: "shield-check",
        disk_check: "hard-drive",
        collect_logs: "file-search",
        ping: "activity",
        cleanup_temp: "sparkles",
        run_script: "code-2",
        install_software: "package-plus",
        windows_update_scan: "badge-check"
    };

    var taskStatusLabels = {
        open: "Aberta",
        scheduled: "Agendada",
        in_progress: "Em andamento",
        waiting: "Aguardando",
        done: "Concluida",
        cancelled: "Cancelada"
    };

    var taskPriorityLabels = {
        low: "Baixa",
        normal: "Normal",
        high: "Alta",
        critical: "Critica"
    };

    var taskCategoryLabels = {
        support: "Suporte",
        maintenance: "Manutencao",
        security: "Seguranca",
        inventory: "Inventario",
        onboarding: "Onboarding",
        offboarding: "Offboarding",
        change: "Mudanca"
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
            operational.showToast(message, { target: toast, timeout: 2800 });
            return;
        }
        if (!toast) return;
        toast.textContent = message || "Acao registrada.";
        toast.hidden = false;
        toast.classList.add("is-visible");
        window.clearTimeout(toast.__jobsToastTimer);
        toast.__jobsToastTimer = window.setTimeout(function () {
            toast.classList.remove("is-visible");
            toast.hidden = true;
        }, 2800);
    }

    function formatDate(value, fallback) {
        if (!value) return fallback || "--";
        var date = new Date(value);
        if (Number.isNaN(date.getTime())) return fallback || "--";
        return date.toLocaleDateString("pt-BR") + " " + date.toLocaleTimeString("pt-BR", { hour: "2-digit", minute: "2-digit" });
    }

    function formatDateInput(value) {
        if (!value) return "";
        var date = new Date(value);
        if (Number.isNaN(date.getTime())) return "";
        var offset = date.getTimezoneOffset();
        var local = new Date(date.getTime() - offset * 60000);
        return local.toISOString().slice(0, 16);
    }

    function fromDateInput(value) {
        if (!value) return "";
        var date = new Date(value);
        return Number.isNaN(date.getTime()) ? "" : date.toISOString();
    }

    function formatDuration(ms) {
        if (!ms) return "--";
        var seconds = Math.max(1, Math.round(ms / 1000));
        if (seconds < 60) return seconds + "s";
        var minutes = Math.floor(seconds / 60);
        var rest = seconds % 60;
        return minutes + "min " + String(rest).padStart(2, "0") + "s";
    }

    function sameDay(a, b) {
        return a.getFullYear() === b.getFullYear() && a.getMonth() === b.getMonth() && a.getDate() === b.getDate();
    }

    function startOfDay(date) {
        return new Date(date.getFullYear(), date.getMonth(), date.getDate());
    }

    function taskDueState(task) {
        if (task.status === "done") return "done";
        if (task.status === "cancelled") return "cancelled";
        var due = new Date(task.dueAt);
        var today = startOfDay(new Date());
        if (Number.isNaN(due.getTime())) return "normal";
        if (startOfDay(due).getTime() < today.getTime()) return "overdue";
        if (sameDay(due, today)) return "today";
        if (due.getTime() <= Date.now() + 7 * 86400000) return "week";
        return "normal";
    }

    function checklistProgress(task) {
        var items = task && task.checklist ? task.checklist : [];
        var done = items.filter(function (item) { return item.done; }).length;
        return { done: done, total: items.length, label: done + "/" + items.length };
    }

    function taskStatusBadge(task) {
        return '<span class="task-status-badge task-status-' + escapeHtml(task.status || "open") + '">' + escapeHtml(taskStatusLabels[task.status] || task.status || "Aberta") + "</span>";
    }

    function taskPriorityBadge(task) {
        return '<span class="task-priority-badge task-priority-' + escapeHtml(task.priority || "normal") + '">' + escapeHtml(taskPriorityLabels[task.priority] || task.priority || "Normal") + "</span>";
    }

    function taskCategoryBadge(task) {
        return '<span class="task-category-badge task-category-' + escapeHtml(task.category || "support") + '">' + escapeHtml(taskCategoryLabels[task.category] || task.category || "Suporte") + "</span>";
    }

    function jobStatus(job) {
        return '<span class="job-status-badge job-status-' + escapeHtml(job.status || "queued") + '">' + escapeHtml(statusLabels[job.status] || job.status || "queued") + "</span>";
    }

    function jobType(job) {
        var type = job.type || "run_script";
        return '<span class="job-type-chip job-type-' + escapeHtml(type) + '">' + icon(typeIcons[type] || "terminal") + escapeHtml(typeLabels[type] || type) + "</span>";
    }

    function canCancel(job) {
        return ["queued", "sent", "running"].indexOf(job.status) >= 0;
    }

    function endpointUrl(job) {
        return job.endpointId ? "/endpoints/" + encodeURIComponent(job.endpointId) + "/" : "/endpoints/?q=" + encodeURIComponent(job.endpoint || "");
    }

    function endpointUrlFromId(id) {
        return id ? "/endpoints/" + encodeURIComponent(id) + "/" : "#";
    }

    function ticketUrlFromId(id) {
        return id ? "/tickets/?q=" + encodeURIComponent(id) : "#";
    }

    function endpointById(id) {
        return endpoints.find(function (item) { return item.id === id; });
    }

    function eventsUrl(job) {
        return "/events/?category=jobs&q=" + encodeURIComponent(job.endpoint || job.id || "");
    }

    function jobOutput(job) {
        return ["Job: " + (job.id || ""), "Endpoint: " + (job.endpoint || ""), "Command: " + (job.command || ""), "", "STDOUT:", job.stdout || "", "", "STDERR:", job.stderr || ""].join("\n");
    }

    function renderIcons() {
        if (window.lucide && typeof window.lucide.createIcons === "function") {
            window.lucide.createIcons();
        }
    }

    function activateTab(name) {
        activeTab = name || activeTab;
        root.querySelectorAll("[data-task-tab]").forEach(function (button) {
            button.classList.toggle("is-active", button.dataset.taskTab === activeTab);
        });
        root.querySelectorAll("[data-task-tab-panel]").forEach(function (panel) {
            var active = panel.dataset.taskTabPanel === activeTab;
            panel.hidden = !active;
            panel.classList.toggle("is-active", active);
        });
        renderIcons();
    }

    function jobFilters() {
        return {
            q: root.querySelector('[data-job-filter="q"]')?.value.trim() || "",
            status: root.querySelector('[data-job-filter="status"]')?.value || "",
            type: root.querySelector('[data-job-filter="type"]')?.value || "",
            endpoint: root.querySelector('[data-job-filter="endpoint"]')?.value || "",
            period: root.querySelector('[data-job-filter="period"]')?.value || ""
        };
    }

    function taskFilters() {
        return {
            q: root.querySelector('[data-task-filter="q"]')?.value.trim() || "",
            status: root.querySelector('[data-task-filter="status"]')?.value || "",
            responsible: root.querySelector('[data-task-filter="responsible"]')?.value || "",
            priority: root.querySelector('[data-task-filter="priority"]')?.value || "",
            category: root.querySelector('[data-task-filter="category"]')?.value || "",
            due: root.querySelector('[data-task-filter="due"]')?.value || ""
        };
    }

    function applyJobFilters() {
        var f = jobFilters();
        var q = f.q.toLowerCase();
        filteredJobs = allJobs.filter(function (job) {
            if (q && [job.endpoint, job.type, job.command, job.createdBy, job.name, job.result].join(" ").toLowerCase().indexOf(q) < 0) return false;
            if (f.status && job.status !== f.status) return false;
            if (f.type && job.type !== f.type) return false;
            if (f.endpoint && job.endpoint !== f.endpoint) return false;
            if (f.period === "24h" && new Date(job.createdAt).getTime() < Date.now() - 86400000) return false;
            if (f.period === "7d" && new Date(job.createdAt).getTime() < Date.now() - (7 * 86400000)) return false;
            return true;
        }).sort(function (a, b) {
            return new Date(b.createdAt || 0).getTime() - new Date(a.createdAt || 0).getTime();
        });
        renderJobs();
    }

    function applyTaskFilters() {
        var f = taskFilters();
        var q = f.q.toLowerCase();
        var now = Date.now();
        var weekEnd = now + 7 * 86400000;
        filteredTasks = allTasks.filter(function (task) {
            if (q && [task.title, task.description, task.responsible, task.category, task.priority, task.linkedUser, task.location].join(" ").toLowerCase().indexOf(q) < 0) return false;
            if (f.status && task.status !== f.status) return false;
            if (f.responsible && task.responsible !== f.responsible) return false;
            if (f.priority && task.priority !== f.priority) return false;
            if (f.category && task.category !== f.category) return false;
            if (f.due === "today" && !sameDay(new Date(task.dueAt), new Date())) return false;
            if (f.due === "overdue" && !(new Date(task.dueAt).getTime() < now && task.status !== "done" && task.status !== "cancelled")) return false;
            if (f.due === "week" && !(new Date(task.dueAt).getTime() >= now && new Date(task.dueAt).getTime() <= weekEnd)) return false;
            return true;
        }).sort(function (a, b) {
            return new Date(a.dueAt || 0).getTime() - new Date(b.dueAt || 0).getTime();
        });
        renderTaskList();
        renderCalendar();
    }

    function updateJobMetrics() {
        var counts = { queued: 0, running: 0, completed: 0, failed: 0, expired: 0 };
        allJobs.forEach(function (job) {
            if (job.status === "sent") counts.queued += 1;
            else if (counts[job.status] != null) counts[job.status] += 1;
        });
        Object.keys(counts).forEach(function (key) {
            root.querySelectorAll('[data-job-metric="' + key + '"]').forEach(function (node) {
                node.textContent = String(counts[key]);
            });
        });
    }

    function renderEndpointOptions() {
        var jobSelect = root.querySelector('[data-job-filter="endpoint"]');
        if (jobSelect) {
            var current = jobSelect.value;
            var options = Array.from(new Set([].concat(endpoints.map(function (item) { return item.hostname; }), allJobs.map(function (item) { return item.endpoint; })))).filter(Boolean).sort();
            jobSelect.innerHTML = '<option value="">Todos</option>' + options.map(function (hostname) {
                return '<option value="' + escapeHtml(hostname) + '">' + escapeHtml(hostname) + '</option>';
            }).join("");
            jobSelect.value = current;
        }
        var taskEndpoint = root.querySelector('[data-task-field="linkedEndpointId"]');
        if (taskEndpoint) {
            var currentEndpoint = taskEndpoint.value;
            taskEndpoint.innerHTML = '<option value="">Nenhum</option>' + endpoints.map(function (endpoint) {
                return '<option value="' + escapeHtml(endpoint.id) + '">' + escapeHtml(endpoint.hostname) + '</option>';
            }).join("");
            taskEndpoint.value = currentEndpoint;
        }
    }

    function renderResponsibleOptions() {
        var select = root.querySelector('[data-task-filter="responsible"]');
        if (!select) return;
        var current = select.value;
        var options = Array.from(new Set(allTasks.map(function (task) { return task.responsible; }))).filter(Boolean).sort();
        select.innerHTML = '<option value="">Todos</option>' + options.map(function (name) {
            return '<option value="' + escapeHtml(name) + '">' + escapeHtml(name) + '</option>';
        }).join("");
        select.value = current;
    }

    function renderJobsTable() {
        if (!jobTbody) return;
        if (!filteredJobs.length) {
            jobTbody.innerHTML = '<tr><td colspan="10" class="empty-state endpoint-empty">' + icon("list-x") + '<strong>Nenhuma tarefa encontrada</strong><span>Ajuste os filtros ou dispare uma acao mockada em endpoints/alertas.</span></td></tr>';
            return;
        }
        jobTbody.innerHTML = filteredJobs.map(function (job) {
            return '<tr data-job-id="' + escapeHtml(job.id) + '">' +
                '<td>' + jobStatus(job) + '</td>' +
                '<td>' + jobType(job) + '<small class="mono job-command">' + escapeHtml(job.command || "") + '</small></td>' +
                '<td><a class="table-link mono" href="' + escapeHtml(endpointUrl(job)) + '">' + escapeHtml(job.endpoint || "-") + '</a></td>' +
                '<td>' + escapeHtml(job.createdBy || "Sistema") + '</td>' +
                '<td class="mono">' + escapeHtml(formatDate(job.createdAt)) + '</td>' +
                '<td class="mono">' + escapeHtml(formatDate(job.startedAt)) + '</td>' +
                '<td class="mono">' + escapeHtml(formatDate(job.finishedAt)) + '</td>' +
                '<td class="mono">' + escapeHtml(formatDuration(job.durationMs)) + '</td>' +
                '<td><strong class="job-result">' + escapeHtml(job.result || "--") + '</strong></td>' +
                '<td><div class="endpoint-row-actions jobs-row-actions">' +
                '<button type="button" data-job-action="details" title="Ver detalhes">' + icon("panel-right-open") + '</button>' +
                '<button type="button" data-job-action="copy" title="Copiar saida">' + icon("copy") + '</button>' +
                '<button type="button" data-job-action="rerun" title="Reexecutar">' + icon("rotate-cw") + '</button>' +
                (canCancel(job) ? '<button type="button" data-job-action="cancel" title="Cancelar">' + icon("ban") + '</button>' : '') +
                '<a href="' + escapeHtml(endpointUrl(job)) + '" title="Abrir endpoint">' + icon("monitor") + '</a>' +
                '<a href="' + escapeHtml(eventsUrl(job)) + '" title="Ver eventos relacionados">' + icon("history") + '</a>' +
                '</div></td>' +
                '</tr>';
        }).join("");
    }

    function renderJobs() {
        updateJobMetrics();
        root.querySelectorAll("[data-filtered-count]").forEach(function (node) {
            node.textContent = String(filteredJobs.length);
        });
        renderEndpointOptions();
        renderJobsTable();
        renderIcons();
    }

    function taskLinks(task) {
        var endpoint = endpointById(task.linkedEndpointId);
        var jobCount = (task.jobIds || []).length;
        var links = [];
        if (task.linkedTicketId) links.push('<span>' + icon("ticket") + escapeHtml(task.linkedTicketId) + '</span>');
        if (endpoint) links.push('<a href="' + escapeHtml(endpointUrlFromId(endpoint.id)) + '">' + icon("monitor") + escapeHtml(endpoint.hostname) + '</a>');
        if (task.linkedUser) links.push('<span>' + icon("user") + escapeHtml(task.linkedUser) + '</span>');
        if (jobCount) links.push('<span>' + icon("terminal") + escapeHtml(jobCount + " job" + (jobCount > 1 ? "s" : "")) + '</span>');
        return links.length ? links.join("") : '<span class="muted-inline">Sem vinculos</span>';
    }

    function taskCalendarBadges(task) {
        var endpoint = endpointById(task.linkedEndpointId);
        var progress = checklistProgress(task);
        var badges = [];
        if (task.linkedTicketId) badges.push('<span title="Chamado vinculado">' + icon("ticket") + '</span>');
        if (endpoint) badges.push('<span title="Endpoint vinculado">' + icon("monitor") + '</span>');
        if ((task.jobIds || []).length) badges.push('<span title="Jobs vinculados">' + icon("terminal") + escapeHtml((task.jobIds || []).length) + '</span>');
        if (progress.total) badges.push('<span title="Checklist">' + icon("check-square") + escapeHtml(progress.label) + '</span>');
        return badges.length ? '<div class="calendar-task-badges">' + badges.join("") + '</div>' : "";
    }

    function renderTaskList() {
        if (!taskListBody) return;
        root.querySelectorAll("[data-task-filtered-count]").forEach(function (node) {
            node.textContent = String(filteredTasks.length);
        });
        renderResponsibleOptions();
        if (!filteredTasks.length) {
            taskListBody.innerHTML = '<tr><td colspan="9" class="empty-state endpoint-empty">' + icon("clipboard-x") + '<strong>Nenhuma tarefa operacional</strong><span>Ajuste filtros ou crie uma tarefa pela Agenda/Modelos.</span></td></tr>';
            return;
        }
        taskListBody.innerHTML = filteredTasks.map(function (task) {
            var progress = checklistProgress(task);
            var dueState = taskDueState(task);
            return '<tr data-task-id="' + escapeHtml(task.id) + '" class="task-row task-due-' + escapeHtml(dueState) + '">' +
                '<td>' + taskStatusBadge(task) + '</td>' +
                '<td><strong class="task-due-label task-due-' + escapeHtml(dueState) + '">' + escapeHtml(formatDate(task.dueAt)) + '</strong></td>' +
                '<td><strong>' + escapeHtml(task.title) + '</strong><small>' + escapeHtml(task.description || "") + '</small></td>' +
                '<td>' + escapeHtml(task.responsible || "-") + '</td>' +
                '<td>' + taskPriorityBadge(task) + '</td>' +
                '<td>' + taskCategoryBadge(task) + '</td>' +
                '<td><span class="task-progress"><i style="width:' + escapeHtml(progress.total ? Math.round(progress.done / progress.total * 100) : 0) + '%"></i></span><small>' + escapeHtml(progress.label) + '</small></td>' +
                '<td><div class="task-links">' + taskLinks(task) + '</div></td>' +
                '<td><div class="endpoint-row-actions jobs-row-actions"><button type="button" data-task-action="details" title="Detalhes">' + icon("panel-right-open") + '</button><button type="button" data-task-action="done" title="Concluir">' + icon("check-circle") + '</button><button type="button" data-task-action="duplicate" title="Duplicar">' + icon("copy") + '</button></div></td>' +
                '</tr>';
        }).join("");
        renderIcons();
    }

    function monthRange(date) {
        var first = new Date(date.getFullYear(), date.getMonth(), 1);
        var start = new Date(first);
        start.setDate(first.getDate() - first.getDay());
        var days = [];
        for (var i = 0; i < 42; i += 1) {
            var current = new Date(start);
            current.setDate(start.getDate() + i);
            days.push(current);
        }
        return days;
    }

    function renderCalendar() {
        if (!taskCalendar) return;
        var label = root.querySelector("[data-calendar-label]");
        if (label) {
            label.textContent = calendarDate.toLocaleDateString("pt-BR", { month: "long", year: "numeric" });
        }
        var weekdays = ["Dom", "Seg", "Ter", "Qua", "Qui", "Sex", "Sab"];
        var header = weekdays.map(function (day) { return '<div class="calendar-weekday">' + day + '</div>'; }).join("");
        var today = new Date();
        var days = monthRange(calendarDate).map(function (date) {
            var inMonth = date.getMonth() === calendarDate.getMonth();
            var tasks = allTasks.filter(function (task) {
                return sameDay(new Date(task.dueAt), date);
            });
            return '<article class="calendar-day ' + (inMonth ? "" : "is-muted") + (sameDay(date, today) ? " is-today" : "") + '">' +
                '<header><strong>' + date.getDate() + '</strong><button type="button" data-task-new-date="' + date.toISOString() + '" title="Criar tarefa">' + icon("plus") + '</button></header>' +
                '<div class="calendar-day-tasks">' + tasks.slice(0, 4).map(function (task) {
                    return '<button type="button" class="calendar-task task-due-' + escapeHtml(taskDueState(task)) + ' task-priority-' + escapeHtml(task.priority) + '" data-calendar-task="' + escapeHtml(task.id) + '"><span>' + escapeHtml(task.title) + '</span><small>' + escapeHtml(task.responsible || "-") + '</small>' + taskCalendarBadges(task) + '</button>';
                }).join("") + (tasks.length > 4 ? '<em>+' + (tasks.length - 4) + ' tarefas</em>' : "") + '</div></article>';
        }).join("");
        taskCalendar.innerHTML = header + days;
        renderIcons();
    }

    function renderTemplates() {
        if (!taskTemplateGrid) return;
        taskTemplateGrid.innerHTML = taskTemplates.map(function (template) {
            return '<article class="task-template-card"><header><span>' + taskCategoryBadge({ category: template.category }) + '</span><strong>' + escapeHtml(template.name) + '</strong></header><p>' + escapeHtml(template.description || "") + '</p><dl><div><dt>Checklist</dt><dd>' + escapeHtml((template.checklist || []).length) + ' itens</dd></div></dl><ol>' + (template.checklist || []).slice(0, 4).map(function (item) { return '<li>' + escapeHtml(item.title) + '</li>'; }).join("") + '</ol><button type="button" data-use-template="' + escapeHtml(template.id) + '">' + icon("play") + 'Usar modelo</button></article>';
        }).join("");
        renderIcons();
    }

    function renderAllTaskViews() {
        applyTaskFilters();
        renderTemplates();
    }

    function setJobDrawerField(name, value) {
        root.querySelectorAll('[data-job-field="' + name + '"]').forEach(function (node) {
            node.textContent = value || "--";
        });
    }

    function openJobDrawer(job) {
        if (!jobDrawer || !job) return;
        currentJob = job;
        setJobDrawerField("name", job.name || typeLabels[job.type] || "Tarefa");
        setJobDrawerField("id", job.id);
        setJobDrawerField("status", statusLabels[job.status] || job.status);
        setJobDrawerField("type", job.type);
        setJobDrawerField("endpoint", job.endpoint);
        setJobDrawerField("createdBy", job.createdBy || "Sistema");
        setJobDrawerField("createdAt", formatDate(job.createdAt));
        setJobDrawerField("startedAt", formatDate(job.startedAt));
        setJobDrawerField("finishedAt", formatDate(job.finishedAt));
        setJobDrawerField("duration", formatDuration(job.durationMs));
        setJobDrawerField("exitCode", job.exitCode == null ? "--" : String(job.exitCode));
        root.querySelector('[data-job-field="payload"]').textContent = JSON.stringify({
            id: job.id,
            command: job.command,
            payload: job.payload || {},
            future_api: { create: "/api/jobs/", agent_pull: "/api/agent/jobs/pull/", agent_result: "/api/agent/jobs/result/" }
        }, null, 2);
        root.querySelector('[data-job-field="stdout"]').textContent = job.stdout || "stdout indisponivel.";
        root.querySelector('[data-job-field="stderr"]').textContent = job.stderr || "stderr vazio.";
        var timeline = root.querySelector("[data-job-timeline]");
        if (timeline) {
            timeline.innerHTML = (job.timeline || [job.status]).map(function (status) {
                return '<li><span class="job-status-badge job-status-' + escapeHtml(status) + '">' + escapeHtml(statusLabels[status] || status) + '</span></li>';
            }).join("");
        }
        var endpointLink = root.querySelector("[data-job-link-endpoint]");
        var eventsLink = root.querySelector("[data-job-link-events]");
        if (endpointLink) endpointLink.href = endpointUrl(job);
        if (eventsLink) eventsLink.href = eventsUrl(job);
        var cancelButton = root.querySelector("[data-job-cancel]");
        if (cancelButton) cancelButton.hidden = !canCancel(job);
        jobDrawer.classList.add("is-open");
        jobDrawer.setAttribute("aria-hidden", "false");
        if (jobBackdrop) jobBackdrop.hidden = false;
        renderIcons();
    }

    function closeJobDrawer() {
        if (!jobDrawer) return;
        jobDrawer.classList.remove("is-open");
        jobDrawer.setAttribute("aria-hidden", "true");
        if (jobBackdrop) jobBackdrop.hidden = true;
    }

    function jobById(id) {
        return allJobs.find(function (job) { return job.id === id; });
    }

    function taskById(id) {
        return allTasks.find(function (task) { return task.id === id; });
    }

    function setTaskField(name, value) {
        var field = root.querySelector('[data-task-field="' + name + '"]');
        if (field) field.value = value == null ? "" : value;
    }

    function renderTaskChecklistEditor(task) {
        if (!taskChecklistEditor) return;
        var items = task && task.checklist ? task.checklist : [];
        taskChecklistEditor.innerHTML = items.map(function (item, index) {
            var action = checklistActionForTitle(item.title || "");
            return '<label class="task-check-row"><input type="checkbox" data-check-index="' + index + '"' + (item.done ? " checked" : "") + '><input type="text" data-check-title="' + index + '" value="' + escapeHtml(item.title || "") + '">' + (action ? '<button type="button" data-check-job-index="' + index + '" title="Criar job mockado para este item">' + icon("terminal") + 'Job</button>' : "") + '</label>';
        }).join("");
    }

    function checklistActionForTitle(title) {
        var value = String(title || "").toLowerCase();
        if (value.indexOf("inventario") >= 0 || value.indexOf("patrimonio") >= 0) return { type: "force_inventory", command: "nightowl.inventory.collect", name: "Forcar inventario" };
        if (value.indexOf("defender") >= 0 || value.indexOf("seguranca") >= 0 || value.indexOf("mfa") >= 0) return { type: "defender_check", command: "nightowl.security.defender", name: "Verificar seguranca" };
        if (value.indexOf("backup") >= 0 || value.indexOf("logs") >= 0 || value.indexOf("evidencia") >= 0) return { type: "collect_logs", command: "nightowl.logs.collect", name: "Coletar logs/evidencias" };
        if (value.indexOf("updates") >= 0 || value.indexOf("atualizacao") >= 0 || value.indexOf("windows") >= 0 || value.indexOf("patch") >= 0) return { type: "windows_update_scan", command: "nightowl.patch.scan", name: "Verificar atualizacoes" };
        if (value.indexOf("instalar") >= 0 || value.indexOf("instalacao") >= 0 || value.indexOf("software") >= 0) return { type: "install_software", command: "nightowl.software.install", name: "Instalar software" };
        if (value.indexOf("conectividade") >= 0 || value.indexOf("retorno do endpoint") >= 0 || value.indexOf("dominio") >= 0) return { type: "ping", command: "nightowl.network.ping", name: "Ping/validacao de rede" };
        return null;
    }

    function draftTaskFromTemplate(template) {
        return {
            id: "",
            title: template.name,
            description: template.description || "",
            status: "open",
            priority: template.category === "security" ? "high" : "normal",
            category: template.category || "support",
            responsible: "Usuario atual",
            startAt: new Date().toISOString(),
            dueAt: new Date(Date.now() + 86400000).toISOString(),
            linkedTicketId: "",
            linkedEndpointId: "",
            linkedUser: "",
            jobIds: [],
            location: "",
            checklist: (template.checklist || []).map(function (item, index) {
                return { id: "draft-" + Date.now() + "-" + index, title: item.title, done: false };
            }),
            timeline: [{ at: new Date().toISOString(), actor: "Sistema", text: "Rascunho criado a partir do modelo " + template.name + "." }]
        };
    }

    function jobsForTask(task) {
        var ids = task && task.jobIds ? task.jobIds : [];
        if (!ids.length) return [];
        return ids.map(function (id) { return jobById(id); }).filter(Boolean);
    }

    function currentDraftTask() {
        return Object.assign({}, currentTask || {}, taskPayloadFromForm());
    }

    function renderTaskLinksPanel(task) {
        if (!taskLinkSummary) return;
        var endpoint = endpointById(task && task.linkedEndpointId);
        var progress = checklistProgress(task || {});
        var jobCount = (task && task.jobIds ? task.jobIds.length : 0);
        var cards = [
            {
                icon: "ticket",
                label: "Chamado",
                value: task && task.linkedTicketId ? task.linkedTicketId : "Sem chamado vinculado",
                muted: !(task && task.linkedTicketId)
            },
            {
                icon: "monitor",
                label: "Endpoint",
                value: endpoint ? endpoint.hostname : "Sem endpoint vinculado",
                muted: !endpoint
            },
            {
                icon: "user",
                label: "Usuario",
                value: task && task.linkedUser ? task.linkedUser : "Sem usuario relacionado",
                muted: !(task && task.linkedUser)
            },
            {
                icon: "check-square",
                label: "Checklist",
                value: progress.total ? progress.label + " itens concluidos" : "Sem checklist",
                muted: !progress.total
            },
            {
                icon: "terminal",
                label: "Jobs",
                value: jobCount ? jobCount + " job" + (jobCount > 1 ? "s" : "") + " vinculado" + (jobCount > 1 ? "s" : "") : "Sem jobs vinculados",
                muted: !jobCount
            }
        ];
        taskLinkSummary.innerHTML = cards.map(function (card) {
            return '<article class="task-link-card' + (card.muted ? " is-muted" : "") + '">' +
                '<span>' + icon(card.icon) + '</span>' +
                '<div><small>' + escapeHtml(card.label) + '</small><strong>' + escapeHtml(card.value) + '</strong></div>' +
                '</article>';
        }).join("");
        renderIcons();
    }

    function renderTaskLinkedJobs(task) {
        if (!taskLinkedJobs) return;
        var jobs = jobsForTask(task || {});
        if (!jobs.length) {
            taskLinkedJobs.innerHTML = '<p class="muted-inline">Nenhum job tecnico vinculado. Crie uma execucao mockada abaixo quando a tarefa estiver associada a um endpoint.</p>';
            return;
        }
        taskLinkedJobs.innerHTML = jobs.map(function (job) {
            return '<article class="task-linked-job" data-job-id="' + escapeHtml(job.id) + '">' +
                '<div>' + jobType(job) + '<strong>' + escapeHtml(job.name || typeLabels[job.type] || job.type) + '</strong><small>' + escapeHtml(job.endpoint || "-") + ' &middot; ' + escapeHtml(formatDate(job.createdAt)) + '</small></div>' +
                '<div>' + jobStatus(job) + '<button type="button" data-job-action="details" title="Ver job">' + icon("panel-right-open") + '</button></div>' +
                '</article>';
        }).join("");
        renderIcons();
    }

    function renderTaskTimeline(task) {
        if (!taskTimeline) return;
        var rows = task && task.timeline ? task.timeline : [];
        taskTimeline.innerHTML = rows.length ? rows.map(function (item) {
            return '<article><strong>' + escapeHtml(item.actor || "Sistema") + '</strong><span>' + escapeHtml(formatDate(item.at)) + '</span><p>' + escapeHtml(item.text || "") + '</p></article>';
        }).join("") : '<p class="muted-inline">Sem comentarios registrados.</p>';
    }

    function openTaskDrawer(task, presetDate) {
        if (!taskDrawer || !taskForm) return;
        currentTask = task || null;
        root.querySelector("[data-task-drawer-title]").textContent = task ? task.title : "Nova tarefa";
        root.querySelector("[data-task-drawer-subtitle]").textContent = task ? (taskStatusLabels[task.status] || task.status) + " · " + (task.responsible || "-") : "Criacao rapida no mock frontend";
        setTaskField("id", task ? task.id : "");
        setTaskField("title", task ? task.title : "");
        setTaskField("description", task ? task.description : "");
        setTaskField("status", task ? task.status : "open");
        setTaskField("priority", task ? task.priority : "normal");
        setTaskField("category", task ? task.category : "support");
        setTaskField("responsible", task ? task.responsible : "Usuario atual");
        setTaskField("startAt", formatDateInput(task ? task.startAt : (presetDate || new Date().toISOString())));
        setTaskField("dueAt", formatDateInput(task ? task.dueAt : (presetDate || new Date(Date.now() + 86400000).toISOString())));
        setTaskField("linkedTicketId", task ? task.linkedTicketId : "");
        setTaskField("linkedEndpointId", task ? task.linkedEndpointId : "");
        setTaskField("linkedUser", task ? task.linkedUser : "");
        setTaskField("location", task ? task.location : "");
        renderTaskChecklistEditor(task || { checklist: [{ id: "c-new", title: "Executar atividade", done: false }] });
        renderTaskLinksPanel(task || { checklist: [{ id: "c-new", title: "Executar atividade", done: false }], jobIds: [] });
        renderTaskLinkedJobs(task || { jobIds: [] });
        renderTaskTimeline(task);
        taskDrawer.classList.add("is-open");
        taskDrawer.setAttribute("aria-hidden", "false");
        if (taskBackdrop) taskBackdrop.hidden = false;
        renderIcons();
    }

    function closeTaskDrawer() {
        if (!taskDrawer) return;
        taskDrawer.classList.remove("is-open");
        taskDrawer.setAttribute("aria-hidden", "true");
        if (taskBackdrop) taskBackdrop.hidden = true;
    }

    function checklistFromEditor() {
        if (!taskChecklistEditor) return [];
        return Array.prototype.slice.call(taskChecklistEditor.querySelectorAll("[data-check-title]")).map(function (input, index) {
            var check = taskChecklistEditor.querySelector('[data-check-index="' + index + '"]');
            return {
                id: currentTask && currentTask.checklist && currentTask.checklist[index] ? currentTask.checklist[index].id : "c-" + Date.now() + "-" + index,
                title: input.value.trim() || "Item de checklist",
                done: Boolean(check && check.checked)
            };
        });
    }

    function taskPayloadFromForm() {
        return {
            title: root.querySelector('[data-task-field="title"]').value.trim() || "Tarefa operacional",
            description: root.querySelector('[data-task-field="description"]').value.trim(),
            status: root.querySelector('[data-task-field="status"]').value,
            priority: root.querySelector('[data-task-field="priority"]').value,
            category: root.querySelector('[data-task-field="category"]').value,
            responsible: root.querySelector('[data-task-field="responsible"]').value.trim() || "Usuario atual",
            startAt: fromDateInput(root.querySelector('[data-task-field="startAt"]').value) || new Date().toISOString(),
            dueAt: fromDateInput(root.querySelector('[data-task-field="dueAt"]').value) || new Date(Date.now() + 86400000).toISOString(),
            linkedTicketId: root.querySelector('[data-task-field="linkedTicketId"]').value.trim(),
            linkedEndpointId: root.querySelector('[data-task-field="linkedEndpointId"]').value,
            linkedUser: root.querySelector('[data-task-field="linkedUser"]').value.trim(),
            location: root.querySelector('[data-task-field="location"]').value.trim(),
            jobIds: currentTask && currentTask.jobIds ? currentTask.jobIds.slice() : [],
            checklist: checklistFromEditor()
        };
    }

    function reloadAll(message) {
        if (!api) {
            showToast("MockNightowlApi indisponivel.");
            return Promise.resolve();
        }
        if (jobLoading) jobLoading.hidden = false;
        return Promise.all([
            api.getJobs({}),
            api.getEndpoints({}),
            api.getOperationalTasks ? api.getOperationalTasks({}) : Promise.resolve([]),
            api.getTaskTemplates ? api.getTaskTemplates() : Promise.resolve([])
        ]).then(function (results) {
            allJobs = results[0] || [];
            endpoints = results[1] || [];
            allTasks = results[2] || [];
            taskTemplates = results[3] || [];
            renderEndpointOptions();
            applyJobFilters();
            renderAllTaskViews();
            if (currentTask && currentTask.id) {
                var freshTask = taskById(currentTask.id);
                if (freshTask) {
                    currentTask = freshTask;
                    renderTaskLinksPanel(freshTask);
                    renderTaskLinkedJobs(freshTask);
                    renderTaskTimeline(freshTask);
                }
            }
            if (message) showToast(message);
        }).finally(function () {
            if (jobLoading) jobLoading.hidden = true;
        });
    }

    function copyOutput(job) {
        var text = jobOutput(job);
        if (operational && typeof operational.runAction === "function") {
            operational.runAction("copy_summary", {
                toastOptions: { target: toast, timeout: 2600 },
                copyText: text,
                endpoint: job.endpoint,
                description: "Saida da tarefa " + job.id + " copiada."
            });
            return;
        }
        showToast("Saida copiada.");
    }

    function rerun(job) {
        if (!api || typeof api.rerunJob !== "function") return;
        api.rerunJob(job.id).then(function () {
            return reloadAll("Tarefa reexecutada no mock.");
        });
    }

    function cancelJob(job) {
        if (!api || typeof api.cancelJob !== "function") return;
        api.cancelJob(job.id).then(function (updated) {
            return reloadAll(updated ? "Tarefa cancelada." : "Tarefa nao encontrada.");
        });
    }

    function saveTask() {
        if (!api) return;
        var id = root.querySelector('[data-task-field="id"]').value;
        var payload = taskPayloadFromForm();
        var request = id && api.updateOperationalTask ? api.updateOperationalTask(id, payload) : api.createOperationalTask(payload);
        request.then(function () {
            closeTaskDrawer();
            return reloadAll(id ? "Tarefa atualizada." : "Tarefa criada.");
        });
    }

    function updateCurrentTask(patch, message) {
        if (!currentTask || !api || typeof api.updateOperationalTask !== "function") return;
        api.updateOperationalTask(currentTask.id, patch).then(function () {
            closeTaskDrawer();
            return reloadAll(message || "Tarefa atualizada.");
        });
    }

    function duplicateCurrentTask() {
        if (!currentTask || !api || typeof api.createOperationalTask !== "function") return;
        var payload = Object.assign({}, currentTask, {
            title: currentTask.title + " (copia)",
            status: "open",
            checklist: (currentTask.checklist || []).map(function (item) { return Object.assign({}, item, { done: false }); })
        });
        delete payload.id;
        api.createOperationalTask(payload).then(function () {
            closeTaskDrawer();
            return reloadAll("Tarefa duplicada.");
        });
    }

    function ensureSavedTask() {
        if (currentTask && currentTask.id) return true;
        showToast("Salve a tarefa antes de criar vinculos operacionais.");
        return false;
    }

    function createTaskJob(type, title) {
        if (!api || typeof api.createMockJob !== "function" || !ensureSavedTask()) return;
        var draft = currentDraftTask();
        var endpoint = endpointById(draft.linkedEndpointId);
        if (!endpoint) {
            showToast("Vincule um endpoint antes de criar job tecnico.");
            return;
        }
        var payload = {
            taskId: currentTask.id,
            endpointId: endpoint.id,
            endpoint: endpoint.hostname,
            type: type || "run_script",
            name: title || ((typeLabels[type] || "Job tecnico") + " - " + currentTask.title),
            payload: { taskId: currentTask.id, title: currentTask.title }
        };
        if (type === "run_script") payload.command = "nightowl.task.linked_job";
        api.createMockJob(payload).then(function () {
            return reloadAll("Job tecnico vinculado a tarefa.");
        });
    }

    function createLinkedJob() {
        createTaskJob("run_script", currentTask ? "Job vinculado - " + currentTask.title : "Job vinculado");
    }

    function createChecklistJob(index) {
        if (!api || typeof api.createMockJob !== "function" || !ensureSavedTask()) return;
        var endpointId = root.querySelector('[data-task-field="linkedEndpointId"]').value;
        var endpoint = endpointById(endpointId);
        if (!endpoint) {
            showToast("Vincule um endpoint antes de criar job do checklist.");
            return;
        }
        var input = taskChecklistEditor ? taskChecklistEditor.querySelector('[data-check-title="' + index + '"]') : null;
        var title = input ? input.value : "";
        var action = checklistActionForTitle(title);
        if (!action) {
            showToast("Este item nao tem acao tecnica mockada.");
            return;
        }
        api.createMockJob({
            taskId: currentTask.id,
            endpointId: endpoint.id,
            endpoint: endpoint.hostname,
            type: action.type,
            name: action.name + " - " + title,
            command: action.command,
            payload: {
                taskId: currentTask ? currentTask.id : "draft",
                checklistItem: title
            }
        }).then(function () {
            return reloadAll("Job mockado criado a partir do checklist.");
        });
    }

    function linkTicketToCurrentTask() {
        if (!api || !ensureSavedTask()) return;
        if (typeof api.createTicketFromTask === "function") {
            api.createTicketFromTask(currentTask.id).then(function (ticket) {
                return reloadAll(ticket ? "Chamado mockado vinculado a tarefa." : "Nao foi possivel vincular chamado.");
            });
            return;
        }
        showToast("API mock de chamados indisponivel.");
    }

    function openTaskTicket() {
        var ticketId = (currentDraftTask().linkedTicketId || "").trim();
        if (!ticketId) {
            showToast("Nenhum chamado vinculado.");
            return;
        }
        window.location.href = ticketUrlFromId(ticketId);
    }

    function openTaskEndpoint() {
        var endpointId = currentDraftTask().linkedEndpointId;
        if (!endpointId) {
            showToast("Nenhum endpoint vinculado.");
            return;
        }
        window.location.href = endpointUrlFromId(endpointId);
    }

    function openTaskEndpointContext(kind) {
        var endpoint = endpointById(currentDraftTask().linkedEndpointId);
        if (!endpoint) {
            showToast("Nenhum endpoint vinculado.");
            return;
        }
        window.location.href = "/" + kind + "/?q=" + encodeURIComponent(endpoint.hostname);
    }

    jobForm?.addEventListener("submit", function (event) {
        event.preventDefault();
        applyJobFilters();
    });

    taskFormFilters?.addEventListener("submit", function (event) {
        event.preventDefault();
        applyTaskFilters();
    });

    taskForm?.addEventListener("submit", function (event) {
        event.preventDefault();
        saveTask();
    });

    taskForm?.addEventListener("change", function (event) {
        if (!event.target.closest("[data-task-field]") && !event.target.closest("[data-check-index]")) return;
        var draft = currentDraftTask();
        renderTaskLinksPanel(draft);
        renderTaskLinkedJobs(draft);
    });

    root.addEventListener("click", function (event) {
        var tab = event.target.closest("[data-task-tab]");
        if (tab) {
            activateTab(tab.dataset.taskTab);
            return;
        }

        var clearJobs = event.target.closest("[data-jobs-clear]");
        if (clearJobs) {
            root.querySelectorAll("[data-job-filter]").forEach(function (field) { field.value = ""; });
            applyJobFilters();
            return;
        }

        var clearTasks = event.target.closest("[data-task-clear]");
        if (clearTasks) {
            root.querySelectorAll("[data-task-filter]").forEach(function (field) { field.value = ""; });
            applyTaskFilters();
            return;
        }

        var jobStatusCard = event.target.closest("[data-job-status-card]");
        if (jobStatusCard) {
            activateTab("jobs");
            var statusField = root.querySelector('[data-job-filter="status"]');
            if (statusField) statusField.value = jobStatusCard.dataset.jobStatusCard;
            applyJobFilters();
            return;
        }

        var density = event.target.closest("[data-jobs-density]");
        if (density) {
            root.querySelectorAll("[data-jobs-density]").forEach(function (item) { item.classList.toggle("is-active", item === density); });
            root.classList.toggle("jobs-compact", density.dataset.jobsDensity === "compact");
            return;
        }

        var row = event.target.closest("[data-job-id]");
        var jobAction = event.target.closest("[data-job-action]");
        if (row) {
            var job = jobById(row.dataset.jobId);
            if (!job) return;
            if (!jobAction && !event.target.closest("a")) {
                openJobDrawer(job);
                return;
            }
            if (!jobAction) return;
            event.preventDefault();
            event.stopPropagation();
            if (jobAction.dataset.jobAction === "details") openJobDrawer(job);
            if (jobAction.dataset.jobAction === "copy") copyOutput(job);
            if (jobAction.dataset.jobAction === "rerun") rerun(job);
            if (jobAction.dataset.jobAction === "cancel") cancelJob(job);
            return;
        }

        var taskRow = event.target.closest("[data-task-id]");
        var taskAction = event.target.closest("[data-task-action]");
        if (taskRow) {
            var task = taskById(taskRow.dataset.taskId);
            if (!task) return;
            if (!taskAction && !event.target.closest("a")) {
                openTaskDrawer(task);
                return;
            }
            if (!taskAction) return;
            event.preventDefault();
            event.stopPropagation();
            if (taskAction.dataset.taskAction === "details") openTaskDrawer(task);
            if (taskAction.dataset.taskAction === "done") {
                currentTask = task;
                updateCurrentTask({ status: "done" }, "Tarefa concluida.");
            }
            if (taskAction.dataset.taskAction === "duplicate") {
                currentTask = task;
                duplicateCurrentTask();
            }
            return;
        }

        var calendarTask = event.target.closest("[data-calendar-task]");
        if (calendarTask) {
            openTaskDrawer(taskById(calendarTask.dataset.calendarTask));
            return;
        }

        var newDate = event.target.closest("[data-task-new-date]");
        if (newDate) {
            openTaskDrawer(null, newDate.dataset.taskNewDate);
            return;
        }

        if (event.target.closest("[data-task-new]")) {
            openTaskDrawer(null);
            return;
        }

        var useTemplate = event.target.closest("[data-use-template]");
        if (useTemplate) {
            var template = taskTemplates.find(function (item) { return item.id === useTemplate.dataset.useTemplate; });
            if (template) {
                openTaskDrawer(draftTaskFromTemplate(template));
                showToast("Modelo carregado. Revise e salve a tarefa.");
            }
            return;
        }

        if (event.target.closest("[data-calendar-prev]")) {
            calendarDate = new Date(calendarDate.getFullYear(), calendarDate.getMonth() - 1, 1);
            renderCalendar();
            return;
        }

        if (event.target.closest("[data-calendar-next]")) {
            calendarDate = new Date(calendarDate.getFullYear(), calendarDate.getMonth() + 1, 1);
            renderCalendar();
            return;
        }

        if (event.target.closest("[data-calendar-today]")) {
            calendarDate = new Date();
            renderCalendar();
            return;
        }

        if (event.target.closest("[data-task-checklist-add]")) {
            if (taskChecklistEditor) {
                taskChecklistEditor.insertAdjacentHTML("beforeend", '<label class="task-check-row"><input type="checkbox" data-check-index="' + taskChecklistEditor.querySelectorAll("[data-check-title]").length + '"><input type="text" data-check-title="' + taskChecklistEditor.querySelectorAll("[data-check-title]").length + '" value="Novo item"></label>');
            }
            return;
        }

        var checkJob = event.target.closest("[data-check-job-index]");
        if (checkJob) {
            createChecklistJob(checkJob.dataset.checkJobIndex);
            return;
        }

        var typedJob = event.target.closest("[data-task-create-job-type]");
        if (typedJob) {
            createTaskJob(typedJob.dataset.taskCreateJobType);
            return;
        }

        if (event.target.closest("[data-task-link-ticket]")) {
            linkTicketToCurrentTask();
            return;
        }

        if (event.target.closest("[data-task-open-ticket]")) {
            openTaskTicket();
            return;
        }

        if (event.target.closest("[data-task-open-endpoint]")) {
            openTaskEndpoint();
            return;
        }

        if (event.target.closest("[data-task-open-alerts]")) {
            openTaskEndpointContext("alerts");
            return;
        }

        if (event.target.closest("[data-task-open-events]")) {
            openTaskEndpointContext("events");
        }
    });

    root.querySelectorAll("[data-job-drawer-close]").forEach(function (button) {
        button.addEventListener("click", closeJobDrawer);
    });
    if (jobBackdrop) jobBackdrop.addEventListener("click", closeJobDrawer);

    root.querySelectorAll("[data-task-drawer-close]").forEach(function (button) {
        button.addEventListener("click", closeTaskDrawer);
    });
    if (taskBackdrop) taskBackdrop.addEventListener("click", closeTaskDrawer);

    document.addEventListener("keydown", function (event) {
        if (event.key === "Escape") {
            closeJobDrawer();
            closeTaskDrawer();
        }
    });

    root.querySelectorAll("[data-job-copy-output]").forEach(function (button) {
        button.addEventListener("click", function () {
            if (currentJob) copyOutput(currentJob);
        });
    });
    root.querySelectorAll("[data-job-rerun]").forEach(function (button) {
        button.addEventListener("click", function () {
            if (currentJob) rerun(currentJob);
        });
    });
    root.querySelectorAll("[data-job-cancel]").forEach(function (button) {
        button.addEventListener("click", function () {
            if (currentJob) cancelJob(currentJob);
        });
    });
    root.querySelectorAll("[data-jobs-refresh]").forEach(function (button) {
        button.addEventListener("click", function () {
            reloadAll("Tarefas atualizadas.");
        });
    });
    root.querySelectorAll("[data-task-complete]").forEach(function (button) {
        button.addEventListener("click", function () {
            updateCurrentTask({ status: "done" }, "Tarefa concluida.");
        });
    });
    root.querySelectorAll("[data-task-cancel]").forEach(function (button) {
        button.addEventListener("click", function () {
            updateCurrentTask({ status: "cancelled" }, "Tarefa cancelada.");
        });
    });
    root.querySelectorAll("[data-task-duplicate]").forEach(function (button) {
        button.addEventListener("click", duplicateCurrentTask);
    });
    root.querySelectorAll("[data-task-create-job]").forEach(function (button) {
        button.addEventListener("click", createLinkedJob);
    });

    ["nightowl:job-created", "nightowl:job-updated", "nightowl:event-created", "nightowl:task-updated"].forEach(function (eventName) {
        window.addEventListener(eventName, function () {
            reloadAll();
        });
    });

    if (operational) {
        operational.initOperationalChrome(root, { countSelector: "[data-job-id]", staleAfterSeconds: 240 });
    }
    activateTab(activeTab);
    reloadAll();
}());
