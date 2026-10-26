(function () {
    const STATUS_LABELS = {
        waiting: "Aguardando execu\u00e7\u00e3o",
        installing: "Instalando",
        confirmed: "Instala\u00e7\u00e3o confirmada por heartbeat",
        confirmation_pending: "Instalado; confirma\u00e7\u00e3o pendente de heartbeat",
        failed: "Falha",
        expired: "Token expirado"
    };
    window.NightOwlManualDeployment = { statusLabel: (status) => STATUS_LABELS[status] || "Status indispon\u00edvel" };

    const modal = document.querySelector("[data-deployment-modal]");
    const form = document.querySelector("[data-deployment-form]");
    if (!modal || !form) return;

    const backdrop = document.querySelector("[data-deployment-backdrop]");
    const optionsNode = document.getElementById("deployment-release-options");
    const releases = optionsNode ? JSON.parse(optionsNode.textContent || "[]") : [];
    const channel = form.querySelector("[data-deployment-channel]");
    const release = form.querySelector("[data-deployment-release]");
    const releaseField = form.querySelector("[data-deployment-release-field]");
    const version = form.querySelector("[data-deployment-version]");
    const error = form.querySelector("[data-deployment-error]");
    const commandBox = form.querySelector("[data-deployment-command-box]");
    const commandOutput = form.querySelector("[data-deployment-command]");
    const progress = form.querySelector("[data-deployment-progress]");
    const statusOutput = form.querySelector("[data-deployment-status]");
    const expiryOutput = form.querySelector("[data-deployment-expires]");
    const messageOutput = form.querySelector("[data-deployment-message]");
    const endpointOutput = form.querySelector("[data-deployment-endpoint]");
    const history = form.querySelector("[data-deployment-history]");
    const submit = form.querySelector("[data-deployment-submit]");
    const newButton = form.querySelector("[data-deployment-new]");
    const createButtonLabel = submit ? submit.textContent.trim() : "Gerar instala\u00e7\u00e3o";
    const historyUrl = form.dataset.historyUrl;
    let currentId = "";
    let pollTimer = null;
    let polling = false;

    function csrfToken() {
        return form.querySelector("input[name='csrfmiddlewaretoken']")?.value || "";
    }

    function showError(text) {
        error.textContent = text || "";
        error.hidden = !text;
    }

    function formatDate(value) {
        if (!value) return "N\u00e3o recebido";
        const date = new Date(value);
        return Number.isNaN(date.getTime()) ? "Data indispon\u00edvel" : new Intl.DateTimeFormat("pt-BR", { dateStyle: "short", timeStyle: "short" }).format(date);
    }

    function currentRelease() {
        if (channel.value === "stable") return releases.find((item) => item.channel === "stable" && item.status === "published" && !item.paused) || null;
        return releases.find((item) => item.id === release.value) || null;
    }

    function updateReleaseOptions() {
        release.replaceChildren();
        const development = channel.value === "development";
        releaseField.hidden = !development;
        if (development) {
            releases.filter((item) => item.channel === "development" && ["published", "paused"].includes(item.status))
                .forEach((item) => {
                    const option = document.createElement("option");
                    option.value = item.id;
                    option.textContent = `${item.version} (${item.status}${item.paused ? ", pausada" : ""})`;
                    release.appendChild(option);
                });
        }
        const selected = currentRelease();
        version.textContent = selected ? selected.version : "Sem release utiliz\u00e1vel";
        submit.disabled = !selected || Boolean(currentId) || form.dataset.trustReady !== "true";
        if (!currentId && form.dataset.trustReady === "true") {
            showError(!selected ? (development
                ? "N\u00e3o h\u00e1 release Development assinada compat\u00edvel com o trust bundle publicado."
                : "N\u00e3o h\u00e1 release Stable dispon\u00edvel. Selecione Development e confirme a vers\u00e3o aprovada para instalar.") : "");
        }
    }

    function setOpen(open) {
        modal.hidden = !open;
        if (backdrop) backdrop.hidden = !open;
        if (open) {
            if (form.dataset.trustReady !== "true") showError("O trust bundle publicado est\u00e1 ausente, expirado ou incompleto. A gera\u00e7\u00e3o fica bloqueada at\u00e9 corrigir a publica\u00e7\u00e3o.");
            else if (!currentId) updateReleaseOptions();
            loadHistory();
        }
    }

    function appendDetail(label, value, href) {
        const wrapper = document.createElement("div");
        const term = document.createElement("dt");
        const description = document.createElement("dd");
        term.textContent = label;
        if (href) {
            const link = document.createElement("a");
            link.href = href;
            link.textContent = value || "Abrir endpoint";
            link.rel = "noopener";
            description.appendChild(link);
        } else {
            description.textContent = value || "N\u00e3o informado";
        }
        wrapper.append(term, description);
        endpointOutput.appendChild(wrapper);
    }

    function showDeployment(item, preserveCommand) {
        currentId = item.id || currentId;
        progress.hidden = false;
        statusOutput.textContent = window.NightOwlManualDeployment.statusLabel(item.ui_status);
        expiryOutput.textContent = `Expira: ${formatDate(item.expires_at)}`;
        if (item.ui_status === "confirmed") {
            messageOutput.textContent = `Heartbeat de ${item.release_version} recebido em ${formatDate(item.heartbeat_at)}.`;
        } else if (item.ui_status === "confirmation_pending") {
            messageOutput.textContent = "O callback do instalador foi conclu\u00eddo, mas ainda n\u00e3o h\u00e1 heartbeat compat\u00edvel. N\u00e3o repita a instala\u00e7\u00e3o; aguarde e revise o endpoint.";
        } else if (item.ui_status === "failed") {
            messageOutput.textContent = `${item.failure_message || "A instala\u00e7\u00e3o falhou."}${item.failure_code ? ` C\u00f3digo: ${item.failure_code}.` : ""}`;
        } else if (item.ui_status === "expired") {
            messageOutput.textContent = "O token expirou. Gere uma nova instala\u00e7\u00e3o somente ap\u00f3s confirmar que a anterior n\u00e3o iniciou ou falhou de forma segura.";
        } else if (item.enrollment_confirmed) {
            messageOutput.textContent = "Endpoint registrado. Aguardando heartbeat da vers\u00e3o selecionada.";
        } else if (item.ui_status === "installing") {
            messageOutput.textContent = "O bootstrap iniciou. Acompanhe o resultado no computador e aguarde a confirma\u00e7\u00e3o do agente.";
        } else {
            messageOutput.textContent = "Aguardando que o comando seja executado no computador de destino.";
        }

        endpointOutput.replaceChildren();
        endpointOutput.hidden = !item.endpoint;
        if (item.endpoint) {
            appendDetail("Hostname", item.endpoint.hostname);
            appendDetail("Dom\u00ednio", item.endpoint.domain);
            appendDetail("Machine ID", item.endpoint.machine_id);
            appendDetail("Vers\u00e3o", item.endpoint.agent_version || item.release_version);
            appendDetail("Servi\u00e7o reportado", item.endpoint.service_status);
            appendDetail("Heartbeat posterior ao enrollment", formatDate(item.heartbeat_at));
            appendDetail("Endpoint", "Abrir endpoint no NightOwl", item.endpoint.url);
        }
        submit.disabled = true;
        if (newButton) newButton.hidden = !["confirmed", "confirmation_pending", "failed", "expired"].includes(item.ui_status);
        if (!preserveCommand) commandBox.hidden = true;
    }

    async function fetchStatus(id) {
        const url = `${form.dataset.createUrl}${encodeURIComponent(id)}/`;
        const response = await fetch(url, { credentials: "same-origin", cache: "no-store", headers: { Accept: "application/json" } });
        if (!response.ok) throw new Error("N\u00e3o foi poss\u00edvel consultar o status agora.");
        return (await response.json()).deployment;
    }

    function startPolling(id) {
        currentId = id;
        if (pollTimer) window.clearInterval(pollTimer);
        pollTimer = window.setInterval(async function () {
            if (polling) return;
            polling = true;
            try {
                const item = await fetchStatus(currentId);
                showDeployment(item, true);
                renderHistoryItem(item);
                if (["confirmed", "failed", "expired"].includes(item.ui_status)) {
                    window.clearInterval(pollTimer);
                    pollTimer = null;
                }
            } catch (fetchError) {
                messageOutput.textContent = "Status temporariamente indispon\u00edvel. O hist\u00f3rico pode ser atualizado sem gerar outro token.";
            } finally {
                polling = false;
            }
        }, 4000);
    }

    function renderHistoryItem(item) {
        const row = document.createElement("button");
        row.type = "button";
        row.className = "deployment-history-item";
        row.setAttribute("role", "listitem");
        row.dataset.deploymentId = item.id;
        const identity = item.endpoint ? `${item.endpoint.hostname}${item.endpoint.domain ? `.${item.endpoint.domain}` : ""}` : "Aguardando endpoint";
        row.textContent = `${identity} · ${item.release_version} · ${window.NightOwlManualDeployment.statusLabel(item.ui_status)} · ${formatDate(item.created_at)}`;
        row.addEventListener("click", async function () {
            showError("");
            try {
                const latest = await fetchStatus(item.id);
                showDeployment(latest, false);
                if (["waiting", "installing", "confirmation_pending"].includes(latest.ui_status)) startPolling(latest.id);
            } catch (fetchError) {
                showError(fetchError.message);
            }
        });
        const existing = Array.from(history.querySelectorAll("[data-deployment-id]")).find((entry) => entry.dataset.deploymentId === item.id);
        if (existing) existing.replaceWith(row);
        else history.prepend(row);
        if (history.children.length > 20) history.lastElementChild.remove();
        return row;
    }

    function renderHistory(items) {
        history.replaceChildren();
        if (!items.length) {
            const empty = document.createElement("p");
            empty.className = "deployment-history-empty";
            empty.textContent = "Nenhuma instala\u00e7\u00e3o recente.";
            history.appendChild(empty);
            return;
        }
        items.forEach((item) => renderHistoryItem(item));
    }

    async function loadHistory() {
        try {
            const response = await fetch(historyUrl, { credentials: "same-origin", cache: "no-store", headers: { Accept: "application/json" } });
            if (!response.ok) throw new Error("Hist\u00f3rico indispon\u00edvel.");
            const items = (await response.json()).deployments || [];
            renderHistory(items);
        } catch (fetchError) {
            history.replaceChildren();
            const message = document.createElement("p");
            message.className = "deployment-history-empty";
            message.textContent = fetchError.message;
            history.appendChild(message);
        }
    }

    function resetForNext() {
        if (pollTimer) window.clearInterval(pollTimer);
        pollTimer = null;
        currentId = "";
        commandOutput.textContent = "";
        commandBox.hidden = true;
        progress.hidden = true;
        showError("");
        newButton.hidden = true;
        submit.disabled = false;
        submit.textContent = createButtonLabel;
        channel.disabled = false;
        release.disabled = false;
        updateReleaseOptions();
    }

    document.querySelectorAll("[data-deployment-open]").forEach((button) => button.addEventListener("click", () => setOpen(true)));
    document.querySelectorAll("[data-deployment-close]").forEach((button) => button.addEventListener("click", () => setOpen(false)));
    if (backdrop) backdrop.addEventListener("click", () => setOpen(false));
    channel.addEventListener("change", updateReleaseOptions);
    release.addEventListener("change", updateReleaseOptions);
    newButton.addEventListener("click", resetForNext);
    form.querySelector("[data-deployment-copy]").addEventListener("click", async function () {
        const value = commandOutput.textContent;
        if (!value) return;
        try {
            await navigator.clipboard.writeText(value);
            this.textContent = "Copiado";
            window.setTimeout(() => { this.textContent = "Copiar comando"; }, 1600);
        } catch (copyError) {
            showError("N\u00e3o foi poss\u00edvel copiar automaticamente. Selecione o comando exibido e copie manualmente.");
        }
    });

    form.addEventListener("submit", async function (event) {
        event.preventDefault();
        showError("");
        const selected = currentRelease();
        if (!selected) {
            showError("N\u00e3o h\u00e1 uma release assinada e eleg\u00edvel para este canal. Revise as releases e o trust bundle publicados.");
            return;
        }
        submit.disabled = true;
        submit.textContent = "Gerando...";
        const payload = new FormData(form);
        payload.set("platform", "windows");
        if (channel.value === "stable") payload.delete("release_id");
        try {
            const response = await fetch(form.dataset.createUrl, {
                method: "POST", headers: { "X-CSRFToken": csrfToken(), Accept: "application/json" },
                body: payload, credentials: "same-origin", cache: "no-store"
            });
            if (response.redirected || !(response.headers.get("content-type") || "").includes("application/json")) {
                throw new Error("A sess\u00e3o expirou ou o acesso foi negado. Entre novamente no NightOwl e gere a instala\u00e7\u00e3o manual.");
            }
            const data = await response.json();
            if (!response.ok || data.status !== "ok") {
                const messages = {
                    trust_bundle_missing: "N\u00e3o h\u00e1 trust bundle publicado. Pe\u00e7a a publica\u00e7\u00e3o antes de instalar.",
                    trust_bundle_unavailable: "O trust bundle est\u00e1 fora da validade ou incompleto. Revise a publica\u00e7\u00e3o de confian\u00e7a.",
                    invalid_release: data.detail || "A release n\u00e3o est\u00e1 dispon\u00edvel para instala\u00e7\u00e3o."
                };
                throw new Error(messages[data.error] || data.detail || "N\u00e3o foi poss\u00edvel preparar a instala\u00e7\u00e3o.");
            }
            const deployment = data.deployment;
            currentId = deployment.id;
            commandOutput.textContent = deployment.command || "";
            commandBox.hidden = false;
            channel.disabled = true;
            release.disabled = true;
            version.textContent = deployment.release_version;
            showDeployment({ ...deployment, ui_status: "waiting", enrollment_confirmed: false, endpoint: null }, true);
            renderHistoryItem({ ...deployment, ui_status: "waiting", endpoint: null });
            startPolling(deployment.id);
        } catch (requestError) {
            showError(requestError.message || "Falha ao gerar a instala\u00e7\u00e3o.");
            submit.disabled = false;
            submit.textContent = createButtonLabel;
        }
    });

    updateReleaseOptions();
}());
