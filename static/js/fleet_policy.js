(function () {
    "use strict";
    const reasonLabels = {eligible: "Elegivel", endpoint_offline: "Offline", endpoint_stale: "Heartbeat desatualizado", protected_endpoint_group: "Grupo protegido", update_job_active: "Update ativo", update_job_stale: "Update antigo", release_paused: "Release pausada"};
    function label(code) { return reasonLabels[code] || String(code); }
    function selected(select) { return Array.from(select.selectedOptions, option => option.value); }
    function rows(body, values) {
        body.replaceChildren();
        values.forEach(values => {
            const tr = document.createElement("tr");
            values.forEach(value => { const td = document.createElement("td"); td.textContent = String(value ?? ""); tr.append(td); });
            body.append(tr);
        });
    }
    async function post(url, payload, form) {
        const response = await fetch(url, {method: "POST", credentials: "same-origin", headers: {"Content-Type": "application/json", "X-CSRFToken": form.elements.csrfmiddlewaretoken.value}, body: JSON.stringify(payload)});
        const data = await response.json();
        if (!response.ok) { const error = new Error(data.error || "Operacao falhou"); error.data = data; error.status = response.status; throw error; }
        return data;
    }
    function lockForm(form, locked) {
        Array.from(form.elements).forEach(input => { input.disabled = locked; });
    }
    const previewRoot = document.querySelector("[data-fleet-preview]");
    if (previewRoot) {
        const form = previewRoot.querySelector("form"), validate = previewRoot.querySelector("[data-fleet-validate]"), status = previewRoot.querySelector("[data-fleet-status]");
        let previous = null, busy = false;
        function render(data) {
            const groups = Object.fromEntries((data.group_options || []).map(group => [group.id, group.name]));
            const groupNames = ids => ids.map(id => groups[id] || id).join(", ");
            previewRoot.querySelector("[data-fleet-summary]").textContent = `${data.total_candidates} candidatos · ${data.eligible_count} elegiveis (${data.eligible_percentage}%) · ${data.excluded_count} excluidos · ${data.cohort_hash.slice(0, 16)} · ${data.generated_at}`;
            const release = data.release;
            previewRoot.querySelector("[data-fleet-release]").textContent = `${release.version} · ${release.channel} · ${release.status} · assinatura: ${release.signature_valid ? "valida" : "invalida"} · rollout: ${release.rollout_percentage}% · paused: ${release.rollout_paused} · grupos: ${groupNames(release.allowed_groups) || "Todos"}`;
            const reasons = previewRoot.querySelector("[data-fleet-reasons]"); reasons.replaceChildren();
            Object.entries(data.reason_counts).forEach(([code, count]) => { const li = document.createElement("li"); li.textContent = `${label(code)}: ${count}`; reasons.append(li); });
            rows(previewRoot.querySelector("tbody"), data.targets.map(target => [target.hostname, target.current_version, target.updater_version, target.channel, target.policy, groupNames(target.groups), target.bucket, target.eligible ? "Sim" : "Nao", label(target.reason_code)]));
        }
        form.addEventListener("input", () => { previous = null; validate.disabled = true; });
        async function run(isValidation) {
            if (busy || (isValidation && !previous)) return;
            busy = true; validate.disabled = true; lockForm(form, true);
            const releaseId = form.elements.release_id.value;
            const filters = {target_group_ids: selected(form.elements.target_group_ids), freshness_seconds: Number(form.elements.freshness_seconds.value)};
            try {
                const payload = isValidation ? {...filters, expected_cohort_hash: previous.cohort_hash, cohort_schema: previous.cohort_schema} : filters;
                const data = await post(`/api/agent/releases/${releaseId}/rollout-preview/${isValidation ? "validate/" : ""}`, payload, form);
                previous = data.preview || data; render(previous); status.textContent = isValidation ? "Preview corresponde ao estado atual." : "Preview atualizado.";
            } catch (error) {
                previous = null;
                if (error.status === 409 && error.data.preview) render(error.data.preview);
                status.textContent = error.status === 409 ? "Preview mudou. Atualize antes de validar." : error.message;
            } finally { busy = false; lockForm(form, false); validate.disabled = !previous; }
        }
        form.addEventListener("submit", event => { event.preventDefault(); run(false); });
        validate.addEventListener("click", () => run(true));
    }
    const dialog = document.querySelector("[data-fleet-bulk]");
    if (!dialog) return;
    const form = dialog.querySelector("form"), apply = dialog.querySelector("[data-fleet-apply]"), confirm = dialog.querySelector("[data-fleet-confirm]"), status = dialog.querySelector("[data-fleet-status]");
    let request = null, plan = null, busy = false, revision = 0;
    function invalidate() { revision += 1; plan = null; request = null; confirm.checked = false; apply.disabled = true; }
    document.querySelector("[data-fleet-open]").addEventListener("click", () => { invalidate(); dialog.showModal(); });
    dialog.querySelector("[data-fleet-close]").addEventListener("click", () => dialog.close());
    form.addEventListener("input", invalidate);
    document.querySelectorAll("[data-endpoint-select], [data-endpoint-select-all]").forEach(input => input.addEventListener("change", invalidate));
    confirm.addEventListener("change", () => { apply.disabled = busy || !confirm.checked || !plan || plan.rejected_count > 0 || plan.applied; });
    function render(data) {
        dialog.querySelector("[data-fleet-summary]").textContent = `${data.selected_count} selecionados · ${data.changed_count} alterados · ${data.rejected_count} rejeitados`;
        rows(dialog.querySelector("tbody"), data.targets.map(target => [target.hostname, JSON.stringify(target.before), JSON.stringify(target.after), [...target.warnings.map(label), target.rejection].filter(Boolean).join(" · ")]));
    }
    async function run(isApply) {
        if (busy || (isApply && (!plan || !confirm.checked || plan.rejected_count))) return;
        busy = true; apply.disabled = true; lockForm(form, true);
        const currentRevision = revision;
        try {
            if (!isApply) {
                const changes = {};
                ["update_channel", "update_policy", "auto_update_enabled", "update_paused"].forEach(key => { const value = form.elements[key].value; if (value) changes[key] = ["true", "false"].includes(value) ? value === "true" : value; });
                if (form.elements.edit_pin.checked) changes.pinned_agent_version = form.elements.pinned_agent_version.value;
                if (form.elements.edit_window.checked) ["maintenance_window_start", "maintenance_window_end", "maintenance_window_timezone"].forEach(key => { changes[key] = form.elements[key].value || (key === "maintenance_window_timezone" ? "" : null); });
                if (form.elements.group_action.value) changes.groups = {action: form.elements.group_action.value, ids: selected(form.elements.group_ids)};
                request = {endpoint_ids: Array.from(document.querySelectorAll("[data-fleet-endpoint-id]:checked"), input => input.dataset.fleetEndpointId), changes, reason: form.elements.reason.value, apply: false};
            }
            const payload = isApply ? {...request, apply: true, confirmed_count: plan.selected_count, expected_bulk_change_hash: plan.bulk_change_hash} : request;
            const result = await post("/api/endpoints/bulk-policy/", payload, form); render(result);
            if (!isApply && currentRevision !== revision) { invalidate(); status.textContent = "Selecao mudou. Refaca o preview."; return; }
            plan = result;
            status.textContent = isApply ? "Politicas aplicadas. Nenhum job criado." : "Impacto calculado.";
            confirm.checked = false;
        } catch (error) {
            invalidate(); if (error.data && error.data.preview) render(error.data.preview);
            status.textContent = error.status === 409 ? "Estado mudou ou alteracao bloqueada. Refaca o preview." : error.message;
        } finally { busy = false; lockForm(form, false); apply.disabled = true; }
    }
    form.addEventListener("submit", event => { event.preventDefault(); run(false); });
    apply.addEventListener("click", () => run(true));
}());
