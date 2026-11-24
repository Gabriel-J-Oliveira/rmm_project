(function () {
    "use strict";

    const root = document.querySelector("[data-endpoint-detail]");
    const dialog = document.querySelector("[data-service-manager-dialog]");
    if (!root || !dialog || root.dataset.endpointId === undefined) return;

    const endpointId = root.dataset.endpointId;
    const baseUrl = "/api/agent/service-manager/" + encodeURIComponent(endpointId) + "/";
    const availabilityUrl = baseUrl + "availability/";
    const status = dialog.querySelector("[data-service-status]");
    const refreshButton = dialog.querySelector("[data-service-refresh]");
    const rowsBody = dialog.querySelector("[data-service-rows]");
    const empty = dialog.querySelector("[data-service-empty]");
    const properties = dialog.querySelector("[data-service-properties]");
    const propertiesContent = dialog.querySelector("[data-service-properties-content]");
    const toolbar = dialog.querySelector("[data-service-toolbar]");
    const collectedAt = dialog.querySelector("[data-service-collected-at]");
    const search = dialog.querySelector("[data-service-search]");
    const stateFilter = dialog.querySelector("[data-service-state-filter]");
    const startupFilter = dialog.querySelector("[data-service-startup-filter]");
    const sortSelect = dialog.querySelector("[data-service-sort]");
    const actionConfirm = dialog.querySelector("[data-service-action-confirm]");
    const actionDescription = dialog.querySelector("[data-service-action-description]");
    const actionSubmit = dialog.querySelector("[data-service-action-submit]");
    const actionStatus = dialog.querySelector("[data-service-action-status]");
    const actionCheck = dialog.querySelector("[data-service-action-check]");
    const endpointLabel = dialog.querySelector("[data-service-endpoint-label]");
    const state = {
        available: false,
        reason: "Verificando autorização, identidade e capacidade do agente.",
        rows: null,
        collectedAt: "",
        total: 0,
        pageSize: 20,
        complete: false,
        currentJobId: "",
        nextCursor: null,
        pendingRequestId: "",
        pendingRows: [],
        snapshotId: "",
        pageIndex: 0,
        scanDeadline: 0,
        scanStartedAt: 0,
        selectedName: "",
        detailsOpen: false,
        pollPromise: null,
        serviceActions: {},
        pendingAction: null,
        pendingActionRequestId: "",
        actionJobId: "",
        actionPollPromise: null,
        staleTimer: null
    };

    const serviceActionLabels = {start_service: "Iniciar", stop_service: "Parar", restart_service: "Reiniciar"};
    const serviceActionErrors = {
        service_control_disabled: "O controle de serviços está desativado no backend.",
        administrative_actions_disabled: "Ações administrativas estão desativadas no backend.",
        admin_resource_not_authorized: "Este serviço ou ação não está autorizado para o endpoint.",
        action_forbidden: "Seu usuário não tem permissão para esta ação.",
        action_capability_unconfirmed: "A capacidade do agente não foi confirmada recentemente.",
        service_endpoint_ineligible: "O endpoint deixou de estar elegível.",
        job_conflict: "Outro job está ativo neste endpoint.",
        SERVICE_RESULT_UNCONFIRMED: "O backend não confirmou o resultado da operação.",
        JOB_TIMEOUT: "A operação excedeu o tempo limite.",
        JOB_EXECUTION_FAILED: "O agente informou falha ao executar a operação.",
        ACTION_AUTHORIZATION_REVOKED: "A autorização foi revogada antes do despacho.",
        SERVICE_ACTION_FAILED: "A operação falhou sem confirmação do estado final."
    };

    function csrfToken() {
        const cookie = document.cookie.split(";").map(part => part.trim()).find(part => part.startsWith("csrftoken="));
        return cookie ? decodeURIComponent(cookie.substring("csrftoken=".length)) : "";
    }

    async function requestJson(url, options) {
        const response = await fetch(url, Object.assign({credentials: "same-origin", headers: {Accept: "application/json"}}, options || {}));
        let body;
        try { body = await response.json(); } catch (_) { body = {}; }
        if (!response.ok) {
            const error = new Error(body.error || "request_failed");
            error.status = response.status;
            throw error;
        }
        return body;
    }

    const reasonText = {
        collection_disabled: "A consulta de serviços está desativada no backend.",
        endpoint_inactive: "O endpoint não está ativo para novas solicitações.",
        endpoint_offline: "O agente precisa estar online para consultar os serviços.",
        identity_unavailable: "A identidade do endpoint não está confirmada como única.",
        capability_unconfirmed: "O agente não oferece consulta paginada. Pode ser necessária uma versão Windows mais recente.",
        service_manager_forbidden: "Seu usuário não tem autorização técnica para consultar serviços.",
        job_conflict: "Há outro job em andamento neste endpoint. Aguarde e tente novamente.",
        cursor_expired: "A sessão de páginas expirou. Atualize para iniciar uma nova coleta.",
        cursor_already_used: "Esta página já foi solicitada. Atualize para iniciar uma nova coleta.",
        service_snapshot_expired: "A cópia temporária do agente expirou. Atualize para consultar novamente.",
        service_limit_exceeded: "A lista excede o limite seguro de 800 serviços; nenhuma lista parcial foi apresentada.",
        SERVICE_SNAPSHOT_EXPIRED: "A cópia temporária do agente expirou. Atualize para consultar novamente.",
        SERVICE_LIMIT_EXCEEDED: "A lista excede o limite seguro de 800 serviços; nenhuma lista parcial foi apresentada.",
        JOB_TIMEOUT: "A consulta excedeu o tempo limite. Atualize para iniciar novamente.",
        timed_out: "A consulta excedeu o tempo limite. Atualize para iniciar novamente.",
        expired: "O job expirou antes de concluir. Atualize para tentar novamente.",
        failed: "O agente não conseguiu concluir a consulta. Atualize para tentar novamente.",
        unsupported: "O agente não aceitou o contrato de consulta. Atualize o agente e tente novamente.",
        service_result_invalid: "A resposta do agente não passou pela validação. Nenhuma lista foi apresentada."
    };

    function explain(error) {
        const code = error && error.message ? error.message : "";
        return reasonText[code] || (error && error.status === 403
            ? reasonText.service_manager_forbidden
            : "Não foi possível consultar os serviços. Nenhum dado parcial foi apresentado.");
    }

    function setStatus(message, kind) {
        status.textContent = message;
        status.dataset.state = kind || "info";
    }

    function stateGroup(value) {
        if (value === "Running") return "running";
        if (value === "Stopped" || value === "Paused") return "stopped";
        if (["StartPending", "StopPending", "ContinuePending", "PausePending"].includes(value)) return "pending";
        return "unknown";
    }

    function stateLabel(value) {
        const labels = {Running: "Executando", Stopped: "Parado", Paused: "Pausado", StartPending: "Início pendente", StopPending: "Parada pendente", ContinuePending: "Retomada pendente", PausePending: "Pausa pendente", unknown: "Desconhecido"};
        return labels[value] || labels.unknown;
    }

    function startupLabel(value) {
        const labels = {Automatic: "Automática", Manual: "Manual", Disabled: "Desabilitada", Boot: "Boot", System: "Sistema", unknown: "Desconhecida"};
        return labels[value] || labels.unknown;
    }

    function addText(parent, tag, className, text) {
        const element = document.createElement(tag);
        if (className) element.className = className;
        element.textContent = text;
        parent.appendChild(element);
        return element;
    }

    function visibleRows() {
        const all = state.rows || [];
        const query = String(search.value || "").trim().toLocaleLowerCase();
        const selectedState = stateFilter.value;
        const selectedStartup = startupFilter.value;
        const filtered = all.filter(item => {
            const matchesQuery = !query || item.name.toLocaleLowerCase().includes(query) || item.display_name.toLocaleLowerCase().includes(query);
            return matchesQuery && (selectedState === "all" || stateGroup(item.status) === selectedState)
                && (selectedStartup === "all" || item.startup_type === selectedStartup);
        });
        const [key, direction] = sortSelect.value.split("-");
        const field = key === "display" ? "display_name" : key === "state" ? "status" : key === "startup" ? "startup_type" : "name";
        filtered.sort((a, b) => String(a[field]).localeCompare(String(b[field]), undefined, {sensitivity: "base"}) * (direction === "desc" ? -1 : 1));
        return filtered;
    }

    function renderProperties(item) {
        propertiesContent.replaceChildren();
        properties.hidden = !state.detailsOpen;
        if (!item) {
            addText(propertiesContent, "p", "", "Selecione um serviço para ver os dados coletados.");
            return;
        }
        const list = document.createElement("dl");
        [["Nome interno", item.name], ["Nome de exibição", item.display_name || "Não informado"],
            ["Estado", stateLabel(item.status)], ["Estado reportado", item.status || "unknown"],
            ["Inicialização", startupLabel(item.startup_type)], ["Coletado em", formatCollectedAt(state.collectedAt)]].forEach(pair => {
            const wrap = document.createElement("div");
            addText(wrap, "dt", "", pair[0]);
            addText(wrap, "dd", "", pair[1]);
            list.appendChild(wrap);
        });
        propertiesContent.appendChild(list);
    }

    function operationReason(action, item) {
        const availability = state.serviceActions[action] || {};
        if (availability.enabled !== true) return availability.reason || "Ação não autorizada para este operador ou endpoint.";
        if (!(availability.services || []).some(name => String(name).toLocaleLowerCase() === item.name.toLocaleLowerCase())) {
            return "Este serviço não está autorizado para esta ação.";
        }
        if (state.actionJobId) return "Aguarde a conclusão do job atual.";
        if (item.status === "unknown" || ["StartPending", "StopPending", "ContinuePending", "PausePending"].includes(item.status)) {
            return "O estado atual não permite uma operação segura.";
        }
        if (action === "start_service" && item.status !== "Stopped") return "O serviço não está parado.";
        if (action === "stop_service" && item.status !== "Running") return "O serviço não está em execução.";
        if (action === "restart_service" && item.status !== "Running") return "Reinício disponível somente para serviço em execução.";
        return "";
    }

    function actionButton(action, item) {
        const reason = operationReason(action, item);
        const button = document.createElement("button");
        button.type = "button";
        button.className = "endpoint-services-action-button";
        button.dataset.serviceControl = action;
        button.dataset.serviceName = item.name;
        button.textContent = serviceActionLabels[action];
        button.disabled = Boolean(reason);
        button.title = reason || (serviceActionLabels[action] + " serviço " + item.name);
        button.setAttribute("aria-label", serviceActionLabels[action] + " " + item.name + (reason ? ": " + reason : ""));
        return button;
    }

    function renderRows() {
        rowsBody.replaceChildren();
        const filtered = visibleRows();
        for (const item of filtered) {
            const row = document.createElement("tr");
            row.dataset.serviceRow = item.name;
            row.dataset.serviceName = item.name;
            row.classList.toggle("is-selected", item.name === state.selectedName);
            const radioCell = document.createElement("td");
            const radioLabel = document.createElement("label");
            radioLabel.className = "endpoint-services-radio-label";
            const radio = document.createElement("input");
            radio.type = "radio";
            radio.name = "endpoint-services-" + endpointId;
            radio.value = item.name;
            radio.checked = item.name === state.selectedName;
            radio.dataset.serviceSelection = "";
            radio.dataset.serviceName = item.name;
            radio.setAttribute("aria-label", "Selecionar serviço " + item.name);
            radioLabel.appendChild(radio);
            radioCell.appendChild(radioLabel);
            row.appendChild(radioCell);
            const nameCell = document.createElement("td");
            addText(nameCell, "span", "endpoint-services-name", item.name).title = item.name;
            row.appendChild(nameCell);
            const displayCell = document.createElement("td");
            addText(displayCell, "span", "endpoint-services-display-name", item.display_name || "Não informado").title = item.display_name || "Não informado";
            row.appendChild(displayCell);
            const stateCell = document.createElement("td");
            addText(stateCell, "span", "endpoint-services-state endpoint-services-state--" + stateGroup(item.status), stateLabel(item.status));
            row.appendChild(stateCell);
            addText(row, "td", "", startupLabel(item.startup_type));
            const actionCell = document.createElement("td");
            const actions = document.createElement("div");
            actions.className = "endpoint-services-row-actions";
            for (const action of ["start_service", "stop_service", "restart_service"]) actions.appendChild(actionButton(action, item));
            const authorized = ["start_service", "stop_service", "restart_service"].some(action =>
                (state.serviceActions[action] && state.serviceActions[action].enabled === true) &&
                (state.serviceActions[action].services || []).some(name => String(name).toLocaleLowerCase() === item.name.toLocaleLowerCase()));
            if (!authorized) addText(actions, "small", "endpoint-services-action-blocked", "Sem autorização para este serviço.");
            actionCell.appendChild(actions);
            row.appendChild(actionCell);
            rowsBody.appendChild(row);
        }
        empty.hidden = filtered.length > 0 || !state.complete || (state.rows || []).length === 0;
        if (state.complete && state.rows && state.rows.length === 0) {
            empty.hidden = false;
            empty.textContent = "O agente confirmou que não há serviços para apresentar.";
        } else {
            empty.textContent = "Nenhum serviço corresponde à pesquisa e aos filtros.";
        }
        toolbar.hidden = !state.complete;
        const selected = (state.rows || []).find(item => item.name === state.selectedName) || null;
        if (!selected) state.detailsOpen = false;
        properties.closest(".endpoint-services-content").classList.toggle("details-closed", !state.detailsOpen);
        renderProperties(selected);
    }

    function formatCollectedAt(value) {
        const date = value ? new Date(value) : null;
        return date && !Number.isNaN(date.getTime()) ? date.toLocaleString() : "Não informado";
    }

    function updateFreshness() {
        if (!state.complete || !state.collectedAt) {
            collectedAt.textContent = "Coleta ainda não concluída.";
            return;
        }
        const ageMs = Date.now() - new Date(state.collectedAt).getTime();
        const stale = !Number.isFinite(ageMs) || ageMs > 15 * 60 * 1000 || ageMs < -5 * 60 * 1000
            || root.dataset.endpointStatus === "offline";
        collectedAt.textContent = "Coletado em " + formatCollectedAt(state.collectedAt) + (stale ? " · Dados desatualizados" : " · Dados recentes");
        collectedAt.dataset.stale = stale ? "true" : "false";
    }

    function updateQueryButton() {
        const buttons = root.querySelectorAll('[data-endpoint-action="query_service"]');
        buttons.forEach(button => {
            button.disabled = !state.available;
            if (state.available) button.removeAttribute("title");
            else button.title = state.reason || "Consulta de serviços indisponível.";
            const wrapper = button.closest(".endpoint-action-availability");
            if (!wrapper) return;
            let note = wrapper.querySelector("[data-service-manager-reason]") || wrapper.querySelector("small");
            if (state.available) { if (note) note.remove(); return; }
            if (!note) { note = document.createElement("small"); wrapper.appendChild(note); }
            note.dataset.serviceManagerReason = "";
            const reason = state.reason || "Consulta de serviços indisponível.";
            if (note.textContent !== reason) note.textContent = reason;
        });
    }

    async function loadAvailability() {
        try {
            const data = await requestJson(availabilityUrl);
            state.available = data.available === true;
            state.reason = data.reason || reasonText[data.reason_code] || "Consulta de serviços indisponível.";
            if (Number.isFinite(data.page_size) && data.page_size > 0) state.pageSize = data.page_size;
            state.serviceActions = data.service_actions && typeof data.service_actions === "object" ? data.service_actions : {};
        } catch (error) {
            state.available = false;
            state.reason = explain(error);
            state.serviceActions = {};
        }
        updateQueryButton();
        if (!state.available) setStatus(state.reason, "blocked");
    }

    async function postPage(cursor) {
        const requestId = (window.crypto && window.crypto.randomUUID) ? window.crypto.randomUUID() : "xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx".replace(/[xy]/g, c => {
            const r = Math.random() * 16 | 0; return (c === "x" ? r : (r & 3 | 8)).toString(16);
        });
        const body = {request_id: requestId, cursor: cursor || null};
        const response = await requestJson(baseUrl, {method: "POST", headers: {"Content-Type": "application/json", "X-CSRFToken": csrfToken()}, body: JSON.stringify(body)});
        state.currentJobId = response.job_id;
        state.pendingRequestId = requestId;
        state.nextCursor = cursor || null;
    }

    async function dispatchPage(cursor) {
        await postPage(cursor);
        return pollCurrentJob();
    }

    function wait(milliseconds) { return new Promise(resolve => window.setTimeout(resolve, milliseconds)); }

    async function pollCurrentJob() {
        if (state.pollPromise) return state.pollPromise;
        state.pollPromise = (async function () {
            const perJobDeadline = Math.min(state.scanDeadline || (Date.now() + 90000), Date.now() + 90000);
            while (state.currentJobId && dialog.open && Date.now() < perJobDeadline) {
                let data;
                try { data = await requestJson(baseUrl + "jobs/" + encodeURIComponent(state.currentJobId) + "/"); }
                catch (error) { state.currentJobId = ""; state.pendingRows = []; state.complete = false; setStatus(explain(error), "error"); return false; }
                if (data.status === "completed" && data.page) {
                    state.currentJobId = "";
                    const page = data.page;
                    if (state.snapshotId && (state.snapshotId !== page.snapshot_id || state.total !== page.total_services || state.collectedAt !== page.collected_at)) {
                        state.pendingRows = []; state.complete = false; setStatus("A identidade da página ou o snapshot mudou. Nenhum resultado parcial foi apresentado; atualize para reiniciar.", "error"); return false;
                    }
                    if (!state.snapshotId) {
                        state.snapshotId = page.snapshot_id;
                        state.total = page.total_services;
                        state.collectedAt = page.collected_at;
                    }
                    if (data.page_index !== state.pageIndex + 1 || !Array.isArray(page.services) || state.pendingRows.length + page.services.length > 800) {
                        state.pendingRows = []; state.complete = false; setStatus("A paginação recebida não passou pela verificação de sequência e limite.", "error"); return false;
                    }
                    state.pageIndex = data.page_index;
                    state.pendingRows.push(...page.services);
                    if (page.cursor) {
                        state.nextCursor = page.cursor;
                        if (Date.now() >= state.scanDeadline) {
                            state.pendingRows = []; state.complete = false; setStatus("A consulta excedeu o limite total de cinco minutos. Atualize para começar novamente.", "error"); return false;
                        }
                        const pageCount = Math.ceil(state.total / state.pageSize);
                        setStatus("Coleta confirmada: página " + state.pageIndex + (pageCount ? " de " + pageCount : "") + "; continuando a leitura limitada.", "pending");
                        try { await postPage(page.cursor); }
                        catch (error) { state.pendingRows = []; state.complete = false; setStatus(explain(error), "error"); return false; }
                        continue;
                    }
                    if (state.pendingRows.length !== state.total) {
                        state.pendingRows = []; state.complete = false; setStatus("A consulta terminou sem cobrir todos os serviços. Nenhuma lista parcial foi apresentada.", "error"); return false;
                    }
                    state.rows = state.pendingRows.slice();
                    state.pendingRows = [];
                    state.complete = true;
                    state.nextCursor = null;
                    await loadAvailability();
                    if (state.selectedName && !state.rows.some(item => item.name === state.selectedName)) state.selectedName = "";
                    const elapsed = state.scanStartedAt ? Math.max(0, (Date.now() - state.scanStartedAt) / 1000) : 0;
                    setStatus("Coleta concluída: " + state.rows.length + " serviços confirmados em " + state.pageIndex + " página(s)" +
                        (elapsed ? " · " + elapsed.toFixed(1) + " s" : "") + ".", "success");
                    state.scanStartedAt = 0;
                    updateFreshness();
                    renderRows();
                    return true;
                }
                if (["failed", "expired", "cancelled", "timed_out", "unsupported", "invalid_parameters", "interrupted"].includes(data.status)) {
                    state.currentJobId = ""; state.pendingRows = []; state.complete = false;
                    setStatus(reasonText[data.error_code] || reasonText[data.status] || "A consulta não foi concluída. Nenhuma lista parcial foi apresentada.", "error");
                    return false;
                }
                const pageCount = state.total ? Math.ceil(state.total / state.pageSize) : 0;
                setStatus("Job " + (data.status === "queued" ? "na fila" : data.status === "sent" ? "entregue ao agente" : data.status === "running" ? "em execução" : "aguardando estado confirmado") +
                    (state.pageIndex ? " · Página " + (state.pageIndex + 1) + (pageCount ? " de " + pageCount : "") : ""), "pending");
                await wait(1500);
            }
            if (state.currentJobId && dialog.open) setStatus("A solicitação continua pendente no backend. Reabra o gerenciador para retomar o acompanhamento; nenhum resultado parcial será exibido.", "pending");
            return false;
        })().finally(() => { state.pollPromise = null; });
        return state.pollPromise;
    }

    async function refresh() {
        if (!state.available || refreshButton.disabled) return;
        refreshButton.disabled = true;
        if (!state.currentJobId) {
            state.rows = null; state.complete = false; state.total = 0; state.collectedAt = "";
            state.snapshotId = ""; state.pageIndex = 0; state.pendingRows = []; state.nextCursor = null;
            state.scanDeadline = Date.now() + 5 * 60 * 1000;
            state.scanStartedAt = Date.now();
            renderRows();
            setStatus("Solicitando leitura somente leitura ao agente.", "pending");
            try { await dispatchPage(null); }
            catch (error) { state.currentJobId = ""; state.pendingRows = []; setStatus(explain(error), "error"); }
        } else {
            state.scanDeadline = Date.now() + 5 * 60 * 1000;
            await pollCurrentJob();
        }
        refreshButton.disabled = !state.available;
    }

    function openManager(trigger) {
        if (!state.available) return;
        dialog.returnFocus = trigger;
        dialog.showModal();
        loadAvailability().then(renderRows);
        if (state.complete) {
            updateFreshness(); renderRows();
            setStatus("Exibindo a última coleta confirmada. Use Atualizar dados para consultar novamente.", "success");
        } else if (state.currentJobId) {
            state.scanDeadline = Date.now() + 5 * 60 * 1000;
            pollCurrentJob();
        } else {
            refresh();
        }
        if (state.actionJobId) pollServiceAction();
    }

    function uuid() {
        if (window.crypto && window.crypto.randomUUID) return window.crypto.randomUUID();
        return "xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx".replace(/[xy]/g, c => {
            const r = Math.random() * 16 | 0; return (c === "x" ? r : (r & 3 | 8)).toString(16);
        });
    }

    function prepareServiceAction(action, serviceName) {
        const item = (state.rows || []).find(row => row.name === serviceName);
        if (!item || operationReason(action, item)) return;
        state.pendingAction = {action, serviceName};
        state.pendingActionRequestId = uuid();
        actionDescription.textContent = serviceActionLabels[action] + " o serviço “" + serviceName + "” no endpoint “" +
            (endpointLabel.textContent || root.dataset.endpointId) + "”? Esta operação será enviada ao agente Windows.";
        actionConfirm.hidden = false;
        actionSubmit.disabled = false;
        actionStatus.hidden = true;
        actionCheck.hidden = true;
        actionSubmit.focus();
    }

    function showActionStatus(message, kind) {
        actionStatus.textContent = message;
        actionStatus.dataset.state = kind || "info";
        actionStatus.hidden = false;
    }

    async function submitServiceAction() {
        if (!state.pendingAction || actionSubmit.disabled) return;
        const action = state.pendingAction;
        actionSubmit.disabled = true;
        showActionStatus("Validando autorização e solicitando o job…", "pending");
        try {
            const response = await requestJson(baseUrl + "actions/", {
                method: "POST", headers: {"Content-Type": "application/json", "X-CSRFToken": csrfToken()},
                body: JSON.stringify({request_id: state.pendingActionRequestId, action_id: action.action,
                    service_name: action.serviceName})
            });
            state.pendingActionRequestId = "";
            state.pendingAction = null;
            state.actionJobId = response.job_id;
            actionConfirm.hidden = true;
            actionCheck.hidden = false;
            actionCheck.disabled = false;
            setStatus("Solicitação aceita; aguardando execução e confirmação real do agente.", "pending");
            showActionStatus("Job " + response.status + ". A operação ainda não está confirmada.", "pending");
            renderRows();
            pollServiceAction();
        } catch (error) {
            const code = error.message || "";
            const message = serviceActionErrors[code] || (code === "request_id_conflict"
                ? "O identificador da solicitação já foi usado com outro conteúdo. Atualize os dados e tente novamente."
                : "A solicitação foi recusada pelo backend: " + (serviceActionErrors[code] || "requisitos ou autorização não atendidos."));
            showActionStatus(message, "error");
            actionSubmit.disabled = false;
            await loadAvailability();
            renderRows();
        }
    }

    async function pollServiceAction() {
        if (state.actionPollPromise || !state.actionJobId || !dialog.open) return state.actionPollPromise;
        state.actionPollPromise = (async function () {
            const deadline = Date.now() + 45000;
            while (state.actionJobId && dialog.open && Date.now() < deadline) {
                let data;
                try { data = await requestJson(baseUrl + "actions/" + encodeURIComponent(state.actionJobId) + "/"); }
                catch (error) {
                    showActionStatus("Não foi possível consultar o job. O estado permanece não confirmado; use Consultar resultado.", "error");
                    actionCheck.hidden = false;
                    return;
                }
                if (data.status === "completed") {
                    state.actionJobId = "";
                    actionCheck.hidden = true;
                    if (data.result_confirmed === true && data.result && typeof data.result.state === "string") {
                        showActionStatus("Concluído e confirmado pelo agente: " + data.service_name + " está " + stateLabel(data.result.state) +
                            (data.result.changed ? "." : " (já estava nesse estado)."), "success");
                        setStatus("Operação confirmada. Consultando novamente a lista de serviços…", "success");
                        await refresh();
                    } else {
                        showActionStatus(serviceActionErrors[data.error_code] || serviceActionErrors.SERVICE_RESULT_UNCONFIRMED, "error");
                    }
                    renderRows();
                    return;
                }
                if (["failed", "expired", "cancelled", "timed_out", "unsupported", "invalid_parameters", "interrupted"].includes(data.status)) {
                    state.actionJobId = "";
                    actionCheck.hidden = true;
                    const code = data.error_code || data.status;
                    showActionStatus(serviceActionErrors[code] || (data.status === "timed_out"
                        ? "A operação excedeu o tempo limite; o estado final não foi confirmado."
                        : "A operação falhou ou foi bloqueada. Nenhum sucesso foi presumido."), "error");
                    await loadAvailability();
                    renderRows();
                    return;
                }
                showActionStatus("Job " + (data.status === "queued" ? "na fila" : data.status === "sent" ? "entregue ao agente" :
                    data.status === "running" ? "em execução" : "aguardando confirmação") + ".", "pending");
                await wait(1200);
            }
            if (state.actionJobId && dialog.open) {
                showActionStatus("Sem confirmação após o período de acompanhamento. O job pode continuar ativo; consulte novamente antes de solicitar outra operação.", "pending");
                actionCheck.hidden = false;
            }
        })().finally(() => { state.actionPollPromise = null; });
        return state.actionPollPromise;
    }

    function closeManager() { dialog.close(); }

    root.addEventListener("click", function (event) {
        const button = event.target.closest('[data-endpoint-action="query_service"]');
        if (!button) return;
        event.preventDefault();
        event.stopImmediatePropagation();
        if (state.available) openManager(button);
        else setStatus(state.reason || "Consulta indisponível.", "blocked");
    }, true);

    dialog.addEventListener("click", function (event) {
        const control = event.target.closest("[data-service-control]");
        if (control) { event.preventDefault(); prepareServiceAction(control.dataset.serviceControl, control.dataset.serviceName); return; }
        if (event.target.closest("[data-service-properties-close]")) {
            state.detailsOpen = false;
            renderRows();
            const selected = rowsBody.querySelector('[data-service-selection]:checked');
            if (selected) selected.focus();
            return;
        }
        if (event.target.closest("[data-service-action-cancel]")) { state.pendingAction = null; state.pendingActionRequestId = ""; actionConfirm.hidden = true; actionStatus.hidden = true; return; }
        if (event.target.closest("[data-service-action-submit]")) { submitServiceAction(); return; }
        if (event.target.closest("[data-service-action-check]")) { pollServiceAction(); return; }
        if (event.target.closest("[data-service-close]")) closeManager();
        const radio = event.target.closest("[data-service-selection]");
        const row = event.target.closest("[data-service-row]");
        if (radio) {
            if (state.selectedName !== radio.value || !state.detailsOpen) {
                state.selectedName = radio.value;
                state.detailsOpen = true;
                renderRows();
                rowsBody.querySelector("[data-service-selection]:checked")?.focus();
            }
            return;
        }
        if (row) {
            state.selectedName = row.dataset.serviceName;
            state.detailsOpen = true;
            renderRows();
            rowsBody.querySelector("[data-service-selection]:checked")?.focus();
        }
        if (event.target === dialog) closeManager();
    });
    rowsBody.addEventListener("change", function (event) {
        const radio = event.target.closest("[data-service-selection]");
        if (!radio) return;
        state.selectedName = radio.value;
        state.detailsOpen = true;
        renderRows();
        rowsBody.querySelector("[data-service-selection]:checked")?.focus();
    });
    dialog.addEventListener("close", function () {
        const trigger = dialog.returnFocus;
        if (trigger && document.contains(trigger)) trigger.focus();
    });
    refreshButton.addEventListener("click", refresh);
    [search, stateFilter, startupFilter, sortSelect].forEach(input => input.addEventListener("input", renderRows));
    [stateFilter, startupFilter, sortSelect].forEach(input => input.addEventListener("change", renderRows));

    const observer = new MutationObserver(updateQueryButton);
    observer.observe(root, {subtree: true, childList: true, attributes: true, attributeFilter: ["data-endpoint-action"]});
    loadAvailability().then(() => { refreshButton.disabled = !state.available; });
    state.staleTimer = window.setInterval(updateFreshness, 60000);
})();
