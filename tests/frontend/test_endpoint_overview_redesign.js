const fs = require("fs");
const path = require("path");

const root = path.resolve(__dirname, "..", "..");
const template = fs.readFileSync(path.join(root, "templates", "dashboard", "endpoint_detail.html"), "utf8");
const source = fs.readFileSync(path.join(root, "static", "js", "endpoint_detail.js"), "utf8");
const css = fs.readFileSync(path.join(root, "static", "css", "nightowl.css"), "utf8");

function assert(condition, message) {
  if (!condition) throw new Error(message);
}

const primaryStart = template.indexOf('<section class="endpoint-primary-row">');
const primaryEnd = template.indexOf("</section>", primaryStart);
const primary = template.slice(primaryStart, primaryEnd);
const expectedCardOrder = [
  'data-overview-card="health"',
  'data-overview-card="inventory"',
  'data-overview-card="agent"'
];
let previousIndex = -1;
for (const selector of expectedCardOrder) {
  const index = primary.indexOf(selector);
  assert(index > previousIndex, `primary row must order cards as ${expectedCardOrder.join(", ")}`);
  previousIndex = index;
}

const summaryStart = template.indexOf('<section class="endpoint-summary-row">');
const summaryEnd = template.indexOf("</section>", summaryStart);
const summary = template.slice(summaryStart, summaryEnd);
assert(summary.indexOf('data-overview-card="summary"') < summary.indexOf('data-overview-card="disks"'), "summary must precede disks");
assert(summary.indexOf('data-overview-card="disks"') < summary.indexOf('data-overview-card="security"'), "security must follow disks");
assert(template.includes('data-endpoint-action="update_agent" aria-label="Atualizar agente"'), "update action must be in the agent card header");
assert(template.includes('data-endpoint-action="repair_agent" aria-label="Reparar agente"'), "repair action must be in the agent card header");
assert(!template.includes('class="endpoint-card-actions"><button type="button" data-endpoint-action="update_agent"'), "old agent action strip must be removed");

const inventoryStart = source.indexOf("const board = asObject(inv.baseboardDetails);");
const inventoryEnd = source.indexOf('setSlot("disks-body"', inventoryStart);
const inventory = source.slice(inventoryStart, inventoryEnd);
for (const label of [
  "Fabricante / modelo", "Sistema operacional", "Processador", "Clock máximo da CPU",
  "Memória RAM", "Placa-mãe", "Modelo do disco principal", "Capacidade do disco principal",
  "Última sincronização do inventário"
]) assert(inventory.includes(label), `inventory summary must include ${label}`);
assert(inventory.includes('"Não informado"'), "missing inventory evidence must remain explicit");
assert(inventory.includes("const inventorySyncAt = inv.lastFullInventory || agent.lastInventoryAt;"), "inventory sync must use a collection timestamp, not heartbeat time");
assert(!inventory.includes("agent.lastHeartbeatAt"), "heartbeat timestamp must not be presented as inventory synchronization");
assert(inventory.includes('value: "Não informado"'), "physical disk model must not be inferred from a logical volume");

const actionsStart = source.indexOf("function renderQuickActions()");
const actionsEnd = source.indexOf("function jobResultLabel(job)", actionsStart);
const actions = source.slice(actionsStart, actionsEnd);
for (const action of ["force_inventory", "collect_software", "check_disk", "check_defender", "restart_agent", "update_agent"])
  assert(actions.includes(`actionButton("${action}"`), `quick actions must preserve ${action}`);
assert(!actions.includes("windows_update_scan") && !actions.includes("collect_logs"), "quick actions must contain only the six specified operations");

assert(source.includes("function latestOverviewJobs(jobs, activeJob, limit)"), "overview jobs must be sorted and bounded separately");
assert(source.includes("job.createdAt || job.created_at || job.dispatchedAt"), "overview jobs must be ordered by creation time");
assert(source.includes("const overviewJobLimit = detail.activeJob ? 5 : 6;"), "overview must show no more than six jobs including an active job");
assert(css.includes("grid-template-columns: repeat(3, minmax(0, 1fr)) !important;"), "primary row must use three equal columns");
assert(css.includes("grid-template-columns: minmax(0, 7fr) minmax(270px, 3fr) !important;"), "operations row must use the requested approximate 70/30 split");
assert(/\.endpoint-health-body\s*\{\s*height: 100% !important;\s*flex: 1 1 auto;[\s\S]*?justify-content: space-between !important;\s*gap: 18px !important;\s*padding: 18px 20px !important;/.test(css), "health indicators must use the available card height with increased spacing");
assert(css.includes("gap: 18px 24px !important;"), "health component rows must have more vertical and horizontal separation");
assert(/\.endpoint-jobs-card \.endpoint-job-history\s*\{\s*grid-template-columns: repeat\(2, minmax\(0, 1fr\)\)/.test(css), "job history must use two columns on desktop");
assert(css.includes(".endpoint-quick-action-button:hover") && css.includes("rgba(141, 104, 255, .2)"), "interactive quick actions must use purple hover");
assert(css.includes(".endpoint-overview-layout .endpoint-dense-card:hover"), "overview cards must not have a hover treatment");

console.log("endpoint overview redesign static checks passed");
