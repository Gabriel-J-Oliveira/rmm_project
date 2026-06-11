(function () {
    const policyData = JSON.parse(document.getElementById("policy-data")?.textContent || "[]");
    const violations = JSON.parse(document.getElementById("policy-violations-data")?.textContent || "[]");
    const exceptions = JSON.parse(document.getElementById("policy-exceptions-data")?.textContent || "[]");
    const logsByPolicy = JSON.parse(document.getElementById("policy-logs-data")?.textContent || "{}");
    const endpointOptions = JSON.parse(document.getElementById("policy-endpoint-options")?.textContent || "[]");

    const overlay = document.querySelector("[data-policy-overlay]");
    const createDrawer = document.querySelector("[data-policy-create-drawer]");
    const detailDrawer = document.querySelector("[data-policy-detail-drawer]");
    const toast = document.getElementById("policy-toast");

    function refreshIcons() {
        if (window.lucide && typeof window.lucide.createIcons === "function") {
            window.lucide.createIcons();
        }
    }

    function showToast(message) {
        if (!toast) return;
        toast.textContent = message || "Políticas atualizadas";
        toast.hidden = false;
        toast.classList.add("is-visible");
        window.setTimeout(() => {
            toast.classList.remove("is-visible");
            toast.hidden = true;
        }, 2200);
    }

    function getCsrfToken(form) {
        return form?.querySelector("[name=csrfmiddlewaretoken]")?.value
            || document.querySelector("[name=csrfmiddlewaretoken]")?.value
            || "";
    }

    function openDrawer(drawer) {
        if (!drawer || !overlay) return;
        overlay.hidden = false;
        drawer.setAttribute("aria-hidden", "false");
        drawer.classList.add("is-open");
        document.body.classList.add("drawer-open");
        refreshIcons();
    }

    function closeDrawers() {
        overlay.hidden = true;
        [createDrawer, detailDrawer].forEach((drawer) => {
            if (!drawer) return;
            drawer.setAttribute("aria-hidden", "true");
            drawer.classList.remove("is-open");
        });
        document.body.classList.remove("drawer-open");
    }

    function escapeHtml(value) {
        return String(value || "")
            .replaceAll("&", "&amp;")
            .replaceAll("<", "&lt;")
            .replaceAll(">", "&gt;")
            .replaceAll('"', "&quot;");
    }

    function setActiveTab(name) {
        detailDrawer?.querySelectorAll("[data-policy-tab]").forEach((button) => {
            button.classList.toggle("active", button.dataset.policyTab === name);
        });
        detailDrawer?.querySelectorAll("[data-policy-panel]").forEach((panel) => {
            panel.classList.toggle("active", panel.dataset.policyPanel === name);
        });
    }

    function renderTargetEndpointSummary(policy) {
        const targets = policy.target_endpoints || [];
        if (policy.scope_type !== "specific_endpoints") return "Nao se aplica";
        if (!targets.length) return "Nenhum endpoint selecionado";
        return targets.map((item) => `<a href="${escapeHtml(item.url)}">${escapeHtml(item.hostname)}</a>`).join(" · ");
    }

    function renderDetail(policyId, initialTab) {
        const policy = policyData.find((item) => item.id === policyId) || policyData[0];
        if (!policy || !detailDrawer) return;

        detailDrawer.querySelector("[data-detail-title]").textContent = policy.name;
        detailDrawer.querySelector("[data-detail-description]").textContent = policy.description;
        detailDrawer.querySelector("[data-detail-overview]").innerHTML = `
            <div><span>Tipo</span><strong>${escapeHtml(policy.type_label)}</strong></div>
            <div><span>Software alvo</span><strong>${escapeHtml(policy.software)}</strong></div>
            <div><span>Correspondência</span><strong>${escapeHtml(policy.match_type)}</strong></div>
            <div><span>Escopo</span><strong>${escapeHtml(policy.scope)}</strong></div>
            <div><span>Severidade</span><strong>${escapeHtml(policy.severity_label)}</strong></div>
            <div><span>Status</span><strong>${escapeHtml(policy.status_label)}</strong></div>
            <div class="wide"><span>Comportamento esperado</span><strong>${policy.behavior.map(escapeHtml).join(" · ")}</strong></div>
            <div class="wide"><span>Endpoints-alvo</span><strong>${renderTargetEndpointSummary(policy)}</strong></div>
        `;

        const policyViolations = violations.filter((item) => item.policy_id === policy.id);
        detailDrawer.querySelector("[data-detail-violations]").innerHTML = policyViolations.length ? policyViolations.map((item) => `
            <div class="policy-table-row">
                <span class="policy-row-endpoint">
                    <strong>${escapeHtml(item.endpoint)}</strong>
                    <small><a href="${escapeHtml(item.endpoint_url)}">Ver endpoint</a></small>
                </span>
                <span class="policy-row-description">
                    <strong>${escapeHtml(item.software_name)}</strong>
                    <small>${escapeHtml(item.publisher)} · versão ${escapeHtml(item.software_version)}</small>
                </span>
                <span class="policy-row-meta">Visto: ${escapeHtml(item.last_seen_at)}</span>
                <span class="policy-row-status">
                    <span class="severity-badge severity-${escapeHtml(item.severity)}">${escapeHtml(item.severity_label)}</span>
                    <span class="policy-status status-${escapeHtml(item.status)}">${escapeHtml(item.status_label)}</span>
                </span>
                <span class="policy-row-action">
                    <small>${escapeHtml(item.alert_label)}</small>
                </span>
            </div>
        `).join("") : `
            <div class="policy-engine-empty">
                <i data-lucide="check-circle"></i>
                <strong>Nenhuma violação aberta para esta política.</strong>
                <p>Execute evaluate_software_policies para atualizar a avaliação com o inventário mais recente.</p>
            </div>
        `;

        const policyExceptions = exceptions.filter((item) => item.policy_id === policy.id);
        const exceptionRows = policyExceptions.map((item) => `
            <div class="policy-table-row">
                <span class="policy-row-endpoint"><strong>${escapeHtml(item.endpoint)}</strong><small>Criado por ${escapeHtml(item.created_by)}</small></span>
                <span class="policy-row-description"><strong>${escapeHtml(item.policy)}</strong><small>${escapeHtml(item.reason)}</small></span>
                <span class="policy-row-meta">${escapeHtml(item.expires_at)}</span>
                <span class="policy-row-status"><span class="policy-status status-${escapeHtml(item.status)}">${escapeHtml(item.status_label)}</span></span>
                <span class="policy-row-action">
                    <form method="post" action="/software-policies/exceptions/${escapeHtml(item.id)}/remove/">
                        ${document.querySelector("[name=csrfmiddlewaretoken]") ? `<input type="hidden" name="csrfmiddlewaretoken" value="${escapeHtml(document.querySelector("[name=csrfmiddlewaretoken]").value)}">` : ""}
                        <button type="submit">Remover</button>
                    </form>
                </span>
            </div>
        `).join("");
        const endpointSelectOptions = endpointOptions.map((item) => `<option value="${escapeHtml(item.id)}">${escapeHtml(item.label)}</option>`).join("");
        detailDrawer.querySelector("[data-detail-exceptions]").innerHTML = `
            <form class="policy-inline-exception-form" method="post" action="/software-policies/${escapeHtml(policy.id)}/exceptions/add/">
                ${document.querySelector("[name=csrfmiddlewaretoken]") ? `<input type="hidden" name="csrfmiddlewaretoken" value="${escapeHtml(document.querySelector("[name=csrfmiddlewaretoken]").value)}">` : ""}
                <select name="exception_endpoint" required><option value="">Endpoint</option>${endpointSelectOptions}</select>
                <input name="exception_reason" placeholder="Motivo">
                <select name="exception_type"><option value="temporary">Temporária</option><option value="permanent">Permanente</option></select>
                <input name="exception_expires_at" type="date">
                <button type="submit">Adicionar</button>
            </form>
            ${exceptionRows || '<div class="policy-engine-empty"><i data-lucide="list-plus"></i><strong>Nenhuma exceção cadastrada.</strong><p>Adicione endpoints acima para liberar exceções desta política.</p></div>'}
        `;

        const logs = logsByPolicy[policy.id] || [];
        detailDrawer.querySelector("[data-detail-logs]").innerHTML = logs.length ? logs.map((item) => `
            <li>
                <span class="severity-dot severity-dot-${escapeHtml(item.severity)}"></span>
                <div><strong>${escapeHtml(item.time)} — ${escapeHtml(item.title)}</strong><p>${escapeHtml(item.description)}</p></div>
            </li>
        `).join("") : '<li><span class="severity-dot"></span><div><strong>Nenhum evento registrado para esta política ainda.</strong><p>Eventos de criação, edição e exceções aparecerão aqui.</p></div></li>';

        setActiveTab(initialTab || "overview");
        openDrawer(detailDrawer);
        refreshIcons();
    }

    function resetPolicyForm() {
        const form = document.querySelector("[data-policy-create-form]");
        if (!form) return;
        form.reset();
        form.action = form.dataset.createAction;
        form.querySelector("[name=is_active]").value = "on";
        form.querySelector("[name=create_alert]").checked = true;
        form.querySelector("[name=show_in_noc]").checked = true;
        form.querySelector("[name=create_audit_event]").checked = true;
        document.querySelector("[data-policy-form-title]").textContent = "Nova política";
        document.querySelectorAll("[data-exception-row]").forEach((row, index) => {
            if (index > 0) row.remove();
        });
        document.querySelectorAll("[data-target-row]").forEach((row, index) => {
            if (index > 0) row.remove();
            if (index === 0) row.querySelector("select").value = "";
        });
        updateExceptionRemoveState();
        updateTargetRemoveState();
        form.querySelector("[data-scope-select]").dispatchEvent(new Event("change"));
    }

    function setRadioValue(form, name, value) {
        const field = form.querySelector(`[name=${name}][value="${CSS.escape(value)}"]`);
        if (field) field.checked = true;
    }

    function openEditPolicy(policyId) {
        const policy = policyData.find((item) => item.id === policyId);
        const form = document.querySelector("[data-policy-create-form]");
        if (!policy || !form) return;
        resetPolicyForm();
        form.action = `/software-policies/${policy.id}/update/`;
        document.querySelector("[data-policy-form-title]").textContent = "Editar política";
        form.querySelector("[name=name]").value = policy.name || "";
        form.querySelector("[name=description]").value = policy.description || "";
        form.querySelector("[name=is_active]").value = policy.is_active ? "on" : "";
        form.querySelector("[name=severity]").value = policy.severity || "info";
        setRadioValue(form, "policy_type", policy.type);
        form.querySelector("[name=software_name]").value = policy.software || "";
        form.querySelector("[name=match_type]").value = policy.match_type_value || "contains";
        form.querySelector("[name=publisher]").value = policy.publisher || "";
        form.querySelector("[name=version_rule]").value = policy.version === "Qualquer" ? "" : (policy.version || "");
        form.querySelector("[name=scope_type]").value = policy.scope_type || "all";
        form.querySelector("[name=scope_value]").value = policy.scope_value || "";
        setTargetRows(policy.target_endpoint_ids || []);
        form.querySelector("[name=create_alert]").checked = Boolean(policy.create_alert);
        form.querySelector("[name=show_in_noc]").checked = Boolean(policy.show_in_noc);
        form.querySelector("[name=create_audit_event]").checked = Boolean(policy.create_audit_event);
        form.querySelector("[name=monitor_only]").checked = Boolean(policy.monitor_only);
        form.querySelector("[data-scope-select]").dispatchEvent(new Event("change"));
        openDrawer(createDrawer);
    }

    function addTargetRow(value) {
        const list = document.querySelector("[data-target-builder-list]");
        const firstRow = list?.querySelector("[data-target-row]");
        if (!list || !firstRow) return null;
        const row = firstRow.cloneNode(true);
        row.querySelector("select").value = value || "";
        list.appendChild(row);
        updateTargetRemoveState();
        refreshIcons();
        return row;
    }

    function setTargetRows(values) {
        const rows = [...document.querySelectorAll("[data-target-row]")];
        rows.forEach((row, index) => {
            if (index > 0) row.remove();
        });
        const firstRow = document.querySelector("[data-target-row]");
        if (!firstRow) return;
        const targetValues = values.length ? values : [""];
        firstRow.querySelector("select").value = targetValues[0] || "";
        targetValues.slice(1).forEach((value) => addTargetRow(value));
        updateTargetRemoveState();
    }

    document.querySelector("[data-open-policy-create]")?.addEventListener("click", () => {
        resetPolicyForm();
        openDrawer(createDrawer);
    });
    document.querySelectorAll("[data-close-policy-drawers], [data-policy-overlay]").forEach((item) => item.addEventListener("click", closeDrawers));

    document.querySelectorAll("[data-open-policy-detail]").forEach((button) => {
        button.addEventListener("click", () => renderDetail(button.dataset.openPolicyDetail, button.dataset.detailTab));
    });

    document.querySelectorAll("[data-edit-policy]").forEach((button) => {
        button.addEventListener("click", () => openEditPolicy(button.dataset.editPolicy));
    });

    document.querySelectorAll("[data-policy-toggle-form]").forEach((form) => {
        form.addEventListener("submit", async (event) => {
            event.preventDefault();
            const button = form.querySelector("[data-policy-toggle-button]");
            const card = form.closest("[data-policy-card]");
            if (button) button.disabled = true;

            try {
                const response = await fetch(form.action, {
                    method: "POST",
                    headers: {
                        "Accept": "application/json",
                        "X-Requested-With": "XMLHttpRequest",
                        "X-CSRFToken": getCsrfToken(form),
                    },
                    body: new FormData(form),
                });
                const data = await response.json().catch(() => ({}));
                if (!response.ok || data.status !== "ok") {
                    throw new Error(data.detail || "Erro ao atualizar polÃ­tica.");
                }

                if (card) {
                    card.dataset.status = data.policy_status || "";
                    card.classList.toggle("policy-card-inactive", !data.is_active);
                    const badge = card.querySelector("[data-policy-status-badge]");
                    if (badge) {
                        badge.textContent = data.policy_status_label || (data.is_active ? "Ativa" : "Inativa");
                        badge.className = `policy-status status-${data.policy_status || (data.is_active ? "active" : "inactive")}`;
                    }
                }

                const policy = policyData.find((item) => item.id === data.policy_id);
                if (policy) {
                    policy.is_active = Boolean(data.is_active);
                    policy.status = data.policy_status || policy.status;
                    policy.status_label = data.policy_status_label || policy.status_label;
                }

                if (button) {
                    const icon = data.is_active ? "pause-circle" : "play-circle";
                    const label = data.button_label || (data.is_active ? "Desativar" : "Reativar");
                    button.innerHTML = `<i data-lucide="${icon}"></i><span>${escapeHtml(label)}</span>`;
                }

                showToast(data.message || (data.is_active ? "PolÃ­tica reativada" : "PolÃ­tica desativada"));
                refreshIcons();
            } catch (error) {
                showToast(error.message || "Erro ao atualizar polÃ­tica.");
            } finally {
                if (button) button.disabled = false;
            }
        });
    });

    document.addEventListener("click", (event) => {
        const trigger = event.target.closest("[data-policy-toast]");
        if (trigger) showToast(trigger.dataset.policyToast);
    });

    detailDrawer?.querySelectorAll("[data-policy-tab]").forEach((button) => {
        button.addEventListener("click", () => setActiveTab(button.dataset.policyTab));
    });

    document.querySelector("[data-scope-select]")?.addEventListener("change", (event) => {
        const wrap = document.querySelector("[data-scope-value-wrap]");
        const targetScope = document.querySelector("[data-target-scope]");
        const isSpecific = event.target.value === "specific_endpoints";
        if (wrap) wrap.hidden = event.target.value === "all" || isSpecific;
        if (targetScope) targetScope.hidden = !isSpecific;
    });

    document.addEventListener("click", (event) => {
        const trigger = event.target.closest("[data-confirm]");
        if (trigger && !window.confirm(trigger.dataset.confirm)) {
            event.preventDefault();
        }
    });

    function updateExceptionRemoveState() {
        const rows = document.querySelectorAll("[data-exception-row]");
        rows.forEach((row) => {
            const removeButton = row.querySelector("[data-remove-exception-row]");
            if (removeButton) removeButton.disabled = rows.length === 1;
        });
    }

    function updateTargetRemoveState() {
        const rows = document.querySelectorAll("[data-target-row]");
        rows.forEach((row) => {
            const removeButton = row.querySelector("[data-remove-target-row]");
            if (removeButton) removeButton.disabled = rows.length === 1;
        });
    }

    document.querySelector("[data-add-exception-row]")?.addEventListener("click", () => {
        const list = document.querySelector("[data-exception-builder-list]");
        const firstRow = list?.querySelector("[data-exception-row]");
        if (!list || !firstRow) return;
        const row = firstRow.cloneNode(true);
        row.querySelectorAll("input").forEach((input) => input.value = "");
        row.querySelectorAll("select").forEach((select) => select.selectedIndex = 0);
        list.appendChild(row);
        updateExceptionRemoveState();
        refreshIcons();
    });

    document.querySelector("[data-exception-builder-list]")?.addEventListener("click", (event) => {
        const button = event.target.closest("[data-remove-exception-row]");
        if (!button) return;
        const rows = document.querySelectorAll("[data-exception-row]");
        if (rows.length <= 1) {
            showToast("Mantenha pelo menos uma linha de exceção.");
            return;
        }
        button.closest("[data-exception-row]")?.remove();
        updateExceptionRemoveState();
    });

    document.querySelector("[data-add-target-row]")?.addEventListener("click", () => {
        addTargetRow("");
    });

    document.querySelector("[data-target-builder-list]")?.addEventListener("click", (event) => {
        const button = event.target.closest("[data-remove-target-row]");
        if (!button) return;
        const rows = document.querySelectorAll("[data-target-row]");
        if (rows.length <= 1) {
            showToast("Mantenha pelo menos uma linha de endpoint-alvo.");
            return;
        }
        button.closest("[data-target-row]")?.remove();
        updateTargetRemoveState();
    });

    updateExceptionRemoveState();
    updateTargetRemoveState();

    const filterForm = document.querySelector("[data-policy-filters]");
    filterForm?.addEventListener("submit", (event) => {
        event.preventDefault();
        const formData = new FormData(filterForm);
        const q = String(formData.get("q") || "").toLowerCase().trim();
        const type = String(formData.get("type") || "");
        const severity = String(formData.get("severity") || "");
        const status = String(formData.get("status") || "");
        const onlyViolations = formData.get("violations") === "on";
        const onlyExceptions = formData.get("exceptions") === "on";
        let visible = 0;
        document.querySelectorAll("[data-policy-card]").forEach((card) => {
            const matches = (!q || card.dataset.name.includes(q))
                && (!type || card.dataset.type === type)
                && (!severity || card.dataset.severity === severity)
                && (!status || card.dataset.status === status)
                && (!onlyViolations || Number(card.dataset.violations) > 0)
                && (!onlyExceptions || Number(card.dataset.exceptions) > 0);
            card.hidden = !matches;
            if (matches) visible += 1;
        });
        const empty = document.querySelector("[data-policy-empty]");
        if (empty) empty.hidden = visible > 0;
        showToast("Filtros aplicados.");
    });

    document.querySelector("[data-policy-clear]")?.addEventListener("click", () => {
        window.setTimeout(() => {
            document.querySelectorAll("[data-policy-card]").forEach((card) => card.hidden = false);
            const empty = document.querySelector("[data-policy-empty]");
            if (empty) empty.hidden = true;
            showToast("Filtros limpos.");
        }, 0);
    });
})();
