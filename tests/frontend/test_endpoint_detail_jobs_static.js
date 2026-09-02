const fs = require("fs");
const path = require("path");

const root = path.resolve(__dirname, "..", "..");
const source = fs.readFileSync(path.join(root, "static", "js", "endpoint_detail.js"), "utf8");

function assert(condition, message) {
  if (!condition) {
    throw new Error(message);
  }
}

assert(source.includes("function renderJobs(jobs, limit)"), "renderJobs must exist");
assert(source.includes("function renderJobItem(job, isActive)"), "renderJobItem must be explicit");
assert(source.includes("endpoint-active-job"), "active job highlight must be rendered");
assert(source.includes("Historico recente"), "recent history section must be rendered");
assert(source.includes("progressPercentage") && source.includes("progress_percentage"), "backend progress fields must be consumed");
assert(source.includes("data-copy-job-details"), "technical detail copy action must exist");
assert(source.includes("data-mark-job-failed"), "manual stale failure action must exist");
assert(source.includes("document.hidden"), "polling must slow down in background tabs");
assert(source.includes("String(endpointDetail.activeJob.id) === String(id)"), "active job must be findable from the drawer");
assert(source.includes("repair_agent"), "repair_agent action must be exposed in endpoint detail UI");
assert(source.includes("Deseja reparar o agente"), "repair_agent must ask for confirmation before creating a job");
assert(source.includes("Job de reparo do agente enfileirado"), "repair_agent must show a queued job toast");
assert(source.includes('uninstall_agent: "trash-2"'), "uninstall_agent jobs must render with the trash icon");
assert(source.includes("function agentLifecycleActionButtons(detail)"), "uninstall lifecycle buttons must be shared by quick actions and tasks");
assert(source.includes("function agentManagementActionButtons(detail)"), "agent management buttons must be shared by quick actions and tasks");
assert(source.includes("const lifecycleButtons = agentLifecycleActionButtons(endpointDetail);"), "quick actions must use shared lifecycle buttons");
assert(source.includes("const lifecycleButtons = agentLifecycleActionButtons(detail);"), "tasks tab must use shared lifecycle buttons");
assert(source.includes("const agentManagementButtons = agentManagementActionButtons(endpointDetail);"), "quick actions must use shared agent management buttons");
assert(source.includes("const agentManagementButtons = agentManagementActionButtons(detail);"), "tasks tab must use shared agent management buttons");
assert(source.includes('actionButton("uninstall_agent", "Desinstalar agente", "trash-2")'), "tasks catalog must expose the uninstall action for installed endpoints");
assert(source.includes('if (isEndpointUninstalled(detail)) return [];'), "uninstalled endpoints must not offer a new uninstall action");
assert(source.includes('if (isEndpointUninstalled(endpointDetail) && ["update_agent", "repair_agent", "uninstall_agent", "purge_agent"].indexOf(action) >= 0)'), "stale clicks must not run lifecycle actions for uninstalled endpoints");
assert(source.includes('if (uninstallRequest) return [cancelUninstallButton(uninstallRequest)];'), "waiting uninstall requests must expose cancellation instead of duplicate submission");
assert(source.includes('data-cancel-uninstall'), "cancel uninstall action must reuse the existing cancel handler");
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
