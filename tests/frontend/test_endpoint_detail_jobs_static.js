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
assert(source.includes('failed: "Falha"'), "failed jobs must render with the Falha badge");
assert(source.includes('if (job.status === "failed") return job.errorMessage || result.error_message || result.message || "Falha no reparo do agente";'), "failed repair jobs must show the real error message");

const renderJobItemMatch = source.match(/function renderJobItem\(job, isActive\) \{[\s\S]*?\n    \}/);
assert(renderJobItemMatch, "renderJobItem body must be parsable");
assert(!renderJobItemMatch[0].includes("data-refresh-endpoint"), "job rows must not render repeated refresh buttons");

console.log("endpoint_detail job static checks passed");
