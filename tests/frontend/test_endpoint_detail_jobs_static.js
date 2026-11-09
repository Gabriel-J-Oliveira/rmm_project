const fs = require("fs");
const path = require("path");

const root = path.resolve(__dirname, "..", "..");
const source = fs.readFileSync(path.join(root, "static", "js", "endpoint_detail.js"), "utf8");
const template = fs.readFileSync(path.join(root, "templates", "dashboard", "endpoint_detail.html"), "utf8");

function assert(condition, message) {
  if (!condition) {
    throw new Error(message);
  }
}

assert(source.includes("function renderJobs(jobs, limit)"), "renderJobs must exist");
assert(source.includes("function renderJobItem(job, isActive)"), "renderJobItem must be explicit");
assert(source.includes("endpoint-active-job"), "active job highlight must be rendered");
assert(template.includes("&Uacute;ltimos jobs"), "jobs header must be rendered in the fixed panel");
assert(source.includes("NightOwlJobDisplay.measuredProgress"), "only measured job progress may be displayed");
assert(source.includes("data-copy-job-details"), "technical detail copy action must exist");
assert(source.includes('document.body.classList.add("endpoint-drawer-open")'), "endpoint job drawer uses a page-scoped scroll lock");
assert(source.includes("window.scrollTo(0, drawerScrollPosition)"), "closing the drawer restores the page position");
assert(source.includes("drawerReturnFocus.focus({ preventScroll: true })"), "closing the drawer restores focus");
assert(source.includes("function trapDrawerFocus(event)"), "keyboard focus remains inside the open drawer");
assert(template.includes('role="dialog" aria-modal="true" aria-hidden="true" aria-labelledby="endpoint-drawer-title"'), "endpoint drawer declares an accessible modal relationship");
assert(/if \(event\.key === "Escape"\) \{\s*closeDrawer\(\);\s*return;/.test(source), "Escape continues to close the endpoint drawer");
assert(source.includes("data-mark-job-failed"), "manual stale failure action must exist");
assert(source.includes("document.hidden"), "polling must slow down in background tabs");
assert(source.includes("String(endpointDetail.activeJob.id) === String(id)"), "active job must be findable from the drawer");
assert(source.includes("repair_agent"), "repair_agent action must be exposed in endpoint detail UI");
assert(source.includes("Deseja reparar o agente"), "repair_agent must ask for confirmation before creating a job");
assert(source.includes("Job de reparo do agente enfileirado"), "repair_agent must show a queued job toast");
assert(source.includes('uninstall_agent: "trash-2"'), "uninstall_agent jobs must render with the trash icon");
assert(source.includes("data-action-group=\"' + escapeHtml(groupId)"), "quick-action groups can be styled through a stable identifier");
assert(source.includes('], "inventory")'), "inventory quick actions use their own stable layout group");
assert(source.includes("function jobFactsMarkup(items)"), "job drawer filters unavailable facts from its presentation");
assert(source.includes("Identificacao tecnica e diagnostico"), "secondary identifiers remain available in the drawer");
assert(source.includes("jobJsonSection(\"Payload sanitizado\", job.payload)"), "sanitized payload is shown only when it contains data");
assert(source.includes("jobJsonSection(\"Resultado sanitizado\", job.resultJson)"), "sanitized result is shown only when it contains data");
assert(source.includes("function agentLifecycleActionButtons(detail)"), "uninstall lifecycle actions must remain available through the task catalog");
assert(source.includes("function agentManagementActionButtons(detail)"), "agent management actions must remain available through the task catalog");
assert(source.includes("function renderTaskPanel(detail)"), "task areas must update through stable containers");
assert(template.includes('data-endpoint-jobs-content'), "jobs must render inside the dedicated scrolling container");
assert(source.includes("const lifecycleButtons = agentLifecycleActionButtons(detail);"), "tasks tab must use shared lifecycle buttons");
assert(source.includes("const agentManagementButtons = agentManagementActionButtons(detail);"), "tasks tab must use shared agent management buttons");
assert(source.includes('actionButton("uninstall_agent", "Desinstalar agente", "trash-2")'), "tasks catalog must expose the uninstall action for installed endpoints");
assert(source.includes('if (isEndpointUninstalled(detail)) return [];'), "uninstalled endpoints must not offer a new uninstall action");
assert(source.includes('if (isEndpointUninstalled(endpointDetail) && ["update_agent", "repair_agent", "uninstall_agent", "purge_agent"].indexOf(action) >= 0)'), "stale clicks must not run lifecycle actions for uninstalled endpoints");
assert(source.includes('if (uninstallRequest) return [cancelUninstallButton(uninstallRequest)];'), "waiting uninstall requests must expose cancellation instead of duplicate submission");
assert(source.includes('data-cancel-uninstall'), "cancel uninstall action must reuse the existing cancel handler");
assert(source.includes("function cancelUninstallJobButton(job)"), "queued uninstall jobs must expose contextual cancellation");
assert(source.includes('if (jobStatus !== "queued" && jobStatus !== "pending") return "";'), "post-dispatch uninstall jobs must not expose cancellation");
assert(source.includes('if (uninstallRequest.job_id && String(uninstallRequest.job_id) !== String(job.id)) return "";'), "job card cancellation must match the request by job_id");
assert(source.includes('data-cancel-uninstall="\' + escapeHtml(uninstallRequest.id)'), "job card cancellation must use the uninstall request id");
assert(source.includes('const label = mode === "purge" ? "Cancelar purge" : "Cancelar desinstalacao";'), "purge and uninstall cancellation labels must be distinct");
assert(source.includes('Cancelar esta solicitacao de desinstalacao?'), "uninstall cancellation must ask for confirmation");
assert(source.includes('Cancelar esta solicitacao de purge?'), "purge cancellation must ask for confirmation");
assert(source.includes("openUninstallModal();"), "uninstall action must reuse the existing uninstall modal");
assert(source.includes('fetch("/api/endpoints/" + encodeURIComponent(id) + "/uninstall/"'), "uninstall flow must post to the existing backend endpoint");
assert(source.includes('if (job.status === "completed") return "Agente desinstalado";'), "completed uninstall jobs must show the uninstall-specific result label");
assert(source.includes('if (job.status === "expired") return "Desinstalacao expirada";'), "expired uninstall jobs must show a lifecycle-specific label");
assert(source.includes('actionButton("purge_agent", "Purgar agente e dados locais", "trash")'), "tasks catalog must expose purge as a destructive panel-only action");
assert(source.includes("function openPurgeModal()"), "purge must use a distinct destructive modal");
assert(source.includes('body.set("mode", options.mode);'), "lifecycle modal must send the selected mode to the existing backend endpoint");
assert(source.includes('body.set("hostname_confirmation", options.hostnameConfirmation);'), "purge must require hostname confirmation");
assert(source.includes('createUninstallRequest(username.value, password.value, { mode: "purge", hostnameConfirmation: confirmation.value })'), "purge modal must use reauth plus hostname confirmation");
assert(source.includes('return lifecycle === "uninstalled" || lifecycle === "purged";'), "uninstalled and purged endpoints must hide lifecycle actions");
const lifecycleGuardStart = source.indexOf("function isEndpointUninstalled(detail)");
const lifecycleGuardEnd = source.indexOf("function pendingUninstallRequest(detail)");
assert(lifecycleGuardStart >= 0 && lifecycleGuardEnd > lifecycleGuardStart, "endpoint lifecycle guard must be locatable");
const lifecycleGuardBody = source.slice(lifecycleGuardStart, lifecycleGuardEnd);
assert(!lifecycleGuardBody.includes("status"), "offline installed endpoints must still expose uninstall lifecycle actions");
assert(source.includes('if (value === "uninstall_agent" && mode === "purge") return "Purge do agente";'), "purge jobs must be labeled distinctly in history");
const quickActionsStart = source.indexOf("function renderQuickActions()");
const quickActionsEnd = source.indexOf("function jobResultLabel(job)");
assert(quickActionsStart >= 0 && quickActionsEnd > quickActionsStart, "renderQuickActions body must be locatable");
const quickActionsBody = source.slice(quickActionsStart, quickActionsEnd);
assert(!quickActionsBody.includes("purge_agent"), "purge must not be exposed in quick actions");
assert(source.includes('failed: "Falha"'), "failed jobs must render with the Falha badge");
assert(source.includes('if (job.status === "failed") return job.errorMessage || result.error_message || result.message || "Falha no reparo do agente";'), "failed repair jobs must show the real error message");

const renderJobItemMatch = source.match(/function renderJobItem\(job, isActive\) \{[\s\S]*?\n    \}/);
assert(renderJobItemMatch, "renderJobItem body must be parsable");
assert(!renderJobItemMatch[0].includes("data-refresh-endpoint"), "job rows must not render repeated refresh buttons");

console.log("endpoint_detail job static checks passed");
